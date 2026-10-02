"""Tests for the L1 direct_api engine (PRD 10-01-v01-engine-l1-l2).

Covers: json_path extraction / {symbol}+{page} URL template expansion /
missing template param / max_pages cap / key-pool rotation (401 swap +
round-robin) / json decode error / POST body passthrough / change-fingerprint
skip on the second run / 304 negotiation / extract config validation.

All I/O runs on httpx.MockTransport; all waiting is recorded by FakeClock.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from myia.engines.direct_api import DirectAPIEngine
from myia.engines.fetch_base import FetchError, extract_json

from conftest import make_client, make_context, make_handler, make_source, run


def json_source(url: str, **overrides):
    fields = overrides.pop("fields", {"title": "$.data[*].title", "url": "$.data[*].url"})
    return make_source(url=url, extract={"type": "json_path", "fields": fields}, **overrides)


def api_payload(page: int | None = None) -> dict:
    suffix = "" if page is None else f"-{page}"
    return {
        "data": [
            {"title": f"公开论坛标题{suffix}", "url": f"https://example.com/t{suffix}"},
            {"title": f"AI 资讯{suffix}", "url": f"https://example.com/n{suffix}"},
        ]
    }


def test_json_path_extract_basic():
    client = make_client(make_handler(lambda r: httpx.Response(200, json=api_payload())))
    source = json_source("https://example.com/api")
    context, _ = make_context(client)
    engine = DirectAPIEngine(source, context)

    items = run(engine.fetch())
    assert items == [
        {"title": "公开论坛标题", "url": "https://example.com/t"},
        {"title": "AI 资讯", "url": "https://example.com/n"},
    ]


def test_extract_json_missing_field_keeps_element_alignment():
    """数组中段元素缺字段:按元素对齐,缺字段的条目只省略该字段。

    Regression: the index-zip shifted every later record by one (t3 配上 u2
    的静默数据污染);missing fields are omitted per element now.
    """
    data = {
        "data": [
            {"title": "t1", "url": "u1"},
            {"url": "u2"},  # 缺 title —— 中段缺字段是真实 API 常态
            {"title": "t3", "url": "u3"},
        ]
    }
    source = json_source("https://example.com/api")
    items = extract_json(data, source.extract)
    assert items == [
        {"title": "t1", "url": "u1"},
        {"url": "u2"},
        {"title": "t3", "url": "u3"},
    ]


def test_extract_json_scalar_paths_keep_index_zip():
    """无 [*] 的标量路径(如 $.chart.result[0].meta.*)保持原 index-zip 语义。"""
    data = {"chart": {"result": [{"meta": {"symbol": "NVDA", "price": 1.0}}]}}
    source = make_source(
        url="https://example.com/chart/{symbol}",
        extract={
            "type": "json_path",
            "fields": {
                "url": "$.chart.result[0].meta.symbol",
                "price": "$.chart.result[0].meta.price",
            },
        },
        symbols=["NVDA"],
    )
    assert extract_json(data, source.extract) == [{"url": "NVDA", "price": 1.0}]


def test_url_template_symbol_and_page_expansion():
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path + "?" + request.url.query.decode())
        return httpx.Response(200, json=api_payload())

    client = make_client(make_handler(responder))
    source = json_source(
        "https://example.com/chart/{symbol}?range=5d&page={page}",
        symbols=["NVDA", "AAPL"],
        pagination={"mode": "template", "max_pages": 2},
        rate_limit={"qps": 1000.0},
    )
    context, _ = make_context(client)
    engine = DirectAPIEngine(source, context)

    items = run(engine.fetch())
    assert len(items) == 8  # 2 symbols x 2 pages x 2 entries
    assert sorted(seen) == [
        "/chart/AAPL?range=5d&page=1",
        "/chart/AAPL?range=5d&page=2",
        "/chart/NVDA?range=5d&page=1",
        "/chart/NVDA?range=5d&page=2",
    ]


def test_missing_template_param_structured_error():
    client = make_client(make_handler(lambda r: httpx.Response(200, json=api_payload())))
    source = json_source("https://example.com/chart/{symbol}")
    context, _ = make_context(client)
    engine = DirectAPIEngine(source, context)

    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "missing_template_param"


def test_pagination_max_pages_cap():
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["page"])
        return httpx.Response(200, json=api_payload())

    client = make_client(make_handler(responder))
    source = json_source(
        "https://example.com/api?page={page}",
        pagination={"mode": "template", "max_pages": 3},
        rate_limit={"qps": 1000.0},
    )
    context, _ = make_context(client)
    engine = DirectAPIEngine(source, context)

    run(engine.fetch())
    assert seen == ["1", "2", "3"]  # max_pages 封顶


def test_key_pool_rotation_on_401(monkeypatch):
    monkeypatch.setenv("MYIA_KEY_A", "key-a")
    monkeypatch.setenv("MYIA_KEY_B", "key-b")
    auth_seen: list[str | None] = []
    rejected = {"first": True}

    def responder(request: httpx.Request) -> httpx.Response:
        auth_seen.append(request.headers.get("Authorization"))
        if rejected["first"]:
            rejected["first"] = False
            return httpx.Response(401, text="unauthorized")
        return httpx.Response(200, json=api_payload())

    client = make_client(make_handler(responder))
    source = json_source(
        "https://example.com/api",
        engine_options={"direct_api": {"api_keys": ["Bearer env:MYIA_KEY_A", "Bearer env:MYIA_KEY_B"]}},
        rate_limit={"qps": 1000.0},
    )
    context, _ = make_context(client)
    engine = DirectAPIEngine(source, context)

    items = run(engine.fetch())
    assert len(items) == 2
    assert auth_seen == ["Bearer key-a", "Bearer key-b"]  # 401 后轮换到下一把


def test_key_pool_round_robin_across_requests(monkeypatch):
    monkeypatch.setenv("MYIA_KEY_A", "key-a")
    monkeypatch.setenv("MYIA_KEY_B", "key-b")
    auth_seen: list[str | None] = []

    def responder(request: httpx.Request) -> httpx.Response:
        auth_seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json=api_payload())

    client = make_client(make_handler(responder))
    source = json_source(
        "https://example.com/api?page={page}",
        engine_options={"direct_api": {"api_keys": ["Bearer env:MYIA_KEY_A", "Bearer env:MYIA_KEY_B"]}},
        pagination={"mode": "template", "max_pages": 2},
        rate_limit={"qps": 1000.0},
    )
    context, _ = make_context(client)
    engine = DirectAPIEngine(source, context)

    run(engine.fetch())
    assert auth_seen == ["Bearer key-a", "Bearer key-b"]  # 逐请求轮换


def test_key_pool_env_missing_is_structured(monkeypatch):
    monkeypatch.delenv("MYIA_KEY_MISSING", raising=False)
    source = json_source(
        "https://example.com/api",
        engine_options={"direct_api": {"api_keys": ["env:MYIA_KEY_MISSING"]}},
    )
    client = make_client(make_handler(lambda r: httpx.Response(200, json=api_payload())))
    context, _ = make_context(client)
    from myia.schema import CredentialResolveError

    with pytest.raises(CredentialResolveError) as excinfo:
        DirectAPIEngine(source, context)
    assert excinfo.value.code == "env_var_missing"


def test_json_decode_error_is_structured():
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>", headers={"content-type": "text/html"})

    client = make_client(make_handler(responder))
    source = json_source("https://example.com/api")
    context, _ = make_context(client)
    engine = DirectAPIEngine(source, context)

    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "json_decode"


def test_empty_page_stops_template_walk():
    seen: list[int] = []

    def responder(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        seen.append(page)
        if page == 1:
            return httpx.Response(200, json=api_payload())
        return httpx.Response(200, json={"data": []})  # 空页提前收尾

    client = make_client(make_handler(responder))
    source = json_source(
        "https://example.com/api?page={page}",
        pagination={"mode": "template", "max_pages": 5},
        rate_limit={"qps": 1000.0},
    )
    context, _ = make_context(client)
    engine = DirectAPIEngine(source, context)

    items = run(engine.fetch())
    assert seen == [1, 2]  # 空页后不再抓 page 3-5
    assert len(items) == 2


def test_fanout_empty_extract_on_one_symbol_does_not_truncate_rest():
    """扇出 walk 相互独立:一个 symbol 提取 0 条只收尾自己的 walk。

    Regression: the early-stop break ran over the flat URL list, so one
    symbol's empty extract (e.g. Yahoo 的 {"chart":{"result":null}}) silently
    truncated every later symbol while the run still reported success.
    """
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        symbol = request.url.path.rsplit("/", 1)[-1]
        seen.append(symbol)
        if symbol == "AAPL":
            return httpx.Response(200, json={"chart": {"result": None}})
        return httpx.Response(200, json=api_payload())

    client = make_client(make_handler(responder))
    source = json_source(
        "https://example.com/chart/{symbol}",
        symbols=["NVDA", "AAPL", "TSLA"],
        rate_limit={"qps": 1000.0},
    )
    context, _ = make_context(client)
    engine = DirectAPIEngine(source, context)

    items = run(engine.fetch())
    assert seen == ["NVDA", "AAPL", "TSLA"]  # AAPL 空提取不得截断 TSLA
    assert len(items) == 4


def test_fanout_304_on_first_symbol_does_not_truncate_rest(engine_store):
    """扇出 walk 相互独立:首个 symbol 304 只跳过自己,其余照常抓取。"""
    engine_store.set_baseline("https://example.com/chart/NVDA", etag="v1")
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        symbol = request.url.path.rsplit("/", 1)[-1]
        seen.append(symbol)
        if symbol == "NVDA":
            return httpx.Response(304, headers={"ETag": "v1"})
        return httpx.Response(200, json=api_payload())

    client = make_client(make_handler(responder))
    source = json_source(
        "https://example.com/chart/{symbol}",
        symbols=["NVDA", "AAPL"],
        rate_limit={"qps": 1000.0},
    )
    context, _ = make_context(client, store=engine_store)
    engine = DirectAPIEngine(source, context)

    items = run(engine.fetch())
    assert seen == ["NVDA", "AAPL"]
    assert len(items) == 2
    assert engine.last_skip_reason == "not_modified"


def test_change_unchanged_second_run_skips(engine_store):
    calls = {"data": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["data"] += 1
        return httpx.Response(200, json=api_payload())

    client = make_client(make_handler(responder))
    context, _ = make_context(client, store=engine_store)
    source = json_source("https://example.com/api")

    first = run(DirectAPIEngine(source, context).fetch())
    assert len(first) == 2
    assert calls["data"] == 1

    engine = DirectAPIEngine(source, context)
    second = run(engine.fetch())
    assert second == []  # 正文哈希兜底命中
    assert calls["data"] == 2  # 每轮各 1 次数据请求:请求先行,指纹命中后跳过的是提取
    assert engine.last_skip_reason == "hash_match"


def test_etag_conditional_request_and_304_skip(engine_store):
    engine_store.set_baseline("https://example.com/api", etag="v1")
    conditional: list[str | None] = []

    def responder(request: httpx.Request) -> httpx.Response:
        conditional.append(request.headers.get("If-None-Match"))
        return httpx.Response(304, headers={"ETag": "v1"})

    client = make_client(make_handler(responder))
    context, _ = make_context(client, store=engine_store)
    source = json_source("https://example.com/api")
    engine = DirectAPIEngine(source, context)

    items = run(engine.fetch())
    assert conditional == ["v1"]  # ETag 协商缓存
    assert items == []
    assert engine.last_skip_reason == "not_modified"


def test_post_body_sent_as_json():
    bodies: list[dict] = []

    def responder(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=api_payload())

    client = make_client(make_handler(responder))
    source = json_source(
        "https://example.com/search",
        method="POST",
        post_body={"query": "显卡", "page": 1},
    )
    context, _ = make_context(client)
    engine = DirectAPIEngine(source, context)

    run(engine.fetch())
    assert bodies == [{"query": "显卡", "page": 1}]


def test_fetch_without_extract_is_structured_error():
    source = make_source(engine="direct_api")  # 无 extract 节
    client = make_client(make_handler(lambda r: httpx.Response(200, json=api_payload())))
    context, _ = make_context(client)
    engine = DirectAPIEngine(source, context)

    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "extract_required"


def test_fetch_with_css_extract_degrades_via_structured_error():
    source = make_source(
        engine="direct_api",
        extract={"type": "list", "item": "div.item", "fields": {"url": "a@href"}},
    )
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json=api_payload())

    client = make_client(make_handler(responder))
    context, _ = make_context(client)
    engine = DirectAPIEngine(source, context)

    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "extract_unsupported"
    assert calls["count"] == 0  # 配置形状不匹配,不浪费一次抓取


# ---------------------------------------------------------------------------
# 真实源 smoke(PRD 10-01-v01-engine-l1-l2):设 MYIA_SMOKE_REAL=1 本地可选跑,
# CI 不依赖外网。凭据零入库、零明文(Yahoo chart 公开端点无需凭据)。
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("MYIA_SMOKE_REAL") != "1",
    reason="真实源 smoke:Yahoo chart API(L1)产出结构化条目(设 MYIA_SMOKE_REAL=1 本地执行)",
)
def test_smoke_yahoo_chart_api_l1():
    from myia.engines.fetch_base import FetchContext

    async def scenario():
        client = httpx.AsyncClient(timeout=30.0)
        context = FetchContext(client=client)
        source = json_source(
            "https://query1.finance.yahoo.com/v8/finance/chart/NVDA?range=5d&interval=1d",
            fields={"title": "$.chart.result[0].meta.symbol", "url": "$.chart.result[0].meta.symbol"},
        )
        engine = DirectAPIEngine(source, context)
        try:
            return await engine.fetch()
        finally:
            await client.aclose()

    items = run(scenario())
    assert items and items[0]["title"] == "NVDA"  # L1 产出结构化条目


@pytest.mark.skipif(
    os.environ.get("MYIA_SMOKE_REAL") != "1",
    reason="真实源 smoke:aihot.news(L2)产出结构化条目(设 MYIA_SMOKE_REAL=1 本地执行)",
)
def test_smoke_aihot_news_l2():
    from myia.engines.static_html import StaticHTMLEngine
    from myia.engines.fetch_base import FetchContext

    async def scenario():
        client = httpx.AsyncClient(timeout=30.0)
        context = FetchContext(client=client)
        source = make_source(
            url="https://aihot.news/",
            extract={
                "type": "list",
                "item": "a[href]",
                "fields": {"title": "a", "url": "a@href"},
            },
        )
        engine = StaticHTMLEngine(source, context)
        try:
            return await engine.fetch()
        finally:
            await client.aclose()

    items = run(scenario())
    assert len(items) > 0  # L2 产出结构化条目(gb18030/编码与选择器在单测覆盖)
