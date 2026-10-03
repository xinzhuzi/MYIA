"""凭证验证 + 余额探测(credhunter R2)。

行为规格(behavior-specs/credcheck.md)语义重实现,零上游代码;上游
AGPL 源码不看不搬。验证核心自包含(插件子模块互不 import,组合归
adapter/宿主 —— 与 packs/specs/fingerprints/findings 同一架构纪律);
唯 :func:`run_from_keystore` 惰性自举 keystore/specs 两兄弟(同目录
compile+exec 手法,与 ghhunt/exposure 一致):

- **resolve 链**:`registry.resolve(apiurl, apikey)`(specs.py,域名后缀 →
  密钥前缀 → unknown;aws_bedrock 域名特判只认 ``bedrock.``/``bedrock-``
  host)经参数注入使用,不复制实现;
- **前缀改道**(§2.3):openai/anthropic/gemini/openrouter 四家,密钥前缀
  命中官方族时强制改道官方 URL(防网关蜜罐 URL 劫持探测);
- **models 存活探测 = 验证本体**(§2):三态归类 final_verified /
  rejected(401/403 auth_denied,或 2xx 无证据 no_model_evidence)/
  transient(429/5xx/传输层失败);Q7:存活探测默认开;
- **余额/身份探测**(§4/§5):BalanceReport 17 字段;13 家匿名探测矩阵 +
  glm/longcat 被动 depleted 标记 + 仅存活性家族(balance "N/A")+ 无探测
  家族(kiro/azure_openai/vertex/longcat 无标记)诚实 unknown;Q7:默认
  **关**,``probe_balance=True`` 显式开;
- **出网纪律(Q8)**:串行 + 每供应商 RPM≤30(间隔 2s,:class:`Pacer`
  注入式 pacing);探测超时 15s;
- **header 安全闸**(§3):apikey 非纯 ASCII 或含 CR/LF → 不发请求,直接
  ``unavailable``(验证侧映射 transient,不武断判死);
- **Q9 掩码**:response_snippet / detail 先把全文密钥替换为前 8 后 4 掩码
  (与 findings.mask_apikey 同语义;子模块不互 import,本处内联同款实现)
  再截 512 字符;请求 URL(gemini ?key=)永不进任何输出。

实现决策(规格模糊处的有据选择,均可在 docstring 定位):
- models 端点拼接的「不重复拼版本段」规则由规格点名的 ``/v1`` 泛化为
  任意 ``/v<n>`` 尾段 —— 使 glm(``/api/paas/v4``)等 25 家全部对齐各自
  官方真实 models 端点(规格字面 ``/v1`` 会在 glm 拼出 ``v4/v1`` 双版本);
- openai 余额链端点取公开 Dashboard Billing 形状
  (``/v1/dashboard/billing/credit_grants|subscription``);anthropic
  admin 取 ``/v1/organizations/me(/cost_report|/rate_limits)``;
- tier 由 ``x-ratelimit-limit-requests`` 头推断:≥10000→tier5(规格点名),
  3000/1000/500/100 → tier4-1 为对称补档;tok�en 头不参与(阈值未核实);
- 匿名余额端点遇 401/403:deepseek 规格钉死 matched+alive=False
  (source ``deepseek:auth_denied``),其余 12 家沿用同一判死语义;
  其它非 2xx = 未命中(全默认值),不武断判死;
- 余额仅对 validation_state ∈ {final_verified, transient} 执行(rejected
  已判死无探测价值;transient 放行是为了 glm/longcat 被动标记在 429/5xx
  错误体上仍可解析 —— 规格 §5 的被动家族正是从错误码取证据);
- **密钥库读跑**(猎→存→验通路的验证段)::func:`run_from_keystore`
  读插件侧本地密钥库(猎手命中入库的指纹→全文记录),逐条验证后
  ``update_check`` 回填 ``check_state``/``last_check``;Q7/Q8 纪律同上
  (存活默认、余额显式;串行 + 每供应商 RPM 间隔),返回值掩码-only
  (fingerprint + 前缀掩码,全文永不进结果)。
"""

from __future__ import annotations

import json
import re
import sys
import time
import types
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

__all__ = [
    "ANONYMOUS_BALANCE_PROVIDERS",
    "BALANCE_EVIDENCE_KINDS",
    "LIVENESS_ONLY_PROVIDERS",
    "MIN_REQUEST_INTERVAL_SECONDS",
    "NO_PROBE_PROVIDERS",
    "PASSIVE_ONLY_PROVIDERS",
    "PER_PROVIDER_RPM_LIMIT",
    "REQUEST_TIMEOUT_SECONDS",
    "RESPONSE_SNIPPET_CHARS",
    "REROUTE_OFFICIAL_PROVIDERS",
    "BalanceReport",
    "HttpxTransport",
    "ModelsProbeOutcome",
    "Pacer",
    "ValidationResult",
    "check_credential",
    "check_credentials",
    "credential_kind",
    "extract_models",
    "format_amount",
    "is_hard_auth_denial",
    "is_safe_header_value",
    "mask_apikey",
    "probe_models",
    "result_to_dict",
    "run_from_keystore",
]

# ---------------------------------------------------------------------------
# 0) 常量(Q8 出网缺省 + §5 探测矩阵)
# ---------------------------------------------------------------------------

#: 每供应商 RPM 上限(Q8 定案:≤30,不抄上游并发 20)。
PER_PROVIDER_RPM_LIMIT = 30

#: 同供应商两次请求的最小间隔(60s / RPM 30 = 2s;Q8「间隔 2s」)。
MIN_REQUEST_INTERVAL_SECONDS = 2.0

#: 验证/余额单次请求超时(README 缺省参数表/规格 §6:15s)。
REQUEST_TIMEOUT_SECONDS = 15.0

#: response_snippet 上限(规格 §2.6:body 前 512 字符;先掩码后截断)。
RESPONSE_SNIPPET_CHARS = 512

#: BalanceReport.evidence_kind 封闭词表(规格 §4)。
BALANCE_EVIDENCE_KINDS = ("cash_balance", "quota", "liveness", "identity", "entitlement")

#: 前缀命中官方族时强制改道官方 URL 的供应商(规格 §2.3)。
REROUTE_OFFICIAL_PROVIDERS = ("openai", "anthropic", "gemini", "openrouter")

#: 可匿名探测余额/身份的 13 家(规格 §5「可直接匿名探测」逐家对齐)。
ANONYMOUS_BALANCE_PROVIDERS = (
    "deepseek", "kimi", "minimax", "cohere", "together", "replicate",
    "fireworks", "openrouter", "openai", "anthropic", "qoder", "cursor", "windsurf",
)

#: 被动 depleted 标记家族(无匿名端点,靠验证响应体的错误码/文案取证据)。
PASSIVE_ONLY_PROVIDERS = ("glm", "longcat")

#: 仅存活性家族(余额 "N/A";其余未列供应商同此兜底,规格 §5)。
LIVENESS_ONLY_PROVIDERS = ("gemini", "xai", "aws_bedrock")

#: 无直接匿名探测 = 诚实 unknown(不装懂;规格 §5)。
NO_PROBE_PROVIDERS = ("kiro", "azure_openai", "vertex")

#: glm 被动错误码(欠费/配额族;规格 §5:1308/1310/1311/1314-1321)。
GLM_DEPLETED_CODES = frozenset({"1308", "1310", "1311", "1314", "1315", "1316", "1317", "1318", "1319", "1320", "1321"})

#: longcat 被动 depleted 文案标记(规格 §5 点名中文两条,补一条英文对称)。
LONGCAT_DEPLETED_MARKERS = ("余额不足", "欠费", "insufficient balance")

#: 专用验证供应商(规格 §2.3 列表;其余按 protocol 走通用路径)。
_SPECIALIZED_VALIDATORS = ("anthropic", "gemini", "azure_openai", "openai", "qoder", "cursor", "openrouter", "aws_bedrock")

#: URL 尾部版本段(/v1、/v4、/v1beta …;models 拼接的去重判据)。
_VERSION_TAIL_RE = re.compile(r"/v\d+[a-z]*$")

#: 掩码保留长度(Q9:前 8 后 4;与 findings.mask_apikey 同语义,见模块 docstring)。
_MASK_HEAD, _MASK_TAIL = 8, 4
_MASK_ELLIPSIS = "…MASKED…"


# ---------------------------------------------------------------------------
# 1) 纯函数:安全闸 / 掩码 / 金额 / 模型提取 / 密钥类型
# ---------------------------------------------------------------------------


def is_safe_header_value(value: str) -> bool:
    """header 安全闸(规格 §3):值必须纯 ASCII 且无 CR/LF,否则禁发请求。"""
    return "\r" not in value and "\n" not in value and all(ord(ch) < 128 for ch in value)


def is_hard_auth_denial(status_code: int) -> bool:
    """401/403 = 终局认证否决(规格 §3:判死依据,transient 不得覆盖)。"""
    return status_code in (401, 403)


def mask_apikey(apikey: str) -> str:
    """Q9 掩码:前 8 后 4(≤12 只露头段;≤8 只露前 2)——与 findings 同款。"""
    if len(apikey) > _MASK_HEAD + _MASK_TAIL:
        return f"{apikey[:_MASK_HEAD]}{_MASK_ELLIPSIS}{apikey[-_MASK_TAIL:]}"
    if len(apikey) > _MASK_HEAD:
        return f"{apikey[:_MASK_HEAD]}{_MASK_ELLIPSIS}"
    return f"{apikey[:2]}{_MASK_ELLIPSIS}"


def format_amount(value: Any) -> str:
    """金额格式化:统一 4 位小数再去尾零(规格 §4;110.50→"110.5"、0→"0")。

    非数值输入返回空串(调用方保持「未匹配」语义,不臆造数字)。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    formatted = f"{number:.4f}".rstrip("0").rstrip(".")
    return "0" if formatted in ("", "-", "-0") else formatted


def extract_models(payload: Any) -> tuple[str, ...]:
    """模型提取三形态(规格 §2.5):data[].id | models[].name | modelSummaries[].modelId。"""
    if not isinstance(payload, dict):
        return ()
    data = payload.get("data")
    if isinstance(data, list):
        return tuple(
            item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
        )
    models = payload.get("models")
    if isinstance(models, list):
        return tuple(
            item["name"]
            for item in models
            if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]
        )
    summaries = payload.get("modelSummaries")
    if isinstance(summaries, list):
        return tuple(
            item["modelId"]
            for item in summaries
            if isinstance(item, dict) and isinstance(item.get("modelId"), str) and item["modelId"]
        )
    return ()


def credential_kind(apikey: str, provider: str) -> str:
    """密钥类型(规格 §2.7):openai/anthropic 官方前缀细分;空键 unknown;否则 standard。"""
    if not apikey:
        return "unknown"
    if provider == "openai":
        if apikey.startswith("sk-admin-"):
            return "admin"
        if apikey.startswith("sk-svcacct-"):
            return "service_account"
    if provider == "anthropic":
        if apikey.startswith("sk-ant-admin"):
            return "admin"
        if apikey.startswith(("sk-ant-oat", "sk-ant-sid")):
            return "oauth"
    return "standard"


def _first_present(payload: Any, keys: Sequence[str]) -> str:
    """按序取 body 顶层首个非空字符串键(scope/tier_evidence 等小抽取器)。"""
    if not isinstance(payload, dict):
        return ""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _to_float(value: Any) -> float | None:
    """宽松数值转换(int/float/数字串);不可解析返回 None(不臆造)。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# 2) 传输层:HttpResponse / HttpxTransport / Pacer(Q8 pacing)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HttpResponse:
    """一次探测响应(transport_error 一律 status_code=0,不抛异常)。"""

    status_code: int
    body: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)


class HttpxTransport:
    """默认同步传输(httpx;transport 层错误 → status 0,映射 transient)。"""

    def __init__(self, timeout: float = REQUEST_TIMEOUT_SECONDS) -> None:
        import httpx  # 惰性导入:模块加载零第三方依赖,仅真出网时装配

        self._client = httpx.Client(timeout=timeout)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> HttpResponse:
        try:
            response = self._client.request(
                method, url, headers=dict(headers) if headers else None, json=dict(json_body) if json_body else None
            )
        except Exception:  # noqa: BLE001 - 传输层任何失败都是 transient,不中断批次(规格 §6)
            return HttpResponse(status_code=0)
        return HttpResponse(
            status_code=response.status_code,
            body=response.text,
            headers={str(k).lower(): str(v) for k, v in response.headers.items()},
        )

    def close(self) -> None:
        self._client.close()


class Pacer:
    """Q8 限速器:串行语境下按「组」(供应商)强制最小请求间隔。

    clock/sleep 可注入(测试零等待);``waited`` 记录每次真实等待
    (组名, 秒数)供诊断与测试断言。
    """

    def __init__(
        self,
        min_interval_seconds: float = MIN_REQUEST_INTERVAL_SECONDS,
        *,
        clock: Any = time.monotonic,
        sleep: Any = time.sleep,
    ) -> None:
        self.min_interval_seconds = float(min_interval_seconds)
        self._clock = clock
        self._sleep = sleep
        self._last_by_group: dict[str, float] = {}
        self.waited: list[tuple[str, float]] = []

    def wait(self, group: str) -> None:
        """同组两次请求间隔不足 min_interval_seconds 时补睡差值。"""
        last = self._last_by_group.get(group)
        if last is not None:
            remaining = self.min_interval_seconds - (self._clock() - last)
            if remaining > 0:
                self.waited.append((group, remaining))
                self._sleep(remaining)
        self._last_by_group[group] = self._clock()


# ---------------------------------------------------------------------------
# 3) URL 装配(规格 §2 端点矩阵;URL 永不进输出 —— gemini ?key= 在内)
# ---------------------------------------------------------------------------


def _join_url(base: str, target: str) -> str:
    """按路径边界拼接;base 尾部与 target 头部整段重叠时去重(防 v1/v1 双拼)。"""
    cleaned = (base or "").rstrip("/")
    path = "/" + (target or "").lstrip("/")
    if cleaned.endswith(path):
        return cleaned
    parts = path.split("/")  # ["", "v1", "x", ...]
    for size in range(len(parts) - 1, 1, -1):  # ≥2 段的前缀才有意义
        prefix = "/".join(parts[:size])
        if prefix and cleaned.endswith(prefix):
            return cleaned + path[len(prefix):]
    return cleaned + path


def _models_url(base: str) -> str:
    """OpenAI 兼容 models 端点:base 尾带版本段则只补 /models(规格 §2 的
    「不重复拼接」泛化为任意 /v\\d+,见模块 docstring 实现决策)。"""
    cleaned = (base or "").rstrip("/")
    if cleaned.endswith("/models"):
        return cleaned
    if _VERSION_TAIL_RE.search(cleaned):
        return f"{cleaned}/models"
    return f"{cleaned}/v1/models"


def _strip_version_tail(base: str) -> str:
    return _VERSION_TAIL_RE.sub("", (base or "").rstrip("/"))


def _origin_of(base: str) -> str:
    candidate = base if "://" in base else f"https://{base}"
    parsed = urlparse(candidate)
    return f"{parsed.scheme}://{parsed.netloc}"


def _gemini_models_url(base: str, apikey: str) -> str:
    return f"{_origin_of(base)}/v1beta/models?{urlencode({'key': apikey})}"


def _azure_models_url(base: str) -> str:
    url = _join_url(base, "/openai/models")
    return f"{url}{'&' if '?' in url else '?'}api-version=2024-10-21"


def _openrouter_base(base: str) -> str:
    """openrouter.ai 域强制基址补 /api、去多余版本尾(规格 fingerprints §5)。"""
    cleaned = _strip_version_tail((base or "").rstrip("/"))
    parsed = urlparse(cleaned if "://" in cleaned else f"https://{cleaned}")
    host = (parsed.hostname or "").lower()
    if host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        if not parsed.path.rstrip("/").endswith("/api"):
            cleaned = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}/api"
    return cleaned


# ---------------------------------------------------------------------------
# 4) models 存活探测 = 验证本体(§2/§3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelsProbeOutcome:
    """models 存活探测结果(规格 §3 五字段)。"""

    models: tuple[str, ...] = ()
    status_code: int = 0
    provider: str = "unknown"
    key_state: str = "unavailable"  # active|expired|invalid_response|rate_limited|unavailable
    error: str = ""


class _ProbeClient:
    """探测客户端:统一 pacing + 三种 auth 头风格;URL/头永不落输出。"""

    def __init__(self, *, provider: str, apikey: str, transport: Any, pacer: Pacer) -> None:
        self.provider = provider
        self.apikey = apikey
        self.transport = transport
        self.pacer = pacer

    def request(
        self,
        method: str,
        url: str,
        *,
        auth: str = "bearer",
        json_body: Mapping[str, Any] | None = None,
    ) -> HttpResponse:
        self.pacer.wait(self.provider)
        headers: dict[str, str] = {}
        if auth == "bearer":
            headers["Authorization"] = f"Bearer {self.apikey}"
        elif auth == "x-api-key":
            headers["x-api-key"] = self.apikey
            headers["anthropic-version"] = "2023-06-01"
        elif auth == "api-key":
            headers["api-key"] = self.apikey
        return self.transport.request(method, url, headers=headers or None, json_body=json_body)


def _identity_evidence(payload: Any) -> bool:
    """cursor 证据规则:body 有 apiKeyName 或 userEmail(规格 §2.3)。"""
    if not isinstance(payload, dict):
        return False
    return any(isinstance(payload.get(key), str) and payload[key] for key in ("apiKeyName", "userEmail"))


def _probe_outcome(provider: str, response: HttpResponse, payload: Any, *, require_models: bool) -> ModelsProbeOutcome:
    """三态归类(§2.6 → §3 key_state 映射)。error 值:auth_denied /
    read_error / no_model_evidence(MYIA 自订机器码,语义对齐 §2.3)。"""
    status = response.status_code
    if 200 <= status < 300:
        models = extract_models(payload)
        evidence = bool(models) if require_models else _identity_evidence(payload)
        if evidence:
            return ModelsProbeOutcome(models=models, status_code=status, provider=provider, key_state="active")
        return ModelsProbeOutcome(
            status_code=status, provider=provider, key_state="invalid_response", error="no_model_evidence"
        )
    if is_hard_auth_denial(status):
        return ModelsProbeOutcome(status_code=status, provider=provider, key_state="expired", error="auth_denied")
    if status == 429:
        return ModelsProbeOutcome(status_code=status, provider=provider, key_state="rate_limited", error="read_error")
    return ModelsProbeOutcome(status_code=status, provider=provider, key_state="unavailable", error="read_error")


def _run_models_probe(
    *, provider: str, spec: Any, base: str, apikey: str, client: _ProbeClient
) -> tuple[ModelsProbeOutcome, HttpResponse, Any]:
    """装配并执行一次 models 存活探测(专用矩阵 → 协议族通用路径)。"""
    protocol = getattr(spec, "protocol", "openai_compatible") or "openai_compatible"
    if provider == "anthropic" or (provider not in _SPECIALIZED_VALIDATORS and protocol == "anthropic"):
        response = client.request("GET", _models_url(base), auth="x-api-key")
        payload = _parse_json(response.body)
        return _probe_outcome(provider, response, payload, require_models=True), response, payload
    if provider == "gemini" or (provider not in _SPECIALIZED_VALIDATORS and protocol == "gemini"):
        response = client.request("GET", _gemini_models_url(base, apikey), auth="none")
        payload = _parse_json(response.body)
        return _probe_outcome(provider, response, payload, require_models=True), response, payload
    if provider == "azure_openai":
        response = client.request("GET", _azure_models_url(base), auth="api-key")
        payload = _parse_json(response.body)
        return _probe_outcome(provider, response, payload, require_models=True), response, payload
    if provider == "qoder":
        response = client.request("GET", _join_url(base, "/api/v1/cloud/models"))
        payload = _parse_json(response.body)
        return _probe_outcome(provider, response, payload, require_models=True), response, payload
    if provider == "cursor":
        response = client.request("GET", _join_url(base, "/v1/me"))
        payload = _parse_json(response.body)
        return _probe_outcome(provider, response, payload, require_models=False), response, payload
    if provider == "openrouter":
        # 两段式(§2.3):auth/key 失败即终,成功再 models。
        auth_response = client.request("GET", _join_url(base, "/v1/auth/key"))
        if not (200 <= auth_response.status_code < 300):
            payload = _parse_json(auth_response.body)
            return _probe_outcome(provider, auth_response, payload, require_models=True), auth_response, payload
        response = client.request("GET", _join_url(base, "/v1/models"))
        payload = _parse_json(response.body)
        return _probe_outcome(provider, response, payload, require_models=True), response, payload
    if provider == "aws_bedrock" or (provider not in _SPECIALIZED_VALIDATORS and protocol == "aws_bedrock"):
        response = client.request("GET", _join_url(base, "/foundation-models"))
        payload = _parse_json(response.body)
        return _probe_outcome(provider, response, payload, require_models=True), response, payload
    # 默认 OpenAiCompatible(vertex 无探测规格,与未列供应商同走兼容路径)
    response = client.request("GET", _models_url(base))
    payload = _parse_json(response.body)
    return _probe_outcome(provider, response, payload, require_models=True), response, payload


def probe_models(
    *,
    apikey: str,
    apiurl: str = "",
    registry: Any,
    transport: Any = None,
    pacer: Pacer | None = None,
) -> ModelsProbeOutcome:
    """单发 models 存活探测(Q7 默认开的「存活探测」独立入口)。

    resolve → 前缀改道 → 安全闸 → 专用/通用探测;不发请求的失败
    (no_api_url / 安全闸)直接以 unavailable 返回。
    """
    resolution = registry.resolve(apiurl, apikey)
    base = _effective_base(resolution, apikey)
    if not base:
        return ModelsProbeOutcome(provider=resolution.provider, key_state="unavailable", error="no_api_url")
    if not is_safe_header_value(apikey):
        return ModelsProbeOutcome(provider=resolution.provider, key_state="unavailable", error="unsafe-key")
    client = _ProbeClient(
        provider=resolution.provider, apikey=apikey, transport=transport or HttpxTransport(), pacer=pacer or Pacer()
    )
    probe, _response, _payload = _run_models_probe(
        provider=resolution.provider, spec=resolution.spec, base=base, apikey=apikey, client=client
    )
    return probe


# ---------------------------------------------------------------------------
# 5) BalanceReport(17 字段,§4)+ 探测矩阵(§5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BalanceReport:
    """余额/身份探测结果 —— 17 字段与语义逐条对齐规格 §4。

    - balance_usd:"N/A"=明确无余额概念(仅存活性家族);空串=未匹配;
    - alive:True=确认存活 / False=确认死亡 / None=未知(诚实三态);
    - matched=False ⇒ 余额字段全默认(未命中任何策略);
    - detail/quota/usage/entitlements/identity 均先经 Q9 掩码净化。
    """

    gateway: str = ""
    provider: str = ""
    balance_usd: str = ""
    tier: str = ""
    plan: str = ""
    account_type: str = ""
    balance_native: str = ""
    currency: str = ""
    source: str = ""
    evidence_kind: str = ""
    detail: Any = None
    quota: Any = None
    usage: Any = None
    entitlements: Any = None
    identity: Any = None
    alive: bool | None = None
    matched: bool = False


@dataclass
class _BalanceContext:
    """余额探测上下文:验证阶段产物复用 + 探测客户端。"""

    provider: str
    base: str
    apikey: str
    client: _ProbeClient
    probe: ModelsProbeOutcome
    payload: Any          # 验证响应解析后的 JSON(被动家族的证据源)
    raw_body: str         # 验证响应原文(depleted 文案标记可能在非 JSON 体上)
    response_headers: Mapping[str, str]
    validation_state: str


def _scrub(value: Any, apikey: str) -> Any:
    """递归把结构里的全文密钥替换为掩码(Q9:detail 等证据对象的红线)。"""
    if isinstance(value, str):
        return value.replace(apikey, mask_apikey(apikey)) if apikey and apikey in value else value
    if isinstance(value, Mapping):
        return {key: _scrub(item, apikey) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, apikey) for item in value]
    return value


def _snippet(body: str, apikey: str) -> str:
    """先掩码后截断(顺序与 findings.build_finding_item 一致)。"""
    redacted = body.replace(apikey, mask_apikey(apikey)) if apikey and apikey in body else body
    return redacted[:RESPONSE_SNIPPET_CHARS]


def _matched(ctx: _BalanceContext, **fields: Any) -> BalanceReport:
    base = {"gateway": ctx.provider, "provider": ctx.provider, "matched": True}
    return BalanceReport(**{**base, **fields})


def _parse_json(body: str) -> Any:
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None


def _unmatched(alive: bool | None = None) -> BalanceReport:
    return BalanceReport(alive=alive)


def _passive_alive(status_code: int) -> bool | None:
    """验证上下文的被动存活:2xx→True;401/403→False;其它→None(§5)。"""
    if 200 <= status_code < 300:
        return True
    if is_hard_auth_denial(status_code):
        return False
    return None


def _liveness_balance(ctx: _BalanceContext) -> BalanceReport:
    """仅存活性家族:balance "N/A" + liveness 证据(429 亦 alive=True,§5)。"""
    alive = _passive_alive(ctx.probe.status_code)
    if ctx.probe.status_code == 429:
        alive = True
    return _matched(ctx, balance_usd="N/A", evidence_kind="liveness", source=f"{ctx.provider}:models", alive=alive)


def _passive_survival_balance(ctx: _BalanceContext) -> BalanceReport:
    """无探测家族:诚实 unknown(不命中策略,只回填被动存活,§5)。"""
    return _unmatched(alive=_passive_alive(ctx.probe.status_code))


def _ratelimit_tier(headers: Mapping[str, str]) -> str:
    """openai 存活档位推断:x-ratelimit-limit-requests ≥10000→tier5(§5)。"""
    raw = headers.get("x-ratelimit-limit-requests", "")
    if not raw.strip().isdigit():
        return ""
    limit = int(raw)
    for threshold, tier in ((10000, "5"), (3000, "4"), (1000, "3"), (500, "2"), (100, "1")):
        if limit >= threshold:
            return f"tier{tier}"
    return ""


def _balance_deepseek(ctx: _BalanceContext) -> BalanceReport:
    response = ctx.client.request("GET", _join_url(_strip_version_tail(ctx.base), "/user/balance"))
    payload = _parse_json(response.body)
    if is_hard_auth_denial(response.status_code):
        return _matched(ctx, source="deepseek:auth_denied", evidence_kind="liveness", alive=False)
    if not (200 <= response.status_code < 300) or not isinstance(payload, dict):
        return _unmatched()
    native = usd = 0.0
    for info in payload.get("balance_infos") or []:
        if not isinstance(info, dict):
            continue
        amount = _to_float(info.get("total_balance"))
        if amount is None:
            continue
        if info.get("currency") == "CNY":
            native += amount
        elif info.get("currency") == "USD":
            usd += amount
    result = _matched(
        ctx, source="deepseek:user_balance", evidence_kind="cash_balance", alive=True, detail=_scrub(payload, ctx.apikey)
    )
    result = replace(result, balance_native=format_amount(native), currency="CNY")
    if usd:
        result = replace(result, balance_usd=format_amount(usd))
    return result


def _kimi_currency(host: str) -> str:
    """kimi 计价币种:moonshot.cn→CNY、moonshot.ai→USD;非 moonshot 退化存活性(§5)。"""
    lowered = (host or "").lower()
    if lowered == "moonshot.cn" or lowered.endswith(".moonshot.cn"):
        return "CNY"
    if lowered == "moonshot.ai" or lowered.endswith(".moonshot.ai"):
        return "USD"
    return ""


def _balance_kimi(ctx: _BalanceContext) -> BalanceReport:
    currency = _kimi_currency(urlparse(ctx.base if "://" in ctx.base else f"https://{ctx.base}").hostname or "")
    if not currency:
        return _liveness_balance(ctx)  # 非 moonshot 域名退化为 models_liveness
    response = ctx.client.request("GET", _join_url(ctx.base, "/users/me/balance"))
    payload = _parse_json(response.body)
    data = payload.get("data") if isinstance(payload, dict) else None
    if is_hard_auth_denial(response.status_code):
        return _matched(ctx, source="kimi:auth_denied", evidence_kind="liveness", alive=False)
    if not (200 <= response.status_code < 300) or not isinstance(data, dict):
        return _unmatched()
    amount = format_amount(data.get("available_balance"))
    fields = {"source": "kimi:users_me_balance", "evidence_kind": "cash_balance", "alive": True,
              "detail": _scrub(payload, ctx.apikey)}
    if currency == "USD":
        fields["balance_usd"] = amount
    else:
        fields.update(balance_native=amount, currency=currency)
    return _matched(ctx, **fields)


def _balance_minimax(ctx: _BalanceContext) -> BalanceReport:
    response = ctx.client.request("GET", _join_url(ctx.base, "/token_plan/remains"))
    payload = _parse_json(response.body)
    base_resp = payload.get("base_resp") if isinstance(payload, dict) else None
    if is_hard_auth_denial(response.status_code):
        return _matched(ctx, source="minimax:auth_denied", evidence_kind="liveness", alive=False)
    if not (200 <= response.status_code < 300) or not isinstance(base_resp, dict) or base_resp.get("status_code") != 0:
        return _unmatched()
    return _matched(
        ctx,
        source="minimax:token_plan_remains",
        evidence_kind="quota",
        alive=True,
        quota=_scrub(payload.get("model_remains"), ctx.apikey),
        detail=_scrub(payload, ctx.apikey),
    )


def _balance_cohere(ctx: _BalanceContext) -> BalanceReport:
    response = ctx.client.request("POST", _join_url(ctx.base, "/v1/check-api-key"), json_body={})
    payload = _parse_json(response.body)
    if is_hard_auth_denial(response.status_code):
        return _matched(ctx, source="cohere:auth_denied", evidence_kind="liveness", alive=False)
    if not (200 <= response.status_code < 300) or not isinstance(payload, dict):
        return _unmatched()
    valid = payload.get("valid") is True
    identity = {
        key: payload[key] for key in ("organization_id", "owner_id") if isinstance(payload.get(key), str) and payload[key]
    }
    return _matched(
        ctx,
        source="cohere:check_api_key",
        evidence_kind="identity",
        alive=valid,
        identity=identity or None,
        detail=_scrub(payload, ctx.apikey),
    )


def _balance_together(ctx: _BalanceContext) -> BalanceReport:
    response = ctx.client.request("GET", _join_url(ctx.base, "/whoami"))
    payload = _parse_json(response.body)
    if is_hard_auth_denial(response.status_code):
        return _matched(ctx, source="together:auth_denied", evidence_kind="liveness", alive=False)
    if not (200 <= response.status_code < 300) or not isinstance(payload, dict):
        return _unmatched()
    identity = {
        key: payload[key]
        for key in ("id", "name", "email", "project_id", "organization_id")
        if isinstance(payload.get(key), str) and payload[key]
    }
    # 有限速头 → 配额证据优先于身份(§5)
    evidence = "quota" if any(key.startswith("x-ratelimit") for key in response.headers) else "identity"
    return _matched(
        ctx, source="together:whoami", evidence_kind=evidence, alive=True, identity=identity or None,
        detail=_scrub(payload, ctx.apikey),
    )


def _balance_replicate(ctx: _BalanceContext) -> BalanceReport:
    response = ctx.client.request("GET", _join_url(ctx.base, "/account"))
    payload = _parse_json(response.body)
    if is_hard_auth_denial(response.status_code):
        return _matched(ctx, source="replicate:auth_denied", evidence_kind="liveness", alive=False)
    if not (200 <= response.status_code < 300) or not isinstance(payload, dict):
        return _unmatched()
    identity = {
        key: payload[key]
        for key in ("type", "username", "name", "github_url")
        if isinstance(payload.get(key), str) and payload[key]
    }
    return _matched(
        ctx, source="replicate:account", evidence_kind="identity", alive=True, identity=identity or None,
        detail=_scrub(payload, ctx.apikey),
    )


_FIREWORKS_TIER_BY_MAX_VALUE = {"50": "tier1", "500": "tier2", "5000": "tier3", "50000": "tier4"}


def _balance_fireworks(ctx: _BalanceContext) -> BalanceReport:
    response = ctx.client.request("GET", _join_url(ctx.base, "/accounts"))
    payload = _parse_json(response.body)
    if is_hard_auth_denial(response.status_code):
        return _matched(ctx, source="fireworks:auth_denied", evidence_kind="liveness", alive=False)
    accounts = payload.get("accounts") if isinstance(payload, dict) else None
    if not (200 <= response.status_code < 300) or not isinstance(accounts, list) or not accounts:
        return _unmatched()
    first = accounts[0]
    account_name = first.get("name") if isinstance(first, dict) else None
    tier = ""
    entitlements: Any = None
    identity: Any = None
    if isinstance(account_name, str) and account_name:
        account_response = ctx.client.request("GET", _join_url(ctx.base, f"/{account_name}"))
        account_payload = _parse_json(account_response.body) or {}
        quotas_response = ctx.client.request("GET", _join_url(ctx.base, f"/{account_name}/quotas"))
        quotas = _parse_json(quotas_response.body)
        quota_list = quotas.get("quotas") if isinstance(quotas, dict) else None
        entitlements = _scrub(quota_list, ctx.apikey) if isinstance(quota_list, list) else None
        for quota in quota_list or []:
            if isinstance(quota, dict) and quota.get("name") == "monthly-spend-usd":
                max_value = str(quota.get("maxValue", quota.get("max_value", "")))
                tier = "enterprise" if "ENTERPRISE" in max_value.upper() else _FIREWORKS_TIER_BY_MAX_VALUE.get(max_value, "")
        identity = {
            key: account_payload.get(key)
            for key in ("accountType", "suspendState")
            if isinstance(account_payload.get(key), str) and account_payload[key]
        } or None
    return _matched(
        ctx,
        balance_usd="N/A",
        tier=tier,
        source="fireworks:accounts",
        evidence_kind="entitlement",
        alive=True,
        entitlements=entitlements,
        identity=identity,
    )


def _balance_openrouter(ctx: _BalanceContext) -> BalanceReport:
    response = ctx.client.request("GET", _join_url(ctx.base, "/v1/auth/key"))
    payload = _parse_json(response.body)
    info = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    if is_hard_auth_denial(response.status_code):
        return _matched(ctx, source="openrouter:auth_denied", evidence_kind="liveness", alive=False)
    if 200 <= response.status_code < 300 and isinstance(info, dict):
        amount: Any = None
        if info.get("limit_remaining") is not None:
            amount = info.get("limit_remaining")
        elif isinstance(info.get("limit"), (int, float)) and isinstance(info.get("usage"), (int, float)):
            amount = float(info["limit"]) - float(info["usage"])
        if amount is None or info.get("is_free_tier") is True:
            # 限额缺失降级 /credits;free tier 余额记 0(§5)
            credits_response = ctx.client.request("GET", _join_url(ctx.base, "/v1/credits"))
            credits = _parse_json(credits_response.body)
            if not (200 <= credits_response.status_code < 300) or not isinstance(credits, dict):
                return _unmatched()
            total, used = credits.get("total_credits"), credits.get("total_usage")
            amount = 0 if info.get("is_free_tier") is True else (
                float(total) - float(used) if isinstance(total, (int, float)) and isinstance(used, (int, float)) else None
            )
            if amount is None:
                return _unmatched()
        return _matched(
            ctx,
            balance_usd=format_amount(amount),
            source="openrouter:auth_key",
            evidence_kind="cash_balance",
            alive=True,
            detail=_scrub(payload, ctx.apikey),
        )
    return _unmatched()


_OPENAI_ACCOUNT_TYPE_BY_PREFIX = (("sk-proj-", "project"), ("sk-svcacct-", "service_account"), ("sk-admin-", "admin"))


def _openai_account_type(apikey: str) -> str:
    for prefix, kind in _OPENAI_ACCOUNT_TYPE_BY_PREFIX:
        if apikey.startswith(prefix):
            return kind
    return "ordinary"


def _balance_openai(ctx: _BalanceContext) -> BalanceReport:
    account_type = _openai_account_type(ctx.apikey)
    origin = _strip_version_tail(ctx.base)
    grants = ctx.client.request("GET", _join_url(origin, "/dashboard/billing/credit_grants"))
    grants_payload = _parse_json(grants.body)
    if grants.status_code == 429:
        # 探测遇 429:rate_limited 且 alive=true(§6)
        return _matched(
            ctx, account_type=account_type, source="openai:credit_grants", evidence_kind="liveness", alive=True,
            tier=_ratelimit_tier(grants.headers) or _ratelimit_tier(ctx.response_headers),
        )
    if 200 <= grants.status_code < 300 and isinstance(grants_payload, dict) and grants_payload.get("total_granted") is not None:
        return _matched(
            ctx, account_type=account_type, balance_usd=format_amount(grants_payload.get("total_granted")),
            source="openai:credit_grants", evidence_kind="cash_balance", alive=True,
            detail=_scrub(grants_payload, ctx.apikey),
        )
    subscription = ctx.client.request("GET", _join_url(origin, "/dashboard/billing/subscription"))
    subscription_payload = _parse_json(subscription.body)
    if subscription.status_code == 429:
        return _matched(
            ctx, account_type=account_type, source="openai:subscription", evidence_kind="liveness", alive=True,
            tier=_ratelimit_tier(subscription.headers) or _ratelimit_tier(ctx.response_headers),
        )
    if 200 <= subscription.status_code < 300 and isinstance(subscription_payload, dict):
        hard_limit = subscription_payload.get("hard_limit_usd")
        if isinstance(hard_limit, (int, float)):
            usage = subscription_payload.get("usage", 0)
            usage = usage if isinstance(usage, (int, float)) else 0
            return _matched(
                ctx, account_type=account_type, balance_usd=format_amount(float(hard_limit) - float(usage)),
                source="openai:subscription", evidence_kind="cash_balance", alive=True,
                detail=_scrub(subscription_payload, ctx.apikey),
            )
    # 两级计费端点都不可用 → /v1/models 存活档位推断(复用验证响应,零新请求)
    result = _liveness_balance(ctx)
    return replace(result, account_type=account_type, tier=_ratelimit_tier(ctx.response_headers), source="openai:models")


def _balance_anthropic(ctx: _BalanceContext) -> BalanceReport:
    if credential_kind(ctx.apikey, ctx.provider) not in ("admin", "oauth"):
        return _liveness_balance(ctx)  # 普通键:/v1/models 存活(429 亦 alive,§5)
    me = ctx.client.request("GET", _join_url(ctx.base, "/v1/organizations/me"), auth="x-api-key")
    payload = _parse_json(me.body)
    if me.status_code == 429:
        return _matched(ctx, source="anthropic:organizations_me", evidence_kind="liveness", alive=True)
    if not (200 <= me.status_code < 300) or not isinstance(payload, dict):
        return _liveness_balance(ctx)
    identity = {
        key: payload[key] for key in ("id", "name", "organization_id") if isinstance(payload.get(key), str) and payload[key]
    }
    tier = payload.get("usage_tier") if isinstance(payload.get("usage_tier"), str) else ""
    usage: Any = None
    cost = ctx.client.request("GET", _join_url(ctx.base, "/v1/organizations/me/cost_report"), auth="x-api-key")
    cost_payload = _parse_json(cost.body)
    if 200 <= cost.status_code < 300 and isinstance(cost_payload, dict):
        buckets = None
        for key in ("usd_by_date", "cost_by_date"):
            candidate = cost_payload.get(key)
            if isinstance(candidate, dict):
                buckets = candidate
                break
        if buckets is not None:
            total = sum(float(v) for v in buckets.values() if isinstance(v, (int, float)))
            usage = {"usd_spend": format_amount(total / 100.0)}  # 1d 桶合计 /100(§5)
    if not tier:
        limits = ctx.client.request("GET", _join_url(ctx.base, "/v1/organizations/me/rate_limits"), auth="x-api-key")
        limits_payload = _parse_json(limits.body)
        if isinstance(limits_payload, dict) and isinstance(limits_payload.get("usage_tier"), str):
            tier = limits_payload["usage_tier"]
    return _matched(
        ctx, tier=tier, source="anthropic:organizations_me", evidence_kind="identity", alive=True,
        identity=identity or None, usage=usage,
    )


def _balance_qoder(ctx: _BalanceContext) -> BalanceReport:
    # 验证端点即余额证据源:零新请求复用验证响应(§5 qoder 行)
    plan = _first_present(ctx.payload, ("plan", "subscription", "tier"))
    return _matched(
        ctx, plan=plan, entitlements=list(ctx.probe.models) or None,
        source="qoder:cloud_models", evidence_kind="entitlement", alive=True,
    )


def _balance_cursor(ctx: _BalanceContext) -> BalanceReport:
    payload = ctx.payload if isinstance(ctx.payload, dict) else {}
    identity = {
        key: payload[key] for key in ("apiKeyName", "userEmail", "name") if isinstance(payload.get(key), str) and payload[key]
    }
    if not identity:
        return _unmatched()
    return _matched(
        ctx, source="cursor:me", evidence_kind="identity", alive=True, identity=identity,
    )


def _balance_windsurf(ctx: _BalanceContext) -> BalanceReport:
    response = ctx.client.request("POST", _join_url(ctx.base, "/GetTeamCreditBalance"), auth="none",
                                  json_body={"service_key": ctx.apikey})
    payload = _parse_json(response.body)
    if is_hard_auth_denial(response.status_code):
        return _matched(ctx, source="windsurf:auth_denied", evidence_kind="liveness", alive=False)
    if not (200 <= response.status_code < 300) or not isinstance(payload, dict):
        return _unmatched()
    credits = payload.get("addOnCreditsAvailable")
    return _matched(
        ctx, balance_native=format_amount(credits), currency="credits",
        source="windsurf:GetTeamCreditBalance", evidence_kind="cash_balance", alive=True,
        detail=_scrub(payload, ctx.apikey),
    )


def _balance_glm(ctx: _BalanceContext) -> BalanceReport:
    error = ctx.payload.get("error") if isinstance(ctx.payload, dict) else None
    code = str(error.get("code", "")) if isinstance(error, dict) else ""
    if code in GLM_DEPLETED_CODES:
        reset_at = error.get("reset_at") if isinstance(error, dict) else None
        return _matched(
            ctx, source="glm:passive_error", evidence_kind="quota", alive=True,
            quota={"depleted_code": code, "reset_at": reset_at},
        )
    return _liveness_balance(ctx)  # 无被动错误码 → 仅存活性(§5)


def _balance_longcat(ctx: _BalanceContext) -> BalanceReport:
    """被动 depleted 文案标记在验证响应原文上判定(§5);无标记 = 诚实 unknown。"""
    if any(marker in ctx.raw_body for marker in LONGCAT_DEPLETED_MARKERS):
        return _matched(
            ctx, source="longcat:passive_depleted", evidence_kind="quota", alive=True, quota={"depleted": True}
        )
    return _passive_survival_balance(ctx)


_BALANCE_HANDLERS: dict[str, Any] = {
    "deepseek": _balance_deepseek,
    "kimi": _balance_kimi,
    "minimax": _balance_minimax,
    "cohere": _balance_cohere,
    "together": _balance_together,
    "replicate": _balance_replicate,
    "fireworks": _balance_fireworks,
    "openrouter": _balance_openrouter,
    "openai": _balance_openai,
    "anthropic": _balance_anthropic,
    "qoder": _balance_qoder,
    "cursor": _balance_cursor,
    "windsurf": _balance_windsurf,
    "glm": _balance_glm,
    "longcat": _balance_longcat,
}


def _probe_balance(ctx: _BalanceContext) -> BalanceReport:
    """余额/身份探测分派(§5):匿名矩阵 → 被动家族 → 无探测 → 存活性兜底。"""
    handler = _BALANCE_HANDLERS.get(ctx.provider)
    if handler is not None:
        return handler(ctx)
    if ctx.provider in NO_PROBE_PROVIDERS:
        return _passive_survival_balance(ctx)
    return _liveness_balance(ctx)


# ---------------------------------------------------------------------------
# 6) 验证编排:check_credential / check_credentials
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationResult:
    """一次凭证验证的完整产物(验证本体 + 可选余额回填)。

    validation_state ∈ {final_verified, rejected, transient}(§2.6);
    error ∈ {"", auth_denied, read_error, no_model_evidence,
    "no_api_url", unsafe-key};balance 仅在 probe_balance=True 且未判死时
    非 None。
    """

    provider: str = "unknown"
    resolve_reason: str = "unknown"
    apiurl: str = ""
    validation_state: str = "transient"
    error: str = ""
    key_state: str = "unavailable"
    status_code: int = 0
    models: tuple[str, ...] = ()
    response_snippet: str = ""
    credential_kind: str = "standard"
    scope: str = ""
    tier_evidence: str = ""
    balance: BalanceReport | None = None


def _classify(probe: ModelsProbeOutcome) -> tuple[str, str]:
    """key_state → (validation_state, error)(§2.6 三态)。"""
    if probe.key_state == "active":
        return "final_verified", ""
    if probe.key_state in ("expired", "invalid_response"):
        return "rejected", probe.error
    return "transient", probe.error


def _effective_base(resolution: Any, apikey: str) -> str:
    """resolve 产物 → 有效基址:前缀命中官方族时强制改道官方 URL(§2.3)。"""
    spec = getattr(resolution, "spec", None)
    base = (getattr(resolution, "apiurl", "") or "").strip()
    if spec is not None and spec.name in REROUTE_OFFICIAL_PROVIDERS:
        if any(apikey.startswith(prefix) for prefix in spec.key_prefixes):
            base = spec.official_api_url
    if spec is not None and spec.name == "openrouter":
        base = _openrouter_base(base) if base else base
    return base


def check_credential(
    *,
    apikey: str,
    apiurl: str = "",
    registry: Any,
    transport: Any = None,
    pacer: Pacer | None = None,
    probe_balance: bool = False,
) -> ValidationResult:
    """验证一把凭证:resolve → 改道 → 安全闸 → models 三态 →(可选)余额。

    Args:
        apikey: 全文密钥(仅本函数作用域使用:auth 头/掩码;永不落返回值)。
        apiurl: 猎取侧归因的 API 地址(可空;空则回落规格官方基址)。
        registry: ``specs.ProviderResolver``(duck-typed ``resolve``)。
        transport: 可注入传输(默认 :class:`HttpxTransport`;测试必注入)。
        pacer: Q8 限速器(默认 :class:`Pacer` 串行 2s/供应商)。
        probe_balance: Q7 余额/身份探测开关(默认 **关**,显式开)。

    Returns:
        :class:`ValidationResult`;余额仅当 probe_balance 且未 rejected 时回填。
    """
    resolution = registry.resolve(apiurl, apikey)
    provider = resolution.provider
    base = _effective_base(resolution, apikey)
    kind = credential_kind(apikey, provider)
    if not base:
        return ValidationResult(
            provider=provider, resolve_reason=resolution.reason, apiurl="",
            validation_state="rejected", error="no_api_url", key_state="unavailable",
            credential_kind=kind,
        )
    if not is_safe_header_value(apikey):
        return ValidationResult(
            provider=provider, resolve_reason=resolution.reason, apiurl=base,
            validation_state="transient", error="unsafe-key", key_state="unavailable",
            credential_kind=kind,
        )
    client = _ProbeClient(
        provider=provider, apikey=apikey, transport=transport or HttpxTransport(), pacer=pacer or Pacer()
    )
    probe, response, payload = _run_models_probe(
        provider=provider, spec=resolution.spec, base=base, apikey=apikey, client=client
    )
    state, error = _classify(probe)
    balance: BalanceReport | None = None
    if probe_balance and state in ("final_verified", "transient"):
        ctx = _BalanceContext(
            provider=provider, base=base, apikey=apikey, client=client, probe=probe,
            payload=payload, raw_body=response.body, response_headers=dict(response.headers),
            validation_state=state,
        )
        balance = _probe_balance(ctx)
    return ValidationResult(
        provider=provider,
        resolve_reason=resolution.reason,
        apiurl=base,
        validation_state=state,
        error=error,
        key_state=probe.key_state,
        status_code=probe.status_code,
        models=probe.models,
        response_snippet=_snippet(response.body, apikey),
        credential_kind=kind,
        scope=_first_present(payload, ("organization_id", "account_id")),
        tier_evidence=_first_present(payload, ("tier", "plan")),
        balance=balance,
    )


def check_credentials(
    records: Sequence[Mapping[str, Any]],
    *,
    registry: Any,
    transport: Any = None,
    pacer: Pacer | None = None,
    probe_balance: bool = False,
) -> list[ValidationResult]:
    """批量串行验证(Q8:天然串行;transport/pacer 全批共享限速语义)。

    records 每项 ``{"apikey": str, "apiurl": str(可省)}``;形状坏抛
    ValueError(调用方结构化降级)。
    """
    validated: list[tuple[str, str]] = []
    for index, record in enumerate(records or []):
        if not isinstance(record, Mapping):
            raise ValueError(f"records[{index}] 必须是映射(apikey/apiurl),当前为 {type(record).__name__}")
        apikey = record.get("apikey")
        apiurl = record.get("apiurl", "")
        if not isinstance(apikey, str) or not apikey:
            raise ValueError(f"records[{index}].apikey 必须是非空字符串")
        if not isinstance(apiurl, str):
            raise ValueError(f"records[{index}].apiurl 必须是字符串或省略")
        validated.append((apikey, apiurl))
    if not validated:
        return []
    shared_transport = transport or HttpxTransport()
    shared_pacer = pacer or Pacer()
    results: list[ValidationResult] = []
    for apikey, apiurl in validated:
        results.append(
            check_credential(
                apikey=apikey, apiurl=apiurl, registry=registry,
                transport=shared_transport, pacer=shared_pacer, probe_balance=probe_balance,
            )
        )
    return results


def result_to_dict(result: ValidationResult) -> dict[str, Any]:
    """ValidationResult → JSON 可序列化 dict(balance 一并展平;掩码-only)。"""
    payload = asdict(result)
    return payload


# ---------------------------------------------------------------------------
# 7) 密钥库读跑(猎→存→验通路的验证段)
# ---------------------------------------------------------------------------


def _require_sibling(name: str) -> types.ModuleType:
    """取同目录 ``credhunter/<name>.py`` 子模块(登记 sys.modules 后 compile+exec)。

    与适配器 ``_load_module``/ghhunt ``_sibling`` 同手法同理由(不走
    importlib,插件目录零 ``__pycache__``);仅 :func:`run_from_keystore`
    惰性使用(keystore 读库回填、specs 缺省注册表),验证核心零兄弟依赖。
    """
    module_name = f"myia_credhunter_{name}"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    module_file = Path(__file__).resolve().parent / f"{name}.py"
    if not module_file.is_file():
        raise FileNotFoundError(f"credhunter 子模块不存在:{module_file}")
    module = types.ModuleType(module_name)
    module.__file__ = str(module_file)
    sys.modules[module_name] = module
    executable = compile(module_file.read_text(encoding="utf-8"), str(module_file), "exec")
    exec(executable, module.__dict__)  # noqa: S102 - 仓库内受控插件代码,非任意输入
    return module


def run_from_keystore(
    keystore_path: str | Path,
    *,
    balance: bool = False,
    limit: int | None = None,
    rpm: int = PER_PROVIDER_RPM_LIMIT,
    registry: Any = None,
    transport: Any = None,
    pacer: Pacer | None = None,
) -> dict[str, Any]:
    """读本地密钥库逐条验证并回填(「猎→存→验」通路的验证段)。

    猎手(ghhunt)命中时已把全文密钥以指纹为主键落进插件侧密钥库(Q9:
    items/模板仍然掩码-only);本函数读库逐条验证 —— Q7:models 存活探测
    默认开、``balance=True`` 才显式探测余额/身份;Q8:串行 + 每供应商
    RPM ``rpm``(缺省 30 → 最小间隔 2s,共享 :class:`Pacer`)。每条验证
    完即 ``update_check`` 回填 ``last_check``/``check_state``
    (final_verified/rejected/transient),中途崩溃不丢已验结论。

    Args:
        keystore_path: 密钥库文件路径(交 ``keystore.Keystore`` 构造;
            缺省位置由 ``keystore.default_keystore_path`` 解析)。
        balance: Q7 余额/身份探测开关(默认**关**,显式开)。
        limit: 最多验证条数(缺省全量;按入库顺序取前 N)。
        rpm: Q8 每供应商 RPM 上限(60/rpm = 同供应商两次请求最小间隔)。
        registry: ``specs.ProviderResolver``(duck-typed ``resolve``;缺省
            惰性自举真实 specs 注册表)。
        transport / pacer: 注入口(测试零网络/零等待;缺省
            :class:`HttpxTransport` + 按 rpm 折算间隔的 :class:`Pacer`)。

    Returns:
        {"keystore_path", "records"(库内指纹总数), "results"(逐条:
        fingerprint + masked_apikey + :func:`result_to_dict` 掩码产物),
        "counts"(checked + 按 validation_state 计数)}—— 全文密钥永不
        进返回值(Q9)。

    Raises:
        ValueError: 库记录形状坏(调用方结构化降级);KeystoreError
            (ValueError 子类):库文件不可读/不可写/指纹丢失。
    """
    store = _require_sibling("keystore").Keystore(keystore_path)
    records = store.load()
    entries = list(records.items())
    take = len(entries) if limit is None else max(0, int(limit))
    resolved_registry = registry
    if resolved_registry is None:
        specs_module = _require_sibling("specs")
        resolved_registry = specs_module.ProviderResolver(specs_module.load_specs())
    shared_transport = transport or HttpxTransport()
    shared_pacer = pacer or Pacer(min_interval_seconds=60.0 / max(1, rpm))
    results: list[dict[str, Any]] = []
    state_counts: dict[str, int] = {}
    for fingerprint, record in entries[:take]:
        if not isinstance(record, Mapping):
            raise ValueError(f"records[{fingerprint}] 必须是映射(猎手入库记录),当前为 {type(record).__name__}")
        apikey = record.get("apikey")
        if not isinstance(apikey, str) or not apikey:
            raise ValueError(f"records[{fingerprint}].apikey 必须是非空字符串")
        outcome = check_credential(
            apikey=apikey,
            apiurl="",
            registry=resolved_registry,
            transport=shared_transport,
            pacer=shared_pacer,
            probe_balance=balance,
        )
        store.update_check(fingerprint, outcome.validation_state)
        results.append(
            {
                "fingerprint": fingerprint,
                "masked_apikey": mask_apikey(apikey),
                **result_to_dict(outcome),
            }
        )
        state_counts[outcome.validation_state] = state_counts.get(outcome.validation_state, 0) + 1
    return {
        "keystore_path": str(store.path),
        "records": len(records),
        "results": results,
        "counts": {"checked": len(results), **state_counts},
    }
