"""L6 engine: LLM-driven browser (skyvern) — the degrade chain's last resort.

Contract (PRD 10-01-v04-engine-llm-browser; 降级链定案「烧 token,只做兜底」):

- **skyvern REST 对接(零 SDK)**:plain httpx against Skyvern's task API —
  create ``POST {endpoint}/v1/run/tasks`` (``x-api-key`` auth) -> poll
  ``GET {endpoint}/v1/runs/{run_id}`` every ``poll_interval`` seconds until a
  terminal status; the extracted payload rides the completed run's ``output``
  field. The adapter imports no SDK — plain httpx, and the firecrawl adapter
  is built the same way. pyproject 的 ``skyvern`` / ``firecrawl`` extras 因此
  不是引擎的运行时依赖,而是**服务端安装便利**(``skyvern`` / ``firecrawl-py``
  PyPI 包即服务端本体,``uv sync --extra skyvern`` 一键拉起自部署后端);
  引擎代码对这两个包零引用。
  自部署与云同面:cloud = ``MYIA_SKYVERN_URL`` + ``MYIA_SKYVERN_API_KEY``;
  self-host = 默认端点 ``http://127.0.0.1:8000``,api_key 可选(自部署可关鉴权)。
- **endpoint + api_key 一律凭据引用**(安全基线: 配置零明文凭据):
  ``engine_options.llm_browser.endpoint`` / ``api_key`` must be ``env:`` /
  ``keychain:`` references — plaintext is refused at fetch time
  (``credentials_plaintext``); unset options fall back to the env vars above.
  Resolved values never reach logs:endpoint 的日志记凭据引用名
  (``env:``/``keychain:``)或内置缺省,api_key 只报已配置与否。
- **自然语言任务描述(源级配置)**:``engine_options.llm_browser.goal`` carries
  the task verbatim into skyvern's ``prompt``. Without it the goal is
  generated deterministically: extract 字段名 -> 「逐条提取字段 …」;无
  extract -> 通用提取目标。「AI 生成」goal 的推荐路径是「AI 写 YAML」流程
  (SKILL.md)——引擎层不持有 LLM 客户端,不做运行时 LLM 生成(再调一次
  LLM 双重烧 token,与兜底层定位相悖)。
- **硬护栏(严于 L3/L5 的成本闸门)**:
  1. *单源白名单* — only ``engine: llm_browser``(显式指定)or ``engine:
     auto`` 链走到链尾(含既有 hint 提前命中——该 hint 本身源自一次链尾成功)
     may invoke this engine; anything else raises ``engine_not_whitelisted``
     (defense in depth: 链构建本就不会把它派给其它首选引擎);
  2. *每 run 调用次数上限* — one skyvern call = one target URL;calls are
     counted per pipeline run(挂在 :class:`FetchContext` 实例上,同 run 全部
     llm_browser 源共享,``engine_options.llm_browser.max_calls``,默认 3 ——
     L5 页面预算默认 10,兜底层更严);exceeding raises
     ``call_budget_exhausted`` before the next backend call;
  3. *token 预算* — the same :class:`shishi.enrich.scoring.BudgetTracker` as
     enrich guards a per-run budget
     (``engine_options.llm_browser.budget_per_run``):每完成一次调用,按
     skyvern 响应上报的用量入账(``usage.total_tokens`` -> ``total_cost`` ->
     ``step_count``,取第一个正数);部署未上报任何用量时按 0 计(debug 可见,
     预算护栏可能低估)——调用次数上限是独立的硬停止。耗尽 ->
     ``budget_exhausted``。
- **失败即终止降级链**:L6 是链尾,没有「下一层」—— every failure is a
  structured :class:`FetchError`,registry records it as the chain's final
  :class:`~shishi.engines.fetch_base.EngineFailure`(所有引擎均失败)。熔断/
  预算 refusal aborts the whole engine fetch(已抓条目一并丢弃,与 L5 页面
  预算同语义:超预算必须显式升预算,不默默烧钱)。
- extraction:``json_path`` extracts over skyvern's JSON ``output`` payload
  (载荷本就是 JSON,L1 语法天然适用);CSS ``list``/``item`` extract 提供字段
  *名*(进入生成的 goal)并把返回记录投影到这些字段 —— skyvern 返回数据而非
  HTML,CSS 选择器本身无从执行;无 extract 自动结构化兜底
  ``{url, title, content}``(yaml-schema 规则 7,与 L3/L4/L5 同形)。
- politeness primitives (robots / rate limit) apply to the *target* site
  (skyvern 代抓),mirroring the crawl4ai/firecrawl split(目标站礼貌 vs
  后端结构化失败)。源 ``pool:`` 代理无法透传 skyvern —— 其 run API 只接受
  服务端代理位置枚举(``proxy_location``),不含任意上游 URL;源显式配置
  pool 代理时 WARNING 可见(显式代理意图不静默丢弃),目标站出口请在
  skyvern 侧配置,或设 ``engine_options.llm_browser.proxy_location``。

``engine_options.llm_browser`` 词汇表(全部可选;未列键由各 _resolver 给缺省):

============================  =============================================
键                            语义
============================  =============================================
endpoint                      skyvern 端点(env:/keychain: 引用)
api_key                       API key(env:/keychain: 引用)
goal                          自然语言任务描述(源级配置)
proxy_location                skyvern 服务端代理位置(如 ``RESIDENTIAL``)
timeout                       单个 run 的轮询预算(秒,默认 300)
poll_interval                 轮询间隔(秒,默认 5)
max_calls                     每 run 调用次数上限(默认 3)
budget_per_run                每 run 预算(默认 30,单位见上)
============================  =============================================

Raises:
    FetchError: 白名单拒绝(``engine_not_whitelisted``)/ 调用次数熔断
        (``call_budget_exhausted``)/ 预算耗尽(``budget_exhausted``)/
        配置错(``invalid_endpoint``/``invalid_api_key``/``invalid_goal``/
        ``invalid_timeout``/``invalid_poll_interval``/``invalid_max_calls``/
        ``invalid_budget``/``invalid_proxy_location``/
        ``credentials_plaintext``)/ 后端错误(``skyvern_error``,含
        failure_reason)/ run 轮询超预算(``timeout``)/ 非 JSON 响应
        (``json_decode``)/ HTTP >= 400(``http_<status>``)。
    CredentialResolveError: 凭据引用无法解析(env 缺失 / 钥匙链未写入)。
    RobotsDisallowedError: robots.txt forbids the target URL(基座抛出)。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from shishi.engines.fetch_base import (
    BaseEngine,
    FetchError,
    extract_json,
    mask_endpoint_url,
)
from shishi.enrich.scoring import BudgetTracker
from shishi.schema import CredentialResolveError, resolve_credential

logger = logging.getLogger(__name__)

LAYER = "L6"

#: Skyvern 端点 / key 的 env 回退(cloud 接入方式;与 firecrawl 同约定)。
ENV_SKYVERN_URL = "MYIA_SKYVERN_URL"
ENV_SKYVERN_API_KEY = "MYIA_SKYVERN_API_KEY"

#: 自部署默认端点(skyvern docker-compose 的 API 服务端口)。
DEFAULT_SKYVERN_ENDPOINT = "http://127.0.0.1:8000"

#: 单个 run 的轮询预算(秒)。skyvern 代理导航多步交互比静态页慢一个量级。
DEFAULT_RUN_TIMEOUT_SECONDS = 300.0

#: 轮询间隔(秒;skyvern 文档建议 ~5s)。
DEFAULT_POLL_INTERVAL_SECONDS = 5.0

#: 每 run 调用次数上限默认值(兜底层严于 L5 的 10 页页面预算)。
DEFAULT_MAX_CALLS_PER_RUN = 3

#: 每 run 预算默认值。单位 = skyvern 响应上报的用量
#: (``usage.total_tokens`` -> ``total_cost`` -> ``step_count`` 第一个正数);
#: 未上报按 0 计。默认值只在与显式调大的 max_calls 组合时才可能触顶。
DEFAULT_BUDGET_PER_RUN = 30

#: skyvern run 的终态集合(文档状态机:created/queued/running 为进行态)。
TERMINAL_RUN_STATUSES: tuple[str, ...] = (
    "completed",
    "failed",
    "terminated",
    "timed_out",
    "canceled",
)

#: 单 run 护栏状态挂在 FetchContext 实例上的属性名(专用前缀防撞;
#: FetchContext 是无 slots 的可变 dataclass,实例属性随 run 上下文生灭,
#: 零泄漏、无 id 复用风险 —— 它不可哈希,WeakKeyDictionary 不可用)。
_GUARD_CONTEXT_ATTR = "_llm_browser_run_guard"

__all__ = [
    "DEFAULT_BUDGET_PER_RUN",
    "DEFAULT_GOAL",
    "DEFAULT_MAX_CALLS_PER_RUN",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_RUN_TIMEOUT_SECONDS",
    "DEFAULT_SKYVERN_ENDPOINT",
    "ENV_SKYVERN_API_KEY",
    "ENV_SKYVERN_URL",
    "LAYER",
    "TERMINAL_RUN_STATUSES",
    "LLMBrowserEngine",
    "build_goal_from_fields",
    "normalize_skyvern_output",
    "observed_usage",
]

#: 无 goal 且无 extract 时的通用提取目标(goal 的最后兜底)。
DEFAULT_GOAL = (
    "打开页面,提取页面主要内容与条目列表;每条记录包含 title(标题)、"
    "url(链接)与 content(正文摘录),以 JSON 数组返回。"
)

#: skyvern 可能包裹输出数组的包装键(与 enrich 评分载荷的解包同思路)。
_OUTPUT_WRAPPER_KEYS: tuple[str, ...] = ("items", "results", "data", "records")


def build_goal_from_fields(field_names: list[str]) -> str:
    """Extract 字段名 -> 自然语言提取目标(确定性生成,非 LLM 生成)."""
    fields = "、".join(field_names)
    return (
        f"在页面上定位目标条目列表,逐条提取字段:{fields};"
        "每条记录必须包含 url 字段(条目链接),以 JSON 数组返回。"
    )


def normalize_skyvern_output(output: Any, target_url: str) -> list[dict]:
    """skyvern ``output`` -> 容忍形态的条目记录(无 extract 兜底的原料).

    - ``None`` -> 空列表(completed 但未提取到内容,健康空结果);
    - 字符串 -> 尽力按 JSON 解析(容忍 ```json 围栏),失败则作为单条 content;
    - 包装对象(``{"items": [...]}`` 等)解包;裸对象视为单条记录;
    - 数组元素非对象 -> 单条 content 记录;记录缺 url -> 回填目标页 URL。
    """
    if output is None:
        return []
    payload: Any = output
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("```"):
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline + 1 :]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        if text[:1] in ("[", "{"):
            try:
                payload = json.loads(text)
            except ValueError:
                payload = output  # 不是 JSON:按纯文本内容单条兜底
    if isinstance(payload, dict):
        for wrapper in _OUTPUT_WRAPPER_KEYS:
            inner = payload.get(wrapper)
            if isinstance(inner, list):
                payload = inner
                break
        else:
            payload = [payload]
    if not isinstance(payload, list):
        payload = [{"url": target_url, "content": str(payload)}]
    records: list[dict] = []
    for element in payload:
        record = (
            dict(element)
            if isinstance(element, dict)
            else {"content": "" if element is None else str(element)}
        )
        record.setdefault("url", target_url)
        records.append(record)
    return records


def observed_usage(payload: dict[str, Any]) -> int | None:
    """skyvern run 响应 -> 预算入账数(用量取第一个正数,未上报 ``None``).

    顺序:``usage.total_tokens`` -> ``total_cost`` -> ``step_count`` ——
    云端报 token/费用,自部署常报步数;都没有则 ``None``(预算按 0 计,
    护栏可能低估,debug 可见)。bool 恒不计(True 是 int 子类)。
    """
    usage = payload.get("usage")
    if isinstance(usage, dict):
        tokens = usage.get("total_tokens")
        if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) and tokens > 0:
            return int(tokens)
    for key in ("total_cost", "step_count"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
    return None


class _RunGuard:
    """单 run 硬护栏:调用次数计数 + 预算(enrich 同一套 BudgetTracker)。

    挂在 :class:`FetchContext` 实例上,同 run 的全部 llm_browser 源共享;
    后到的源只能把上限**收紧**(最严者胜,与限速器合并约定一致)。
    """

    def __init__(self, *, max_calls: int, budget_limit: int) -> None:
        self.max_calls = max_calls
        self.budget = BudgetTracker(limit=budget_limit)
        self.calls = 0

    def tighten(self, *, max_calls: int, budget_limit: int) -> None:
        """Merge another source's caps keeping the strictest (min)."""
        self.max_calls = min(self.max_calls, max_calls)
        if budget_limit < self.budget.limit:
            self.budget.limit = budget_limit
            self.budget.overshoot = max(0, self.budget.used - budget_limit)

    def claim_call(self, *, source: str) -> None:
        """占用一个调用名额;超限即熔断(check-then-increment 间无 await,原子)."""
        if self.calls >= self.max_calls:
            raise FetchError(
                f"单 run 引擎调用次数熔断 source={source}:本次 run 已调用 "
                f"{self.calls} 次,达到上限 {self.max_calls}"
                "(engine_options.llm_browser.max_calls);L6 是最昂贵的兜底层,"
                "确需更多调用请显式调大 max_calls",
                error_type="call_budget_exhausted",
            )
        self.calls += 1

    def ensure_budget(self, *, source: str) -> None:
        """预算耗尽即拒绝发起新调用(can_spend 先于点火,enrich 同语义)."""
        if not self.budget.can_spend():
            raise FetchError(
                f"单 run token 预算耗尽 source={source}:budget_per_run="
                f"{self.budget.limit} used={self.budget.used}"
                "(engine_options.llm_browser.budget_per_run;与 enrich 同一套 "
                "BudgetTracker 护栏),确需更多请显式调大预算",
                error_type="budget_exhausted",
            )


def _run_guard_for(context: Any, *, max_calls: int, budget_limit: int) -> _RunGuard:
    """FetchContext -> 本 run 的护栏(无则建,有则收紧)."""
    guard: _RunGuard | None = getattr(context, _GUARD_CONTEXT_ATTR, None)
    if guard is None:
        guard = _RunGuard(max_calls=max_calls, budget_limit=budget_limit)
        setattr(context, _GUARD_CONTEXT_ATTR, guard)
        logger.debug(
            "llm_browser 单 run 护栏已建立 max_calls=%s budget=%s", max_calls, budget_limit
        )
        return guard
    guard.tighten(max_calls=max_calls, budget_limit=budget_limit)
    return guard


@dataclass(frozen=True)
class _Options:
    """Validated ``engine_options.llm_browser`` snapshot(全量先于首次调用)."""

    endpoint: str
    #: endpoint 的日志安全展示形态(凭据引用名 / env 回退名 / 内置缺省):
    #: 解析值不落日志(模块契约;fetch_base 的 mask 先例同旨)。
    endpoint_display: str
    api_key: str | None
    goal: str | None
    proxy_location: str | None
    timeout: float
    poll_interval: float
    max_calls: int
    budget_per_run: int


class LLMBrowserEngine(BaseEngine):
    """Natural-language browser automation via a Skyvern backend (L6, chain tail)."""

    LAYER = "L6"
    ENGINE_NAME = "llm_browser"
    REQUIRES_EXTRACT = False  # 无 extract 自动结构化兜底(yaml-schema 规则 7)
    SUPPORTED_EXTRACT_TYPES = ("list", "item", "json_path")

    # ------------------------------------------------------- configuration

    def _resolve_ref(self, value: str, label: str) -> str:
        """Resolve an ``env:``/``keychain:`` reference; plaintext is refused."""
        try:
            return resolve_credential(value, backend=self.context.keychain_backend)
        except CredentialResolveError as exc:
            if exc.code == "invalid_credential_ref":
                raise FetchError(
                    f"{label} 必须是 env:/keychain: 凭据引用,禁明文(安全基线)",
                    error_type="credentials_plaintext",
                ) from exc
            raise

    def _endpoint(self) -> tuple[str, str]:
        """Resolve endpoint -> ``(concrete value, log-safe display form)``.

        展示形态记来源 —— options 引用原样(``env:``/``keychain:`` 名)、env
        回退记 ``env:MYIA_SKYVERN_URL``、无配置记内置缺省 —— 解析值不落
        日志(模块契约;fetch_base 的 mask 先例同旨)。
        """
        configured = self.engine_options().get("endpoint")
        if configured is not None:
            if not isinstance(configured, str):
                raise FetchError(
                    f"engine_options.llm_browser.endpoint 应为字符串凭据引用,"
                    f"当前为 {type(configured).__name__}",
                    error_type="invalid_endpoint",
                )
            resolved = self._resolve_ref(configured, "engine_options.llm_browser.endpoint")
            return resolved, configured
        from_env = os.environ.get(ENV_SKYVERN_URL, "").strip()
        if from_env:
            return from_env, f"env:{ENV_SKYVERN_URL}"
        return DEFAULT_SKYVERN_ENDPOINT, f"默认({DEFAULT_SKYVERN_ENDPOINT})"

    def _api_key(self) -> str | None:
        configured = self.engine_options().get("api_key")
        if configured is not None:
            if not isinstance(configured, str):
                raise FetchError(
                    f"engine_options.llm_browser.api_key 应为字符串凭据引用,"
                    f"当前为 {type(configured).__name__}",
                    error_type="invalid_api_key",
                )
            return self._resolve_ref(configured, "engine_options.llm_browser.api_key")
        return os.environ.get(ENV_SKYVERN_API_KEY, "").strip() or None

    def _goal(self) -> str | None:
        value = self.engine_options().get("goal")
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise FetchError(
                f"engine_options.llm_browser.goal 应为非空自然语言任务描述,当前为 {value!r}",
                error_type="invalid_goal",
            )
        return value

    def _proxy_location(self) -> str | None:
        value = self.engine_options().get("proxy_location")
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise FetchError(
                f"engine_options.llm_browser.proxy_location 应为 skyvern 服务端代理位置"
                f"(如 RESIDENTIAL),当前为 {value!r}",
                error_type="invalid_proxy_location",
            )
        return value

    def _timeout(self) -> float:
        value = self.engine_options().get("timeout")
        if value is None:
            return DEFAULT_RUN_TIMEOUT_SECONDS
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise FetchError(
                f"engine_options.llm_browser.timeout 应为正数秒,当前为 {value!r}",
                error_type="invalid_timeout",
            )
        return float(value)

    def _poll_interval(self) -> float:
        value = self.engine_options().get("poll_interval")
        if value is None:
            return DEFAULT_POLL_INTERVAL_SECONDS
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise FetchError(
                f"engine_options.llm_browser.poll_interval 应为正数秒,当前为 {value!r}",
                error_type="invalid_poll_interval",
            )
        return float(value)

    def _max_calls(self) -> int:
        value = self.engine_options().get("max_calls")
        if value is None:
            return DEFAULT_MAX_CALLS_PER_RUN
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise FetchError(
                f"engine_options.llm_browser.max_calls 应为正整数,当前为 {value!r}",
                error_type="invalid_max_calls",
            )
        return int(value)

    def _budget_per_run(self) -> int:
        value = self.engine_options().get("budget_per_run")
        if value is None:
            return DEFAULT_BUDGET_PER_RUN
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise FetchError(
                f"engine_options.llm_browser.budget_per_run 应为正整数,当前为 {value!r}",
                error_type="invalid_budget",
            )
        return int(value)

    def _options(self) -> _Options:
        """engine_options.llm_browser 全量校验快照(先于任何调用,fail-fast 于配置)."""
        endpoint, endpoint_display = self._endpoint()
        return _Options(
            endpoint=endpoint,
            endpoint_display=endpoint_display,
            api_key=self._api_key(),
            goal=self._goal(),
            proxy_location=self._proxy_location(),
            timeout=self._timeout(),
            poll_interval=self._poll_interval(),
            max_calls=self._max_calls(),
            budget_per_run=self._budget_per_run(),
        )

    # -------------------------------------------------------------- guards

    def _check_whitelist(self) -> None:
        """单源白名单:显式 engine: llm_browser 或 auto 链(其链尾即本引擎)."""
        if self.source.engine in ("llm_browser", "auto"):
            return
        raise FetchError(
            f"llm_browser 未列入该源的白名单 engine={self.source.engine!r}:"
            "L6 是烧 token 的兜底层,只允许 engine: llm_browser 显式指定,"
            "或 engine: auto 降级链走到链尾时触达",
            error_type="engine_not_whitelisted",
        )

    def _effective_goal(self, options: _Options) -> str:
        """源级配置优先;否则按 extract 字段确定性生成;最后通用目标."""
        if options.goal is not None:
            return options.goal
        extract = self.source.extract
        if extract is not None and extract.fields:
            return build_goal_from_fields(list(extract.fields))
        return DEFAULT_GOAL

    # -------------------------------------------------------------- fetch

    async def _fetch_impl(self) -> list[dict]:
        self._check_whitelist()
        options = self._options()
        guard = _run_guard_for(
            self.context, max_calls=options.max_calls, budget_limit=options.budget_per_run
        )
        if self._active_pool is not None:
            # 显式代理意图不静默丢弃,但 skyvern run API 无任意上游 URL 字段,
            # pool 代理透传不了 —— 大声说出来,出口请在 skyvern 侧配置。
            logger.warning(
                "源 pool 代理无法透传 skyvern(其 API 只接受服务端 proxy_location 枚举) "
                "source=%s pool=%s;目标站出口请在 skyvern 侧配置,或设 "
                "engine_options.llm_browser.proxy_location",
                self.source.name,
                self._active_pool,
            )
        logger.info(
            "skyvern 后端就绪 endpoint=%s timeout=%ss poll_interval=%ss "
            "max_calls(run)=%s budget_per_run=%s api_key=%s",
            # 展示形态 = 凭据引用名 / 内置缺省;解析值不落日志(fetch_base mask 先例)。
            options.endpoint_display,
            options.timeout,
            options.poll_interval,
            guard.max_calls,
            guard.budget.limit,
            "已配置" if options.api_key else "未配置",
        )
        items: list[dict] = []
        for target_url in self._template_urls():
            # 礼貌约束作用于目标站点(由 skyvern 代抓),而非我们自己的后端。
            await self._ensure_robots_allowed(target_url)
            await self._acquire_rate_limit(target_url)
            items.extend(await self._run_task(guard, options, target_url))
        return items

    async def _run_task(self, guard: _RunGuard, options: _Options, target_url: str) -> list[dict]:
        """One skyvern run: 熔断/预算检查 -> create -> poll -> output 提取."""
        guard.ensure_budget(source=self.source.name)
        guard.claim_call(source=self.source.name)
        run_id = await self._create_run(options, target_url)
        payload = await self._poll_until_terminal(options, run_id, target_url)
        status = payload.get("status")
        assert isinstance(status, str)  # _poll_until_terminal 已保证终态字符串
        if status != "completed":
            raise self._terminal_failure(status, payload, target_url)
        guard.budget.spend(self._observed_usage(payload, run_id))
        return self._extract_output(payload.get("output"), target_url)

    # ------------------------------------------------------------- skyvern

    def _auth_headers(self, options: _Options) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if options.api_key:
            headers["x-api-key"] = options.api_key
        return headers

    def _http_status_failure(
        self, exc: httpx.HTTPStatusError, endpoint_display: str
    ) -> FetchError:
        """HTTP >= 400 -> 结构化 FetchError:错误类 ``http_<status>`` 不变,但
        消息只记端点展示形态 —— httpx 异常的 str 内嵌完整解析 URL,直接透传
        会把后端端点写进 failures[] 与日志(模块契约:解析值不落日志)。"""
        status = exc.response.status_code
        return FetchError(
            f"skyvern 后端 HTTP {status} endpoint={endpoint_display}: {exc.response.reason_phrase}",
            error_type=f"http_{status}",
        )

    async def _create_run(self, options: _Options, target_url: str) -> str:
        """``POST /v1/run/tasks`` -> run_id(prompt = 任务描述)."""
        url = options.endpoint.rstrip("/") + "/v1/run/tasks"
        body: dict[str, Any] = {"url": target_url, "prompt": self._effective_goal(options)}
        if options.proxy_location is not None:
            body["proxy_location"] = options.proxy_location
        try:
            response = await self._send_with_retry(
                "POST",
                url,
                json_body=body,
                headers=self._auth_headers(options),
                # 重试 WARNING 记掩码形态:解析端点不落日志。
                log_url=mask_endpoint_url(url),
            )
        except httpx.HTTPStatusError as exc:
            raise self._http_status_failure(exc, options.endpoint_display) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise FetchError(
                f"skyvern 创建 run 响应不是有效 JSON url={target_url}: {exc}",
                error_type="json_decode",
            ) from exc
        run_id = payload.get("run_id") if isinstance(payload, dict) else None
        if not isinstance(run_id, str) or not run_id:
            raise FetchError(
                f"skyvern 创建 run 响应缺少 run_id url={target_url}",
                error_type="skyvern_error",
            )
        logger.info("skyvern run 已创建 run_id=%s url=%s", run_id, target_url)
        return run_id

    async def _poll_until_terminal(
        self, options: _Options, run_id: str, target_url: str
    ) -> dict[str, Any]:
        """``GET /v1/runs/{run_id}`` 轮询至终态或耗尽 run 轮询预算(timeout)."""
        url = options.endpoint.rstrip("/") + f"/v1/runs/{run_id}"
        headers = {"x-api-key": options.api_key} if options.api_key else None
        deadline = self.context.clock() + options.timeout
        while True:
            try:
                response = await self._send_with_retry(
                    "GET", url, headers=headers, log_url=mask_endpoint_url(url)
                )
            except httpx.HTTPStatusError as exc:
                raise self._http_status_failure(exc, options.endpoint_display) from exc
            try:
                payload = response.json()
            except ValueError as exc:
                raise FetchError(
                    f"skyvern 轮询响应不是有效 JSON run_id={run_id}: {exc}",
                    error_type="json_decode",
                ) from exc
            status = payload.get("status") if isinstance(payload, dict) else None
            if isinstance(status, str) and status in TERMINAL_RUN_STATUSES:
                return payload
            if not isinstance(status, str):
                raise FetchError(
                    f"skyvern 轮询响应缺少 status run_id={run_id} url={target_url}",
                    error_type="skyvern_error",
                )
            if self.context.clock() >= deadline:
                raise FetchError(
                    f"skyvern run 超出轮询预算 run_id={run_id} url={target_url} "
                    f"(timeout={options.timeout:.0f}s,最后状态 {status!r});"
                    "多步交互耗时长请显式调大 engine_options.llm_browser.timeout",
                    error_type="timeout",
                )
            await self.context.sleep(options.poll_interval)

    def _terminal_failure(
        self, status: str, payload: dict[str, Any], target_url: str
    ) -> FetchError:
        """非 completed 终态 -> 结构化失败(timed_out 单列,链据此统计)."""
        reason = payload.get("failure_reason")
        if not reason:
            errors = payload.get("errors")
            if isinstance(errors, list) and errors:
                reason = "; ".join(str(item) for item in errors)
        error_type = "timeout" if status == "timed_out" else "skyvern_error"
        return FetchError(
            f"skyvern run 未成功完成 status={status} url={target_url}: {reason or '未知原因'}",
            error_type=error_type,
        )

    def _observed_usage(self, payload: dict[str, Any], run_id: str) -> int | None:
        """响应 -> 预算入账数;未上报按 0 计(debug 可见,护栏可能低估)."""
        usage = observed_usage(payload)
        if usage is None:
            logger.debug(
                "skyvern 响应未携带用量,预算按 0 计(护栏可能低估) run_id=%s", run_id
            )
        return usage

    # ----------------------------------------------------------- extraction

    def _extract_output(self, output: Any, target_url: str) -> list[dict]:
        extract = self.source.extract
        records = normalize_skyvern_output(output, target_url)
        if extract is None:
            return self._auto_structure(records, target_url)
        if extract.type == "json_path":
            # 载荷本就是 JSON:L1 的 json_path 语法直接适用。
            return extract_json(records if output is not None else {}, extract)
        # CSS list/item:skyvern 返回数据而非 HTML,字段名即语义,投影对齐。
        field_names = list(extract.fields)
        projected: list[dict] = []
        for record in records:
            item = {name: record[name] for name in field_names if record.get(name) is not None}
            if item:
                projected.append(item)
        return projected

    @staticmethod
    def _auto_structure(records: list[dict], target_url: str) -> list[dict]:
        """无 extract 兜底:{url, title, content}(与 L3/L4/L5 同形)."""
        items: list[dict] = []
        for record in records:
            url = record.get("url") if isinstance(record.get("url"), str) and record.get("url") else target_url
            title = record.get("title") or record.get("name") or ""
            content = record.get("content") or record.get("text") or ""
            if not content:
                extra = {k: v for k, v in record.items() if k not in ("url", "title", "name")}
                content = json.dumps(extra, ensure_ascii=False) if extra else ""
            items.append(
                {
                    "url": url,
                    "title": title if isinstance(title, str) else str(title),
                    "content": content if isinstance(content, str) else str(content),
                }
            )
        return items
