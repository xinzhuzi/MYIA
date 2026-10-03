"""Tests for the shared fetch base (PRD 10-01-v01-fetch-base, 10-01-v02-proxy-transport).

Covers the acceptance list: same-host rate-limit merge (two sources, one
limiter, strictest qps) / 429 exponential backoff rhythm / ETag hit -> unchanged
/ body-hash fallback / env: expansion / noncanonical keychain name refusal /
robots violation skip / proxy grammar (direct effective; pool accepted since
v0.2 with pools resolved at fetch time; residential structured
not-implemented) — plus gb18030 decoding, retry budgets (permanent errors fire
exactly once) and 304 baseline preservation.

All I/O runs on httpx.MockTransport; all waiting is recorded by FakeClock.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from myia.engines.fetch_base import (
    ChangeDetector,
    ChangeVerdict,
    HostLimiterRegistry,
    ProxyConfigError,
    ProxyNotSupportedError,
    RateLimiter,
    RobotsDisallowedError,
    BaseEngine,
    classify_exception,
    content_hash,
    decode_response,
    normalize_text,
    resolve_headers,
    resolve_proxy,
)
from myia.schema import CredentialResolveError, RateLimitConfig
from myia.engines.direct_api import DirectAPIEngine

from conftest import make_client, make_context, make_handler, make_source, run


def response_for(url: str, **kwargs) -> httpx.Response:
    return httpx.Response(200, request=httpx.Request("GET", url), **kwargs)


# ------------------------------------------------------- same-host limiter merge


def test_limiter_registry_same_host_shares_one_limiter():
    registry = HostLimiterRegistry()
    first = registry.limiter_for("example.com", RateLimitConfig(qps=1.0))
    second = registry.limiter_for("example.com", RateLimitConfig(qps=0.5))
    assert first is second
    assert second.qps == 0.5  # 同域合并取最严 qps


def test_limiter_registry_different_hosts_are_independent():
    registry = HostLimiterRegistry()
    first = registry.limiter_for("a.example.com", RateLimitConfig(qps=1.0))
    second = registry.limiter_for("b.example.com", RateLimitConfig(qps=1.0))
    assert first is not second


def test_rate_limiter_second_acquire_waits_min_interval():
    from conftest import FakeClock

    clock = FakeClock()
    limiter = RateLimiter(2.0, clock=clock.time, sleeper=clock.sleep)  # interval 0.5s
    run(limiter.acquire())
    waited = run(limiter.acquire())
    assert clock.sleeps == [waited]
    assert waited == pytest.approx(0.5)


def test_rate_limiter_jitter_stays_in_bounds():
    from conftest import FakeClock

    clock = FakeClock()
    limiter = RateLimiter(1000.0, 2.0, clock=clock.time, sleeper=clock.sleep)
    for _ in range(20):
        run(limiter.acquire())
    assert len(clock.sleeps) == 20
    assert all(0.0 <= waited <= 2.0 for waited in clock.sleeps)


def test_rate_limiter_concurrent_acquires_space_out():
    """并发 acquire 各自排到独立槽位,不再同刻齐发(同域合并限速并发下生效)。

    Regression: the pre-fix limiter read ``_last`` after waking, so N
    coroutines arriving together all computed the same wait and bursted
    (实测 5 并发 @5qps 的完成时刻间隔为 [0.2, 0, 0, 0]).
    """
    import asyncio
    from conftest import FakeClock

    clock = FakeClock()

    async def yielding_sleep(seconds: float) -> None:
        """Record the wait AND yield to the loop (lets the next acquirer in)."""
        await asyncio.sleep(0)
        clock.sleeps.append(seconds)
        clock.now += seconds

    limiter = RateLimiter(5.0, clock=clock.time, sleeper=yielding_sleep)  # interval 0.2s
    waits: list[float] = []

    async def acquire_and_record() -> None:
        waits.append(await limiter.acquire())

    async def acquire_all() -> None:
        await asyncio.gather(*(acquire_and_record() for _ in range(5)))

    asyncio.run(acquire_all())
    assert waits == pytest.approx([0.0, 0.2, 0.4, 0.6, 0.8])


# --------------------------------------------------------- retry & backoff


def test_backoff_429_rhythm_exponential_then_raises():
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(429, text="slow down")

    source = make_source(rate_limit={"qps": 100.0}, retry=3)
    client = make_client(make_handler(responder))
    context, clock = make_context(client)
    engine = BaseEngine(source, context)

    with pytest.raises(httpx.HTTPStatusError):
        run(engine.request("https://example.com/data"))
    assert calls["count"] == 4  # 1 initial + retry 3
    assert clock.sleeps == [1.0, 2.0, 4.0]  # exponential rhythm


def test_permanent_404_fires_exactly_once():
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(404, text="gone")

    source = make_source(retry=3)
    client = make_client(make_handler(responder))
    context, clock = make_context(client)
    engine = BaseEngine(source, context)

    with pytest.raises(httpx.HTTPStatusError):
        run(engine.request("https://example.com/data"))
    assert calls["count"] == 1  # 永久性错误不重试
    assert clock.sleeps == []


def test_transport_error_retries_then_succeeds():
    calls = {"count": 0}
    failures = {"remaining": 2}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if failures["remaining"] > 0:
            failures["remaining"] -= 1
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"ok": True})

    source = make_source(retry=3)
    client = make_client(make_handler(responder))
    context, clock = make_context(client)
    engine = BaseEngine(source, context)

    response = run(engine.request("https://example.com/data"))
    assert response.status_code == 200
    assert calls["count"] == 3
    assert clock.sleeps == [1.0, 2.0]


def test_transport_error_exhausts_retry_budget():
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ConnectError("connection refused")

    source = make_source(retry=2)
    client = make_client(make_handler(responder))
    context, _ = make_context(client)
    engine = BaseEngine(source, context)

    with pytest.raises(httpx.ConnectError):
        run(engine.request("https://example.com/data"))
    assert calls["count"] == 3  # 1 initial + retry 2
    assert classify_exception(httpx.ConnectError("x")) == "network"


# ----------------------------------------------------- change fingerprint


def test_change_etag_match_returns_unchanged(engine_store):
    url = "https://example.com/doc"
    engine_store.set_baseline(url, etag="v1", content_hash="old")
    detector = ChangeDetector(engine_store)
    response = response_for(url, headers={"ETag": "v1"}, text="body")
    verdict = detector.check(url, response, decoded_text="body")
    assert verdict == ChangeVerdict(False, "validators_match")


def test_change_etag_differ_returns_changed(engine_store):
    url = "https://example.com/doc"
    engine_store.set_baseline(url, etag="v1")
    detector = ChangeDetector(engine_store)
    response = response_for(url, headers={"ETag": "v2"}, text="new body")
    assert detector.check(url, response, decoded_text="new body").changed is True


def test_change_hash_fallback_ignores_whitespace_noise(engine_store):
    url = "https://example.com/doc"
    baseline_text = "<div>标题一</div>\n<div>标题二</div>"
    digest = content_hash(response_for(url), decoded_text=baseline_text)
    engine_store.set_baseline(url, content_hash=digest)
    detector = ChangeDetector(engine_store)
    # 模板噪声:仅缩进/空行不同,正文相同 -> unchanged
    noisy = "<div>标题一</div>\n\n  <div>标题二</div>  \n"
    response = response_for(url, text=noisy)
    verdict = detector.check(url, response, decoded_text=noisy)
    assert verdict == ChangeVerdict(False, "hash_match")
    # 正文真变了 -> changed
    response = response_for(url, text="<div>标题三</div>")
    verdict = detector.check(url, response, decoded_text="<div>标题三</div>")
    assert verdict == ChangeVerdict(True, "hash_differ")


def test_change_first_fetch_writes_full_baseline(engine_store):
    url = "https://example.com/doc"
    detector = ChangeDetector(engine_store)
    response = response_for(url, headers={"ETag": "v9", "Last-Modified": "Tue, 01 Oct 2026 00:00:00 GMT"}, text="body")
    verdict = detector.check(url, response, decoded_text="body")
    assert verdict == ChangeVerdict(True, "first_fetch")
    baseline = engine_store.get_baseline(url)
    assert baseline is not None
    assert baseline.etag == "v9"
    assert baseline.last_modified == "Tue, 01 Oct 2026 00:00:00 GMT"
    assert baseline.content_hash == content_hash(response, decoded_text="body")
    assert baseline.last_changed is not None


def test_change_304_preserves_baseline(engine_store):
    url = "https://example.com/doc"
    stamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
    engine_store.set_baseline(url, etag="v1", content_hash="old", last_changed=stamp)
    detector = ChangeDetector(engine_store)
    response = httpx.Response(304, headers={"ETag": "v2"}, request=httpx.Request("GET", url))
    verdict = detector.check(url, response)
    assert verdict == ChangeVerdict(False, "not_modified")
    baseline = engine_store.get_baseline(url)
    assert baseline.etag == "v1"  # 304 不回写空正文指纹
    assert baseline.content_hash == "old"
    assert baseline.last_changed == stamp


def test_change_without_store_defaults_changed():
    detector = ChangeDetector(None)
    response = response_for("https://example.com/doc", text="body")
    assert detector.check("https://example.com/doc", response, decoded_text="body") == ChangeVerdict(
        True, "first_fetch"
    )


# ------------------------------------------------- credentials & proxy


def test_resolve_headers_env_expansion(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_COOKIE", "cookie-value")
    resolved = resolve_headers(
        {
            "Cookie": "env:MYIA_TEST_COOKIE",
            "Authorization": "Bearer env:MYIA_TEST_COOKIE",
            "User-Agent": "UA/1.0",
        }
    )
    assert resolved["Cookie"] == "cookie-value"
    assert resolved["Authorization"] == "Bearer cookie-value"
    assert resolved["User-Agent"] == "UA/1.0"


def test_resolve_headers_env_missing_is_structured(monkeypatch):
    monkeypatch.delenv("MYIA_TEST_MISSING", raising=False)
    with pytest.raises(CredentialResolveError) as excinfo:
        resolve_headers({"Cookie": "env:MYIA_TEST_MISSING"})
    assert excinfo.value.code == "env_var_missing"


def test_resolve_headers_noncanonical_keychain_name_raises():
    """keychain: 已在 v0.2 实装;非规范扁平名在触碰钥匙链前结构化拒绝。"""
    with pytest.raises(CredentialResolveError) as excinfo:
        resolve_headers({"Cookie": "keychain:linuxsb_cookie"})
    assert excinfo.value.code == "invalid_secret_name"


def test_proxy_direct_passes():
    assert resolve_proxy("direct") is None


def test_proxy_pool_grammar_accepted_since_v02():
    """v0.2(proxy-transport)起 pool:<名称> 语法合法:池在 fetch 时经全局
    ProxyPools 解析,这里只做语法门(resolve_proxy 本身不触网)。"""
    assert resolve_proxy("pool:main") is None


def test_proxy_residential_not_implemented_with_schedule():
    with pytest.raises(ProxyNotSupportedError) as excinfo:
        resolve_proxy("residential:us")
    assert excinfo.value.scheduled_version == "v0.3"


def test_fetch_with_pool_proxy_fails_before_any_network():
    """pool 源在全局 pools 未声明时,任何 I/O 之前结构化拒绝
    (proxy_pools_not_configured;池错误分类见 test_proxy_transport)。"""
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json=[])

    source = make_source(
        proxy="pool:main",
        extract={"type": "json_path", "fields": {"url": "$[*].u"}},
    )
    client = make_client(make_handler(responder))
    context, _ = make_context(client)  # proxy_pools 缺省 None = 未声明
    engine = DirectAPIEngine(source, context)

    with pytest.raises(ProxyConfigError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.code == "proxy_pools_not_configured"
    assert calls["count"] == 0  # 不留半实现:未声明的 pool 在任何 I/O 之前拒绝


# ------------------------------------------------------------- robots


def test_robots_disallowed_url_raises_structured_skip():
    calls = {"data": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["data"] += 1
        return httpx.Response(200, json={"ok": True})

    rules = "User-agent: *\nDisallow: /private/\n"
    source = make_source()  # respect_robots 默认 True
    client = make_client(make_handler(responder, robots=rules))
    context, _ = make_context(client)
    engine = BaseEngine(source, context)

    with pytest.raises(RobotsDisallowedError) as excinfo:
        run(engine.request("https://example.com/private/list"))
    assert excinfo.value.error_type == "robots_disallowed"
    assert calls["data"] == 0  # 违规源跳过,不发起抓取

    response = run(engine.request("https://example.com/public/list"))
    assert response.status_code == 200


def test_robots_missing_fails_open():
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    source = make_source()
    client = make_client(make_handler(responder, robots=None))  # 404 -> 允许
    context, _ = make_context(client)
    engine = BaseEngine(source, context)
    response = run(engine.request("https://example.com/list"))
    assert response.status_code == 200


def test_robots_check_disabled_by_flag():
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    rules = "User-agent: *\nDisallow: /\n"
    source = make_source(rate_limit={"respect_robots": False})
    client = make_client(make_handler(responder, robots=rules))
    context, _ = make_context(client)
    engine = BaseEngine(source, context)
    response = run(engine.request("https://example.com/anything"))
    assert response.status_code == 200


# ------------------------------------------------------------- decoding


def test_decode_gb18030_via_meta_charset():
    body = "<html><head><meta charset=\"gb18030\"></head><body>显卡行情</body></html>".encode("gb18030")
    response = httpx.Response(
        200, headers={"content-type": "text/html"}, content=body,
        request=httpx.Request("GET", "https://example.com/"),
    )
    assert "显卡行情" in decode_response(response)


def test_decode_content_type_charset_wins():
    body = "羊毛情报站".encode("gb18030")
    response = httpx.Response(
        200, headers={"content-type": "text/html; charset=gbk"}, content=body,
        request=httpx.Request("GET", "https://example.com/"),
    )
    assert decode_response(response) == "羊毛情报站"


def test_decode_utf8_default():
    body = "AI 资讯".encode("utf-8")
    response = httpx.Response(
        200, headers={"content-type": "text/html"}, content=body,
        request=httpx.Request("GET", "https://example.com/"),
    )
    assert decode_response(response) == "AI 资讯"


def test_decode_undecodable_never_raises():
    response = httpx.Response(
        200, headers={"content-type": "text/html"}, content=b"caf\xe9 \xa0\x81",
        request=httpx.Request("GET", "https://example.com/"),
    )
    decoded = decode_response(response)  # 最终 replace 解码,不抛异常
    assert isinstance(decoded, str)


def test_decode_big5_declared_charset():
    body = "顯卡降價了".encode("big5")
    response = httpx.Response(
        200, headers={"content-type": "text/html; charset=big5"}, content=body,
        request=httpx.Request("GET", "https://example.com/"),
    )
    assert decode_response(response) == "顯卡降價了"


def test_decode_big5_undeclared_reaches_big5_fallback():
    """无 charset 声明的 big5 页面不再被 gb18030 静默解码成乱码。

    Regression: gb18030 strict-decodes nearly every big5 byte string (laced
    with PUA junk), so the big5 fallback branch was unreachable and legacy
    Big5 forums silently produced mojibake. The PUA signature now rejects the
    wrong candidate and the big5 branch decodes cleanly.
    """
    body = "<html><body><div>顯卡降價了</div></body></html>".encode("big5")
    response = httpx.Response(
        200, headers={"content-type": "text/html"}, content=body,
        request=httpx.Request("GET", "https://example.com/"),
    )
    assert "顯卡降價了" in decode_response(response)


def test_decode_gb18030_undeclared_still_wins_for_gb18030_bytes():
    """PUA 启发式不伤正常 gb18030 无声明页面(无 PUA 字符,gb18030 候选照常命中)。"""
    body = "显卡行情速报".encode("gb18030")
    response = httpx.Response(
        200, headers={"content-type": "text/html"}, content=body,
        request=httpx.Request("GET", "https://example.com/"),
    )
    assert "显卡行情速报" in decode_response(response)


def test_normalize_text_collapses_template_noise():
    assert normalize_text("行一\n  行二  \n\n\n行三") == "行一\n行二\n行三"


def test_content_hash_raw_bytes_when_no_text():
    first = content_hash(response_for("https://example.com/a", content=b"raw-bytes"))
    second = content_hash(response_for("https://example.com/b", content=b"raw-bytes"))
    third = content_hash(response_for("https://example.com/c", content=b"other"))
    assert first == second
    assert first != third
    assert normalize_text("x")  # keep import used


# ------------------------------------------------- extract.url_template(D1, 10-03-games)


def test_extract_json_renders_url_template_from_item_fields():
    """字段齐 → URL 拼对;int 字段(steam_id 形态)渲染成字符串。"""
    from myia.engines.fetch_base import extract_json
    from myia.schema import ExtractConfig

    data = {"apps": [
        {"name": "The Outlast Trials", "id": 1593500, "final_price": 1360},
        {"name": "深埋之星", "id": 1130, "final_price": 0},
    ]}
    extract = ExtractConfig(
        type="json_path",
        url_template="https://store.steampowered.com/app/{steam_id}",
        fields={
            "title": "$.apps[*].name",
            "steam_id": "$.apps[*].id",
            "final_price": "$.apps[*].final_price",
        },
    )
    items = extract_json(data, extract)
    assert [item["url"] for item in items] == [
        "https://store.steampowered.com/app/1593500",  # int → str
        "https://store.steampowered.com/app/1130",
    ]
    assert items[0]["title"] == "The Outlast Trials"


def test_extract_json_missing_placeholder_field_yields_empty_url():
    """逐条目占位缺「值」(如 Epic urlSlug=null 元素)→ url 置空串、提取层
    不抛其余条目照常;该空 url 条目随后在管线 fetch 阶段被记 invalid_item
    丢弃(不带坏链接入库——design R1 修正口径)。注意与装载期交叉校验的
    分工:字段名拼错在 schema 装载期即拒,到不了这里。"""
    from myia.engines.fetch_base import extract_json
    from myia.schema import ExtractConfig

    data = {"elements": [
        {"title": "无 slug 条目"},  # urlSlug 为 None 的元素(实测 Epic 形态)
        {"title": "有 slug 条目", "urlSlug": "buried-stars"},
    ]}
    extract = ExtractConfig(
        type="json_path",
        url_template="https://store.epicgames.com/zh-CN/p/{url_slug}",
        fields={
            "title": "$.elements[*].title",
            "url_slug": "$.elements[*].urlSlug",
        },
    )
    items = extract_json(data, extract)
    assert [item["url"] for item in items] == [
        "",  # 占位缺值 → 空串(提取层保留;管线随后按 invalid_item 拒掉该条)
        "https://store.epicgames.com/zh-CN/p/buried-stars",
    ]


def test_extract_json_url_field_value_wins_over_template():
    """都有 = url 字段值胜出,模板静默不用(schema/implement 步骤 1 同款定死)。"""
    from myia.engines.fetch_base import extract_json
    from myia.schema import ExtractConfig

    data = {"apps": [{"name": "以 symbol 为稳定键的 API", "symbol": "NVDA"}]}
    extract = ExtractConfig(
        type="json_path",
        url_template="https://example.com/app/{symbol}",
        fields={"title": "$.apps[*].name", "url": "$.apps[*].symbol", "symbol": "$.apps[*].symbol"},
    )
    items = extract_json(data, extract)
    assert items[0]["url"] == "NVDA"  # fields 抽出的 url 原样保留


def test_extract_html_renders_url_template_for_list_type():
    """list 提取同样走模板出口:href 不可得的列表页用 slug 字段构 URL。"""
    from myia.engines.fetch_base import extract_html
    from myia.schema import ExtractConfig

    html = (
        "<html><body>"
        "<div class='deal'><a class='t'>深埋之星</a><span class='s'>buried-stars</span></div>"
        "<div class='deal'><a class='t'>无 slug</a></div>"
        "</body></html>"
    )
    extract = ExtractConfig(
        type="list",
        item="div.deal",
        url_template="https://store.epicgames.com/zh-CN/p/{slug}",
        fields={"title": "a.t", "slug": "span.s"},
    )
    items = extract_html(html, extract)
    assert [item["url"] for item in items] == [
        "https://store.epicgames.com/zh-CN/p/buried-stars",
        "",
    ]


# ------------------------------------------- extract.type=rss(10-03-news-rss)


def _gcores_fixture_text() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parent / "fixtures" / "news-gcores-rss.xml").read_text(
        encoding="utf-8"
    )


def test_extract_rss_maps_whitelisted_entry_attributes():
    """fixture=机核 gcores RSS 实录裁剪(2026-10-03 探查取证);fields 值=entry
    属性白名单逐条映射:键=归一字段名(url←link),值原样直出(CDATA summary
    保留 HTML 原文——feedparser 不剥 CDATA,模板消费方自担)。"""
    from myia.engines.fetch_base import extract_rss
    from myia.schema import ExtractConfig

    extract = ExtractConfig(
        type="rss",
        fields={
            "title": "title",
            "url": "link",
            "published": "published",
            "author": "author",
            "summary": "summary",
        },
    )
    items = extract_rss(_gcores_fixture_text(), extract)
    assert len(items) == 3, "实录裁剪 fixture 应有 3 条 item"
    first = items[0]
    assert first["title"] == "《恶魔城：贝尔蒙特的诅咒》试玩版今日上线"
    assert first["url"] == "https://www.gcores.com/articles/220477"
    assert first["published"] == "Fri, 02 Oct 2026 20:01:17 +0800"
    assert first["author"] == "YT17"
    assert first["summary"].startswith("<img"), "CDATA summary 应保留 HTML 原文"


def test_extract_rss_omits_missing_attributes_per_entry():
    """条目缺某属性 → 该字段逐条省略、其余字段照常(逐条目语义,同 json_path;
    区别于装载期的属性名拼错校验——那是 schema 的活,到不了这里)。"""
    from myia.engines.fetch_base import extract_rss
    from myia.schema import ExtractConfig

    feed = (
        "<rss version='2.0'><channel>"
        "<item><title>缺作者条目</title><link>https://x/1</link></item>"
        "<item><title>缺日期条目</title><link>https://x/2</link>"
        "<author>someone</author></item>"
        "</channel></rss>"
    )
    extract = ExtractConfig(
        type="rss",
        fields={"title": "title", "url": "link", "author": "author", "published": "published"},
    )
    items = extract_rss(feed, extract)
    assert len(items) == 2
    assert items[0] == {"title": "缺作者条目", "url": "https://x/1"}
    assert items[1] == {"title": "缺日期条目", "url": "https://x/2", "author": "someone"}


def test_extract_rss_bozo_malformed_feed_tolerated(caplog):
    """bozo 容错:截断的 malformed feed 只记 WARNING 不拒(截断 feed 常仍带出
    前几条可用条目);条目为空 → 空列表,不抛——失败形态交给管线失败记录。"""
    import logging

    from myia.engines.fetch_base import extract_rss
    from myia.schema import ExtractConfig

    extract = ExtractConfig(type="rss", fields={"title": "title", "url": "link"})
    with caplog.at_level(logging.WARNING, logger="myia.engines.fetch_base"):
        items = extract_rss("<rss version='2.0'><channel><title>truncated", extract)
    assert items == []
    assert any("bozo" in record.message for record in caplog.records)

    # 半截 feed 仍带出已完整的条目:容错解析继续产出(不整源拒)
    partial = (
        "<rss version='2.0'><channel>"
        "<item><title>完整条目</title><link>https://x/1</link></item>"
        "<item><title>被截断"
    )
    items = extract_rss(partial, extract)
    assert [item["url"] for item in items] == ["https://x/1"]


# ---------------------------------------------------------------------------
# 真实源 smoke(PRD 10-01-v01-fetch-base):手动可选执行,CI 不依赖外网。
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="真实源 smoke:对 linux.do 一类站点按 qps=0.5 抓 3 页无封禁(手动取消 skip 运行)")
def test_smoke_real_source_politeness_three_pages():
    from myia.engines.fetch_base import DEFAULT_USER_AGENT, FetchContext

    async def scenario():
        client = httpx.AsyncClient(timeout=30.0, headers={"User-Agent": DEFAULT_USER_AGENT})
        context = FetchContext(client=client)  # 真实时钟/真实 sleep,限速真实生效
        source = make_source(
            url="https://linux.do/latest?page={page}",
            pagination={"mode": "template", "max_pages": 3},
            rate_limit={"qps": 0.5, "jitter": "2s"},  # 礼貌:0.5 qps + 随机抖动
        )
        engine = BaseEngine(source, context)
        try:
            statuses = []
            for page in (1, 2, 3):
                response = await engine.request(f"https://linux.do/latest?page={page}")
                statuses.append(response.status_code)
            return statuses
        finally:
            await client.aclose()

    statuses = run(scenario())
    assert all(status < 400 for status in statuses)  # 无封禁


# ------------------------------------------- pagination.mode: scroll 层级边界


def test_scroll_mode_rejected_by_non_l4_engines_with_zero_network():
    """pagination.mode: scroll 仅 L4 scrapling 实装:其余引擎在 robots/网络/
    代理之前结构化拒绝(scroll_unsupported),engine: auto 才能沿链降级到 L4。

    镜像先例:L4/L5 对 json_path 的拒绝(scrapling.py/stealth_browser.py)。
    """
    from myia.engines.crawl4ai import Crawl4AIEngine
    from myia.engines.firecrawl import FirecrawlEngine
    from myia.engines.llm_browser import LLMBrowserEngine
    from myia.engines.scrapling import ScraplingEngine
    from myia.engines.static_html import StaticHTMLEngine
    from myia.engines.stealth_browser import StealthBrowserEngine

    def handler(request: httpx.Request) -> httpx.Response:  # 任何请求即失败
        raise AssertionError("scroll 拒绝必须发生在任何网络 I/O 之前")

    client = make_client(handler)
    context, _ = make_context(client)

    for engine_cls in (
        DirectAPIEngine,
        StaticHTMLEngine,
        Crawl4AIEngine,
        FirecrawlEngine,
        StealthBrowserEngine,
        LLMBrowserEngine,
    ):
        source = make_source(
            engine=engine_cls.ENGINE_NAME,
            pagination={"mode": "scroll", "max_pages": 3},
            extract={"type": "list", "item": "div.item",
                     "fields": {"title": "a.title", "url": "a.title@href"}},
        )
        engine = engine_cls(source, context)
        with pytest.raises(Exception) as excinfo:
            run(engine.fetch())
        failure = classify_exception(excinfo.value)
        assert failure == "scroll_unsupported", engine_cls.__name__
        assert "scrapling" in str(excinfo.value), engine_cls.__name__

    # L4 本引擎放行(自身再校验 scroll×{page}/static 后端组合)
    source = make_source(engine="scrapling", pagination={"mode": "scroll", "max_pages": 2})
    assert ScraplingEngine(source, context).SUPPORTS_SCROLL is True


# ------------------------------------------------- 数值文本规整(基线/路由取数形态)


def test_extract_html_coerces_numeric_looking_text_only():
    """纯数值文本(含货币前缀/千分位)抽取即成数值;日期/版本/百分比保持 str。

    回归:官方插件 gpu-prices 的 price 选择器抽出 '¥19800'(str),
    record_item_metrics 写 0 行、price >= 10000 路由 TypeError 被吞成
    conservative_default —— 数值语义字段必须在抽取层规整。
    """
    from myia.engines.fetch_base import extract_html
    from myia.schema import ExtractConfig

    html = (
        "<html><body>"
        "<div class='item'><a class='t' href='/1'>A 卡</a>"
        "<span class='p'>¥19,800</span><span class='d'>2026-10-02</span></div>"
        "<div class='item'><a class='t' href='/2'>B 卡</a>"
        "<span class='p'>4099.5</span><span class='v'>1.2.10</span></div>"
        "</body></html>"
    )
    extract = ExtractConfig(
        type="list", item="div.item",
        fields={"url": "a.t@href", "price": "span.p", "date": "span.d", "version": "span.v"},
    )
    items = extract_html(html, extract, base_url="https://x/")

    assert items == [
        {"url": "https://x/1", "price": 19800, "date": "2026-10-02"},
        {"url": "https://x/2", "price": 4099.5, "version": "1.2.10"},
    ]


def test_coerced_string_price_feeds_metrics_and_route(tmp_path):
    """端到端取数形态:抽取 str 价格 → 规整数值 → 基线快照落行、阈值路由命中。"""
    import sqlite3  # noqa: F401

    from myia.engines.fetch_base import extract_html
    from myia.push.route import resolve_route
    from myia.push.templates import record_item_metrics
    from myia.schema import ExtractConfig
    from myia.store import SQLiteStore

    html = (
        "<html><body><div class='item'>"
        "<a class='t' href='https://x/gpu1'>索泰 RTX 5090</a>"
        "<span class='p'>¥19800</span></div></body></html>"
    )
    extract = ExtractConfig(
        type="list", item="div.item",
        fields={"title": "a.t", "url": "a.t@href", "price": "span.p"},
    )
    item = extract_html(html, extract)[0]
    assert item["price"] == 19800 and isinstance(item["price"], int)

    store = SQLiteStore(tmp_path / "m.db")
    written = record_item_metrics(
        store, category="gpu", items=[item], fields=["price"],
        now=datetime.now(timezone.utc),
    )
    assert written == 1  # 此前字符串价格写 0 行

    from myia.push.route import routes_from_config

    decision = resolve_route(
        {**item, "score": None},
        routes_from_config([{"when": "price >= 10000", "mode": "immediate"}]),
    )
    assert decision.mode == "immediate"  # 此前 TypeError 被吞成 conservative_default
    store.close()
