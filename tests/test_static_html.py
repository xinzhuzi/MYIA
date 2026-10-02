"""Tests for the L2 static_html engine (PRD 10-01-v01-engine-l1-l2).

Covers: list extraction with @href/@src attribute fields + relative-URL
resolution / item single-page mode / gb18030 decoding (meta charset, no HTTP
charset) / template pagination with max_pages cap and empty-page stop /
selector pagination (follow, stop without next, loop guard) / change
fingerprint skip / unsupported extract type.

All I/O runs on httpx.MockTransport; all waiting is recorded by FakeClock.
"""

from __future__ import annotations

import httpx
import pytest

from myia.engines.fetch_base import FetchError
from myia.engines.static_html import StaticHTMLEngine

from conftest import make_client, make_context, make_handler, make_source, run

PAGE_TEMPLATE = """
<html><head><meta charset="utf-8"><title>示例列表页</title></head><body>
<div class="list">
  <div class="item"><h3><a class="title" href="/t/{n}">公开帖子 {n}</a></h3>
  <img class="thumb" src="/img/{n}.png"><span class="price">¥{price}</span></div>
  <div class="item"><h3><a class="title" href="/t/{n1}">AI 资讯 {n1}</a></h3>
  <img class="thumb" src="/img/{n1}.png"><span class="price">¥{price1}</span></div>
</div>
</body></html>
"""


def build_site(pages: dict[int, str | bytes], *, next_pages: set[int] | None = None):
    """Serve /list?page=N from ``pages``; append a next-link for pages in the set."""
    next_pages = next_pages or set()
    calls: list[int] = []

    def responder(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        calls.append(page)
        body = pages[page]
        if page in next_pages and isinstance(body, str):
            body = body.replace(
                "</body>", '<a class="next" href="/list?page={}">下一页</a></body>'.format(page + 1)
            )
        headers = {"content-type": "text/html"}
        return httpx.Response(200, headers=headers, content=body)

    return responder, calls


LIST_FIELDS = {
    "title": "a.title",
    "url": "a.title@href",
    "image": "img.thumb@src",
    "price": "span.price",
}


def html_source(url: str, **overrides):
    return make_source(url=url, extract={"type": "list", "item": "div.item", "fields": LIST_FIELDS}, **overrides)


def test_list_extract_attrs_and_relative_url_resolution():
    responder, _ = build_site({1: PAGE_TEMPLATE.format(n=1, n1=2, price=100, price1=200)})
    client = make_client(make_handler(responder))
    source = html_source("https://example.com/list")
    context, _ = make_context(client)
    engine = StaticHTMLEngine(source, context)

    items = run(engine.fetch())
    # price 文本经 coerce_numeric_text 规整为数值(¥ 前缀剥离):基线快照与
    # 阈值路由(price >= 10000)消费数值语义,字符串形态会双双静默失效。
    assert items == [
        {"title": "公开帖子 1", "url": "https://example.com/t/1", "image": "https://example.com/img/1.png", "price": 100},
        {"title": "AI 资讯 2", "url": "https://example.com/t/2", "image": "https://example.com/img/2.png", "price": 200},
    ]


def test_item_extract_single_page_mode():
    responder, _ = build_site({1: PAGE_TEMPLATE.format(n=1, n1=2, price=100, price1=200)})
    client = make_client(make_handler(responder))
    source = make_source(
        url="https://example.com/detail",
        extract={
            "type": "item",
            "fields": {"title": "h3 a.title", "image": "img.thumb@src"},
        },
    )
    context, _ = make_context(client)
    engine = StaticHTMLEngine(source, context)

    items = run(engine.fetch())
    assert items == [{"title": "公开帖子 1", "image": "https://example.com/img/1.png"}]


def test_gb18030_decoding_without_http_charset():
    gb_page = (
        "<html><head><meta charset=\"gb18030\"></head><body>"
        "<div class=\"item\"><h3><a class=\"title\" href=\"/t/9\">显卡降价</a></h3></div>"
        "</body></html>"
    ).encode("gb18030")
    responder, _ = build_site({1: gb_page})
    client = make_client(make_handler(responder))
    source = html_source("https://example.com/list")
    context, _ = make_context(client)
    engine = StaticHTMLEngine(source, context)

    items = run(engine.fetch())
    assert items[0]["title"] == "显卡降价"  # 生产源痛点:gb18030 自动识别


def test_pagination_template_respects_max_pages():
    pages = {p: PAGE_TEMPLATE.format(n=p, n1=p + 1, price=p, price1=p + 1) for p in (1, 2, 3)}
    responder, calls = build_site(pages)
    client = make_client(make_handler(responder))
    source = html_source(
        "https://example.com/list?page={page}",
        pagination={"mode": "template", "max_pages": 2},
        rate_limit={"qps": 1000.0},
    )
    context, _ = make_context(client)
    engine = StaticHTMLEngine(source, context)

    items = run(engine.fetch())
    assert calls == [1, 2]  # max_pages 封顶,第 3 页不抓
    assert len(items) == 4


def test_pagination_template_stops_on_empty_page():
    pages = {
        1: PAGE_TEMPLATE.format(n=1, n1=2, price=1, price1=2),
        2: "<html><body><div class=\"list\"></div></body></html>",  # 空页
        3: PAGE_TEMPLATE.format(n=5, n1=6, price=5, price1=6),
    }
    responder, calls = build_site(pages)
    client = make_client(make_handler(responder))
    source = html_source(
        "https://example.com/list?page={page}",
        pagination={"mode": "template", "max_pages": 5},
        rate_limit={"qps": 1000.0},
    )
    context, _ = make_context(client)
    engine = StaticHTMLEngine(source, context)

    items = run(engine.fetch())
    assert calls == [1, 2]  # 空页提前收尾,礼貌不空翻
    assert len(items) == 2


def test_pagination_selector_follows_next_links():
    pages = {p: PAGE_TEMPLATE.format(n=p, n1=p + 1, price=p, price1=p + 1) for p in (1, 2, 3)}
    responder, calls = build_site(pages, next_pages={1, 2})  # 第 3 页无下一页
    client = make_client(make_handler(responder))
    source = html_source(
        "https://example.com/list",
        pagination={"mode": "selector", "selector": "a.next@href", "max_pages": 5},
        rate_limit={"qps": 1000.0},
    )
    context, _ = make_context(client)
    engine = StaticHTMLEngine(source, context)

    items = run(engine.fetch())
    assert calls == [1, 2, 3]  # 跟随 a.next@href,无下一页自然收尾
    assert len(items) == 6


def test_pagination_selector_stops_at_max_pages():
    pages = {p: PAGE_TEMPLATE.format(n=p, n1=p + 1, price=p, price1=p + 1) for p in (1, 2, 3, 4, 5)}
    responder, calls = build_site(pages, next_pages={1, 2, 3, 4, 5})
    client = make_client(make_handler(responder))
    source = html_source(
        "https://example.com/list",
        pagination={"mode": "selector", "selector": "a.next@href", "max_pages": 2},
        rate_limit={"qps": 1000.0},
    )
    context, _ = make_context(client)
    engine = StaticHTMLEngine(source, context)

    run(engine.fetch())
    assert calls == [1, 2]  # max_pages 封顶


def test_pagination_selector_loop_guard():
    pages = {p: PAGE_TEMPLATE.format(n=p, n1=p + 1, price=p, price1=p + 1) for p in (1, 2)}
    responder, calls = build_site(pages, next_pages={1})  # 页 1 -> 页 2
    # 页 2 的"下一页"指向自己 -> 环路
    def responder_with_loop(request: httpx.Request) -> httpx.Response:
        response = responder(request)
        if int(request.url.params.get("page", "1")) == 2:
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=str(response.content, "utf-8").replace(
                    "</body>", '<a class="next" href="/list?page=2">下一页</a></body>'
                ),
            )
        return response

    client = make_client(make_handler(responder_with_loop))
    source = html_source(
        "https://example.com/list",
        pagination={"mode": "selector", "selector": "a.next@href", "max_pages": 10},
        rate_limit={"qps": 1000.0},
    )
    context, _ = make_context(client)
    engine = StaticHTMLEngine(source, context)

    run(engine.fetch())
    assert calls == [1, 2]  # 环路检测:同一 URL 只抓一次


def test_change_unchanged_second_run_skips(engine_store):
    responder, calls = build_site({1: PAGE_TEMPLATE.format(n=1, n1=2, price=1, price1=2)})
    client = make_client(make_handler(responder))
    context, _ = make_context(client, store=engine_store)
    source = html_source("https://example.com/list")

    first = run(StaticHTMLEngine(source, context).fetch())
    assert len(first) == 2
    assert calls == [1]

    engine = StaticHTMLEngine(source, context)
    second = run(engine.fetch())
    assert second == []
    assert calls == [1, 1]  # 每轮各 1 次:请求先行,指纹命中后跳过的是提取
    assert engine.last_skip_reason == "hash_match"


def test_unsupported_extract_type_is_structured_error():
    source = make_source(
        engine="static_html",
        extract={"type": "json_path", "fields": {"url": "$[*].u"}},
    )
    client = make_client(make_handler(lambda r: httpx.Response(200, json={})))
    context, _ = make_context(client)
    engine = StaticHTMLEngine(source, context)

    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "extract_unsupported"
