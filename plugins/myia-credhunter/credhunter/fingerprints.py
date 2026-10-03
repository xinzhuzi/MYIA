"""密钥指纹库:联合正则 + 细正则 + 归因表 + 噪声过滤(credhunter R3)。

行为规格(behavior-specs/fingerprints.md §7 + ghhunt.md §4-§5),语义
自实现、零上游代码:

1. **联合正则**(JOINT_KEY_RE,GitHub 泳道主扫描):单一正则覆盖十大
   密钥族 —— sk-(≥16)/AIza(≥20)/gsk_/nvapi-/r8_/xai-/ksk_(≥16)/
   crsr_(≥32)/pt-(≥16)/ABSK(≥20,字符类含 ``+=/.``);同文本内按命中
   串去重。
2. **细正则**(FINE_KEY_PATTERNS,17 条):比联合正则更窄的家族级模式
   (sk-or-v1- 带 hex 约束、pplx-/hf_/fw_/ghp_/JWT eyJ…/32hex.16alnum
   等),承担**供应商归因**(联合正则只给密钥族,细正则给到供应商)。
   与规格点名示例的差异:裸 ``key_`` 泛型前缀信噪比过低,以家族化细正则
   补足至 17 条,规格点名的五类示例全部在列。
3. **变量名归因表**(PROVIDER_HINTS,约 40 个子串提示):上下文变量名
   (先剥离 _API_KEY/_TOKEN 等后缀)→ 供应商;长键先匹配(azure_openai
   先于 openai 这类包含关系由长度排序保证)。
4. **apiurl 前缀归因**(PREFIX_APIURL_HINT):密钥前缀 → 官方地址的
   静态映射(xai-/ksk_/crsr_/pt-/ABSK/AIza 七项,规格 §4 点名);调用方
   给了 endpoint(通常是命中项 URL)则直接用,windsurf 服务键靠上下文
   变量名归因 server.codeium.com。
5. **噪声过滤**:命中串长度 <15、占位噪声子串(39 项,example/dummy/
   your-key 类)、连续 8 个字母序递增段(abcdefgh 类键盘序样例键)、
   阻断格式(gocspx- 前缀 / AKIA AWS 访问键 / 纯 32 位 hex)一律丢弃;
   同一 key 出现于 >5 个不同位置按蜜罐/教程键剔除(跨文档预过滤)。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "BLOCKED_KEY_PREFIXES",
    "CONTEXT_APIURL_HINT",
    "FINE_KEY_PATTERNS",
    "JOINT_KEY_RE",
    "JOINT_KEY_FAMILIES",
    "MIN_KEY_LENGTH",
    "NOISE_SUBSTRINGS",
    "OVEREXPOSED_LOCATION_LIMIT",
    "PREFIX_APIURL_HINT",
    "PROVIDER_HINTS",
    "VARIABLE_SUFFIXES",
    "KeyHit",
    "attribute_apiurl",
    "attribute_by_variable",
    "drop_overexposed",
    "extract_secrets",
    "has_alphabet_run",
    "is_blocked_format",
    "is_noise",
    "strip_variable_suffix",
]

# ---------------------------------------------------------------------------
# 1) 联合正则:十大密钥族(单正则,命名组即族名)
# ---------------------------------------------------------------------------

#: 十大族联合正则(ghhunt.md §4)。命名组 = 族标签;sk_ 泛族归因靠细正则/
#: 变量名兜底(规格明示 sk- 无前缀分支)。
JOINT_KEY_RE = re.compile(
    r"\bsk-(?P<sk_generic>[A-Za-z0-9_-]{16,})"
    r"|\bAIza(?P<google_api>[A-Za-z0-9_-]{20,})"
    r"|\bgsk_(?P<groq>[A-Za-z0-9]{16,})"
    r"|\bnvapi-(?P<nvidia>[A-Za-z0-9_-]{16,})"
    r"|\br8_(?P<replicate>[A-Za-z0-9]{16,})"
    r"|\bxai-(?P<xai>[A-Za-z0-9_-]{16,})"
    r"|\bksk_(?P<kiro>[A-Za-z0-9_-]{16,})"
    r"|\bcrsr_(?P<cursor>[A-Za-z0-9_-]{32,})"
    r"|\bpt-(?P<qoder>[A-Za-z0-9_-]{16,})"
    r"|\bABSK(?P<aws_bedrock>[A-Za-z0-9+/.=]{20,})"
)

#: 联合正则族标签 → 默认供应商归因(sk_generic 刻意留空:泛 sk- 网关键
#: 太多,归因交给细正则与变量名;其余前缀即供应商)。
_JOINT_FAMILY_PROVIDER = {
    "sk_generic": "",
    "google_api": "gemini",
    "groq": "groq",
    "nvidia": "nvidia",
    "replicate": "replicate",
    "xai": "xai",
    "kiro": "kiro",
    "cursor": "cursor",
    "qoder": "qoder",
    "aws_bedrock": "aws_bedrock",
}

#: 十大族标签清单(诊断/测试用)。
JOINT_KEY_FAMILIES = tuple(_JOINT_FAMILY_PROVIDER)

# ---------------------------------------------------------------------------
# 2) 细正则:17 条家族级模式(承担供应商归因)
# ---------------------------------------------------------------------------

_FINE = (
    # provider, 模式(编译后的正则)
    ("openrouter", r"\bsk-or-v1-[a-f0-9-]{30,}"),
    ("openai", r"\bsk-(?:proj|admin|svcacct)-[A-Za-z0-9_-]{20,}"),
    ("anthropic", r"\bsk-ant-[A-Za-z0-9_-]{16,}"),
    ("gemini", r"\bAIza[A-Za-z0-9_-]{20,}"),
    ("groq", r"\bgsk_[A-Za-z0-9]{20,}"),
    ("nvidia", r"\bnvapi-[A-Za-z0-9_-]{20,}"),
    ("replicate", r"\br8_[A-Za-z0-9]{16,}"),
    ("xai", r"\bxai-[A-Za-z0-9_-]{16,}"),
    ("kiro", r"\bksk_[A-Za-z0-9_-]{16,}"),
    ("cursor", r"\bcrsr_[A-Za-z0-9_-]{32,}"),
    ("qoder", r"\bpt-[A-Za-z0-9_-]{16,}"),
    ("perplexity", r"\bpplx-[A-Za-z0-9]{20,}"),
    ("huggingface", r"\bhf_[A-Za-z0-9]{20,}"),
    ("fireworks", r"\bfw_[A-Za-z0-9]{20,}"),
    ("github", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{36,}"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"),
    ("glm", r"\b[0-9a-f]{32}\.[A-Za-z0-9]{16,}\b"),
)

#: 细正则清单(供应商, 编译正则)——恰好 17 条,顺序即优先级。
FINE_KEY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (provider, re.compile(pattern)) for provider, pattern in _FINE
)

# ---------------------------------------------------------------------------
# 3) 变量名归因表 + 后缀剥离
# ---------------------------------------------------------------------------

#: 变量名提示子串(小写匹配):剥后缀后的变量名含子串即归因该供应商。
#: 长子串先匹配(模块加载期按长度降序排序,保证 azure_openai 先于 openai
#: 这类包含关系)。
_PROVIDER_HINT_RAW = (
    ("azure_openai", "azure_openai"),
    ("aws_bedrock", "bedrock"),
    ("aws_bedrock", "aws_bearer"),
    ("windsurf", "windsurf"),
    ("windsurf", "codeium"),
    ("gemini", "generative_language"),
    ("gemini", "gemini"),
    ("gemini", "google_api"),
    ("anthropic", "anthropic"),
    ("anthropic", "claude"),
    ("openai", "openai"),
    ("xai", "grok"),
    ("xai", "xai"),
    ("qoder", "qoder"),
    ("kiro", "kiro"),
    ("cursor", "cursor"),
    ("glm", "zhipu"),
    ("glm", "bigmodel"),
    ("glm", "glm"),
    ("qwen", "dashscope"),
    ("qwen", "qwen"),
    ("kimi", "moonshot"),
    ("kimi", "kimi"),
    ("deepseek", "deepseek"),
    ("minimax", "minimax"),
    ("longcat", "longcat"),
    ("siliconflow", "siliconflow"),
    ("groq", "groq"),
    ("openrouter", "openrouter"),
    ("together", "together"),
    ("fireworks", "fireworks"),
    ("replicate", "replicate"),
    ("cohere", "cohere"),
    ("nvidia", "nvapi"),
    ("nvidia", "nvidia"),
    ("ksyun", "ksyun"),
    ("ksyun", "kingsoft"),
    ("perplexity", "pplx"),
    ("perplexity", "perplexity"),
    ("huggingface", "hf_token"),
    ("huggingface", "huggingface"),
    ("github", "github"),
)

#: 归因表(子串降序,首命中即归因)。
PROVIDER_HINTS: tuple[tuple[str, str], ...] = tuple(
    sorted(_PROVIDER_HINT_RAW, key=lambda pair: (-len(pair[1]), pair[1]))
)

#: 变量名常见后缀(剥离后再查归因表;ghhunt/fingerprints 规格语义)。
VARIABLE_SUFFIXES = (
    "_PERSONAL_ACCESS_TOKEN",
    "_ACCESS_TOKEN",
    "_SERVICE_KEY",
    "_API_TOKEN",
    "_API_KEY",
    "_TOKEN",
    "_KEY",
    "_SECRET",
    "_PAT",
)

# ---------------------------------------------------------------------------
# 4) apiurl 前缀归因(静态官方地址映射,规格 §4 点名七项)
# ---------------------------------------------------------------------------

#: 密钥前缀 → 官方地址(调用方未给 endpoint 时的归因通道;windsurf 特例
#: 无前缀,靠上下文变量名走 CONTEXT_APIURL_HINT)。
PREFIX_APIURL_HINT = {
    "xai-": "https://api.x.ai/v1",
    "ksk_": "https://app.kiro.dev",
    "crsr_": "https://api.cursor.com",
    "pt-": "https://api.qoder.com",
    "ABSK": "https://bedrock-runtime.us-east-1.amazonaws.com",
    "AIza": "https://generativelanguage.googleapis.com",
    "sk-or-": "https://openrouter.ai/api",
}

#: 上下文变量名子串 → 官方地址(windsurf 服务键归因,规格 §4/§6)。
CONTEXT_APIURL_HINT = {
    "windsurf_service_key": "https://server.codeium.com/api/v1",
    "codeium_service_key": "https://server.codeium.com/api/v1",
}

# ---------------------------------------------------------------------------
# 5) 噪声过滤
# ---------------------------------------------------------------------------

#: 命中串最短长度(<15 一律噪声)。
MIN_KEY_LENGTH = 15

#: 占位/示例噪声子串(小写包含匹配;39 项,规格 §7.2 语义对齐)。
NOISE_SUBSTRINGS = frozenset({
    "example", "sample", "dummy", "placeholder", "your-key", "your_key",
    "your-api-key", "yourtoken", "your-token", "yourkey", "your_api_key",
    "paste_here", "insert_", "replace_me", "changeme", "change-me",
    "notareal", "fake", "test_key", "sk-mocha", "sk-proj-xxxx",
    "sk-test", "abc123", "abcd1234", "1234567890", "aaaaaaaaaa",
    "0123456789", "abcdefgh", "deadbeef", "default", "redacted",
    "revoked", "invalid", "undefined", "xxxxx", "yyyyy", "zzzzz",
    "todo", "fixme", "secretkey", "apikeyhere", "internal-use",
})

#: 阻断前缀(刻意不猎取的格式:gocspx- 类闭源网关示范键)。
BLOCKED_KEY_PREFIXES = ("gocspx-",)

#: AWS 访问键 ID(AKIA 开头)属另一类凭据管理面,不属密钥猎取面 → 阻断。
_AWS_ACCESS_KEY_RE = re.compile(r"^AKIA[A-Z0-9]{16}$")

#: 纯 32 位 hex(与真实 API key 无特征差异的 UUID/摘要噪声)→ 阻断。
_PURE_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")

#: 连续字母序递增段的最小长度(abcdefgh 类键盘序样例键)。
_ALPHABET_RUN = 8

#: 同一 key 出现于多少个不同位置即按教程/蜜罐键剔除(跨文档预过滤)。
OVEREXPOSED_LOCATION_LIMIT = 5

#: 变量赋值上下文正则缓存(key → 模式);设上限防长扫描进程无界增长。
_VARIABLE_CONTEXT_RE_CACHE: dict[str, re.Pattern[str]] = {}
_VARIABLE_CONTEXT_RE_CACHE_LIMIT = 4096


def _variable_context_re(apikey: str) -> re.Pattern[str]:
    pattern = _VARIABLE_CONTEXT_RE_CACHE.get(apikey)
    if pattern is None:
        pattern = re.compile(
            r"(?P<var>[A-Za-z_][A-Za-z0-9_]{1,63})\s*[:=]\s*[\"']?" + re.escape(apikey)
        )
        if len(_VARIABLE_CONTEXT_RE_CACHE) < _VARIABLE_CONTEXT_RE_CACHE_LIMIT:
            _VARIABLE_CONTEXT_RE_CACHE[apikey] = pattern
    return pattern


def strip_variable_suffix(name: str) -> str:
    """剥离变量名常见凭据后缀(_API_KEY/_TOKEN/…),返回词干。"""
    stem = name
    for suffix in VARIABLE_SUFFIXES:
        if stem.endswith(suffix) and len(stem) > len(suffix):
            return stem[: -len(suffix)]
    return stem


def attribute_by_variable(name: str) -> str | None:
    """变量名(或其剥后缀词干)→ 供应商提示;未命中返回 None。

    匹配口径:小写包含,长子串优先(``AZURE_OPENAI_API_KEY`` → 词干
    ``AZURE_OPENAI`` → azure_openai,不会被泛 ``openai`` 抢先)。
    """
    lowered = name.lower()
    stem = strip_variable_suffix(lowered)
    for provider, substring in PROVIDER_HINTS:
        if substring in lowered or substring in stem:
            return provider
    return None


def attribute_apiurl(apikey: str, *, variable: str | None = None, endpoint: str | None = None) -> str:
    """密钥 → 官方 API 地址归因(ghhunt.md §4 语义)。

    优先级:调用方 endpoint(通常是命中项 URL)→ 前缀静态映射 → windsurf
    上下文变量名 → 空串(交由验证层 resolve 兜底)。
    """
    if endpoint:
        return endpoint
    for prefix, url in PREFIX_APIURL_HINT.items():
        if apikey.startswith(prefix):
            return url
    if variable:
        lowered = variable.lower()
        for substring, url in CONTEXT_APIURL_HINT.items():
            if substring in lowered:
                return url
    return ""


def has_alphabet_run(text: str, run: int = _ALPHABET_RUN) -> bool:
    """检测连续字母序递增段(如 abcdefgh):样例键/教程键的强特征。"""
    length = len(text)
    if length < run:
        return False
    ascending = 0
    for index in range(1, length):
        current, previous = text[index], text[index - 1]
        if current.isalpha() and previous.isalpha() and current.lower() == chr(ord(previous.lower()) + 1):
            ascending += 1
            if ascending >= run - 1:
                return True
        else:
            ascending = 0
    return False


def is_blocked_format(apikey: str) -> bool:
    """阻断格式:gocspx- 前缀 / AKIA AWS 访问键 / 纯 32 位 hex。"""
    if any(apikey.startswith(prefix) for prefix in BLOCKED_KEY_PREFIXES):
        return True
    if _AWS_ACCESS_KEY_RE.match(apikey):
        return True
    return bool(_PURE_HEX32_RE.match(apikey))


def is_noise(apikey: str) -> bool:
    """综合噪声判定:过短 / 占位子串 / 字母序递增段 / 阻断格式。"""
    if len(apikey) < MIN_KEY_LENGTH:
        return True
    lowered = apikey.lower()
    if any(substring in lowered for substring in NOISE_SUBSTRINGS):
        return True
    if has_alphabet_run(apikey):
        return True
    return is_blocked_format(apikey)


def drop_overexposed(
    keys_by_location: Mapping[str, Sequence[str]], *, limit: int = OVEREXPOSED_LOCATION_LIMIT
) -> set[str]:
    """跨位置预过滤:同一 key 出现于 > limit 个不同位置 → 视为教程/蜜罐键。

    Args:
        keys_by_location: 位置标识(文件路径/URL)→ 该位置命中的 key 列表。
        limit: 位置数上限(规格 §7.2:>5 剔除)。

    Returns:
        应剔除的 key 集合(调用方从各位置命中里移除)。
    """
    locations_by_key: dict[str, set[str]] = {}
    for location, keys in keys_by_location.items():
        for key in keys:
            locations_by_key.setdefault(key, set()).add(location)
    return {key for key, locations in locations_by_key.items() if len(locations) > limit}


# ---------------------------------------------------------------------------
# 扫描主入口
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyHit:
    """一次密钥命中:串 + 归因(供应商/族/官方地址/上下文变量)。"""

    apikey: str
    provider: str            # 未归因为 "unknown"
    family: str              # 联合族标签或细正则供应商标签
    matched_by: str          # joint | fine | joint+fine
    apiurl: str              # 归因出的官方地址(可为空串)
    variable: str | None = None  # 命中处的赋值变量名(若有)


def _fine_provider(apikey: str) -> str | None:
    for provider, pattern in FINE_KEY_PATTERNS:
        if pattern.search(apikey):
            return provider
    return None


def _joint_family(match: re.Match[str]) -> str:
    for family in JOINT_KEY_FAMILIES:
        if match.group(family) is not None:
            return family
    return "sk_generic"  # 不可达(正则必命中某命名组),防御式兜底


def extract_secrets(text: str, *, endpoint: str | None = None) -> list[KeyHit]:
    """对一段文本跑指纹:联合正则 ∪ 细正则 → 噪声过滤 → 归因。

    流程(ghhunt.md §4 + fingerprints.md §7 语义):
    1. 联合正则与细正则各自命中,按命中串去重合并(细正则补联合正则
       覆盖不到的家族:ghp_/JWT/32hex.16alnum 等);
    2. 每个候选过 :func:`is_noise`(长度/占位子串/字母序段/阻断格式);
    3. 归因优先级:细正则供应商 → 命中处赋值变量名(剥后缀查归因表)→
       联合族缺省(sk_generic 留 unknown);
    4. apiurl 按 :func:`attribute_apiurl`(endpoint 参数优先)。

    空文本/无命中返回空列表,不报错。
    """
    if not text:
        return []
    candidates: dict[str, tuple[str, str]] = {}  # apikey -> (matched_by, family)
    for match in JOINT_KEY_RE.finditer(text):
        family = _joint_family(match)
        candidates.setdefault(match.group(0), ("joint", family))
    for provider, pattern in FINE_KEY_PATTERNS:
        for match in pattern.finditer(text):
            apikey = match.group(0)
            if apikey in candidates:
                matched_by, _joint_label = candidates[apikey]
                # 细正则标签比联合族更具体(joint+fine 时以细正则供应商为族)。
                candidates[apikey] = (
                    "joint+fine" if matched_by == "joint" else matched_by,
                    provider,
                )
            else:
                candidates[apikey] = ("fine", provider)
    hits: list[KeyHit] = []
    for apikey, (matched_by, family) in candidates.items():
        if is_noise(apikey):
            continue
        provider = _fine_provider(apikey)
        variable_match = _variable_context_re(apikey).search(text)
        variable = variable_match.group("var") if variable_match else None
        if provider is None and variable is not None:
            provider = attribute_by_variable(variable)
        if provider is None:
            provider = _JOINT_FAMILY_PROVIDER.get(family, "") or "unknown"
        hits.append(
            KeyHit(
                apikey=apikey,
                provider=provider,
                family=family,
                matched_by=matched_by,
                apiurl=attribute_apiurl(apikey, variable=variable, endpoint=endpoint),
                variable=variable,
            )
        )
    return hits
