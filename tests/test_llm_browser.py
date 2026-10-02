"""Tests for the llm_browser engine & its place at the auto-chain tail
(PRD 10-01-v04-engine-llm-browser).

Covers:

- **skyvern REST 往返(MockTransport,零真实网络)**:create ``POST
  /v1/run/tasks`` 请求体(url/prompt/proxy_location)与 ``x-api-key`` 鉴权
  (env: / keychain: 引用;未配置不带头)→ poll ``GET /v1/runs/{run_id}``
  轮询至终态(FakeClock 记录等待,零真实 sleep);completed 的 ``output``
  解析为条目;
- **output 形态容忍**:数组直返 / 包装对象解包 / ```json 围栏字符串 / 纯文本
  content 兜底 / null -> 健康空 / 非 dict 元素与缺 url 回填;
- **extract 三形态**:json_path 作用于 JSON output 载荷;CSS list/item 只取
  字段名(生成 goal)并投影记录;无 extract 自动结构化兜底 {url,title,content};
- **硬护栏(严于 L3)**:单源白名单(engine_not_whitelisted)/ 每 run 调用
  次数熔断(call_budget_exhausted,同 run 跨源共享、新 run 重置、最严者胜)/
  token 预算(enrich 同一套 BudgetTracker,budget_exhausted;用量按
  usage.total_tokens -> total_cost -> step_count 入账,未上报按 0);
- **终态语义**:failed/terminated -> skyvern_error(failure_reason 可见)/
  timed_out -> timeout / 轮询超预算 -> timeout / 非 JSON 与缺 run_id ->
  结构化报错;
- **自然语言任务描述**:goal 源级配置逐字透传;缺省按 extract 字段确定性生成;
  再缺省通用目标;
- **auto 链七层终测(yaml-schema 规则 5)**:L1→L2→crawl4ai→firecrawl→
  scrapling→stealth_browser→llm_browser;注入假引擎全败后 llm_browser 兜底
  成功且 hint 回写;llm_browser 失败即链尾终止(无下一层,结构化上报)。

真实 skyvern(自部署/云)不做联网验证 —— 记 openIssues「需主人手动验证」;
所有请求走 httpx.MockTransport,所有等待由 FakeClock 记录。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from conftest import (
    make_client,
    make_context,
    make_handler,
    make_raw_source,
    make_source,
    run,
)

from myia.engines import registry
from myia.engines.fetch_base import (
    BaseEngine,
    FetchError,
    RobotsDisallowedError,
    classify_exception,
    load_proxy_pools,
)
from myia.engines.llm_browser import (
    DEFAULT_BUDGET_PER_RUN,
    DEFAULT_GOAL,
    DEFAULT_MAX_CALLS_PER_RUN,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_RUN_TIMEOUT_SECONDS,
    DEFAULT_SKYVERN_ENDPOINT,
    LLMBrowserEngine,
    build_goal_from_fields,
    normalize_skyvern_output,
    observed_usage,
)
from myia.engines.registry import auto_degrade, fetch_source, resolve_engine
from myia.secrets import SECRET_SERVICE, InMemoryKeychainBackend

TARGET_URL = "https://form.example/wizard"
CREATE_PATH = "/v1/run/tasks"

LIST_EXTRACT = {
    "type": "list",
    "item": "div.thread",
    "fields": {"title": "a.title", "url": "a.title@href"},
}

JSON_FIELDS = {"title": "$[*].title", "url": "$[*].url"}


@pytest.fixture(autouse=True)
def _isolate_skyvern_env(monkeypatch: pytest.MonkeyPatch):
    """测试独立:skyvern env 回退一律先清空,防宿主环境串味."""
    monkeypatch.delenv("MYIA_SKYVERN_URL", raising=False)
    monkeypatch.delenv("MYIA_SKYVERN_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Mock skyvern 后端:create/poll 按脚本应答,记录请求
# ---------------------------------------------------------------------------


class FakeSkyvern:
    """skyvern 假后端:记录 create/poll 请求,payload 按脚本弹出(耗尽后重复末项)."""

    def __init__(
        self,
        *,
        poll_payloads: list[dict[str, Any]] | None = None,
        create_status: int = 200,
        create_payload: dict[str, Any] | None = None,
        run_id: str = "tsk_1",
    ) -> None:
        self.create_status = create_status
        self.create_payload = create_payload
        self.run_id = run_id
        self._poll_payloads = list(poll_payloads) if poll_payloads else [
            {"run_id": run_id, "status": "completed", "step_count": 2, "output": None}
        ]
        self._poll_index = 0
        self.create_calls: list[dict[str, Any]] = []
        self.poll_calls: list[dict[str, Any]] = []

    def next_poll_payload(self) -> dict[str, Any]:
        """按脚本推进;耗尽后固定应答末项(always-running 超时测试用)."""
        if self._poll_index < len(self._poll_payloads):
            payload = self._poll_payloads[self._poll_index]
            self._poll_index += 1
            return payload
        return self._poll_payloads[-1] if self._poll_payloads else {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == CREATE_PATH:
            self.create_calls.append(
                {
                    "url": str(request.url),
                    "key": request.headers.get("x-api-key"),
                    "body": json.loads(request.content),
                }
            )
            if self.create_payload is not None:
                return httpx.Response(self.create_status, json=self.create_payload)
            return httpx.Response(
                self.create_status, json={"run_id": self.run_id, "status": "queued"}
            )
        if request.url.path.startswith("/v1/runs/"):
            self.poll_calls.append(
                {"url": str(request.url), "key": request.headers.get("x-api-key")}
            )
            return httpx.Response(200, json=self.next_poll_payload())
        raise AssertionError(f"unexpected skyvern request path: {request.url.path}")


def make_llm_parts(
    *, fake: FakeSkyvern | None = None, store: Any = None, **source_overrides: Any
) -> tuple[LLMBrowserEngine, Any, FakeSkyvern]:
    """一条 llm_browser 源 + 注入 FakeSkyvern 的上下文 -> (engine, context, fake)."""
    fake = fake or FakeSkyvern()
    client = make_client(make_handler(fake.handler))
    context, _clock = make_context(client, store=store)
    data: dict[str, Any] = {"engine": "llm_browser", "url": TARGET_URL, "retry": 0}
    data.update(source_overrides)
    return LLMBrowserEngine(make_source(**data), context), context, fake


def llm_on_context(context: Any, *, fake: FakeSkyvern | None = None, **source_overrides: Any) -> LLMBrowserEngine:
    """在既有(共享 run)上下文上再挂一条 llm_browser 源(跨源护栏测试用)."""
    fake = fake or FakeSkyvern()
    context.client = make_client(make_handler(fake.handler))
    data: dict[str, Any] = {"engine": "llm_browser", "url": TARGET_URL, "retry": 0}
    data.update(source_overrides)
    return LLMBrowserEngine(make_source(**data), context)


# ---------------------------------------------------------------------------
# create/poll REST 往返与鉴权
# ---------------------------------------------------------------------------


def test_create_poll_round_trip_parses_output():
    """create 请求体(url/prompt)+ 默认自部署端点;poll 至 completed;output -> 条目."""
    fake = FakeSkyvern(
        poll_payloads=[
            {"run_id": "tsk_1", "status": "queued"},
            {"run_id": "tsk_1", "status": "running"},
            {
                "run_id": "tsk_1",
                "status": "completed",
                "step_count": 4,
                "output": [
                    {"title": "表单结果一", "url": "https://form.example/r/1"},
                    {"title": "表单结果二", "url": "https://form.example/r/2"},
                ],
            },
        ]
    )
    engine, context, _ = make_llm_parts(
        fake=fake, engine_options={"llm_browser": {"goal": "填完三步表单并提取确认页"}}
    )

    items = run(engine.fetch())

    assert items == [  # 无 extract -> 自动结构化兜底(yaml-schema 规则 7)
        {"url": "https://form.example/r/1", "title": "表单结果一", "content": ""},
        {"url": "https://form.example/r/2", "title": "表单结果二", "content": ""},
    ]
    assert len(fake.create_calls) == 1
    create = fake.create_calls[0]
    assert create["url"] == f"{DEFAULT_SKYVERN_ENDPOINT}{CREATE_PATH}"  # 自部署默认端点
    assert create["key"] is None  # 未配置 api_key 不带头
    assert create["body"] == {"url": TARGET_URL, "prompt": "填完三步表单并提取确认页"}
    assert len(fake.poll_calls) == 3  # 立即一查 + 两次等待后
    assert fake.poll_calls[-1]["url"] == f"{DEFAULT_SKYVERN_ENDPOINT}/v1/runs/tsk_1"
    guard = context._llm_browser_run_guard
    assert guard.calls == 1
    assert guard.budget.used == 4  # step_count 入账(enrich 同一套 BudgetTracker)


def test_api_key_from_env_ref(monkeypatch):
    """api_key env: 引用解析进 x-api-key,create 与 poll 同鉴权(值不落日志)."""
    monkeypatch.setenv("MYIA_SKYVERN_KEY", "sk-env-secret")
    engine, _, fake = make_llm_parts(
        engine_options={"llm_browser": {"api_key": "env:MYIA_SKYVERN_KEY"}}
    )
    run(engine.fetch())
    assert fake.create_calls[0]["key"] == "sk-env-secret"
    assert fake.poll_calls[0]["key"] == "sk-env-secret"


def test_api_key_from_keychain_ref():
    """api_key keychain: 引用经注入后端解析(与基座凭据同一通道)."""
    engine, _, fake = make_llm_parts()
    backend = InMemoryKeychainBackend()
    backend.set_password(SECRET_SERVICE, "myia/demo/skyvern_key", "sk-kc-secret")
    engine.context.keychain_backend = backend
    engine.source = make_source(
        engine="llm_browser",
        url=TARGET_URL,
        retry=0,
        engine_options={"llm_browser": {"api_key": "keychain:myia/demo/skyvern_key"}},
    )
    run(engine.fetch())
    assert fake.create_calls[0]["key"] == "sk-kc-secret"


def test_endpoint_from_env_and_options_ref(monkeypatch):
    """endpoint:env: 引用与 options 引用都生效;缺省落自部署地址."""
    engine, _, fake = make_llm_parts()
    run(engine.fetch())
    assert fake.create_calls[0]["url"].startswith(DEFAULT_SKYVERN_ENDPOINT)

    monkeypatch.setenv("MYIA_SKYVERN_URL", "https://api.skyvern.example")
    engine, _, fake = make_llm_parts()
    run(engine.fetch())
    assert fake.create_calls[0]["url"].startswith("https://api.skyvern.example")

    engine, _, fake = make_llm_parts(
        engine_options={"llm_browser": {"endpoint": "env:MYIA_SKYVERN_URL"}}
    )
    run(engine.fetch())
    assert fake.create_calls[0]["url"].startswith("https://api.skyvern.example")


def test_plaintext_endpoint_and_api_key_refused():
    """凭据禁明文:endpoint/api_key 配成裸值 -> credentials_plaintext(安全基线)."""
    with pytest.raises(FetchError) as excinfo:
        engine, _, _ = make_llm_parts(
            engine_options={"llm_browser": {"endpoint": "http://127.0.0.1:8000"}}
        )
        run(engine.fetch())
    assert excinfo.value.error_type == "credentials_plaintext"

    # engine 层防御纵深:绕过 schema 构造时,明文 api_key 同样结构化拒绝。
    client = make_client(make_handler(lambda r: pytest.fail("明文 key 不应发起请求")))
    context, _ = make_context(client)
    raw_source = make_raw_source(
        engine="llm_browser",
        url=TARGET_URL,
        engine_options={"llm_browser": {"api_key": "sk-plaintext"}},
    )
    with pytest.raises(FetchError) as excinfo:
        run(LLMBrowserEngine(raw_source, context).fetch())
    assert excinfo.value.error_type == "credentials_plaintext"


def test_schema_rejects_plaintext_api_key_at_load_time():
    """第一道防线:schema 对疑似凭据键 api_key 的明文/非引用值加载即拒."""
    with pytest.raises(ValueError):
        make_source(engine="llm_browser", url=TARGET_URL, api_key="sk-plaintext")


def test_invalid_options_are_structured_errors():
    """engine_options.llm_browser 拼错 -> 先报配置错,零连接且护栏未建立(fail-fast).

    api_key 的类型错走 schema 疑似凭据键校验(见上测),不在本表。
    """
    cases = (
        {"endpoint": 123},
        {"goal": "   "},
        {"goal": 7},
        {"proxy_location": 9},
        {"timeout": "soon"},
        {"timeout": True},
        {"timeout": 0},
        {"poll_interval": 0},
        {"poll_interval": True},
        {"max_calls": 0},
        {"max_calls": True},
        {"max_calls": "3"},
        {"budget_per_run": 0},
        {"budget_per_run": True},
    )
    for options in cases:
        engine, context, fake = make_llm_parts(engine_options={"llm_browser": options})
        with pytest.raises(FetchError) as excinfo:
            run(engine.fetch())
        assert excinfo.value.error_type.startswith("invalid_"), options
        assert getattr(context, "_llm_browser_run_guard", None) is None, options
        assert fake.create_calls == [], options


# ---------------------------------------------------------------------------
# output 形态容忍与 extract 三形态
# ---------------------------------------------------------------------------


def test_output_shape_tolerance():
    url = "https://form.example/wizard"
    assert normalize_skyvern_output(None, url) == []
    assert normalize_skyvern_output({"items": [{"title": "x"}]}, url) == [
        {"title": "x", "url": url}
    ]
    assert normalize_skyvern_output({"results": [{"title": "y"}]}, url) == [
        {"title": "y", "url": url}
    ]
    fenced = '```json\n[{"title": "z"}]\n```'
    assert normalize_skyvern_output(fenced, url) == [{"title": "z", "url": url}]
    assert normalize_skyvern_output("提取完成,无条目", url) == [
        {"url": url, "content": "提取完成,无条目"}
    ]
    assert normalize_skyvern_output({"title": "单条", "price": "9.9"}, url) == [
        {"title": "单条", "price": "9.9", "url": url}
    ]
    assert normalize_skyvern_output(["纯文本条目", {"title": "t"}], url) == [
        {"url": url, "content": "纯文本条目"},
        {"title": "t", "url": url},
    ]


def test_json_path_extract_runs_over_output_payload():
    """json_path 属 L1 语法,但 skyvern 载荷本就是 JSON —— 直接适用."""
    fake = FakeSkyvern(
        poll_payloads=[
            {
                "run_id": "tsk_1",
                "status": "completed",
                "output": [{"title": "接口条目", "url": "https://form.example/1"}],
            }
        ]
    )
    engine, _, _ = make_llm_parts(fake=fake, extract={"type": "json_path", "fields": JSON_FIELDS})
    items = run(engine.fetch())
    assert items == [{"title": "接口条目", "url": "https://form.example/1"}]


def test_list_extract_projects_field_names_only():
    """CSS list/item:skyvern 返回数据非 HTML —— 字段名投影,选择器不执行."""
    fake = FakeSkyvern(
        poll_payloads=[
            {
                "run_id": "tsk_1",
                "status": "completed",
                "output": [
                    {"title": "帖子一", "url": "https://form.example/t/1", "extra": "x"},
                    {"title": "帖子二"},  # 缺 url -> 回填目标页
                ],
            }
        ]
    )
    engine, _, _ = make_llm_parts(fake=fake, extract=LIST_EXTRACT)
    items = run(engine.fetch())
    assert items == [
        {"title": "帖子一", "url": "https://form.example/t/1"},
        {"title": "帖子二", "url": TARGET_URL},
    ]


def test_no_extract_auto_structures_same_shape_as_lower_layers():
    fake = FakeSkyvern(
        poll_payloads=[
            {
                "run_id": "tsk_1",
                "status": "completed",
                "output": [{"title": "向导页", "text": "第一步选择套餐"}],
            }
        ]
    )
    engine, _, _ = make_llm_parts(fake=fake)  # 无 extract
    items = run(engine.fetch())
    assert items == [{"url": TARGET_URL, "title": "向导页", "content": "第一步选择套餐"}]


def test_completed_without_output_is_healthy_empty():
    """completed 但 output=null -> 0 条(健康空结果,不是失败)."""
    fake = FakeSkyvern(poll_payloads=[{"run_id": "tsk_1", "status": "completed", "output": None}])
    engine, _, _ = make_llm_parts(fake=fake)
    items = run(engine.fetch())
    assert items == []


# ---------------------------------------------------------------------------
# 自然语言任务描述:源级配置 / extract 生成 / 通用兜底
# ---------------------------------------------------------------------------


def test_goal_configured_verbatim():
    engine, _, fake = make_llm_parts(engine_options={"llm_browser": {"goal": "显式任务描述"}})
    run(engine.fetch())
    assert fake.create_calls[0]["body"]["prompt"] == "显式任务描述"


def test_goal_generated_from_extract_fields():
    engine, _, fake = make_llm_parts(extract=LIST_EXTRACT)
    run(engine.fetch())
    prompt = fake.create_calls[0]["body"]["prompt"]
    assert "title" in prompt and "url" in prompt  # extract 字段名确定性生成
    assert "JSON 数组" in prompt


def test_goal_defaults_to_generic_when_no_extract():
    engine, _, fake = make_llm_parts()
    run(engine.fetch())
    assert fake.create_calls[0]["body"]["prompt"] == DEFAULT_GOAL


def test_build_goal_from_fields_lists_every_field():
    goal = build_goal_from_fields(["title", "url", "price"])
    assert "title" in goal and "url" in goal and "price" in goal
    assert "JSON 数组" in goal


# ---------------------------------------------------------------------------
# 终态语义与结构化错误
# ---------------------------------------------------------------------------


def test_failed_status_is_structured_with_failure_reason():
    fake = FakeSkyvern(
        poll_payloads=[{"run_id": "tsk_1", "status": "failed", "failure_reason": "登录墙无法通过"}]
    )
    engine, _, _ = make_llm_parts(fake=fake)
    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "skyvern_error"
    assert "登录墙无法通过" in str(excinfo.value)


def test_failed_status_falls_back_to_errors_list():
    fake = FakeSkyvern(
        poll_payloads=[{"run_id": "tsk_1", "status": "terminated", "errors": ["step 3 失败"]}]
    )
    engine, _, _ = make_llm_parts(fake=fake)
    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "skyvern_error"
    assert "step 3 失败" in str(excinfo.value)


def test_timed_out_status_maps_to_timeout_class():
    fake = FakeSkyvern(
        poll_payloads=[{"run_id": "tsk_1", "status": "timed_out", "failure_reason": "步数超限"}]
    )
    engine, _, _ = make_llm_parts(fake=fake)
    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "timeout"


def test_poll_budget_timeout_records_waiting():
    """后端一直 running:轮询预算耗尽 -> timeout;等待经 context.sleep 可见."""
    fake = FakeSkyvern(poll_payloads=[{"run_id": "tsk_1", "status": "running"}])
    engine, _, _ = make_llm_parts(
        fake=fake, engine_options={"llm_browser": {"timeout": 8, "poll_interval": 5}}
    )
    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "timeout"
    assert "轮询预算" in str(excinfo.value)
    assert len(fake.poll_calls) == 3  # t=0/5/10,第三次应答后 clock 10 >= 预算 8


def test_create_response_not_json_is_structured():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == CREATE_PATH:
            return httpx.Response(200, text="<html>gateway</html>")
        raise AssertionError("create 失败后不应轮询")  # pragma: no cover

    client = make_client(make_handler(handler))
    context, _ = make_context(client)
    engine = LLMBrowserEngine(make_source(engine="llm_browser", url=TARGET_URL, retry=0), context)
    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "json_decode"


def test_create_response_missing_run_id_is_structured():
    fake = FakeSkyvern(create_payload={"status": "queued"})  # 缺 run_id
    engine, _, _ = make_llm_parts(fake=fake)
    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "skyvern_error"
    assert fake.poll_calls == []  # 无 run_id 不轮询


def test_http_500_after_retry_budget_is_structured():
    """后端 5xx 走满 retry 预算后原样抛 HTTPStatusError —— registry 以
    classify_exception 归为 http_500(firecrawl 后端失败同语义)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == CREATE_PATH:
            return httpx.Response(500, text="skyvern down")
        raise AssertionError("create 失败后不应轮询")  # pragma: no cover

    client = make_client(make_handler(handler))
    context, _ = make_context(client)
    engine = LLMBrowserEngine(make_source(engine="llm_browser", url=TARGET_URL, retry=1), context)
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        run(engine.fetch())
    assert classify_exception(excinfo.value) == "http_500"


# ---------------------------------------------------------------------------
# 硬护栏:白名单 / 调用次数熔断 / token 预算
# ---------------------------------------------------------------------------


def test_whitelist_refuses_sources_not_explicit_or_auto():
    """单源白名单:非 llm_browser/auto 的源直接拒绝,零调用(防御纵深)."""
    client = make_client(make_handler(lambda r: pytest.fail("白名单拒绝时不应有请求")))
    context, _ = make_context(client)
    engine = LLMBrowserEngine(make_raw_source(engine="firecrawl", url=TARGET_URL), context)
    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "engine_not_whitelisted"


def test_call_cap_circuit_breaks_within_one_source():
    """调用次数熔断:模板翻页目标数超 max_calls -> 第二次调用前结构化熔断."""
    fake = FakeSkyvern()
    engine, _, _ = make_llm_parts(
        fake=fake,
        url=f"{TARGET_URL}?page={{page}}",
        pagination={"mode": "template", "max_pages": 2},
        engine_options={"llm_browser": {"max_calls": 1}},
    )
    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "call_budget_exhausted"
    assert "max_calls" in str(excinfo.value)
    assert len(fake.create_calls) == 1  # 只打满 1 个名额,第二个目标未发起


def test_call_cap_shared_across_sources_resets_per_run():
    """熔断计数是 run 级:同 context 的第二个源被拒;新 context(新 run)重置."""
    first_engine, shared_context, first_fake = make_llm_parts(
        engine_options={"llm_browser": {"max_calls": 1}}
    )
    run(first_engine.fetch())
    assert first_fake.create_calls
    assert shared_context._llm_browser_run_guard.calls == 1

    second_engine = llm_on_context(shared_context)
    with pytest.raises(FetchError) as excinfo:
        run(second_engine.fetch())
    assert excinfo.value.error_type == "call_budget_exhausted"

    fresh_engine, fresh_context, _fresh_fake = make_llm_parts()  # 新 run:名额重置
    items = run(fresh_engine.fetch())
    assert isinstance(items, list)
    assert fresh_context._llm_browser_run_guard.calls == 1


def test_strictest_caps_win_when_sources_disagree():
    """后到源只能收紧上限(最严者胜,与限速器合并约定一致)."""
    fake = FakeSkyvern()
    client = make_client(make_handler(fake.handler))
    context, _ = make_context(client)
    loose = LLMBrowserEngine(
        make_source(
            engine="llm_browser",
            url=f"{TARGET_URL}?page={{page}}",
            pagination={"mode": "template", "max_pages": 2},
            retry=0,
            engine_options={"llm_browser": {"max_calls": 2}},
        ),
        context,
    )
    run(loose.fetch())  # 两次调用打满名额 2
    strict = LLMBrowserEngine(
        make_source(
            engine="llm_browser",
            url=TARGET_URL,
            retry=0,
            engine_options={"llm_browser": {"max_calls": 1}},
        ),
        context,
    )
    with pytest.raises(FetchError) as excinfo:
        run(strict.fetch())
    assert excinfo.value.error_type == "call_budget_exhausted"


def test_budget_exhausted_blocks_next_call_and_spends_reported_usage():
    """token 预算:skyvern 上报 step_count 入账;越过上限后下一调用前拒绝."""
    fake = FakeSkyvern(
        poll_payloads=[
            {"run_id": "tsk_1", "status": "completed", "step_count": 4, "output": None}
        ]
    )
    engine, context, _ = make_llm_parts(
        fake=fake,
        url=f"{TARGET_URL}?page={{page}}",
        pagination={"mode": "template", "max_pages": 2},
        engine_options={"llm_browser": {"budget_per_run": 2}},
    )
    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "budget_exhausted"
    assert "budget_per_run=2" in str(excinfo.value)
    guard = context._llm_browser_run_guard
    assert guard.calls == 1  # 只完成了一次调用
    assert guard.budget.used == 4 and guard.budget.overshoot == 2  # 单请求内越顶可见


def test_usage_priority_tokens_then_cost_then_steps():
    """用量入账顺序:usage.total_tokens -> total_cost -> step_count -> 未上报 None."""
    assert observed_usage({"usage": {"total_tokens": 120}, "total_cost": 3, "step_count": 9}) == 120
    assert observed_usage({"total_cost": 2.5, "step_count": 9}) == 2
    assert observed_usage({"step_count": 7}) == 7
    assert observed_usage({"status": "completed"}) is None
    assert observed_usage({"usage": {"total_tokens": True}}) is None  # bool 不是用量


def test_auto_source_is_whitelisted_at_chain_tail():
    """engine: auto 的源触达本引擎即链尾 —— 白名单放行(auto 在名单内)."""
    engine, _context, fake = make_llm_parts()
    engine.source = make_source(engine="auto", url=TARGET_URL, retry=0)
    items = run(engine.fetch())
    assert isinstance(items, list)
    assert fake.create_calls


# ---------------------------------------------------------------------------
# 礼貌原语与代理边界
# ---------------------------------------------------------------------------


def test_robots_disallowed_costs_no_skyvern_call():
    """robots 拒绝:零 skyvern 调用(礼貌先于花钱)."""
    fake = FakeSkyvern()
    client = make_client(
        make_handler(lambda r: pytest.fail("robots 拒绝时不应有任何请求"), robots="User-agent: *\nDisallow: /")
    )
    context, _ = make_context(client)
    engine = LLMBrowserEngine(make_source(engine="llm_browser", url=TARGET_URL, retry=0), context)
    with pytest.raises(RobotsDisallowedError):
        run(engine.fetch())
    assert fake.create_calls == []


def test_pool_proxy_untranslatable_is_logged_not_silent(monkeypatch, caplog):
    """pool 代理透传不了 skyvern:WARNING 可见(显式代理意图不静默丢弃),调用照常."""
    fake = FakeSkyvern()
    real_client = httpx.AsyncClient

    def pool_client_factory(**kwargs):
        kwargs.pop("proxy", None)  # MockTransport 与 proxy= 互斥
        kwargs.setdefault("transport", httpx.MockTransport(make_handler(fake.handler)))
        return real_client(**kwargs)

    monkeypatch.setattr("myia.engines.fetch_base.httpx.AsyncClient", pool_client_factory)
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    context.proxy_pools = load_proxy_pools({"pools": {"main": "http://proxy.example.com:8080"}})
    engine = LLMBrowserEngine(
        make_source(engine="llm_browser", url=TARGET_URL, retry=0, proxy="pool:main"), context
    )
    with caplog.at_level(logging.WARNING, logger="myia.engines.llm_browser"):
        items = run(engine.fetch())
    assert isinstance(items, list)
    assert any("无法透传 skyvern" in record.message for record in caplog.records)


def test_proxy_location_option_reaches_create_body():
    fake = FakeSkyvern()
    engine, _, _ = make_llm_parts(
        fake=fake, engine_options={"llm_browser": {"proxy_location": "RESIDENTIAL"}}
    )
    run(engine.fetch())
    assert fake.create_calls[0]["body"]["proxy_location"] == "RESIDENTIAL"


# ---------------------------------------------------------------------------
# auto 链七层终测(yaml-schema 规则 5):L1→…→L6,注入假引擎
# ---------------------------------------------------------------------------

CHAIN = (
    "direct_api",
    "static_html",
    "crawl4ai",
    "firecrawl",
    "scrapling",
    "stealth_browser",
    "llm_browser",
)


def make_fail_engine(name: str, error_type: str) -> type[BaseEngine]:
    """Stand-in engine failing with a distinct structured error class."""

    class FailEngine(BaseEngine):
        LAYER = "fail"
        ENGINE_NAME = name
        REQUIRES_EXTRACT = False
        SUPPORTED_EXTRACT_TYPES = ("list", "item", "json_path")

        async def _fetch_impl(self) -> list[dict]:
            raise FetchError(f"{name} 挂了", error_type=error_type)

    return FailEngine


def install_fail_engines(monkeypatch: pytest.MonkeyPatch, spec: dict[str, str]) -> None:
    for name, error_type in spec.items():
        fail_cls = make_fail_engine(name, error_type)
        monkeypatch.setitem(registry.ENGINE_REGISTRY, name, lambda cls=fail_cls: cls)


def test_auto_chain_full_seven_layers():
    """链终形态断言:auto 全七层;链尾 llm_browser 不再降级;排期表清空."""
    assert registry.AUTO_CHAIN == CHAIN
    assert auto_degrade("auto") == list(CHAIN)
    assert auto_degrade("direct_api") == list(CHAIN)
    assert auto_degrade("crawl4ai") == list(CHAIN[2:])
    assert auto_degrade("stealth_browser") == ["stealth_browser", "llm_browser"]
    assert auto_degrade("llm_browser") == ["llm_browser"]  # 链尾无下一层
    assert registry.ENGINE_SCHEDULED_VERSIONS == {}  # 全部引擎已实装


def test_registry_resolves_llm_engine():
    assert resolve_engine("llm_browser") is LLMBrowserEngine
    assert LLMBrowserEngine.LAYER == "L6"
    assert LLMBrowserEngine.ENGINE_NAME == "llm_browser"


def test_auto_chain_first_six_fail_llm_browser_rescues(monkeypatch, engine_store):
    """七层终测:L1-L2 无 extract 拒载 + 四层假引擎失败 → llm_browser(mock
    skyvern)链尾兜底成功,hint 回写."""
    install_fail_engines(
        monkeypatch,
        {
            "crawl4ai": "crawl4ai_down",
            "firecrawl": "firecrawl_down",
            "scrapling": "scrapling_down",
            "stealth_browser": "stealth_down",
        },
    )
    fake = FakeSkyvern(
        poll_payloads=[
            {
                "run_id": "tsk_1",
                "status": "completed",
                "output": [{"title": "兜底条目", "url": "https://form.example/last"}],
            }
        ]
    )
    client = make_client(make_handler(fake.handler))
    source = make_source(engine="auto", url=TARGET_URL, retry=0)  # 无 extract
    context, _ = make_context(client, store=engine_store)

    outcome = run(fetch_source(source, context))

    assert outcome.engine == "llm_browser"
    assert outcome.items == [  # 无 extract -> 自动结构化兜底,与 L3/L4/L5 同形
        {"url": "https://form.example/last", "title": "兜底条目", "content": ""}
    ]
    assert [failure.error_type for failure in outcome.failures] == [
        "extract_required",  # L1 需要 extract(无 extract 即拒)
        "extract_required",  # L2 同样需要 extract
        "crawl4ai_down",
        "firecrawl_down",
        "scrapling_down",
        "stealth_down",
    ]
    assert engine_store.get_engine_hint(TARGET_URL) == "llm_browser"  # 成功选择回写 hint
    assert DEFAULT_MAX_CALLS_PER_RUN < 10  # 硬护栏缺省严于 L5 页面预算(10)


def test_llm_browser_failure_terminates_chain_structured(monkeypatch, engine_store):
    """链尾终止语义:llm_browser 也失败 -> 链耗尽,结构化上报,无「下一层」."""
    install_fail_engines(
        monkeypatch,
        {
            "crawl4ai": "crawl4ai_down",
            "firecrawl": "firecrawl_down",
            "scrapling": "scrapling_down",
            "stealth_browser": "stealth_down",
        },
    )

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == CREATE_PATH:
            return httpx.Response(503, text="skyvern quota exceeded")
        raise AssertionError("create 失败后不应轮询")  # pragma: no cover

    client = make_client(make_handler(responder))
    source = make_source(engine="auto", url=TARGET_URL, retry=0)
    context, _ = make_context(client, store=engine_store)

    outcome = run(fetch_source(source, context))  # 链尾失败不抛,结构化上报

    assert outcome.engine is None
    assert outcome.items == []
    assert [(f.engine, f.error_type) for f in outcome.failures] == [
        ("direct_api", "extract_required"),
        ("static_html", "extract_required"),
        ("crawl4ai", "crawl4ai_down"),
        ("firecrawl", "firecrawl_down"),
        ("scrapling", "scrapling_down"),
        ("stealth_browser", "stealth_down"),
        ("llm_browser", "http_503"),  # 链尾:最后一次尝试,之后链条自然终止
    ]
    assert engine_store.get_engine_hint(TARGET_URL) is None  # 全败不写 hint


def test_explicit_llm_browser_source_runs_alone_and_writes_hint(engine_store):
    """显式 engine: llm_browser:单引擎链(不降级),成功后 hint 回写."""
    fake = FakeSkyvern(
        poll_payloads=[
            {
                "run_id": "tsk_1",
                "status": "completed",
                "output": [{"title": "显式源条目", "url": "https://form.example/1"}],
            }
        ]
    )
    client = make_client(make_handler(fake.handler))
    source = make_source(engine="llm_browser", url=TARGET_URL, retry=0)
    context, _ = make_context(client, store=engine_store)

    outcome = run(fetch_source(source, context))

    assert outcome.engine == "llm_browser"
    assert outcome.failures == []
    assert engine_store.get_engine_hint(TARGET_URL) == "llm_browser"


def test_auto_hint_llm_browser_first_then_cleared_and_chain_walks_rest(monkeypatch, engine_store):
    """hint=llm_browser 失败:hint 清除,链按 canonical 序走完其余六层(hint 重排
    已把 llm_browser 提到链首,本轮不再重试第二次 —— registry 既有 hint 语义)."""
    install_fail_engines(
        monkeypatch,
        {
            "crawl4ai": "crawl4ai_down",
            "firecrawl": "firecrawl_down",
            "scrapling": "scrapling_down",
            "stealth_browser": "stealth_down",
        },
    )
    engine_store.set_engine_hint(TARGET_URL, "llm_browser")

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == CREATE_PATH:
            return httpx.Response(500, text="skyvern down")
        raise AssertionError("create 失败后不应轮询")  # pragma: no cover

    client = make_client(make_handler(responder))
    source = make_source(engine="auto", url=TARGET_URL, retry=0)
    context, _ = make_context(client, store=engine_store)

    outcome = run(fetch_source(source, context))

    assert outcome.engine is None
    assert [(f.engine, f.error_type) for f in outcome.failures] == [
        ("llm_browser", "http_500"),  # hint 先行(链首)
        ("direct_api", "extract_required"),
        ("static_html", "extract_required"),
        ("crawl4ai", "crawl4ai_down"),
        ("firecrawl", "firecrawl_down"),
        ("scrapling", "scrapling_down"),
        ("stealth_browser", "stealth_down"),
    ]
    assert engine_store.get_engine_hint(TARGET_URL) is None  # 失效清除


def test_default_guard_values_are_stricter_than_l5():
    """硬护栏缺省:调用次数 3 < L5 页面预算 10;轮询参数量级合理."""
    assert DEFAULT_MAX_CALLS_PER_RUN < 10
    assert DEFAULT_BUDGET_PER_RUN > 0
    assert DEFAULT_RUN_TIMEOUT_SECONDS > DEFAULT_POLL_INTERVAL_SECONDS
