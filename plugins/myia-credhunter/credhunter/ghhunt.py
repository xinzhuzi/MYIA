"""GitHub 工件凭证猎手(credhunter R1)——两泳道 + 工件二次加工。

行为规格(``.trellis/tasks/10-03-aipocket-fusion/research/behavior-specs/
ghhunt.md``)的 MYIA 功能重实现:httpx.AsyncClient 全异步、零上游代码/
标识符/文案。语义要点(与规格的偏离处均已注明):

- **code 泳道**:逐查询走 ``GET /search/code`` 1..=5 页 ×100,逐条公开性
  校验(``repository/private == false`` 或 ``visibility == "public"``)→
  噪音路径过滤(:func:`should_discard_path`)→ 基础文本 =
  ``text_matches`` 的 JSON 串;能取到 blob sha 则 ``GET
  /repos/{o}/{r}/git/blobs/{sha}``,base64 解码后 **≤1 MiB 才采用**
  (采用 = 替换基础文本作为扫描对象,基础文本是取不到 blob 时的回落);
  返回条数 < per_page 提前停页;每查询页游标跨 run 接续(扫描出错停在
  当前页),跑完(提前停或页数封顶)重置回 1。
- **commit 泳道**:``GET /search/commits`` 仅第 1 页;run 预算 12 条查询
  跨 run 轮转(Q8:游标存 checkpoint);仅 ``repository/private == false``
  的 item 参与(visibility 在此泳道不算数);对 ``commit.message`` 全文
  跑指纹。
- **token 池轮转 + 403/429 退避**:游标取起始下标,每轮依次尝试全部
  token;一轮内某 token 403/429 就换下一个,整轮全 403/429 才降级 ——
  读 ``retry-after`` 头,否则 ``x-ratelimit-reset - now``,等待值 ≤90s
  才睡(等待值+1 秒)**仅重试一轮**,仍全 403/429 或等待值 >90s 即报错
  放弃;非 403/429 状态码直接失败;无 token 直接报错(该源不启用)。
- **apiurl 前缀归因静态映射**:扫描一律不传 endpoint,apiurl 只来自
  指纹库前缀→官方地址映射(:data:`fingerprints.PREFIX_APIURL_HINT`)。
  偏离说明:上游把命中项 html_url 当 endpoint 直传,那会让 apiurl 变成
  github.com 页面地址、污染后续验证基址;本实现里 html_url 只作 item.url
  (命中位置证据 URL)。
- **工件二次加工**:两泳道扫完后从工件队列领 ≤200 条,并发 8、每工件
  首试 + 至多 5 次重试;commit 类取 ``GET /repos/{o}/{r}/commits/{sha}``
  的 message,blob 类取 blobs 接口(≤1 MiB);超限归类
  ``artifact_too_large``,其它错误 ``transient``,成功 ``terminal``。
- **统一 diff 三侧解析增强件**(上游已建未接线,此处接线=增值):被扫描
  文本形如统一 diff(:func:`is_unified_diff`)时,按 Added→Removed→
  Context 三侧各自聚合行文本再跑指纹,命中带 ``change_side`` 与该侧行号
  范围 —— 被删除行/未变更行里的密钥同样可见。普通文本照旧整段扫描
  (``change_side="context"``,无行号)。
- **输出 = 掩码-only items**(Q9):全部经 ``findings.build_finding_item``
  装配;上下文摘录先对**同文本全部命中键**做 redact 再截断(防兄弟键
  全文漏进 content),全文密钥永不入 item。
- **命中入密钥库**(猎→存→验通路):``hunt(keystore=…)`` 注入 duck-typed
  ``record``(插件侧 ``keystore.Keystore``)时,每次命中在装配掩码 item
  的同时以 ``findings.key_fingerprint`` 为主键把全文落进本地密钥库
  (``$MYIA_HOME/credhunter-keystore.json``,600;适配器面缺省注入)。
  缺省 ``None`` = 纯扫描不入库(引擎/测试自主决定);库文件失败按结构化
  错上抛(本地盘故障不该静默丢全文)。

模块加载:与 adapter.py 同款 compile+exec 手法(零 ``__pycache__``);
兄弟模块(fingerprints/findings)经 :func:`_sibling` 从 sys.modules 复用
或就地自举(canonical 名登记,适配器先加载过则同一模块对象)。
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
import time
import types
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

__all__ = [
    "API_VERSION",
    "ARTIFACT_BATCH_LIMIT",
    "ARTIFACT_CONCURRENCY",
    "ARTIFACT_RETRY_LIMIT",
    "CODE_PAGES_MAX",
    "COMMIT_QUERY_BUDGET",
    "GITHUB_API_BASE",
    "MAX_BLOB_BYTES",
    "PER_PAGE_DEFAULT",
    "RATE_LIMIT_MAX_WAIT_SECONDS",
    "ArtifactWork",
    "GithubClient",
    "GithubHuntError",
    "TokenPool",
    "build_async_client",
    "decode_blob",
    "hunt",
    "should_discard_path",
    "is_unified_diff",
    "parse_diff_sides",
    "process_artifacts",
    "rotate_queries",
    "scan_text",
    "scan_unified_patch",
]

# ---------------------------------------------------------------------------
# 0) 兄弟子模块自举(与 adapter._load_module 同手法)
# ---------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).resolve().parent
_MODULE_PREFIX = "myia_credhunter_"


def _sibling(name: str) -> types.ModuleType:
    """取兄弟子模块:sys.modules 复用 → 就地 compile+exec 自举。

    与适配器的 ``_load_module`` 同手法同理由(不走 importlib,插件目录零
    ``__pycache__``);canonical 名(``myia_credhunter_<name>``)登记进
    sys.modules,适配器先加载过则直接复用同一模块对象。packs 懒加载:
    仅 :func:`hunt` 缺省 queries 时才读数据文件。
    """
    canonical = f"{_MODULE_PREFIX}{name}"
    existing = sys.modules.get(canonical)
    if existing is not None:
        return existing
    module_file = _MODULE_DIR / f"{name}.py"
    if not module_file.is_file():
        raise FileNotFoundError(f"credhunter 子模块不存在:{module_file}")
    module = types.ModuleType(canonical)
    module.__file__ = str(module_file)
    sys.modules[canonical] = module
    executable = compile(module_file.read_text(encoding="utf-8"), str(module_file), "exec")
    exec(executable, module.__dict__)  # noqa: S102 - 仓库内受控插件代码,非任意输入
    return module


#: 指纹库(密钥命中)与 items 装配(Q9 掩码)——扫描与产出两侧的依赖。
_fingerprints = _sibling("fingerprints")
_findings = _sibling("findings")

# ---------------------------------------------------------------------------
# 1) 常量(规格数值,测试钉死)
# ---------------------------------------------------------------------------

#: GitHub REST API 基址(可注入改写,测试走 MockTransport 不出网)。
GITHUB_API_BASE = "https://api.github.com"

#: 每请求 API 版本头(规格 §2)。
API_VERSION = "2022-11-28"

#: code 泳道分页上限:硬编码 1..=5 页(规格 §2)。
CODE_PAGES_MAX = 5

#: 每页条数缺省(规格 §2:per_page 默认 100,亦为 GitHub 上限)。
PER_PAGE_DEFAULT = 100

#: commit 泳道 run 查询预算(Q8:12 条/run,游标跨 run 轮转)。
COMMIT_QUERY_BUDGET = 12

#: 限速等待值上限:超过即报错放弃(规格 §2:默认 90s)。
RATE_LIMIT_MAX_WAIT_SECONDS = 90.0

#: 限速等待的固定加成秒数(规格 §2:等待值 +1 秒后再试)。
_BACKOFF_EXTRA_SECONDS = 1.0

#: 触发退避的状态码(仅 403/429;其它非 2xx 直接失败)。
_RATE_LIMIT_STATUS = frozenset({403, 429})

#: 退避轮数:初始轮 + 一次重试轮(规格 §2:仅重试一轮)。
_RATE_RETRY_ROUNDS = 2

#: blob 解码后的字节上限(规格:解码后 ≤1 MiB 才采用,无截断降级)。
MAX_BLOB_BYTES = 1024 * 1024

#: 工件二次加工:单轮领取上限(规格 §6:≤200 条)。
ARTIFACT_BATCH_LIMIT = 200

#: 工件二次加工并发(规格 §6:默认 8)。
ARTIFACT_CONCURRENCY = 8

#: 工件二次加工重试上限(规格 §6:重试上限 5,即首试 + 至多 5 次重试)。
ARTIFACT_RETRY_LIMIT = 5

#: 工件重试间退避秒数(首试失败后的依次退避;规格未钉数值,自定小步长)。
_ARTIFACT_RETRY_BACKOFFS = (0.5, 1.0, 2.0, 4.0, 8.0)

#: 命中上下文摘录的字符上限(上游 raw_context = 前 2048 字符语义)。
RAW_CONTEXT_CHARS = 2048

#: 出网请求超时(上游 github_request_timeout 默认 20s)。
REQUEST_TIMEOUT_SECONDS = 20.0


class GithubHuntError(ValueError):
    """结构化猎取错误:code + message + details(``to_dict()`` 进 JSON/错误面)。

    刻意继承 ValueError:适配器 ``run`` 的既有错误通道按 ValueError 收口
    (getattr(exc, "code", …) 保留结构化码)。
    """

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """结构化形态:{"code", "message", **details}。"""
        return {"code": self.code, "message": self.message, **self.details}


# ---------------------------------------------------------------------------
# 2) token 池 + GitHub 客户端(轮转、退避、四个端点)
# ---------------------------------------------------------------------------


class TokenPool:
    """GitHub token 池:游标取起始下标,每轮依次尝试全部 token。

    asyncio 单线程事件循环内游标自增无 await 间隙,天然原子;无 token
    直接抛 :class:`GithubHuntError`(规格 §2:该源不启用)。
    """

    def __init__(self, tokens: Sequence[str]) -> None:
        cleaned = tuple(token.strip() for token in tokens if isinstance(token, str) and token.strip())
        if not cleaned:
            raise GithubHuntError("tokens_missing", "GitHub token 池为空:无 token 该源不启用(显式报错,不静默)")
        self.tokens: tuple[str, ...] = cleaned
        self._cursor = 0

    def __len__(self) -> int:
        return len(self.tokens)

    def next_start(self) -> int:
        """取本轮起始下标并步进游标(下次请求从下一个 token 起轮)。"""
        start = self._cursor
        self._cursor = (self._cursor + 1) % len(self.tokens)
        return start

    def token_at(self, start: int, offset: int) -> str:
        """起始下标 start 起第 offset 个 token(环形取)。"""
        return self.tokens[(start + offset) % len(self.tokens)]


class GithubClient:
    """GitHub REST 客户端:token 轮转 + 403/429 退避,全部走注入的 AsyncClient。"""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        tokens: Sequence[str],
        api_base: str = GITHUB_API_BASE,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rate_limit_max_wait: float = RATE_LIMIT_MAX_WAIT_SECONDS,
    ) -> None:
        self._client = client
        self._pool = TokenPool(tokens)  # 无 token 在构造期即报错
        self._api_base = api_base.rstrip("/")
        self._now = now
        self._sleep = sleep
        self._rate_limit_max_wait = rate_limit_max_wait

    @property
    def pool(self) -> TokenPool:
        """token 池(诊断/测试观察轮转用)。"""
        return self._pool

    def _headers(self, token: str) -> dict[str, str]:
        """每请求头(规格 §2:Bearer + API 版本 + Accept)。"""
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }

    def _rate_limit_wait(self, response: httpx.Response) -> float:
        """从 403/429 响应算等待秒数:retry-after 优先,否则 reset - now。

        retry-after 非数字(HTTP-date 形态)不解析、落到 reset 头;两头都
        没有则 0(调用方仍会 +1s 后重试一轮)。
        """
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        reset = response.headers.get("x-ratelimit-reset")
        if reset is not None:
            try:
                return max(0.0, float(reset) - self._now())
            except ValueError:
                pass
        return 0.0

    async def _request_json(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        """带 token 轮转与限速退避的 GET;返回解析后的 JSON。

        轮次语义(规格 §2):初始轮依次尝试全部 token;某 token 403/429 换
        下一个,整轮全 403/429 才降级 —— 等待值(各 token 取最大)>上限
        报错放弃,否则睡(等待值+1s)后**仅再试一轮**;仍全 403/429 报错。
        非 403/429 的错误状态码与坏 JSON 直接抛结构化错。
        """
        url = f"{self._api_base}{path}"
        token_count = len(self._pool)
        start = self._pool.next_start()
        for round_index in range(_RATE_RETRY_ROUNDS):
            round_waits: list[float] = []
            for offset in range(token_count):
                token = self._pool.token_at(start, offset)
                response = await self._client.get(url, params=dict(params or {}), headers=self._headers(token))
                if response.status_code in _RATE_LIMIT_STATUS:
                    round_waits.append(self._rate_limit_wait(response))
                    continue
                if response.status_code >= 400:
                    raise GithubHuntError(
                        "github_api_error",
                        f"GitHub API {path} 返回 {response.status_code}(非 403/429 直接失败)",
                        status=response.status_code,
                        path=path,
                    )
                try:
                    return response.json()
                except ValueError as exc:
                    raise GithubHuntError(
                        "github_response_invalid", f"GitHub API {path} 返回非 JSON:{exc}", path=path
                    ) from exc
            if round_index == _RATE_RETRY_ROUNDS - 1:
                raise GithubHuntError(
                    "github_rate_limited",
                    f"GitHub API {path} 退避重试一轮后仍全部 403/429({token_count} 个 token)",
                    path=path,
                )
            wait_value = max(round_waits, default=0.0)
            if wait_value > self._rate_limit_max_wait:
                raise GithubHuntError(
                    "github_rate_limit_wait_too_long",
                    f"限速等待 {wait_value:g}s 超过上限 {self._rate_limit_max_wait:g}s,放弃",
                    wait_seconds=wait_value,
                    path=path,
                )
            await self._sleep(wait_value + _BACKOFF_EXTRA_SECONDS)
        raise GithubHuntError("github_unreachable", f"GitHub API {path} 请求轮次耗尽(不可达分支)", path=path)

    # ---- 四个端点 + 诊断端点(规格 §2) ----

    async def search_code(self, query: str, *, page: int = 1, per_page: int = PER_PAGE_DEFAULT) -> Any:
        """code 泳道:``GET /search/code?q&page&per_page``。"""
        return await self._request_json("/search/code", params={"q": query, "page": page, "per_page": per_page})

    async def search_commits(self, query: str, *, page: int = 1, per_page: int = PER_PAGE_DEFAULT) -> Any:
        """commit 泳道:``GET /search/commits``(调用方只取第 1 页)。"""
        return await self._request_json("/search/commits", params={"q": query, "page": page, "per_page": per_page})

    async def get_commit(self, repo_full_name: str, sha: str) -> Any:
        """取单个 commit(其 ``commit.message`` 是工件二次加工的扫描对象)。"""
        return await self._request_json(f"/repos/{repo_full_name}/commits/{sha}")

    async def get_blob(self, repo_full_name: str, sha: str) -> Any:
        """取 git blob(base64 文件内容,≤1 MiB 由 :func:`decode_blob` 把关)。"""
        return await self._request_json(f"/repos/{repo_full_name}/git/blobs/{sha}")

    async def get_rate_limit(self) -> Any:
        """诊断端点:``GET /rate_limit``。"""
        return await self._request_json("/rate_limit")


def build_async_client(*, timeout: float = REQUEST_TIMEOUT_SECONDS, **kwargs: Any) -> httpx.AsyncClient:
    """生产装配:缺省超时的裸 AsyncClient(headers 逐请求按 token 注入)。"""
    return httpx.AsyncClient(timeout=timeout, **kwargs)


# ---------------------------------------------------------------------------
# 3) 噪音路径过滤(规格 §5)
# ---------------------------------------------------------------------------

#: 目录段黑名单(路径任一段命中即丢弃;changelog 同时也是 basename 前缀)。
_NOISE_DIR_SEGMENTS = frozenset({
    "examples", "example", "samples", "fixtures", "fixture",
    "testdata", "test-data", "test_data", "mocks", "mock",
    "docs", "documentation", "changelog",
})

#: 路径包含即丢弃的特殊文件/目录名(规格点名的目录清单文件)。
_NOISE_PATH_SUBSTRINGS = (
    "provider-catalog", "official-external-provider",
    "openclaw.plugin.json", "catalog.json", "catalog.toml",
)

#: basename 噪音前缀(小写化后匹配;readme 等文档类)。
_NOISE_BASENAME_PREFIXES = ("readme", "changelog", "license", "contributing")


def should_discard_path(path: str) -> bool:
    """工件路径噪音判定(规格 §5,code 泳道扫描期先行过滤)。

    - basename 以 ``.env`` 开头 → **永不判噪**(保留 .env.example);
    - 路径含目录段黑名单(examples/docs/testdata/mocks/…)→ 丢弃;
    - 路径含目录清单特殊文件(provider-catalog/catalog.json/…)→ 丢弃;
    - basename 以 ``.md`` 结尾,或小写化后以 readme/changelog/license/
      contributing 开头 → 丢弃;
    - 空路径不判噪(无依据丢弃证据,保守放行给指纹层)。
    """
    if not path:
        return False
    basename = path.rsplit("/", 1)[-1]
    if basename.startswith(".env"):
        return False
    segments = [segment.lower() for segment in path.split("/") if segment]
    if any(segment in _NOISE_DIR_SEGMENTS for segment in segments):
        return True
    lowered = path.lower()
    if any(substring in lowered for substring in _NOISE_PATH_SUBSTRINGS):
        return True
    if basename.lower().endswith(".md"):
        return True
    return basename.lower().startswith(_NOISE_BASENAME_PREFIXES)


# ---------------------------------------------------------------------------
# 4) 统一 diff 三侧解析(规格 §3;上游已建未接线,此处接线为增强件)
# ---------------------------------------------------------------------------

#: hunk 头正则:捕获取 old/new 起始行号,重置两个行号计数器。
_HUNK_HEADER_RE = re.compile(r"^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@")

#: 跳过的 diff 元数据行前缀(含 ``\ No newline at end of file`` 的反斜杠行)。
_DIFF_SKIP_PREFIXES = ("---", "+++", "diff ", "index ", "\\")


@dataclass(frozen=True)
class PatchLine:
    """统一 diff 的一行:侧别 + 内容(剥离首字符)+ 双侧行号。"""

    side: str  # "added" | "removed" | "context"
    content: str
    old_lineno: int | None = None
    new_lineno: int | None = None

    @property
    def lineno(self) -> int | None:
        """该行的展示行号:优先 new(文件现行号),removed 行退 old。"""
        return self.new_lineno if self.new_lineno is not None else self.old_lineno


def parse_diff_sides(text: str) -> list[PatchLine]:
    """解析统一 diff 为带侧别与行号的行列表(规格 §3 语义)。

    - 元数据行(``---``/``+++``/``diff ``/``index ``/``\\``)跳过;
    - hunk 头重置 old/new 行号计数器为捕获的起始行号;
    - 行首字符按**字符**剥离(Python str 即码点,等价上游按 UTF-8 字符
      宽度剥离):``+``→added(new 行号)、``-``→removed(old 行号)、
      空格与空行→context(两计数器同进),其它首字符丢弃;
    - hunk 头出现前的 +/- 行按计数器 0 起算(畸形输入的防御口径)。
    """
    parsed: list[PatchLine] = []
    old_lineno, new_lineno = 0, 0
    for raw_line in text.splitlines():
        if raw_line.startswith(_DIFF_SKIP_PREFIXES):
            continue
        header = _HUNK_HEADER_RE.match(raw_line)
        if header is not None:
            old_lineno = int(header.group(1))
            new_lineno = int(header.group(2))
            continue
        if not raw_line:
            parsed.append(PatchLine("context", "", old_lineno, new_lineno))
            old_lineno += 1
            new_lineno += 1
            continue
        first, rest = raw_line[0], raw_line[1:]
        if first == "+":
            parsed.append(PatchLine("added", rest, None, new_lineno))
            new_lineno += 1
        elif first == "-":
            parsed.append(PatchLine("removed", rest, old_lineno, None))
            old_lineno += 1
        elif first == " ":
            parsed.append(PatchLine("context", rest, old_lineno, new_lineno))
            old_lineno += 1
            new_lineno += 1
        # 其它首字符:丢弃(规格 §3)
    return parsed


def is_unified_diff(text: str) -> bool:
    """diff 侦测启发:存在 hunk 头行的文本按统一 diff 处理。

    JSON 串(text_matches 基础文本)是单行且不以 ``@@`` 开头,不会误判;
    commit message 里粘贴的 diff 会被识别(增强件的自然收益)。
    """
    return any(_HUNK_HEADER_RE.match(line) is not None for line in text.splitlines())


@dataclass(frozen=True)
class SideHit:
    """一次带侧别的命中:指纹命中(fingerprints.KeyHit)+ diff 侧别与行号范围。"""

    hit: Any  # fingerprints.KeyHit(避免跨模块注解解析,以 Any 承载)
    change_side: str  # "added" | "removed" | "context"
    line_start: int | None = None  # 该侧所选行行号最小值(仅 diff 命中有)
    line_end: int | None = None  # 该侧所选行行号最大值


def scan_unified_patch(text: str) -> list[SideHit]:
    """三侧聚合扫描(规格 §3:Added→Removed→Context 顺序)。

    每侧把该侧全部行的内容聚合为一段文本跑一次指纹;命中带 ``change_side``
    与该侧行号范围(非命中行精确行号 —— 上游语义)。行号口径:优先 new
    行号,removed 行用 old 行号。被删除行/未变更行里的密钥同样可见。
    """
    patch_lines = parse_diff_sides(text)
    results: list[SideHit] = []
    for side in ("added", "removed", "context"):
        selected = [line for line in patch_lines if line.side == side]
        if not selected:
            continue
        side_text = "\n".join(line.content for line in selected)
        linenos = [line.lineno for line in selected if line.lineno is not None]
        line_start = min(linenos) if linenos else None
        line_end = max(linenos) if linenos else None
        for hit in _fingerprints.extract_secrets(side_text):
            results.append(SideHit(hit=hit, change_side=side, line_start=line_start, line_end=line_end))
    return results


def scan_text(text: str) -> list[SideHit]:
    """扫描入口:统一 diff → 三侧解析;普通文本 → 整段扫描(context 侧)。

    apiurl 归因只走前缀静态映射(不传 endpoint,见模块 docstring 偏离说明)。
    空文本/无命中返回空列表,不报错。
    """
    if not text:
        return []
    if is_unified_diff(text):
        return scan_unified_patch(text)
    return [SideHit(hit=hit, change_side="context") for hit in _fingerprints.extract_secrets(text)]


# ---------------------------------------------------------------------------
# 5) blob 解码(≤1 MiB 才采用)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecodedBlob:
    """blob 解码结果:text 为解码文本;too_large 标记超限弃用。"""

    text: str | None
    too_large: bool = False


def decode_blob(blob: Mapping[str, Any]) -> DecodedBlob:
    """base64 blob → 文本;解码后 >1 MiB 即弃(无截断降级,规格 §7)。

    ``size`` 字段(GitHub 语义 = 解码后字节数)超限走快速路径不解码;
    非 base64/内容缺失返回空结果(交调用方按无 blob 处理);UTF-8 容错
    解码(上游 lossy 语义,二进制文件照常进正则)。
    """
    size = blob.get("size")
    if isinstance(size, (int, float)) and size > MAX_BLOB_BYTES:
        return DecodedBlob(text=None, too_large=True)
    content = blob.get("content")
    encoding = blob.get("encoding")
    if encoding != "base64" or not isinstance(content, str):
        return DecodedBlob(text=None, too_large=False)
    try:
        raw = base64.b64decode(content)
    except ValueError:  # binascii.Error 是 ValueError 子类
        return DecodedBlob(text=None, too_large=False)
    if len(raw) > MAX_BLOB_BYTES:
        return DecodedBlob(text=None, too_large=True)
    return DecodedBlob(text=raw.decode("utf-8", errors="replace"), too_large=False)


# ---------------------------------------------------------------------------
# 6) items 装配(掩码-only,Q9)
# ---------------------------------------------------------------------------


def _masked_excerpt(text: str, keys: Sequence[str]) -> str | None:
    """上下文摘录:取前 2048 字符并对**全部**命中键做 redact(防兄弟键全文漏进 content)。"""
    if not text:
        return None
    return _findings.redact_secrets(text[:RAW_CONTEXT_CHARS], list(keys))


def _build_items(
    side_hits: Sequence[SideHit],
    *,
    context_text: str,
    source_url: str,
    source_type: str,
    file_path: str | None = None,
    query: str = "",
    object_sha: str | None = None,
    commit_sha: str | None = None,
    keystore: Any = None,
) -> list[dict[str, Any]]:
    """SideHit 批 → 掩码-only items(附 change_side/行号/来源元数据)。

    keystore(duck-typed ``record``)非空时,每键同时以指纹为主键入本地
    密钥库(全文只进库,Q9 对外掩码口径不变;见模块 docstring)。
    """
    if not side_hits or not source_url:
        return []
    excerpt = _masked_excerpt(context_text, [side_hit.hit.apikey for side_hit in side_hits])
    items: list[dict[str, Any]] = []
    for side_hit in side_hits:
        hit = side_hit.hit
        if keystore is not None:
            keystore.record(_findings.key_fingerprint(hit.apikey), hit.apikey, hit.provider, source_url)
        item = _findings.build_finding_item(
            apikey=hit.apikey,
            provider=hit.provider,
            source_url=source_url,
            apiurl=hit.apiurl,
            source_type=source_type,
            matched_by=hit.matched_by,
            variable=hit.variable,
            file_path=file_path,
            context_excerpt=excerpt,
        )
        item["change_side"] = side_hit.change_side
        if side_hit.line_start is not None:
            item["line_start"] = side_hit.line_start
            item["line_end"] = side_hit.line_end
        if object_sha:
            item["object_sha"] = object_sha
        if commit_sha:
            item["commit_sha"] = commit_sha
        if query:
            item["query"] = query
        items.append(item)
    return items


def _evidence_url(repo_full_name: str, sha: str, *, kind: str, html_url: str = "") -> str:
    """证据 URL:命中项 html_url 优先,缺失时按 repo+sha 构造 GitHub 页面地址。"""
    if html_url:
        return html_url
    if not repo_full_name:
        return ""
    suffix = f"/commit/{sha}" if kind == "commit" else f"/blob/{sha}"
    return f"https://github.com/{repo_full_name}{suffix}"


def _repo_public(repository: Mapping[str, Any], *, visibility_counts: bool) -> bool:
    """公开性校验:code 泳道认 private==false 或 visibility=="public";
    commit 泳道只认 private==false(规格 §6 的两泳道差异)。"""
    if repository.get("private") is False:
        return True
    return visibility_counts and repository.get("visibility") == "public"


def _search_items(payload: Any) -> list[Mapping[str, Any]]:
    """搜索响应 → items 列表(形状不符给空,单源 0 命中≠失败)。"""
    if not isinstance(payload, Mapping):
        return []
    entries = payload.get("items")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, Mapping)]


# ---------------------------------------------------------------------------
# 7) 工件二次加工(≤200 / 并发 8 / 重试 5)
# ---------------------------------------------------------------------------


@dataclass
class ArtifactWork:
    """一件待二次加工的工件(两泳道各自入队;状态随处理回写)。"""

    repo_full_name: str
    sha: str
    kind: str  # "blob" | "commit"
    html_url: str = ""
    file_path: str | None = None
    query: str = ""
    lane: str = ""  # "code_snapshot" | "commit_message"
    status: str = "fetch_pending"  # fetch_pending | terminal | transient | artifact_too_large
    attempts: int = 0
    error: str | None = None


async def _process_one_work(
    gh: GithubClient,
    work: ArtifactWork,
    *,
    retries: int,
    sleep: Callable[[float], Awaitable[None]],
    keystore: Any = None,
) -> list[dict[str, Any]]:
    """单工件处理:取数 → 扫描 → items;状态回写 work.status/attempts/error。

    规格归类:超限 ``artifact_too_large``、其它错误 ``transient``、成功
    ``terminal``(取数成功但零命中也是 terminal)。
    """
    for attempt in range(retries + 1):
        work.attempts = attempt + 1
        try:
            if work.kind == "commit":
                commit = await gh.get_commit(work.repo_full_name, work.sha)
                inner = commit.get("commit") if isinstance(commit, Mapping) else None
                message = inner.get("message") if isinstance(inner, Mapping) else None
                text = message if isinstance(message, str) else ""
                items = _build_items(
                    scan_text(text),
                    context_text=text,
                    source_url=_evidence_url(work.repo_full_name, work.sha, kind="commit", html_url=work.html_url),
                    source_type=work.lane or "commit_message",
                    query=work.query,
                    commit_sha=work.sha,
                    keystore=keystore,
                )
                work.status = "terminal"
                return items
            blob = await gh.get_blob(work.repo_full_name, work.sha)
            decoded = decode_blob(blob)
            if decoded.too_large:
                work.status = "artifact_too_large"
                return []
            text = decoded.text or ""
            items = _build_items(
                scan_text(text),
                context_text=text,
                source_url=_evidence_url(work.repo_full_name, work.sha, kind="blob", html_url=work.html_url),
                source_type=work.lane or "code_snapshot",
                file_path=work.file_path,
                query=work.query,
                object_sha=work.sha,
                keystore=keystore,
            )
            work.status = "terminal"
            return items
        except Exception as exc:  # noqa: BLE001 - 单工件失败只降级自身状态(其它错误=transient)
            work.error = f"{type(exc).__name__}: {exc}"[:200]
            if attempt < retries:
                await sleep(_ARTIFACT_RETRY_BACKOFFS[min(attempt, len(_ARTIFACT_RETRY_BACKOFFS) - 1)])
                continue
            work.status = "transient"
            return []
    return []  # 不可达(retries ≥ 0 时循环必经 return)


async def process_artifacts(
    gh: GithubClient,
    works: Sequence[ArtifactWork],
    *,
    limit: int = ARTIFACT_BATCH_LIMIT,
    concurrency: int = ARTIFACT_CONCURRENCY,
    retries: int = ARTIFACT_RETRY_LIMIT,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    keystore: Any = None,
) -> dict[str, Any]:
    """工件二次加工:领 ≤limit 条,并发 concurrency,每工件首试 + 至多 retries 次重试。

    keystore 注入时命中同步入本地密钥库(与两泳道同口径;并发工件的
    入库串行化由密钥库自身的进程内锁保证)。

    Returns:
        {"items", "works", "counts"}:items 为掩码-only 管线 items;works 为
        处理后的工件(状态已回写);counts 含领取数与四态计数。
    """
    batch = list(works)[:limit]
    semaphore = asyncio.Semaphore(max(1, concurrency))
    produced: list[dict[str, Any]] = []

    async def _guarded(work: ArtifactWork) -> None:
        async with semaphore:
            produced.extend(await _process_one_work(gh, work, retries=retries, sleep=sleep, keystore=keystore))

    await asyncio.gather(*(_guarded(work) for work in batch))
    statuses = {"fetch_pending": 0, "terminal": 0, "transient": 0, "artifact_too_large": 0}
    for work in batch:
        statuses[work.status] = statuses.get(work.status, 0) + 1
    return {
        "items": produced,
        "works": batch,
        "counts": {
            "artifact_queued": len(works),
            "artifact_batch": len(batch),
            "artifact_terminal": statuses["terminal"],
            "artifact_transient": statuses["transient"],
            "artifact_too_large": statuses["artifact_too_large"],
        },
    }


# ---------------------------------------------------------------------------
# 8) 查询轮转(Q8:12 条/run,游标跨 run)
# ---------------------------------------------------------------------------


def rotate_queries(queries: Sequence[str], cursor: int, *, budget: int) -> tuple[list[str], int]:
    """从查询池按游标环形取 budget 条(不足取全池);返回 (选中, 下一游标)。"""
    total = len(queries)
    if total == 0 or budget <= 0:
        return [], cursor % total if total else 0
    take = min(budget, total)
    start = cursor % total
    selected = [queries[(start + offset) % total] for offset in range(take)]
    return selected, (start + take) % total


# ---------------------------------------------------------------------------
# 9) 两条泳道
# ---------------------------------------------------------------------------


async def _hunt_code_lane(
    gh: GithubClient,
    queries: Sequence[str],
    state: dict[str, Any],
    *,
    per_page: int,
    pages_max: int,
    errors: list[dict[str, Any]],
    counters: dict[str, int],
    keystore: Any = None,
) -> tuple[list[dict[str, Any]], list[ArtifactWork]]:
    """code 泳道:/search/code 1..=pages_max 页,逐条公开性/噪音/blob 处理。"""
    items: list[dict[str, Any]] = []
    works: list[ArtifactWork] = []
    pages_state: dict[str, int] = state.setdefault("code_pages", {})
    if not isinstance(pages_state, dict):
        pages_state = {}
        state["code_pages"] = pages_state
    for query in queries:
        counters["code_queries"] += 1
        raw_page = pages_state.get(query, 1)
        page = raw_page if isinstance(raw_page, int) and not isinstance(raw_page, bool) and raw_page >= 1 else 1
        completed = False
        while page <= pages_max:
            try:
                payload = await gh.search_code(query, page=page, per_page=per_page)
            except GithubHuntError as exc:
                # 搜索报错:记 errors、中断该查询分页(游标留本页),继续下一查询
                errors.append({"lane": "code_snapshot", "query": query, "page": page, **exc.to_dict()})
                break
            counters["code_pages"] += 1
            entries = _search_items(payload)
            for entry in entries:
                counters["code_items"] += 1
                repository = entry.get("repository") if isinstance(entry.get("repository"), Mapping) else {}
                if not _repo_public(repository, visibility_counts=True):
                    counters["code_private_skipped"] += 1
                    continue
                path = entry.get("path") if isinstance(entry.get("path"), str) else ""
                if should_discard_path(path):
                    counters["noise_dropped"] += 1
                    continue
                repo_full = repository.get("full_name") if isinstance(repository.get("full_name"), str) else ""
                file_sha = entry.get("sha") if isinstance(entry.get("sha"), str) else ""
                html_url = entry.get("html_url") if isinstance(entry.get("html_url"), str) else ""
                source_url = _evidence_url(repo_full, file_sha, kind="blob", html_url=html_url)
                if not source_url:
                    counters["code_malformed_skipped"] += 1
                    continue
                # 基础文本 = text_matches 的 JSON 串(blob 取到才替换为全文)
                scan_target = json.dumps(entry.get("text_matches") or [], ensure_ascii=False)
                if file_sha and repo_full:
                    try:
                        blob = await gh.get_blob(repo_full, file_sha)
                    except GithubHuntError as exc:
                        errors.append(
                            {"lane": "code_snapshot", "query": query, "blob": file_sha, **exc.to_dict()}
                        )
                        counters["blob_errors"] += 1
                    else:
                        decoded = decode_blob(blob)
                        if decoded.too_large:
                            counters["blob_too_large"] += 1
                        elif decoded.text is not None:
                            scan_target = decoded.text
                            counters["blobs_decoded"] += 1
                    works.append(
                        ArtifactWork(
                            repo_full_name=repo_full,
                            sha=file_sha,
                            kind="blob",
                            html_url=html_url,
                            file_path=path or None,
                            query=query,
                            lane="code_snapshot",
                        )
                    )
                items.extend(
                    _build_items(
                        scan_text(scan_target),
                        context_text=scan_target,
                        source_url=source_url,
                        source_type="code_snapshot",
                        file_path=path or None,
                        query=query,
                        object_sha=file_sha or None,
                        keystore=keystore,
                    )
                )
            if len(entries) < per_page:
                completed = True
                break
            page += 1
            pages_state[query] = page
        else:
            completed = True
        if completed:
            pages_state[query] = 1  # 跑完(提前停或页数封顶)即重置游标
    return items, works


async def _hunt_commit_lane(
    gh: GithubClient,
    queries: Sequence[str],
    state: dict[str, Any],
    *,
    budget: int,
    errors: list[dict[str, Any]],
    counters: dict[str, int],
    keystore: Any = None,
) -> tuple[list[dict[str, Any]], list[ArtifactWork]]:
    """commit 泳道:/search/commits 仅第 1 页,预算条查询轮转(Q8)。"""
    items: list[dict[str, Any]] = []
    works: list[ArtifactWork] = []
    raw_cursor = state.get("commit_rotation", 0)
    cursor = raw_cursor if isinstance(raw_cursor, int) and not isinstance(raw_cursor, bool) else 0
    selected, next_cursor = rotate_queries(queries, cursor, budget=budget)
    state["commit_rotation"] = next_cursor
    for query in selected:
        counters["commit_queries"] += 1
        try:
            payload = await gh.search_commits(query, page=1)
        except GithubHuntError as exc:
            errors.append({"lane": "commit_message", "query": query, **exc.to_dict()})
            continue  # 继续下一查询
        for entry in _search_items(payload):
            counters["commit_items"] += 1
            repository = entry.get("repository") if isinstance(entry.get("repository"), Mapping) else {}
            if repository.get("private") is not False:
                counters["commit_private_skipped"] += 1
                continue
            repo_full = repository.get("full_name") if isinstance(repository.get("full_name"), str) else ""
            sha = entry.get("sha") if isinstance(entry.get("sha"), str) else ""
            html_url = entry.get("html_url") if isinstance(entry.get("html_url"), str) else ""
            inner = entry.get("commit") if isinstance(entry.get("commit"), Mapping) else {}
            message = inner.get("message") if isinstance(inner.get("message"), str) else ""
            source_url = _evidence_url(repo_full, sha, kind="commit", html_url=html_url)
            items.extend(
                _build_items(
                    scan_text(message),
                    context_text=message,
                    source_url=source_url,
                    source_type="commit_message",
                    query=query,
                    commit_sha=sha or None,
                    keystore=keystore,
                )
            )
            if repo_full and sha:
                works.append(
                    ArtifactWork(
                        repo_full_name=repo_full,
                        sha=sha,
                        kind="commit",
                        html_url=html_url,
                        query=query,
                        lane="commit_message",
                    )
                )
    return items, works


# ---------------------------------------------------------------------------
# 10) 主入口
# ---------------------------------------------------------------------------

_COUNTER_KEYS = (
    "code_queries", "code_pages", "code_items", "code_private_skipped", "noise_dropped",
    "code_malformed_skipped", "blobs_decoded", "blob_too_large", "blob_errors",
    "commit_queries", "commit_items", "commit_private_skipped",
)


def _new_counters() -> dict[str, int]:
    return {key: 0 for key in _COUNTER_KEYS}


async def hunt(
    *,
    tokens: Sequence[str],
    client: httpx.AsyncClient | None = None,
    queries: Sequence[str] | None = None,
    checkpoint: Mapping[str, Any] | None = None,
    commit_query_budget: int = COMMIT_QUERY_BUDGET,
    code_pages_max: int = CODE_PAGES_MAX,
    per_page: int = PER_PAGE_DEFAULT,
    artifact_limit: int = ARTIFACT_BATCH_LIMIT,
    artifact_concurrency: int = ARTIFACT_CONCURRENCY,
    artifact_retries: int = ARTIFACT_RETRY_LIMIT,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    artifact_sleep: Callable[[float], Awaitable[None]] | None = None,
    keystore: Any = None,
) -> dict[str, Any]:
    """跑一轮 GitHub 工件猎取(code + commit 两泳道 + 工件二次加工)。

    Args:
        tokens: GitHub token 池(空即抛 ``tokens_missing``,该源不启用)。
        client: 注入的 httpx.AsyncClient(测试走 MockTransport);缺省自建
            并在结束时关闭。
        queries: 查询池;缺省 = 发现层数据文件全部包 github_terms 去重。
        checkpoint: 跨 run 状态({"code_pages": {query: 下一页},
            "commit_rotation": 游标});调用方持久化后下轮传入即接续。
        commit_query_budget / code_pages_max / per_page: 泳道预算与分页
            (Q8 与规格 §2 缺省)。
        artifact_limit / artifact_concurrency / artifact_retries: 工件二次
            加工三参数(规格 §6 缺省)。
        now / sleep: 限速退避的时钟与睡眠注入口(测试 FakeClock)。
        artifact_sleep: 工件重试退避睡眠(独立注入,免污染限速断言)。
        keystore: 本地密钥库(duck-typed ``record``,插件侧
            ``keystore.Keystore``);每次命中以指纹为主键把全文入库,
            供 credcheck ``run_from_keystore`` 读库验证。缺省 None =
            纯扫描不入库(适配器面缺省注入缺省库位置)。

    Returns:
        {"items"(掩码-only 管线 items), "errors"(结构化错误列表),
        "checkpoint"(更新后的跨 run 状态), "counts"(扫描统计)}。
        单查询/单工件失败只进 errors/状态,不打断整轮(规格 §7);
        密钥库读写失败是本地系统性故障,按结构化错上抛(不静默丢全文)。
    """
    owned_client: httpx.AsyncClient | None = None
    if client is None:
        owned_client = build_async_client()
        client = owned_client
    try:
        gh = GithubClient(client=client, tokens=tokens, now=now, sleep=sleep)
        state: dict[str, Any] = dict(checkpoint) if isinstance(checkpoint, Mapping) else {}
        # 内层再拷一层:防与调用方传入的 checkpoint 共享子字典(跨 run 复用同一
        # 对象时游标写穿)。
        state["code_pages"] = dict(state.get("code_pages") or {}) if isinstance(state.get("code_pages"), Mapping) else {}
        if queries is None:
            packs_module = _sibling("packs")
            queries = packs_module.github_query_pool(packs_module.load_packs())
        pool = [query for query in queries if isinstance(query, str) and query.strip()]
        errors: list[dict[str, Any]] = []
        counters = _new_counters()
        code_items, code_works = await _hunt_code_lane(
            gh, pool, state, per_page=per_page, pages_max=code_pages_max, errors=errors, counters=counters,
            keystore=keystore,
        )
        commit_items, commit_works = await _hunt_commit_lane(
            gh, pool, state, budget=commit_query_budget, errors=errors, counters=counters, keystore=keystore
        )
        artifact_outcome = await process_artifacts(
            gh,
            [*code_works, *commit_works],
            limit=artifact_limit,
            concurrency=artifact_concurrency,
            retries=artifact_retries,
            sleep=artifact_sleep or asyncio.sleep,
            keystore=keystore,
        )
        items = [*code_items, *commit_items, *artifact_outcome["items"]]
        counters.update(artifact_outcome["counts"])
        counters["items"] = len(items)
        return {"items": items, "errors": errors, "checkpoint": state, "counts": counters}
    finally:
        if owned_client is not None:
            await owned_client.aclose()
