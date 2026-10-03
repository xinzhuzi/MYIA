"""Category YAML schema: 12-section pydantic models + load-time validation.

This module is the foundation for "AI writes the YAML": every section has
explicit semantics and defaults, unknown fields fail fast at load time, and
credentials can only appear as ``env:VAR`` / ``keychain:NAME`` references.

Two distinct phases (see PRD v01-yaml-schema):

- **Load / validate** (:func:`load_category`, :func:`load_category_file`):
  YAML -> :class:`CategoryConfig`. Credential *syntax* is checked here
  (plaintext in credential-like headers/targets is refused with
  :class:`LoadError`). ``keychain:`` references are valid syntax at this
  phase -- the official ``plugins/stocks.yaml`` example uses one.
- **Resolve** (:func:`resolve_credential`): reference -> concrete value at
  fetch/push time. ``env:VAR`` reads the environment; ``keychain:NAME`` reads
  the system keychain through :mod:`shishi.secrets` (macOS Keychain / Windows
  DPAPI via the ``keyring`` library; 无钥匙链环境结构化报错并引导改用 ``env:``).

Structured errors: :class:`LoadError` carries a list of
:class:`LoadErrorDetail` (JSONPath-style field path + machine error type +
human message in Chinese) so ``myia doctor`` (v0.2) and repairing agents can
consume them programmatically.

Scenario plugin sidecar (v0.3 plugin market): a category may also carry an
optional top-level ``plugin:`` section (v1.7 双模式声明:local docker compose /
remote endpoint + keychain token), validated against
:class:`CategoryPluginConfig` at :func:`load_category` and attached to
:attr:`CategoryConfig.plugin`. It is deliberately *not* one of the twelve
sections (the documented 12-section contract is locked field-for-field by
tests/test_skill_doc.py); the mode models are shared with the market manifest
in :mod:`shishi.plugins`. A broken/uninstalled plugin never blocks the core
pipeline — it degrades to structured findings (security-baseline 铁律).

Trend baseline sidecar (v0.4, PRD 10-01-v04-trend-baseline): an optional
top-level ``baseline:`` section (:class:`BaselineConfig`) declares which
numeric item fields (:attr:`fields`) get a per-item history snapshot and
which compare windows (:attr:`windows`, ``day``/``week``) power the
「vs 昨日 / vs 上周」 template functions, plus an optional MSRP 对照表
(:attr:`msrp`). Like ``plugin:`` it is validated at :func:`load_category`
(structured ``$.baseline`` errors, ``baseline: null`` treated as absent) and
attached to :attr:`CategoryConfig.baseline` without growing the locked
twelve-section contract.

Event aggregation sidecar (v0.4, PRD 10-01-v04-event-aggregation): an
optional top-level ``aggregate:`` section (:class:`AggregateConfig`) turns on
multi-source same-event merging — two-level dedup (local title-similarity
coarse screen, zero token, then LLM confirmation inside the enrich
batch/cache/budget rails) folds items describing the same event into one
merged card with a 「另见 N 源」 list. Like ``baseline:`` it is a sidecar
section (structured ``$.aggregate`` errors, ``aggregate: null`` absent),
attached to :attr:`CategoryConfig.aggregate`; the LLM endpoint settings are
shared with the ``enrich:`` section (the aggregator reuses enrich's
model/batch/budget/cache rails).

Image processing sidecar (10-03-vision-pipeline): an optional top-level
``images:`` section (:class:`ImagesConfig`) turns on the fetch-tail image
ring — item image URLs (extract ``img@src`` / L3 markdown same-domain
collection) are downloaded (SSRF-guarded, magic-byte-checked, size-capped),
OCRed locally and optionally captioned by a vision LLM; the products land on
``metadata.image_ocr`` / ``image_caption`` / ``image_status`` for
analyze/enrich/push. Like ``aggregate:`` it is a sidecar section
(structured ``$.images`` errors, ``images: null`` absent) attached to
:attr:`CategoryConfig.images`; disabled or absent means the ring never
runs (behavior identical to before the section existed).

Raises:
    LoadError: YAML is unreadable/unparsable or fails any schema rule.
    CredentialResolveError: a credential reference cannot be resolved.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field, replace as dataclass_replace
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Mapping, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from apscheduler.triggers.cron import CronTrigger
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
    ValidationInfo,
)

from shishi import secrets as secrets_store
from shishi.secrets import KeychainBackend

__all__ = [
    "CATEGORY_ID_RE",
    "CHANNEL_PLATFORMS",
    "ENGINES",
    "PUSH_CHANNELS",
    "ROUTE_MODES",
    "ENRICH_SCORES",
    "PAGINATION_MODES",
    "EXTRACT_TYPES",
    "BACKOFF_POLICIES",
    "VACUUM_CADENCES",
    "BASELINE_WINDOWS",
    "CREDENTIAL_KEY_SUFFIXES",
    "REQUIRES_TOKENS",
    "CategoryConfig",
    "SourceConfig",
    "PaginationConfig",
    "ExtractConfig",
    "RateLimitConfig",
    "WatchlistConfig",
    "ClassifyConfig",
    "ClassifyRuleConfig",
    "DedupConfig",
    "EnrichConfig",
    "PushConfig",
    "RouteRuleConfig",
    "StorageConfig",
    "BaselineConfig",
    "AggregateConfig",
    "ImagesConfig",
    "CategoryPluginConfig",
    "PluginLocalModeConfig",
    "PluginRemoteModeConfig",
    "PluginModesConfig",
    "SchemaValueError",
    "LoadErrorDetail",
    "LoadError",
    "SecretRef",
    "CredentialResolveError",
    "parse_secret_value",
    "resolve_credential",
    "is_credential_key",
    "normalize_plugin_requires",
    "load_category",
    "load_category_file",
    "read_yaml_document",
]

# ---------------------------------------------------------------------------
# Enumerations (fail-fast vocabulary for every section)
# ---------------------------------------------------------------------------

ENGINES = (
    "auto",
    "direct_api",
    "static_html",
    "crawl4ai",
    "firecrawl",
    "scrapling",
    "stealth_browser",
    "llm_browser",
    # 链外源引擎(10-03-aipocket-fusion):显式选择才生效,不参与 auto 降级链。
    "credhunter",
)
PAGINATION_MODES = ("template", "selector", "scroll")
EXTRACT_TYPES = ("list", "item", "json_path", "rss")
BACKOFF_POLICIES = ("exponential", "linear", "none")
# W3 长尾 22 家(10-03-messaging-w3-longtail):组一 Slack 系 + 组二 Matrix 系
# + 组三长尾壳;与 shishi.push.CHANNELS 的 _W3_LONGTAIL_CHANNELS 一一对应。
_W3_LONGTAIL = (
    "slack",
    "discord",
    "whatsapp_cloud",
    "line",
    "qqbot",
    "google_chat",
    "teams",
    "msgraph_webhook",
    "matrix",
    "mattermost",
    "irc",
    "simplex",
    "signal",
    "bluebubbles",
    "email",
    "sms",
    "homeassistant",
    "a2a",
    "yuanbao",
    "buzz",
    "photon",
    "raft",
)
PUSH_CHANNELS = (
    "feishu_card",
    "telegram",
    "ntfy",
    "dingtalk",
    "wecom",
    "weixin",
    "webhook",
    "stdout",
    *_W3_LONGTAIL,
)
ROUTE_MODES = ("immediate", "digest", "archive")
ENRICH_SCORES = ("value", "relevance", "credibility")
VACUUM_CADENCES = ("daily", "weekly", "monthly", "never")
#: 趋势基线对比窗口(v0.4):day = vs 昨日,week = vs 上周。封闭词表 —— AI 拼
#: 错即拒载并列出合法值,而不是静默跳过一个从不生效的窗口。
BASELINE_WINDOWS = ("day", "week")
#: 场景插件 ``requires`` 的封闭词表(宿主能力)。封闭 = AI 拼错即拒载并列出
#: 合法值,而不是静默带过一个装不出来的依赖。
REQUIRES_TOKENS = ("docker",)

EngineName = Literal[
    "auto", "direct_api", "static_html", "crawl4ai", "firecrawl", "scrapling", "stealth_browser", "llm_browser",
    "credhunter",
]
PaginationMode = Literal["template", "selector", "scroll"]
ExtractType = Literal["list", "item", "json_path", "rss"]
BackoffPolicy = Literal["exponential", "linear", "none"]
PushChannel = Literal[
    "feishu_card", "telegram", "ntfy", "dingtalk", "wecom", "weixin", "webhook", "stdout",
    "slack", "discord", "whatsapp_cloud", "line", "qqbot", "google_chat", "teams",
    "msgraph_webhook", "matrix", "mattermost", "irc", "simplex", "signal",
    "bluebubbles", "email", "sms", "homeassistant", "a2a", "yuanbao", "buzz",
    "photon", "raft",
]
RouteMode = Literal["immediate", "digest", "archive"]
ScoreName = Literal["value", "relevance", "credibility"]
VacuumCadence = Literal["daily", "weekly", "monthly", "never"]
BaselineWindow = Literal["day", "week"]

ShortStr = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ExprStr = Annotated[str, StringConstraints(min_length=1, max_length=256)]

# Header keys whose values must be credential references (case/space
# insensitive suffix match; "X-Api-Key" -> "xapikey" ends with "apikey").
CREDENTIAL_KEY_SUFFIXES = (
    "cookie",
    "authorization",
    "token",
    "secret",
    "password",
    "passwd",
    "apikey",
    "session",
)

DEFAULT_QPS = 0.5
DEFAULT_RETRY = 3
DEFAULT_ENRICH_MODEL = "glm-4-flash"
DEFAULT_BATCH_SIZE = 20
DEFAULT_BUDGET_PER_RUN = 50_000
DEFAULT_RETENTION = "90d"
DEFAULT_DEDUP_KEY = "{url}"
#: 事件聚合缺省窗口(小时):两个候选条目都带可解析发布时间且相差超过该窗口
#: 即视为不同事件(PRD 10-01-v04-event-aggregation: 同时间窗内判重)。
DEFAULT_AGGREGATE_WINDOW_HOURS = 24.0
#: 事件聚合标题相似度粗筛缺省阈值(字符 shingle Jaccard):达到阈值才进 LLM
#: 精筛候选(粗筛只管召回候选,精确率由 LLM 判重保证)。
DEFAULT_AGGREGATE_SIMILARITY = 0.6
#: 图片处理环缺省每条上限(张):超出静默截断(首张优先,extract 顺序)。
DEFAULT_IMAGES_MAX_IMAGES = 3
#: 图片处理环缺省每 run 总上限(张):VL 时长的硬闸,耗尽后条目标
#: ``skipped:run_limit``(降级矩阵见 myia/vision/collect.py)。
DEFAULT_IMAGES_MAX_PER_RUN = 30
#: 图片处理环缺省最小字节(<10KB 视为图标/追踪像素跳过)。
DEFAULT_IMAGES_MIN_BYTES = 10_240
#: 详情页追抓缺省每 run 条目上限(10-03-detail-images:fetch 尾部对本轮
#: 无图条目追抓详情页,串行 + 每请求 ≥1s + 10s/页;N 上限兜底 SPA 白抓)。
DEFAULT_IMAGES_DETAIL_MAX_ITEMS = 10

#: dedup.key placeholders resolvable for every item without appearing in
#: extract.fields: the Item top-level pipeline fields plus the slot context
#: the pipeline injects at the dedup stage (``date`` = local YYYY-MM-DD,
#: ``slot`` = am/pm push slot). ``title`` stays banned (永不标题指纹).
RESERVED_DEDUP_FIELDS = frozenset({"url", "source", "category", "scores", "date", "slot"})

#: 品类 id 的标识符规则(小写字母/数字/``-``/``_``,字母或数字开头,1-64 字符)。
#: 公开单一事实源:桌面 sidecar 的 YAML 编辑器把它同时用作**新建文件名 stem**
#: 规则(路径围栏的一环),前端预检 import 同一常量语义 —— 不复制正则,防两处
#: 漂移(task 10-03-yaml-editor);manifest 能力名(provides)同源复用。
CATEGORY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
#: 场景插件 id(市场 manifest 与品类 plugin 节共用同一 id 空间;惯例 myia-<名称>)。
_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)(ms|s|m|h)?\s*$")
_DURATION_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
_RETENTION_RE = re.compile(r"^\s*(\d+)([dw])\s*$")
_PROXY_RE = re.compile(r"^direct|pool:[A-Za-z0-9_-]+|residential:[A-Za-z0-9_-]+$")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# ``env:VAR`` / ``keychain:NAME``, optionally preceded by an RFC 7235 auth
# scheme word ("Bearer env:AIPOCKET_TOKEN" in official credentials.yaml).
# keychain names may be namespaced with ``/`` (myia/<scope>/<name>, the
# canonical form enforced by shishi.secrets at resolve time).
_SECRET_REF_RE = re.compile(
    r"^(?:(?P<scheme>[A-Za-z][A-Za-z0-9+\-.]*)[ \t]+)?"
    r"(?:env:(?P<env_var>[A-Za-z_][A-Za-z0-9_]*)|keychain:(?P<kc>[A-Za-z0-9_./\-]+))[ \t]*$"
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SchemaValueError(ValueError):
    """Validation error raised inside pydantic validators.

    Carries a machine-readable ``code`` (becomes ``LoadErrorDetail.error_type``)
    and an optional ``path_suffix`` appended to the field path when the check
    runs in a model-level validator (whose loc stops at the model itself).
    """

    def __init__(self, code: str, message: str, *, path_suffix: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.path_suffix = path_suffix


@dataclass(frozen=True)
class LoadErrorDetail:
    """One structured load failure: field path + error type + reason."""

    path: str
    error_type: str
    message: str


class LoadError(Exception):
    """Category YAML refused at load/validate time (CLI exit code 1).

    Attributes:
        errors: all detected failures, structured for ``myia doctor`` (v0.2)
            and agent self-repair; see :meth:`to_dict`.
        source: file path the config was loaded from, when known.
    """

    def __init__(
        self,
        errors: Sequence[LoadErrorDetail],
        *,
        source: str | None = None,
    ) -> None:
        self.errors: list[LoadErrorDetail] = list(errors)
        self.source = source
        super().__init__(self._format())

    def _format(self) -> str:
        head = f"品类 YAML 校验失败,共 {len(self.errors)} 处"
        if self.source:
            head += f"({self.source})"
        lines = [head]
        for index, detail in enumerate(self.errors, start=1):
            lines.append(f"  {index}. [{detail.error_type}] {detail.path} — {detail.message}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form for ``myia doctor`` (JSON) and agents."""
        return {
            "source": self.source,
            "errors": [
                {"path": d.path, "error_type": d.error_type, "message": d.message}
                for d in self.errors
            ],
        }


@dataclass(frozen=True)
class SecretRef:
    """A parsed credential reference (``env:VAR`` or ``keychain:NAME``).

    ``scheme`` keeps an optional RFC 7235 auth word so "Bearer env:TOKEN"
    round-trips: :func:`resolve_credential` re-emits "Bearer <value>".
    """

    kind: Literal["env", "keychain"]
    name: str
    scheme: str | None = field(default=None)

    @property
    def prefix(self) -> str:
        return f"{self.scheme} " if self.scheme else ""


class CredentialResolveError(RuntimeError):
    """A credential reference cannot be resolved to a concrete value.

    Attributes:
        code: ``env_var_missing`` | ``invalid_credential_ref`` |
            ``invalid_secret_name`` | ``secret_not_found`` |
            ``keychain_backend_unavailable`` | ``keychain_operation_failed``
            (the last four mirror :class:`shishi.secrets.SecretError` codes).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Credential reference handling
# ---------------------------------------------------------------------------


def is_credential_key(key: str) -> bool:
    """Whether a header key looks like it carries a credential.

    Match is substring over the normalized key (lowercase, non-alphanumerics
    stripped) so ``X-Api-Key`` and ``Proxy-Authorization`` are both caught.
    Over-catching is the safe direction: a false positive merely forces the
    value to be referenced instead of plaintext.
    """
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(suffix in normalized for suffix in CREDENTIAL_KEY_SUFFIXES)


def parse_secret_value(
    value: Any,
    *,
    label: str,
    allow_scheme: bool = True,
    path_suffix: str = "",
) -> SecretRef:
    """Parse ``env:VAR`` / ``keychain:NAME`` (optionally ``<scheme> env:VAR``).

    Args:
        value: raw YAML value; must be a string containing a valid reference.
        label: human context for the error message, e.g. ``疑似凭据键 'Cookie'``.
        allow_scheme: whether a leading auth-scheme word (``Bearer``) is
            allowed before the reference.
        path_suffix: appended to ``SchemaValueError.path_suffix`` so the final
            ``LoadErrorDetail`` path can point at the exact header key.

    Raises:
        SchemaValueError: value is not a valid credential reference.
    """
    match = _SECRET_REF_RE.match(value) if isinstance(value, str) else None
    if match is None:
        raise SchemaValueError(
            "credential_plaintext",
            f"{label} 只允许 env:/keychain: 凭据引用(禁明文),当前值不含有效凭据引用",
            path_suffix=path_suffix,
        )
    scheme = match.group("scheme")
    if scheme and not allow_scheme:
        raise SchemaValueError(
            "invalid_credential_ref",
            f"{label} 必须是纯 env:/keychain: 引用,不允许携带 {scheme} 前缀",
            path_suffix=path_suffix,
        )
    if match.group("env_var") is not None:
        return SecretRef(kind="env", name=match.group("env_var"), scheme=scheme)
    return SecretRef(kind="keychain", name=match.group("kc"), scheme=scheme)


def _scan_secret_refs(node: Any, path: str = "") -> None:
    """Recursively refuse plaintext values under credential-like keys.

    Used for ``sources[].post_body`` and every source-level extension
    parameter (``engine_options.*`` etc.) — the security baseline applies to
    the whole YAML document, not just headers: 配置文件出现明文凭据 =
    启动即报错拒跑. Lists (key pools like ``api_keys``) and nested mappings
    are walked; ``path`` stays relative to the scan root so each call site
    can anchor it (field validator vs model validator).

    Raises:
        SchemaValueError: first plaintext/invalid value found (code
            ``credential_plaintext``), with a field-path suffix pointing at
            the offending key.
    """
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and is_credential_key(key):
                _require_secret_refs(value, key, child)
            else:
                _scan_secret_refs(value, child)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            _scan_secret_refs(value, f"{path}[{index}]")


def _require_secret_refs(value: Any, key: str, path: str) -> None:
    """Every value under a credential-like key must be a ``env:``/``keychain:`` ref."""
    if isinstance(value, Mapping):
        for sub_key, sub_value in value.items():
            _require_secret_refs(sub_value, str(sub_key), f"{path}.{sub_key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _require_secret_refs(item, key, f"{path}[{index}]")
        return
    parse_secret_value(value, label=f"疑似凭据键 {key}", allow_scheme=True, path_suffix=path)


def resolve_credential(
    value: str,
    *,
    backend: KeychainBackend | None = None,
) -> str:
    """Resolve a credential reference to its concrete value at run time.

    ``env:VAR`` reads the process environment; ``keychain:NAME`` reads the
    system keychain via :mod:`shishi.secrets` — the name must be in the
    canonical ``myia/<scope>/<name>`` namespace, and hosts without a keychain
    get a structured error guiding to ``env:`` (回退策略: env: 为主).
    Scheme prefixes round-trip: ``Bearer env:T`` -> ``Bearer <value>``.

    Args:
        value: the credential reference as written in YAML.
        backend: injected keychain backend for ``keychain:`` references
            (tests inject :class:`shishi.secrets.InMemoryKeychainBackend`);
            ``None`` = lazily discovered system keyring.

    Raises:
        CredentialResolveError: env var missing, the keychain name is not in
            the canonical namespace, the secret is not set, no keychain
            backend exists, a keychain operation failed, or the value is not
            a credential reference at all.
    """
    try:
        ref = parse_secret_value(value, label="凭据引用")
    except SchemaValueError as exc:
        raise CredentialResolveError("invalid_credential_ref", str(exc)) from exc
    if ref.kind == "keychain":
        try:
            secret = secrets_store.resolve_keychain_ref(ref.name, backend=backend)
        except secrets_store.SecretError as exc:
            raise CredentialResolveError(exc.code, str(exc)) from exc
        return ref.prefix + secret
    try:
        resolved = os.environ[ref.name]
    except KeyError as exc:
        raise CredentialResolveError(
            "env_var_missing",
            f"环境变量 {ref.name} 未设置,无法解析凭据引用 {value!r}",
        ) from exc
    return ref.prefix + resolved


# ---------------------------------------------------------------------------
# Shared validators
# ---------------------------------------------------------------------------


def _parse_duration(value: Any, *, label: str) -> float:
    """Normalize a duration to float seconds (``"2s"`` -> 2.0)."""
    if isinstance(value, bool):
        raise SchemaValueError("invalid_duration", f"{label} 应为数字秒数或时长字符串(如 2s / 500ms / 1m),当前为 {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _DURATION_RE.match(value)
        if match:
            return float(match.group(1)) * _DURATION_UNITS[match.group(2) or "s"]
    raise SchemaValueError("invalid_duration", f"{label} 应为数字秒数或时长字符串(如 2s / 500ms / 1m),当前为 {value!r}")


class _StrictModel(BaseModel):
    """Base for every section: unknown fields are refused (fail-fast)."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Section models (order mirrors the YAML documents)
# ---------------------------------------------------------------------------


class PaginationConfig(_StrictModel):
    """How a list source turns one fetch into many pages.

    ``template`` iterates ``{page}`` in the URL; ``selector`` follows a
    next-page link; ``scroll`` is engine-side infinite scroll (only honored
    by L4+ engines, enforced by the engine layer, not here).
    """

    mode: PaginationMode = "template"
    max_pages: int = Field(default=1, ge=1, le=10000)
    selector: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _check_selector(self) -> "PaginationConfig":
        if self.mode == "selector" and not self.selector:
            raise SchemaValueError(
                "missing_pagination_selector",
                "pagination.mode 为 selector 时必须提供下一页链接选择器",
                path_suffix="selector",
            )
        return self


#: ``extract.type: rss`` 的 feedparser entry 属性白名单(10-03-news-rss):
#: fields 的**值**=entry 属性名(键=归一字段名,如 ``url: link``)。封闭白名单
#: —— AI 拼错属性名(如 ``pubdate``)装载即拒,而不是运行期整源静默零产出
#: (同 url_template 占位符交叉校验的设计动机);条目缺某属性 → 该字段逐条
#: 省略(同 json_path 语义)。summary 是 CDATA HTML,白名单保留但模板慎用。
RSS_ENTRY_FIELDS = frozenset({"title", "link", "published", "updated", "summary", "author"})


class ExtractConfig(_StrictModel):
    """Field extraction for one source.

    ``list`` scrapes repeated items (``item`` selector + per-field
    selectors); ``item`` scrapes a single page; ``json_path`` reads JSON
    APIs. Without ``extract`` at all, L3+ engines auto-structure (schema
    keeps it ``None``).

    ``rss`` (task 10-03-news-rss) reads RSS feeds: ``fields`` values are
    feedparser entry attribute names from the closed whitelist
    :data:`RSS_ENTRY_FIELDS` (键=归一字段名,如 ``url: link``)。The entry
    URL is the mapped ``link`` — the ``url``-required rule applies the same
    way (去重键根基); a typo'd attribute name is refused at load time, an
    entry missing one attribute simply omits that field. Engine-side rss is
    a ``static_html`` text path only (``direct_api`` stays JSON-only).

    ``url_template`` (D1, task 10-03-games): when the payload carries no
    clickable page URL (only a slug / numeric id — Epic freeGamesPromotions,
    Steam featuredcategories), the per-item ``url`` is rendered from
    ``{field}`` placeholders at the extraction outlet
    (:func:`shishi.engines.fetch_base.extract_json` / ``extract_html``)
    instead of being read from ``fields``. This closes the gap stocks.yaml
    documented as「json_path cannot express "item URL = f(field)"」: the
    ``url`` field remains the stable-identity fallback, ``url_template``
    renders a real link. Either/or with the ``url`` field (both declared →
    the ``url`` field wins, the template is silently unused); rejected on
    ``item`` extracts (single-page source: the item URL *is* the request
    URL). Placeholders must name keys of ``fields`` — load-time cross-check
    with the same rationale as the dedup-key check: a typo'd placeholder
    would render every item's url empty and the pipeline would silently
    reject the whole source as ``invalid_item``. A field missing *values* on
    some elements (Epic ``urlSlug: null``) is the normal render-time path:
    that item's url renders empty and is dropped with a visible failure
    record, the rest of the source is unaffected.
    """

    type: ExtractType
    item: str | None = Field(default=None, min_length=1)
    #: 条目 URL 渲染模板,``{field}`` 纯占位(与 dedup.key 同款迷你模板语义,
    #: 装载期至少一个占位符);仅 ``list`` / ``json_path`` 可配。
    url_template: str | None = Field(default=None, min_length=1)
    fields: dict[str, str] = Field(min_length=1)

    @field_validator("url_template")
    @classmethod
    def _check_url_template_placeholders(cls, value: str | None) -> str | None:
        """至少一个 ``{field}`` 占位符(纯占位语法,_PLACEHOLDER_RE 同款)。"""
        if value is not None and not _PLACEHOLDER_RE.search(value):
            raise SchemaValueError(
                "invalid_url_template",
                f"extract.url_template 至少要包含一个占位符(如 {{url_slug}}),当前为 {value!r}",
            )
        return value

    @model_validator(mode="after")
    def _check_shape(self) -> "ExtractConfig":
        if self.type != "list" and self.item is not None:
            raise SchemaValueError(
                "unexpected_extract_item",
                "extract.item 仅在 type 为 list 时有效",
                path_suffix="item",
            )
        if self.type == "list" and not self.item:
            raise SchemaValueError(
                "missing_extract_item",
                "extract.type 为 list 时必须提供 item 选择器",
                path_suffix="item",
            )
        if self.type in ("item", "rss") and self.url_template is not None:
            raise SchemaValueError(
                "unexpected_url_template",
                "extract.url_template 仅在 type 为 list/json_path 时有效(单页源条目 url 即请求 URL;"
                "rss 条目 url 由 fields.url←entry.link 映射,无需模板)",
                path_suffix="url_template",
            )
        if self.url_template is not None:
            # 占位符-字段交叉校验(同 _check_dedup_key_fields 的设计动机:
            # AI 生成质量问题要在加载期可检出)。拼错的占位符装载通过的话,
            # 运行期每个条目都渲染成空 url、整源被管线逐条拒成 invalid_item
            # ——静默整源全灭。逐条目的缺「值」(如 Epic urlSlug=null 元素)不
            # 在此列:那是渲染层按缺字段处理的正常路径(条目被拒、其余照常)。
            missing = set(_PLACEHOLDER_RE.findall(self.url_template)) - set(self.fields)
            if missing:
                raise SchemaValueError(
                    "invalid_url_template",
                    f"extract.url_template 占位符 {sorted(missing)} 不在 extract.fields"
                    f"({sorted(self.fields)})中:运行期将恒渲染为空 url、整源条目被拒",
                    path_suffix="url_template",
                )
        if self.type == "rss":
            # 白名单校验(10-03-news-rss):fields 值=feedparser entry 属性名。
            # 拼错装载通过的话,运行期 getattr 恒 None → 该字段整源静默缺失
            # (拼的是 url 时=整源 invalid_item)——同 url_template 交叉校验
            # 的设计动机:AI 生成质量问题要在加载期可检出。第一个违例即拒,
            # path 指到 fields.<键>。
            for name, attr in self.fields.items():
                if attr not in RSS_ENTRY_FIELDS:
                    raise SchemaValueError(
                        "invalid_rss_field",
                        f"extract.type 为 rss 时 fields.{name} 的值必须是 feedparser entry 属性"
                        f"({sorted(RSS_ENTRY_FIELDS)})之一,当前为 {attr!r}",
                        path_suffix=f"fields.{name}",
                    )
        if self.type in ("list", "json_path", "rss") and "url" not in self.fields and not self.url_template:
            raise SchemaValueError(
                "missing_url_field",
                "extract.fields 必须包含 url 字段,或提供 extract.url_template 渲染条目 URL"
                "(二者必居其一;去重键依赖 URL,禁止标题指纹)",
                path_suffix="fields",
            )
        return self


class RateLimitConfig(_StrictModel):
    """Politeness defaults; throttling lives once in the engine layer.

    ``jitter`` accepts a number (seconds) or a duration string and is
    normalized to float seconds.
    """

    qps: float = Field(default=DEFAULT_QPS, gt=0, le=1000)
    jitter: float = Field(default=0.0, ge=0)
    backoff: BackoffPolicy = "exponential"
    respect_robots: bool = True

    @field_validator("jitter", mode="before")
    @classmethod
    def _normalize_jitter(cls, value: Any) -> float:
        return _parse_duration(value, label="rate_limit.jitter")


class SourceConfig(BaseModel):
    """One fetch source.

    Unlike every other section this model allows unknown keys: source-level
    extension parameters (e.g. ``symbols: [...]``) are passed through to the
    engine via :attr:`extra_params`. Known-field typos are still caught; the
    open parameter namespace is deliberate (PRD: 源级扩展参数).
    """

    model_config = ConfigDict(extra="allow")

    name: ShortStr
    engine: EngineName = "auto"
    url: str
    method: Literal["GET", "POST"] = "GET"
    post_body: dict[str, Any] | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    pagination: PaginationConfig | None = None
    extract: ExtractConfig | None = None
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    proxy: str = "direct"
    retry: int = Field(default=DEFAULT_RETRY, ge=0, le=10)

    @property
    def extra_params(self) -> dict[str, Any]:
        """Unknown source-level keys (e.g. ``symbols``), for engine consumption."""
        return dict(self.model_extra or {})

    @field_validator("url")
    @classmethod
    def _check_url_scheme(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise SchemaValueError("invalid_url", f"source url 必须是 http(s) 地址,当前为 {value!r}")
        return value

    @field_validator("headers")
    @classmethod
    def _check_header_credentials(cls, value: dict[str, str]) -> dict[str, str]:
        for key, header_value in value.items():
            if is_credential_key(key):
                parse_secret_value(
                    header_value,
                    label=f"疑似凭据键 {key}",
                    allow_scheme=True,
                    path_suffix=key,
                )
        return value

    @field_validator("post_body")
    @classmethod
    def _check_post_body_credentials(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        """POST 表单/JSON 是 token 最常见的落点,明文凭据同 headers 一样启动即拒。"""
        if value is not None:
            _scan_secret_refs(value)
        return value

    @field_validator("proxy", mode="before")
    @classmethod
    def _check_proxy(cls, value: Any) -> str:
        if isinstance(value, str) and _PROXY_RE.match(value):
            return value
        raise SchemaValueError(
            "invalid_proxy",
            f"proxy 取值 {value!r} 无效,应为 direct / pool:<名称> / residential:<区域>",
        )

    @model_validator(mode="after")
    def _check_method_and_pagination(self) -> "SourceConfig":
        if self.method == "POST" and self.post_body is None:
            raise SchemaValueError(
                "missing_post_body",
                "method 为 POST 时必须提供 post_body",
                path_suffix="post_body",
            )
        if self.method == "GET" and self.post_body is not None:
            raise SchemaValueError(
                "post_body_on_get",
                "method 为 GET 时不应提供 post_body",
                path_suffix="post_body",
            )
        if (
            self.pagination is not None
            and self.pagination.mode == "template"
            and "{page}" not in self.url
        ):
            raise SchemaValueError(
                "missing_page_placeholder",
                "pagination.mode 为 template 时 url 必须包含 {page} 模板占位符",
                path_suffix="url",
            )
        return self

    @model_validator(mode="after")
    def _check_extra_params(self) -> "SourceConfig":
        """Guard the open extension namespace: credentials + known-key typos.

        ``extra="allow"`` passes engine extension params (``symbols``,
        ``engine_options``) through untouched, so this validator keeps the two
        invariants the open namespace would otherwise silently drop:
        credential-like keys may only carry ``env:``/``keychain:`` references
        (security baseline, whole-document rule), and an unknown key that is
        a near-match of a known field is refused as a probable typo instead
        of being ignored by the engines (rate_limt → rate_limit).
        """
        extras = self.model_extra or {}
        for key, value in extras.items():
            # 裸标量扩展参数(如 access_secret: topsecret)也要过凭据键检查:
            # _scan_secret_refs 只走容器,标量值在入口处按键名直接要求引用。
            if isinstance(key, str) and is_credential_key(key):
                _require_secret_refs(value, key, str(key))
            else:
                _scan_secret_refs(value, str(key))
        for key in extras:
            close = difflib.get_close_matches(
                str(key), list(type(self).model_fields), n=1, cutoff=0.8
            )
            if close:
                raise SchemaValueError(
                    "unknown_field",
                    f"未知字段 {key!r} 疑似已知字段 {close[0]!r} 的拼写错误"
                    f"(源级扩展参数保留,但已知字段拼错会被引擎静默忽略)",
                    path_suffix=str(key),
                )
        return self


class WatchlistConfig(_StrictModel):
    """Relevance profile: ``keywords`` boost, ``mute`` demotes/archives."""

    keywords: list[ShortStr] = Field(default_factory=list)
    mute: list[ShortStr] = Field(default_factory=list)


class ClassifyRuleConfig(_StrictModel):
    """One custom rule: ``name`` / ``when`` expression / ``tag``.

    ``when`` is evaluated later against a whitelisted AST (never raw
    ``eval``) by the classify layer; the schema only enforces presence.
    """

    name: ShortStr
    when: ExprStr
    tag: str = Field(default="", max_length=64)


class ClassifyConfig(_StrictModel):
    """First funnel. ``builtin`` keeps the seven-category keyword scan on
    (default) in addition to any custom ``rules``."""

    builtin: bool = True
    rules: list[ClassifyRuleConfig] = Field(default_factory=list)


class DedupConfig(_StrictModel):
    """Dedup key template. Composite keys or ``{url}`` only — title
    fingerprints are forbidden (schema-level guard)."""

    key: str = DEFAULT_DEDUP_KEY

    @field_validator("key")
    @classmethod
    def _check_key(cls, value: str) -> str:
        if "{title}" in value.lower():
            raise SchemaValueError(
                "title_fingerprint_forbidden",
                "dedup.key 禁止使用 {title}(永不标题指纹,只用 URL 或组合键)",
            )
        if not _PLACEHOLDER_RE.search(value):
            raise SchemaValueError(
                "invalid_dedup_key",
                f"dedup.key 至少要包含一个占位符(如 {{url}} / {{symbol}}-{{date}}),当前为 {value!r}",
            )
        return value


class EnrichConfig(_StrictModel):
    """Second funnel: LLM precision scoring with batch/cache/budget rails.

    ``base_url`` / ``api_key`` carry the endpoint as pure ``env:``/``keychain:``
    references (PRD 10-01-v02-enrich-llm: enrich 节承载 base_url;grill Q6:
    无内置端点、无默认 key)。明文即拒载;留空时由 EnrichSettings 的
    missing_base_url / missing_api_key 结构化报错兜底(构造注入路径仍在)。
    """

    enabled: bool = False
    model: str = Field(default=DEFAULT_ENRICH_MODEL, min_length=1)
    scores: list[ScoreName] = Field(default_factory=lambda: list(ENRICH_SCORES))
    batch: int = Field(default=DEFAULT_BATCH_SIZE, ge=1, le=1000)
    cache: bool = True
    budget_per_run: int = Field(default=DEFAULT_BUDGET_PER_RUN, ge=1)
    base_url: str | None = None
    api_key: str | None = None

    @field_validator("base_url", "api_key")
    @classmethod
    def _check_endpoint_refs(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parse_secret_value(value, label="enrich", allow_scheme=False)
        return value

    @model_validator(mode="after")
    def _check_scores(self) -> "EnrichConfig":
        if self.enabled and not self.scores:
            raise SchemaValueError(
                "missing_scores",
                "enrich.enabled 为 true 时至少要声明一个评分维度",
                path_suffix="scores",
            )
        if len(set(self.scores)) != len(self.scores):
            raise SchemaValueError(
                "duplicate_scores",
                "enrich.scores 存在重复维度",
                path_suffix="scores",
            )
        return self


#: 通道名 → 平台前缀的内置字面映射(10-03-messaging-core design D4:targets
#: 同平台约束;schema 不反依赖 push 层,新平台接入目录寻址时同步登记;
#: ntfy/dingtalk/wecom 三行随 10-03-messaging-w2-platforms 登记;weixin 随
#: 10-03-messaging-weixin-bridge 登记;W3 长尾 22 家随
#: 10-03-messaging-w3-longtail 终局接线登记——平台名 = 通道名,与各适配器
#: parse_direct_ref 的 platform= 字面一致)。
CHANNEL_PLATFORMS: dict[str, str] = {
    "feishu_card": "feishu",
    "telegram": "telegram",
    "ntfy": "ntfy",
    "dingtalk": "dingtalk",
    "wecom": "wecom",
    "weixin": "weixin",
    **{channel: channel for channel in _W3_LONGTAIL},
}

#: targets 元素形态 ``platform:名称或id``(与 shishi.push.targets.SPEC_RE 同源;
#: schema 层本地定值,避免反向 import)。
_TARGET_SPEC_RE = re.compile(r"^([a-z][a-z0-9_]*):(.+)$")


def _validate_target_specs(value: list[str], label: str) -> list[str]:
    """targets 列表公共校验:元素格式 + 去重(保序,首个胜出)。

    Raises:
        SchemaValueError: 元素不是 ``platform:名称或id`` 形态(空串/缺冒号/
            平台前缀非法/名称为空)。
    """
    cleaned: list[str] = []
    for element in value:
        spec = element.strip() if isinstance(element, str) else element
        if not isinstance(spec, str) or _TARGET_SPEC_RE.fullmatch(spec) is None:
            raise SchemaValueError(
                "invalid_target_spec",
                f"{label} 元素必须是 platform:名称或id 形态(如 feishu:AI中转站合伙人群),当前为 {element!r}",
            )
        if spec not in cleaned:
            cleaned.append(spec)
    return cleaned


class RouteRuleConfig(_StrictModel):
    """One threshold route: ``when`` expression -> ``mode``.

    ``targets``(可选)覆盖通道级推送对象(10-03-messaging-core design D4,
    优先级:规则 ``targets`` > 通道级 ``targets`` > legacy 单 ``target``);
    元素格式在此校验,同平台约束在 :class:`PushConfig` 层统一执行——规则
    单独不知道自己挂在哪个通道上。
    """

    when: ExprStr
    mode: RouteMode
    targets: list[str] = Field(default_factory=list)

    @field_validator("targets")
    @classmethod
    def _check_target_specs(cls, value: list[str]) -> list[str]:
        return _validate_target_specs(value, "push.route[].targets")


class PushConfig(_StrictModel):
    """One delivery channel.

    ``target`` must be a pure ``env:``/``keychain:`` reference (plaintext
    refused). Empty ``route`` means: fall back to the seven-category mapping
    (grill Q1: 羊毛/节点/代买 -> immediate,其余 -> digest,未命中 -> 保守
    digest); v0.2 LLM score routing takes precedence once available.
    ``template`` falls back to the channel's built-in layout when omitted.

    ``timeout`` / ``retries`` / ``retry_backoff_seconds`` are the webhook
    transport contract (PRD 10-01-v02-push-telegram: 超时/重试可配);defaults
    mirror ``shishi.push.webhook``. They are rejected on other channels (no
    silent ignore).

    ``targets``(10-03-messaging-core,design D4):定向推送对象列表,元素
    ``platform:名称或id``。**同平台约束**:元素平台前缀必须与本条目通道
    对应平台一致(:data:`CHANNEL_PLATFORMS` 字面表,webhook/stdout 不支持
    目录寻址、配即拒);跨平台 = 写多条 push 条目。**targets 在场时
    ``target`` 可省**;优先级:规则 ``targets`` > 通道级 ``targets`` >
    legacy 单 ``target``(高层在场时低层不再投递)。不配 targets = 现行为,
    零迁移。
    """

    #: 与 shishi.push.webhook 的缺省一致(schema 不反依赖 push 层,本地定值)。
    channel: PushChannel
    target: str | None = None
    targets: list[str] = Field(default_factory=list)
    route: list[RouteRuleConfig] = Field(default_factory=list)
    template: str | None = Field(default=None, min_length=1)
    timeout: float = Field(default=10.0, gt=0)
    retries: int = Field(default=2, ge=0)
    retry_backoff_seconds: float = Field(default=1.0, ge=0)
    # ---- W2 平台可选凭据引用(10-03-messaging-w2-platforms design D2)----
    #: ntfy 可选鉴权 token(值 = Bearer token 或 ``user:pass``;省略且
    #: ``NTFY_TOKEN`` env 未设 = 无鉴权,公共 topic 合法态)。
    ntfy_token: str | None = None
    #: 钉钉可选加签密钥(配置即 HMAC-SHA256 加签,MYIA 增量;省略 = 裸
    #: webhook,蓝本行为)。
    dingtalk_secret: str | None = None
    #: 企微自建应用三凭据(省略走缺省 env 引用 WECOM_CORPID/
    #: WECOM_CORPSECRET/WECOM_AGENTID,发送期解析)。
    wecom_corpid: str | None = None
    wecom_corpsecret: str | None = None
    wecom_agentid: str | None = None
    # ---- 微信桥接可选字段(10-03-messaging-weixin-bridge design D1/D2)----
    #: Hermes CLI 本地路径覆写(缺省 ``~/.hermes/hermes-agent/.hermes/bin/
    #: hermes``)。**本地路径,非凭据**——不走 env:/keychain: 引用体系,
    #: 也不含任何秘密;MYIA 对微信零凭据(登录态只存在 Hermes 侧)。
    weixin_hermes_bin: str | None = None

    #: 各通道专属可选凭据字段的合法宿主(仅本通道可配;与 timeout/retries
    #: 仅 webhook 同一 fail-fast 哲学,不留静默忽略)。
    _CHANNEL_OPTIONAL_FIELD_HOSTS: ClassVar[dict[str, tuple[str, ...]]] = {
        "ntfy": ("ntfy_token",),
        "dingtalk": ("dingtalk_secret",),
        "wecom": ("wecom_corpid", "wecom_corpsecret", "wecom_agentid"),
        "weixin": ("weixin_hermes_bin",),
    }

    @model_validator(mode="before")
    @classmethod
    def _check_transport_fields_scope(cls, data: Any) -> Any:
        """超时/重试仅 webhook 生效;其他通道显式配置即拒(不留静默忽略)。"""
        if isinstance(data, dict) and data.get("channel") != "webhook":
            for key in ("timeout", "retries", "retry_backoff_seconds"):
                if key in data:
                    raise SchemaValueError(
                        "unexpected_transport_field",
                        f"channel 为 {data.get('channel')!r} 时不允许配置 {key}"
                        "(超时/重试配置仅 webhook 通道支持)",
                        path_suffix=key,
                    )
        return data

    @model_validator(mode="before")
    @classmethod
    def _check_platform_credential_fields_scope(cls, data: Any) -> Any:
        """W2 平台凭据字段仅其宿主通道可配(别处出现 = 配置错误,fail-fast)。"""
        if not isinstance(data, dict):
            return data
        channel = data.get("channel")
        allowed = cls._CHANNEL_OPTIONAL_FIELD_HOSTS.get(channel, ())
        every = {
            field
            for fields in cls._CHANNEL_OPTIONAL_FIELD_HOSTS.values()
            for field in fields
        }
        for key in data:
            if key in every and key not in allowed:
                raise SchemaValueError(
                    "unexpected_platform_field",
                    f"channel 为 {channel!r} 时不允许配置 {key}"
                    f"(该字段仅 {sorted(cls._CHANNEL_OPTIONAL_FIELD_HOSTS)} 对应通道支持)",
                    path_suffix=key,
                )
        return data

    @field_validator("target")
    @classmethod
    def _check_target_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parse_secret_value(value, label="push[].target", allow_scheme=False)
        return value

    @field_validator("ntfy_token", "dingtalk_secret", "wecom_corpid", "wecom_corpsecret", "wecom_agentid")
    @classmethod
    def _check_platform_credential_refs(cls, value: str | None, info: ValidationInfo) -> str | None:
        """W2 平台凭据字段同样只收纯 ``env:``/``keychain:`` 引用(禁明文)。"""
        if value is None:
            return value
        parse_secret_value(value, label=f"push[].{info.field_name}", allow_scheme=False)
        return value

    @field_validator("targets")
    @classmethod
    def _check_targets(cls, value: list[str]) -> list[str]:
        return _validate_target_specs(value, "push[].targets")

    @field_validator("template")
    @classmethod
    def _check_template_syntax(cls, value: str | None) -> str | None:
        """Jinja2 语法错误在加载期拒绝(fail-fast 于配置,不拖到发送时)。

        Only syntax is checked here — undefined variables depend on the item
        fields at send time and stay a channel-level structured failure
        (``PushSendError`` with code ``template_render_error``).
        """
        if value is None:
            return value
        import jinja2  # deferred: schema stays importable in tooling without it

        try:
            jinja2.Environment().parse(value)
        except jinja2.TemplateError as exc:
            raise SchemaValueError(
                "invalid_template",
                f"push[].template Jinja2 语法错误: {type(exc).__name__}: {exc}",
            )
        return value

    @model_validator(mode="after")
    def _check_channel_target(self) -> "PushConfig":
        platform = CHANNEL_PLATFORMS.get(self.channel)
        if self.channel == "stdout":
            if self.target is not None:
                raise SchemaValueError(
                    "unexpected_target",
                    "channel 为 stdout 时不允许配置 target",
                    path_suffix="target",
                )
            if self.targets:
                raise SchemaValueError(
                    "targeting_not_supported",
                    "channel 为 stdout 时不支持定向推送(不能配置 targets)",
                    path_suffix="targets",
                )
        elif platform is None:
            # webhook 等不支持目录寻址的通道:targets 即拒(fail-fast 于配置)。
            if self.targets:
                raise SchemaValueError(
                    "targeting_not_supported",
                    f"channel 为 {self.channel} 时不支持配置 targets"
                    f"(仅目录寻址通道 {sorted(CHANNEL_PLATFORMS)} 支持)",
                    path_suffix="targets",
                )
            if self.target is None:
                raise SchemaValueError(
                    "missing_target",
                    f"channel 为 {self.channel} 时必须提供 target(env:/keychain: 引用)",
                    path_suffix="target",
                )
        else:
            self._check_same_platform(platform)
            if self.target is None and not self.targets:
                # targets 在场时 target 可省(design D4 放宽);两者皆无才拒。
                raise SchemaValueError(
                    "missing_target",
                    f"channel 为 {self.channel} 时必须提供 target(env:/keychain: 引用)"
                    "或 targets(定向推送对象列表)",
                    path_suffix="target",
                )
        if platform is None:
            # 不支持寻址的通道:规则级 targets 与通道级同罪(不留静默忽略)。
            for index, rule in enumerate(self.route):
                if rule.targets:
                    raise SchemaValueError(
                        "targeting_not_supported",
                        f"channel 为 {self.channel!r} 不支持定向推送"
                        f"(push.route[{index}].targets 不能配置)",
                        path_suffix=f"route[{index}].targets",
                    )
        return self

    def _check_same_platform(self, platform: str) -> None:
        """同平台约束:通道级与规则级 targets 的平台前缀必须与条目通道一致。"""
        bad = [spec for spec in self.targets if spec.split(":", 1)[0] != platform]
        if bad:
            raise SchemaValueError(
                "platform_mismatch",
                f"push[].targets 元素平台前缀必须与通道 {self.channel!r} 对应平台"
                f" {platform!r} 一致(跨平台 = 写多条 push 条目),越界元素: {bad}",
                path_suffix="targets",
            )
        for index, rule in enumerate(self.route):
            bad_rules = [spec for spec in rule.targets if spec.split(":", 1)[0] != platform]
            if bad_rules:
                raise SchemaValueError(
                    "platform_mismatch",
                    f"push.route[{index}].targets 元素平台前缀必须与通道"
                    f" {self.channel!r} 对应平台 {platform!r} 一致,越界元素: {bad_rules}",
                    path_suffix=f"route[{index}].targets",
                )


class StorageConfig(_StrictModel):
    """Data lifecycle for long-running desktops.

    ``retention`` takes ``<n>d`` / ``<n>w`` and is exposed in days via
    :attr:`retention_days`.
    """

    retention: str = DEFAULT_RETENTION
    vacuum: VacuumCadence = "monthly"

    @field_validator("retention")
    @classmethod
    def _check_retention(cls, value: str) -> str:
        if not _RETENTION_RE.match(value):
            raise SchemaValueError(
                "invalid_retention",
                f"storage.retention 格式应为 <数字>d 或 <数字>w(如 90d),当前为 {value!r}",
            )
        return value

    @property
    def retention_days(self) -> int:
        match = _RETENTION_RE.match(self.retention)
        assert match is not None  # guaranteed by the validator
        days = int(match.group(1))
        return days * 7 if match.group(2) == "w" else days


# ---------------------------------------------------------------------------
# Trend baseline sidecar section (v0.4 趋势基线, PRD 10-01-v04-trend-baseline)
# ---------------------------------------------------------------------------


class BaselineConfig(_StrictModel):
    """品类级趋势基线声明(top-level ``baseline:`` sidecar 节)。

    语义:开启后,流水线把源抽取出的数值字段(:attr:`fields`,如 ``price``)
    按 ``(品类, 条目键, 字段)`` 存入 ``metric_history`` 数值历史快照表;推送
    模板经沙箱自定义函数 ``vs_yesterday`` / ``vs_last_week`` 拿到
    「较昨日 / 较上周」对比文本,``keyword_trends`` 上下文携带关键词提及量
    周环比(见 :mod:`shishi.push.templates`)。:attr:`msrp` 是可选的
    建议零售价对照表(公开数字),模板经 ``vs_msrp`` 消费。

    与 ``plugin:`` 同一套 sidecar 机制:由 :func:`load_category` 在装载入口
    校验(错误路径统一 ``$.baseline`` 前缀)并挂到
    :attr:`CategoryConfig.baseline`;12 节公开契约(SKILL.md / docs 逐字段
    锁定)不随之增长。历史快照的保留期由 store 层保证长于条目期
    (``BASELINE_RETENTION_MULTIPLIER × storage.retention``,周环比至少要
    两个完整窗口的历史)。
    """

    enabled: bool = False
    #: 数值字段清单(源 extract 产出的字段名);enabled 时至少声明一个。
    fields: list[ShortStr] = Field(default_factory=list)
    #: 对比窗口,day = vs 昨日 / week = vs 上周;缺省两个都要。
    windows: list[BaselineWindow] = Field(default_factory=lambda: list(BASELINE_WINDOWS))
    #: MSRP 对照表(公开建议零售价,键为商品名子串):可选,缺省不对照。
    msrp: dict[ShortStr, Annotated[float, Field(gt=0, allow_inf_nan=False)]] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _check_shape(self) -> "BaselineConfig":
        if not self.windows:
            raise SchemaValueError(
                "missing_baseline_windows",
                f"baseline.windows 至少要声明一个对比窗口 {list(BASELINE_WINDOWS)} 之一",
                path_suffix="windows",
            )
        if len(set(self.windows)) != len(self.windows):
            raise SchemaValueError(
                "duplicate_baseline_windows",
                "baseline.windows 存在重复窗口",
                path_suffix="windows",
            )
        if len(set(self.fields)) != len(self.fields):
            raise SchemaValueError(
                "duplicate_baseline_fields",
                "baseline.fields 存在重复字段",
                path_suffix="fields",
            )
        if self.enabled and not self.fields:
            raise SchemaValueError(
                "missing_baseline_fields",
                "baseline.enabled 为 true 时至少要声明一个数值字段(如 price)",
                path_suffix="fields",
            )
        return self


# ---------------------------------------------------------------------------
# Event aggregation sidecar section (v0.4 事件聚合, PRD 10-01-v04-event-aggregation)
# ---------------------------------------------------------------------------


class AggregateConfig(_StrictModel):
    """品类级事件聚合声明(top-level ``aggregate:`` sidecar 节)。

    语义:开启后,流水线在 dedup(同 URL/组合键,正交层)之后、push 之前做
    **两级判重**——先本地零 token 的标题相似度粗筛(:attr:`similarity_threshold`,
    字符 shingle Jaccard)圈出候选组,再把候选组交 LLM 判重精筛(并入 enrich
    的批量/缓存/预算护栏,端点配置复用 ``enrich:`` 节)。判为同事件的条目合并
    为单卡推送:主条目 + 「另见 N 源」列表,digest/immediate 均生效。

    :attr:`window_hours` 约束「同时间窗」:两个候选都带可解析发布时间且相差
    超过该窗口 → 直接视为不同事件(连 LLM 都不问,零 token);缺时间戳的
    条目无法排除,始终在窗内。

    与 ``baseline:`` 同一套 sidecar 机制:由 :func:`load_category` 在装载入口
    校验(错误路径统一 ``$.aggregate`` 前缀)并挂到 :attr:`CategoryConfig.aggregate`;
    12 节公开契约(SKILL.md / docs 逐字段锁定)不随之增长。
    """

    enabled: bool = False
    #: 同事件时间窗(小时);必须是正数。
    window_hours: float = Field(
        default=DEFAULT_AGGREGATE_WINDOW_HOURS, gt=0, allow_inf_nan=False
    )
    #: 标题相似度粗筛阈值(0-1,含 1):字符 shingle Jaccard 达到阈值才进 LLM 候选。
    similarity_threshold: float = Field(
        default=DEFAULT_AGGREGATE_SIMILARITY, gt=0, le=1, allow_inf_nan=False
    )


# ---------------------------------------------------------------------------
# Image processing sidecar section (10-03-vision-pipeline, 看图入管线)
# ---------------------------------------------------------------------------


class ImagesConfig(_StrictModel):
    """品类级图片处理声明(top-level ``images:`` sidecar 节)。

    语义:开启后,fetch 阶段尾部(条目入 checkpoint 队列前)对条目携带的
    图片 URL 执行 下载(SSRF 拒私网/魔法字节白名单/流式 10MB 截断/10s 超时)
    → 本地 OCR(to_thread 信号量 4)→ 可选 VL 情报向描述,产物挂
    ``metadata.image_ocr`` / ``image_caption`` / ``image_status``,喂给
    analyze/enrich 评分与推送模板(见 :mod:`shishi.vision.collect` 的降级
    矩阵——任何失败只写标记,绝不阻断管线)。**未开启 = 整环零进入**,
    行为与本节不存在时逐字段一致(零影响默认)。

    图片 URL 的来源:extract ``fields`` 配 ``image: img@src``(机制现成,
    fetch_base 对 src 属性做 urljoin)或 L3 crawl4ai 无 extract 时的
    markdown 同域图链接收集(拍板⑥:跨域广告/追踪像素不收)。

    源级覆写:``SourceConfig`` 本就 ``extra="allow"``,约定同键平铺参数
    ``images_enabled`` / ``images_max_images`` / ``images_min_bytes`` /
    ``images_vl`` / ``images_ocr_engine`` / ``images_detail_fetch`` /
    ``images_detail_max_items`` 覆写品类节(装载期不做 schema
    强校验,非法值告警忽略——与引擎扩展参数同一宽容度;
    ``max_per_run`` 是 run 级硬闸,不开放源级覆写)。

    与 ``aggregate:`` 同一套 sidecar 机制:由 :func:`load_category` 在装载
    入口校验(错误路径统一 ``$.images`` 前缀)并挂到
    :attr:`CategoryConfig.images`;12 节公开契约(SKILL.md / docs 逐字段
    锁定)不随之增长。
    """

    enabled: bool = False
    #: 每条目处理上限(张,1-10):超出部分静默截断。
    max_images: int = Field(default=DEFAULT_IMAGES_MAX_IMAGES, ge=1, le=10)
    #: 每 run 图处理总上限(张):VL 时长的硬闸;耗尽后条目标 ``skipped:run_limit``。
    max_per_run: int = Field(default=DEFAULT_IMAGES_MAX_PER_RUN, ge=1, le=1000)
    #: 小于该字节数的图视为图标/追踪像素跳过(缺省 10KB)。
    min_bytes: int = Field(default=DEFAULT_IMAGES_MIN_BYTES, ge=1, le=10_485_760)
    #: 视觉描述通道:off = 只 OCR(缺省,零 VL 开销);local = 本地 OpenAI
    #: 兼容端点(vision.yaml ``local`` 节,并发 1、45s/图超时降级);cloud =
    #: 云端视觉模型(vision.yaml ``cloud`` 节,token 走 enrich 预算池)。
    vl: Literal["off", "local", "cloud"] = "off"
    #: OCR 引擎覆写(vision | rapidocr);缺省 None = 按 vision.yaml 的
    #: ``ocr.engine_default``。
    ocr_engine: Literal["vision", "rapidocr"] | None = None
    #: 详情页追抓开关(10-03-detail-images):开 = fetch 尾部对本轮**无图**
    #: 条目(metadata 无可用 image/images URL)按管线顺序追抓其详情页,
    #: HTML 同域收 ``<img>`` 写回 ``metadata.images`` 后进同一识图环;
    #: 缺省 false 整链零进入(零影响默认)。列表页已带图的条目不追抓。
    detail_fetch: bool = False
    #: 每 run 追抓条目上限(1-50):串行 + 每请求 ≥1s 间隔 + 10s/页超时,
    #: 耗尽后其余无图条目照常入库(零标记);追抓失败/超时只写
    #: ``metadata.detail_status = failed:<原因>``,绝不阻管线。源级
    #: ``images_detail_max_items`` 覆写 = **该源独立预算**(不吃也不占
    #: 共享池,可低于也可高于品类值)。
    detail_max_items: int = Field(default=DEFAULT_IMAGES_DETAIL_MAX_ITEMS, ge=1, le=50)
    #: 图片落库开关(10-03-vision-v2,PRD 待拍板②「落图与否」的应用内开关):
    #: 开 = 通过全部下载关的图持久化到 ``MYIA_HOME/images/<sha16>.<ext>``
    #: (内容寻址,同图同文件不重复落盘),metadata 增 ``image_files``
    #: (绝对路径 list)与 ``image_ocr_lines``(逐行 ``{text, conf}``,
    #: 供 feed 详情展开);缺省 false = 纯文本产物、图文件即弃,行为与
    #: 落图能力引入前逐字段一致(零影响默认;隐私与体积由用户裁量)。
    persist: bool = False


# ---------------------------------------------------------------------------
# Scenario plugin declaration (v1.7 dual-mode sidecar section)
# ---------------------------------------------------------------------------


def normalize_plugin_requires(value: Any) -> list[str]:
    """Normalize ``requires`` from bare string or list; validate vocabulary.

    品类 plugin 节写 ``requires: docker``、市场 manifest 写 ``requires: [docker]``
    ——两种写法都收,归一后统一校验词表与重复(两处共用此函数,规则零漂移)。

    Raises:
        SchemaValueError: 非字符串(列表)、未知词表项或重复项。
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SchemaValueError("invalid_requires", f"requires 应为字符串或字符串列表,当前为 {value!r}")
    for token in value:
        if token not in REQUIRES_TOKENS:
            raise SchemaValueError(
                "unknown_requires_token",
                f"requires 取值 {token!r} 不在允许范围 {list(REQUIRES_TOKENS)} 内",
            )
    if len(set(value)) != len(value):
        raise SchemaValueError("duplicate_requires", f"requires 存在重复项: {value}")
    return value


class PluginLocalModeConfig(_StrictModel):
    """plugin 双模式的 local 侧:本机 Docker compose 交付。

    ``compose`` 是插件目录内的 compose 文件路径;``install`` 是安装/启动命令
    (如 ``docker compose up -d``)。至少声明其一。
    """

    compose: str | None = Field(default=None, min_length=1)
    install: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _check_has_content(self) -> PluginLocalModeConfig:
        if self.compose is None and self.install is None:
            raise SchemaValueError(
                "missing_local_mode_content",
                "modes.local 至少要声明 compose(文件路径)或 install(安装命令)之一",
            )
        return self


class PluginRemoteModeConfig(_StrictModel):
    """plugin 双模式的 remote 侧:指向已部署服务,桌面用户零 Docker。

    ``token`` 只允许 ``keychain:myia/<scope>/<name>`` 引用(插件 remote 凭据
    只走系统钥匙链;``env:`` 也不行 —— 桌面场景环境变量不可靠,且 PRD 明确
    token 入钥匙链)。规范名空间加载期即校验,不用等 resolve 期才报错。
    """

    endpoint: str
    token: str | None = Field(default=None, min_length=1)

    @field_validator("endpoint")
    @classmethod
    def _check_endpoint(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise SchemaValueError(
                "invalid_endpoint", f"modes.remote.endpoint 必须是 http(s) 地址,当前为 {value!r}"
            )
        return value

    @field_validator("token")
    @classmethod
    def _check_token_keychain(cls, value: str | None) -> str | None:
        if value is None:
            return value
        ref = parse_secret_value(value, label="modes.remote.token", allow_scheme=False)
        if ref.kind != "keychain":
            raise SchemaValueError(
                "plugin_token_requires_keychain",
                "modes.remote.token 必须是 keychain:myia/<scope>/<name> 引用"
                "(插件 remote 凭据只走系统钥匙链);请先 myia secret set 写入再引用",
            )
        try:
            secrets_store.validate_secret_name(ref.name)
        except secrets_store.SecretError as exc:
            raise SchemaValueError("invalid_secret_name", str(exc)) from exc
        return value


class PluginModesConfig(_StrictModel):
    """双模式集合:local(本机 Docker)与 remote(已部署服务)至少声明一个。"""

    local: PluginLocalModeConfig | None = None
    remote: PluginRemoteModeConfig | None = None

    @model_validator(mode="after")
    def _check_has_mode(self) -> PluginModesConfig:
        if self.local is None and self.remote is None:
            raise SchemaValueError(
                "missing_plugin_mode",
                "plugin.modes 至少要声明 local 或 remote 之一(双模式至少支持一种)",
            )
        return self


class CategoryPluginConfig(_StrictModel):
    """品类顶层的场景插件声明(v1.7 双模式 sidecar 节)。

    语义:本品类依赖市场插件 ``id`` 提供的服务,``modes`` 声明本部署可用的
    接入方式。该节只服务安装指引与诊断(src/shishi/plugins 包的启动自检/
    doctor);**任何插件装不上/配置坏/remote 不可达都不拦核心流水线**
    (security-baseline 铁律)—— 插件缺失降级为结构化 finding,品类照常跑。
    """

    id: str
    requires: list[str] = Field(default_factory=list)
    modes: PluginModesConfig

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _PLUGIN_ID_RE.match(value):
            raise SchemaValueError(
                "invalid_plugin_id",
                f"插件 id 只允许小写字母/数字/连字符/下划线且字母数字开头(2-64 字符,惯例 myia-<名称>),"
                f"当前为 {value!r}",
            )
        return value

    @field_validator("requires", mode="before")
    @classmethod
    def _normalize_requires(cls, value: Any) -> list[str]:
        return normalize_plugin_requires(value)


class CategoryConfig(_StrictModel):
    """Root model: one intelligence category, twelve schema sections."""

    id: str
    name: ShortStr
    schedule: str
    timezone: str | None = None
    sources: list[SourceConfig] = Field(min_length=1)
    watchlist: WatchlistConfig = Field(default_factory=WatchlistConfig)
    classify: ClassifyConfig = Field(default_factory=ClassifyConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    enrich: EnrichConfig = Field(default_factory=EnrichConfig)
    push: list[PushConfig] = Field(default_factory=list)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    #: 顶层 ``plugin:`` 节(v1.7 场景插件声明)。刻意用 PrivateAttr 而非字段:
    #: 12 节是 schema 的公开契约(SKILL.md / docs 由一致性测试逐字段锁定),
    #: 双模式声明是插件市场侧的 sidecar 元数据 —— 由 :func:`load_category` 在
    #: 装载入口校验并挂载,直接构造模型时为 None。
    _plugin: CategoryPluginConfig | None = PrivateAttr(default=None)

    #: 顶层 ``baseline:`` 节(v0.4 趋势基线 sidecar,见 :class:`BaselineConfig`)。
    _baseline: BaselineConfig | None = PrivateAttr(default=None)

    #: 顶层 ``aggregate:`` 节(v0.4 事件聚合 sidecar,见 :class:`AggregateConfig`)。
    _aggregate: AggregateConfig | None = PrivateAttr(default=None)

    #: 顶层 ``images:`` 节(10-03-vision-pipeline 图片处理环 sidecar,
    #: 见 :class:`ImagesConfig`)。
    _images: ImagesConfig | None = PrivateAttr(default=None)

    @property
    def plugin(self) -> CategoryPluginConfig | None:
        """品类声明的场景插件节;未声明或未经 :func:`load_category` 装载时为 None."""
        return self._plugin

    @property
    def baseline(self) -> BaselineConfig | None:
        """品类声明的趋势基线节;未声明或未经 :func:`load_category` 装载时为 None."""
        return self._baseline

    @property
    def aggregate(self) -> AggregateConfig | None:
        """品类声明的事件聚合节;未声明或未经 :func:`load_category` 装载时为 None."""
        return self._aggregate

    @property
    def images(self) -> ImagesConfig | None:
        """品类声明的图片处理节;未声明或未经 :func:`load_category` 装载时为 None."""
        return self._images

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not CATEGORY_ID_RE.match(value):
            raise SchemaValueError(
                "invalid_id",
                f"id 只允许小写字母/数字/连字符/下划线,且以字母或数字开头(1-64 字符),当前为 {value!r}",
            )
        return value

    @field_validator("schedule")
    @classmethod
    def _check_cron(cls, value: str) -> str:
        try:
            CronTrigger.from_crontab(value)
        except ValueError as exc:
            raise SchemaValueError(
                "invalid_cron",
                f"schedule 不是合法的 5 段 cron 表达式({exc})",
            ) from exc
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None  # default: follow the system timezone at schedule time
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise SchemaValueError(
                "invalid_timezone",
                f"timezone 不是有效的 IANA 时区名称: {value!r}",
            ) from exc
        return value

    @model_validator(mode="after")
    def _check_dedup_key_fields(self) -> "CategoryConfig":
        """dedup.key 占位符必须能被 extract.fields(或保留字段)渲染。

        A key placeholder no source can supply turns into a per-item
        ``dedup_key_error`` at run time (every item dropped, status partial) —
        a load-time cross-check keeps that AI-generated drift fail-fast
        (PRD: AI 生成质量问题要在加载期可检出). Sources without ``extract``
        (L3+ auto-structuring) are skipped: their field names are unknown
        until the engine structures the page.
        """
        placeholders = set(_PLACEHOLDER_RE.findall(self.dedup.key))
        if not placeholders:
            return self
        for index, source in enumerate(self.sources):
            if source.extract is None:
                continue
            available = set(source.extract.fields) | RESERVED_DEDUP_FIELDS
            missing = placeholders - available
            if missing:
                raise SchemaValueError(
                    "invalid_dedup_key",
                    f"dedup.key 占位符 {sorted(missing)} 无法由源 {source.name!r} 渲染:"
                    f"不在 extract.fields({sorted(source.extract.fields)})中,"
                    f"也不是保留字段 {sorted(RESERVED_DEDUP_FIELDS)}",
                    path_suffix="dedup.key",
                )
        return self


# ---------------------------------------------------------------------------
# Load entry points
# ---------------------------------------------------------------------------


def _format_loc(loc: tuple[Any, ...]) -> str:
    """pydantic error loc -> JSONPath-style string (``sources.0`` -> ``$.sources[0]``)."""
    path = "$"
    for part in loc:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def _pydantic_error_detail(err: Mapping[str, Any]) -> LoadErrorDetail:
    """Map one pydantic error to a structured, Chinese-message detail."""
    path = _format_loc(err.get("loc", ()))
    ptype = str(err.get("type", ""))
    ctx = err.get("ctx") or {}
    raw_error = ctx.get("error")

    if isinstance(raw_error, SchemaValueError):
        detail_path = f"{path}.{raw_error.path_suffix}" if raw_error.path_suffix else path
        return LoadErrorDetail(detail_path, raw_error.code, str(raw_error))
    if ptype == "extra_forbidden":
        name = str(err["loc"][-1]) if err.get("loc") else "?"
        return LoadErrorDetail(path, "unknown_field", f"未知字段 {name!r},schema 不允许(fail-fast)")
    if ptype == "missing":
        return LoadErrorDetail(path, "missing_field", "缺少必填字段")
    if ptype == "literal_error":
        return LoadErrorDetail(path, "invalid_value", f"取值不在允许范围内(期望 {ctx.get('expected', '')})")
    if ptype == "value_error":
        message = str(raw_error) if raw_error else str(err.get("msg", "")).removeprefix("Value error, ")
        return LoadErrorDetail(path, "value_error", message)
    if ptype == "string_too_short":
        return LoadErrorDetail(path, "string_too_short", f"长度不能少于 {ctx.get('min_length')} 个字符")
    if ptype == "string_too_long":
        return LoadErrorDetail(path, "string_too_long", f"长度不能超过 {ctx.get('max_length')} 个字符")
    if ptype == "string_pattern_mismatch":
        return LoadErrorDetail(path, "string_pattern_mismatch", f"格式不符合要求(应匹配 {ctx.get('pattern')})")
    if ptype in ("list_too_short", "too_short"):
        return LoadErrorDetail(path, "too_short", f"长度/项数不足,至少需要 {ctx.get('min_length')}")
    if ptype in ("string_type",):
        return LoadErrorDetail(path, ptype, "应为字符串")
    if ptype in ("int_type", "int_parsing", "int_from_float"):
        return LoadErrorDetail(path, "int_type", "应为整数")
    if ptype in ("float_type", "float_parsing"):
        return LoadErrorDetail(path, "float_type", "应为数字")
    if ptype == "bool_type":
        return LoadErrorDetail(path, ptype, "应为布尔值")
    if ptype == "list_type":
        return LoadErrorDetail(path, ptype, "应为列表")
    if ptype == "dict_type":
        return LoadErrorDetail(path, ptype, "应为键值映射")
    if ptype in ("greater_than", "greater_than_equal"):
        return LoadErrorDetail(path, ptype, f"数值必须大于等于 {ctx.get('gt', ctx.get('ge'))}")
    if ptype in ("less_than", "less_than_equal"):
        return LoadErrorDetail(path, ptype, f"数值必须小于等于 {ctx.get('lt', ctx.get('le'))}")
    return LoadErrorDetail(path, ptype, str(err.get("msg", "校验失败")))


def load_category(data: Mapping[str, Any], *, source: str | None = None) -> CategoryConfig:
    """Validate a parsed YAML mapping into :class:`CategoryConfig`.

    The optional top-level ``plugin:`` section (v1.7 场景插件双模式声明) is
    validated against :class:`CategoryPluginConfig` and attached to the
    result's :attr:`CategoryConfig.plugin`; its errors merge into the same
    :class:`LoadError` with paths prefixed ``$.plugin``. ``plugin: null`` is
    treated as absent (占位写法不拒载). The optional top-level ``baseline:``
    section (v0.4 趋势基线) follows the same sidecar mechanism against
    :class:`BaselineConfig` (paths prefixed ``$.baseline``, ``null`` absent)
    and lands on :attr:`CategoryConfig.baseline`. The optional top-level
    ``aggregate:`` section (v0.4 事件聚合) follows the same mechanism against
    :class:`AggregateConfig` (paths prefixed ``$.aggregate``, ``null`` absent)
    and lands on :attr:`CategoryConfig.aggregate`. The optional top-level
    ``images:`` section (10-03-vision-pipeline 图片处理环) follows the same
    mechanism against :class:`ImagesConfig` (paths prefixed ``$.images``,
    ``null`` absent) and lands on :attr:`CategoryConfig.images`.

    Args:
        data: top-level mapping from ``yaml.safe_load``.
        source: optional file path, carried on :class:`LoadError` for
            doctor-style reporting.

    Raises:
        LoadError: any of the twelve sections *or* a sidecar section (plugin
            / baseline / aggregate / images) fails validation; collects *all*
            errors in one report.
    """
    if not isinstance(data, Mapping):
        raise LoadError(
            [LoadErrorDetail("$", "invalid_root", f"品类配置必须是键值映射,当前为 {type(data).__name__}")],
            source=source,
        )
    payload = dict(data)
    plugin_data = payload.pop("plugin", None)  # sidecar 节单独校验,不进 12 节模型
    baseline_data = payload.pop("baseline", None)  # 同上(v0.4 趋势基线)
    aggregate_data = payload.pop("aggregate", None)  # 同上(v0.4 事件聚合)
    images_data = payload.pop("images", None)  # 同上(10-03-vision-pipeline)
    errors: list[LoadErrorDetail] = []
    config: CategoryConfig | None = None
    try:
        config = CategoryConfig.model_validate(payload)
    except ValidationError as exc:
        errors.extend(_pydantic_error_detail(err) for err in exc.errors())
    plugin_section = _validate_plugin_section(plugin_data, errors)
    baseline_section = _validate_baseline_section(baseline_data, errors)
    aggregate_section = _validate_aggregate_section(aggregate_data, errors)
    images_section = _validate_images_section(images_data, errors)
    if config is not None and baseline_section is not None:
        # 交叉校验(baseline 节 vs sources):fields 拼错要装载期可检出。
        _check_baseline_fields(config, baseline_section, errors)
    if errors:
        raise LoadError(errors, source=source)
    assert config is not None  # errors 为空则 12 节校验必已成功
    config._plugin = plugin_section
    config._baseline = baseline_section
    config._aggregate = aggregate_section
    config._images = images_section
    return config


def _validate_plugin_section(
    plugin_data: Any, errors: list[LoadErrorDetail]
) -> CategoryPluginConfig | None:
    """Validate the sidecar ``plugin:`` section, appending structured errors.

    ``None`` = 未声明;非映射 = 结构错误;校验失败的错误路径统一加 ``$.plugin``
    前缀(子映射校验的 loc 相对于节根)。返回 None 时 errors 里必有对应明细。
    """
    if plugin_data is None:
        return None
    if not isinstance(plugin_data, Mapping):
        errors.append(
            LoadErrorDetail(
                "$.plugin",
                "invalid_plugin_section",
                f"plugin 节必须是键值映射,当前为 {type(plugin_data).__name__}",
            )
        )
        return None
    try:
        return CategoryPluginConfig.model_validate(dict(plugin_data))
    except ValidationError as exc:
        for err in exc.errors():
            detail = _pydantic_error_detail(err)
            errors.append(dataclass_replace(detail, path=f"$.plugin{detail.path[1:]}"))
        return None


def _validate_baseline_section(
    baseline_data: Any, errors: list[LoadErrorDetail]
) -> BaselineConfig | None:
    """Validate the sidecar ``baseline:`` section, appending structured errors.

    与 :func:`_validate_plugin_section` 同一契约:``None`` = 未声明;非映射 =
    结构错误;校验失败的错误路径统一加 ``$.baseline`` 前缀(子映射校验的 loc
    相对于节根)。返回 None 时 errors 里必有对应明细。
    """
    if baseline_data is None:
        return None
    if not isinstance(baseline_data, Mapping):
        errors.append(
            LoadErrorDetail(
                "$.baseline",
                "invalid_baseline_section",
                f"baseline 节必须是键值映射,当前为 {type(baseline_data).__name__}",
            )
        )
        return None
    try:
        return BaselineConfig.model_validate(dict(baseline_data))
    except ValidationError as exc:
        for err in exc.errors():
            detail = _pydantic_error_detail(err)
            errors.append(dataclass_replace(detail, path=f"$.baseline{detail.path[1:]}"))
        return None


#: L3+ 自动结构化兜底的固定字段(无 extract 的源只会产出这三个;
#: baseline.fields 声明它们之外的姓名却无 extract 源可产时,必是拼错)。
_BASELINE_STRUCTURAL_FIELDS = frozenset({"url", "title", "content"})


def _check_baseline_fields(
    config: CategoryConfig, baseline: BaselineConfig, errors: list[LoadErrorDetail]
) -> None:
    """``baseline.fields`` 至少要有一个声明来源(装载期交叉校验)。

    与 :func:`CategoryConfig._check_dedup_key_fields` 同一设计动机(AI 生成
    质量问题要在加载期可检出):字段名拼错时 ``record_item_metrics`` /
    ``build_trend_table`` 全部静默跳过,快照永远 0 行、对比永远为空,用户侧
    无任何报错。带 ``extract`` 的源字段名单在装载期已知;无 extract 的 L3+ 源
    只产出固定结构化字段(见 :data:`_BASELINE_STRUCTURAL_FIELDS`),一并计入
    可用集合。
    """
    available: set[str] = set(_BASELINE_STRUCTURAL_FIELDS)
    for source in config.sources:
        if source.extract is not None:
            available.update(source.extract.fields)
    missing = [name for name in baseline.fields if name not in available]
    if not missing:
        return
    errors.append(
        LoadErrorDetail(
            "$.baseline.fields",
            "invalid_baseline_fields",
            f"baseline.fields {sorted(missing)} 无法由任何源产出"
            f"(各源 extract.fields 与结构化兜底字段合计:{sorted(available)});"
            "拼错的字段会静默产出零快照、零对比",
        )
    )


def _validate_aggregate_section(
    aggregate_data: Any, errors: list[LoadErrorDetail]
) -> AggregateConfig | None:
    """Validate the sidecar ``aggregate:`` section, appending structured errors.

    与 :func:`_validate_baseline_section` 同一契约:``None`` = 未声明;非映射 =
    结构错误;校验失败的错误路径统一加 ``$.aggregate`` 前缀(子映射校验的 loc
    相对于节根)。返回 None 时 errors 里必有对应明细。
    """
    if aggregate_data is None:
        return None
    if not isinstance(aggregate_data, Mapping):
        errors.append(
            LoadErrorDetail(
                "$.aggregate",
                "invalid_aggregate_section",
                f"aggregate 节必须是键值映射,当前为 {type(aggregate_data).__name__}",
            )
        )
        return None
    try:
        return AggregateConfig.model_validate(dict(aggregate_data))
    except ValidationError as exc:
        for err in exc.errors():
            detail = _pydantic_error_detail(err)
            errors.append(dataclass_replace(detail, path=f"$.aggregate{detail.path[1:]}"))
        return None


def _validate_images_section(
    images_data: Any, errors: list[LoadErrorDetail]
) -> ImagesConfig | None:
    """Validate the sidecar ``images:`` section, appending structured errors.

    与 :func:`_validate_baseline_section` 同一契约:``None`` = 未声明;非映射 =
    结构错误;校验失败的错误路径统一加 ``$.images`` 前缀(子映射校验的 loc 相对
    于节根)。返回 None 时 errors 里必有对应明细。
    """
    if images_data is None:
        return None
    if not isinstance(images_data, Mapping):
        errors.append(
            LoadErrorDetail(
                "$.images",
                "invalid_images_section",
                f"images 节必须是键值映射,当前为 {type(images_data).__name__}",
            )
        )
        return None
    try:
        return ImagesConfig.model_validate(dict(images_data))
    except ValidationError as exc:
        for err in exc.errors():
            detail = _pydantic_error_detail(err)
            errors.append(dataclass_replace(detail, path=f"$.images{detail.path[1:]}"))
        return None


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys (AI 合并/误编辑典型伤).

    Plain ``yaml.safe_load`` silently keeps the *last* occurrence of a
    duplicated key (e.g. two ``dedup:`` blocks), losing the author's intent
    without a trace. Raising here turns the conflict into the existing
    structured ``yaml_parse_error`` LoadError path.
    """


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    loader.flatten_mapping(node)
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            hash(key)
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found unhashable key {key!r}",
                key_node.start_mark,
            ) from exc
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def read_yaml_document(path: str | Path) -> Any:
    """Read and parse one YAML file (UTF-8, duplicate keys refused).

    Shared low-level loader for every YAML entry point(品类 12 节、插件市场
    manifest、后续社区目录):同一套结构化错误契约(file_not_found /
    file_read_error / invalid_encoding / yaml_parse_error),解析结果原样返回,
    空文件返回 None 由调用方按各自语义报错。

    Raises:
        LoadError: file missing/unreadable, not valid UTF-8, or YAML syntax
            broken (including duplicate keys).
    """
    file_path = Path(path)
    source = str(file_path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # GBK/ANSI 记事本文件等:裸 UnicodeDecodeError 会打穿结构化错误契约
        # (error-handling spec: 字段路径+错误类+原因,退出码 1)。
        raise LoadError(
            [
                LoadErrorDetail(
                    "$",
                    "invalid_encoding",
                    f"配置文件不是有效的 UTF-8 编码({source}): {exc}"
                    "(请将文件另存为 UTF-8 后重试)",
                )
            ],
            source=source,
        ) from exc
    except OSError as exc:
        error_type = "file_not_found" if isinstance(exc, FileNotFoundError) else "file_read_error"
        raise LoadError(
            [LoadErrorDetail("$", error_type, f"无法读取配置文件 {source}: {exc}")],
            source=source,
        ) from exc
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise LoadError(
            [LoadErrorDetail("$", "yaml_parse_error", f"YAML 语法无法解析: {exc}")],
            source=source,
        ) from exc


def load_category_file(path: str | Path) -> CategoryConfig:
    """Read, parse and validate a category YAML file.

    Raises:
        LoadError: file missing/unreadable, not valid UTF-8, YAML syntax
            broken (including duplicate keys), empty, or schema validation
            failed.
    """
    file_path = Path(path)
    source = str(file_path)
    data = read_yaml_document(file_path)
    if data is None:
        raise LoadError(
            [LoadErrorDetail("$", "invalid_root", "品类配置文件为空")],
            source=source,
        )
    return load_category(data, source=source)
