"""Official plugin YAMLs validated end-to-end (PRD 10-01-v01-plugins-official).

The rewritten plugins are the schema's acceptance sample: every file
must load through :func:`myia.schema.load_category_file` (which forbids
unknown fields and plaintext credentials by construction), declare all twelve
sections explicitly, keep the two-tier push route (immediate + digest), and
render its push template against representative items. Extraction configs are
additionally exercised against minimal HTML/JSON snippets mirroring the
verified live markup (engines use recorded/replayed shapes — no test hits the
network except the opt-in smoke at the bottom).

games (task 10-03-games) joined the battery: its two official-API sources
have no page URL in the payload (only urlSlug / numeric id), so the snippet
tests also pin the extract.url_template rendering at the extraction outlet.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from myia.classify import rules_from_config
from myia.engines.fetch_base import extract_html, extract_json
from myia.pipeline import Pipeline
from myia.push.base import SendContext
from myia.push.route import resolve_route, routes_from_config
from myia.push.templates import TemplateRenderer
from myia.schema import ClassifyConfig, LoadError, SourceConfig, load_category, load_category_file
from myia.store import SQLiteStore

PLUGINS_DIR = Path(__file__).resolve().parents[1] / "plugins"
# gpu-prices 补入(10-03-games-v2,grill 决议⑧):全套电池自 v0.4 起从未
# 覆盖过它;zol 源现被反爬检查页拦(2026-10-03 实测),无 _SNIPPETS 录制
# 样本(同 v2ex parked 先例),两跑计划用合成 zol 形状 markup。
OFFICIAL_PLUGINS = ("stocks", "ai-news", "wool", "games", "gpu-prices")


# ---------------------------------------------------------------------------
# Helpers (fresh load per test — no cross-test shared state)
# ---------------------------------------------------------------------------


def _load(name: str):
    """Load one official plugin through the real schema entry point."""
    return load_category_file(PLUGINS_DIR / f"{name}.yaml")


def _raw(name: str) -> dict:
    """Parsed YAML mapping (for unknown-field mutation tests)."""
    with open(PLUGINS_DIR / f"{name}.yaml", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    assert isinstance(data, dict), f"{name}.yaml must parse to a mapping"
    return data


# ---------------------------------------------------------------------------
# Schema load + completeness (acceptance: passes schema, no unknown fields)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", OFFICIAL_PLUGINS)
def test_plugin_yaml_loads_through_schema(name):
    """Each official plugin loads via the real schema entry point."""
    config = _load(name)
    assert config.id == name


@pytest.mark.parametrize("name", OFFICIAL_PLUGINS)
def test_plugin_declares_all_twelve_sections_explicitly(name):
    """All schema sections are spelled out (AI-generation showcase duty)."""
    config = _load(name)
    required = {
        "id", "name", "schedule", "timezone", "sources", "watchlist",
        "classify", "dedup", "enrich", "push", "storage",
    }
    missing = required - config.model_fields_set
    assert not missing, f"{name}.yaml misses explicit sections: {sorted(missing)}"


@pytest.mark.parametrize("name", OFFICIAL_PLUGINS)
def test_plugin_rejects_unknown_field(name):
    """Unknown fields fail fast for these files (铁律 2 is real, not vacuous)."""
    data = _raw(name)
    data["bogus_section"] = {"oops": True}
    with pytest.raises(LoadError) as excinfo:
        load_category(data)
    assert any(d.error_type == "unknown_field" for d in excinfo.value.errors)


# ---------------------------------------------------------------------------
# Per-plugin semantics
# ---------------------------------------------------------------------------


def test_stocks_uses_direct_api_with_json_path_extract():
    config = _load("stocks")
    (source,) = config.sources
    assert source.engine == "direct_api"
    assert source.extract is not None and source.extract.type == "json_path"
    assert source.extra_params["symbols"], "symbol fan-out list must be present"
    assert "{symbol}" in source.url
    assert config.schedule == "0 9,15 * * 1-5", "trading-day cron"
    assert config.dedup.key == "{symbol}-{date}-{slot}", (
        "slot-rotating key: a bare {symbol} is all-time and would silence the plugin after run 1"
    )
    assert config.classify.builtin is False, "stock quotes would drop under the builtin scan"


def test_ai_news_carries_firecrawl_semantics_and_discourse_list():
    config = _load("ai-news")
    by_name = {source.name: source for source in config.sources}
    aihot = by_name["aihot"]
    # firecrawl is in the schema engine vocabulary now; the auto chain lands
    # on it, and the backend options are carried through the sanctioned
    # source-level pass-through.
    assert aihot.engine == "auto"
    assert aihot.extract.item == "article[data-item-id]"
    cocoloop = by_name["cocoloop"]
    assert cocoloop.engine == "static_html"
    assert cocoloop.pagination is not None and cocoloop.pagination.max_pages >= 1
    assert "{page}" in cocoloop.url


def test_wool_declares_seven_source_slots():
    """Five live L2 sources + the two documented parked placeholders
    (linux.do: login-walled; v2ex: challenge-gated until a firecrawl backend
    exists — shipping it live meant a guaranteed failure every run)."""
    text = (PLUGINS_DIR / "wool.yaml").read_text(encoding="utf-8")
    assert "# - name: linuxdo" in text, "linux.do placeholder comment must stay"
    assert "# - name: v2ex" in text, "v2ex placeholder comment must stay"
    config = _load("wool")
    assert len(config.sources) == 5
    by_name = {source.name: source for source in config.sources}
    assert set(by_name) == {"linuxsb", "bbsbtsb", "nodeloc", "cocoloop", "sbsb"}
    assert all(source.engine in ("static_html", "auto") for source in config.sources)


def test_games_uses_direct_api_json_path_with_url_template():
    """10-03-games D1/D6:双源 direct_api + json_path,响应无页面 URL
    (Epic 只有 urlSlug、Steam 只有数字 id),条目 URL 全靠 url_template
    渲染;dedup 稳定键 {url}(baseline 价格历史按 dedup_key 存,不能带日期)。"""
    config = _load("games")
    assert {source.name for source in config.sources} == {"epic-free", "steam-specials"}
    for source in config.sources:
        assert source.engine == "direct_api"
        assert source.extract is not None and source.extract.type == "json_path"
        assert source.extract.url_template, "两源响应无页面 URL,url 必须来自模板渲染"
        assert "url" not in source.extract.fields
    assert config.dedup.key == "{url}"
    assert config.classify.builtin is False, "游戏标题不落七大类,内置扫描只会误杀"
    assert config.baseline is not None and config.baseline.enabled
    assert config.baseline.fields == ["final_price"], "两家单位同为分,final_price 可直接比"


def test_games_free_item_hits_rules_and_immediate_route():
    """10-03-games D3 零 token 双漏斗:合成限免条目(final_price=0)命中限免
    规则与 immediate 路由;普通大折扣只进 digest。规则求值对缺字段整条让路,
    所以两源必须产出同名归一化字段。"""
    config = _load("games")
    rules = {
        rule.tag: rule
        for rule in rules_from_config([rule.model_dump() for rule in config.classify.rules])
    }
    free = {
        "title": "合成限免", "url": "https://store.epicgames.com/zh-CN/p/synthetic",
        "final_price": 0, "discount_pct": 0,
    }
    discount = {
        "title": "合成大折扣", "url": "https://store.steampowered.com/app/1",
        "final_price": 1360, "discount_pct": 90,
    }
    assert rules["限免"].evaluate(free) is True
    assert rules["限免"].evaluate(discount) is False
    assert rules["半价+"].evaluate(discount) is True

    (push,) = config.push
    routes = routes_from_config(push.route)
    assert resolve_route(free, routes).mode == "immediate"
    assert resolve_route(discount, routes).mode == "digest"


# ---------------------------------------------------------------------------
# Security / policy red lines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", OFFICIAL_PLUGINS)
def test_plugin_push_targets_are_credential_references(name):
    """No plaintext targets: every push target is an env:/keychain: reference."""
    config = _load(name)
    for push in config.push:
        assert push.target is not None
        assert push.target.startswith(("env:", "keychain:")), push.target


@pytest.mark.parametrize("name", OFFICIAL_PLUGINS)
def test_plugin_headers_keep_credential_values_referenced(name):
    """Credential-like headers, when present, carry env:/keychain: values only."""
    config = _load(name)
    for source in config.sources:
        for key, value in source.headers.items():
            if key.lower().lstrip("x-") in ("cookie", "authorization"):
                assert value.startswith(("env:", "keychain:", "Bearer env:", "Bearer keychain:")), (
                    f"{name}/{source.name}/{key} must reference credentials, never embed them"
                )


@pytest.mark.parametrize("name", OFFICIAL_PLUGINS)
def test_plugin_sources_stay_on_direct_proxy(name):
    """v0.1 only implements proxy: direct — the official plugins must not
    promise pool/residential behaviour before the transport exists."""
    config = _load(name)
    for source in config.sources:
        assert source.proxy == "direct"


# ---------------------------------------------------------------------------
# Routing: two tiers + rule grammar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", OFFICIAL_PLUGINS)
def test_plugin_route_covers_immediate_and_digest(name):
    """Every plugin ships both tiers (route semantics, PRD requirement)."""
    config = _load(name)
    modes = {rule.mode for push in config.push for rule in push.route}
    assert {"immediate", "digest"} <= modes
    # declared order is score rules first (v0.2 slot), data rules after
    for push in config.push:
        route_rules = routes_from_config(push.route)
        if any(rule.references_score for rule in route_rules):
            assert route_rules[0].references_score, "score-threshold rules must come first"


@pytest.mark.parametrize("name", OFFICIAL_PLUGINS)
def test_plugin_classify_rules_parse_with_whitelist_grammar(name):
    """classify.rules survive the whitelist-AST construction (never eval)."""
    config = _load(name)
    rules = rules_from_config([rule.model_dump() for rule in config.classify.rules])
    assert len(rules) == len(config.classify.rules)


# ---------------------------------------------------------------------------
# Push templates render (Jinja2 sandbox, StrictUndefined — grill Q2)
# ---------------------------------------------------------------------------


def _send_context() -> SendContext:
    return SendContext(slot="am", date="2026-10-01", category="演示", kind="digest")


@pytest.mark.parametrize("name", OFFICIAL_PLUGINS)
def test_plugin_template_renders_with_representative_items(name):
    """Templates survive the sandboxed renderer with realistic item fields."""
    config = _load(name)
    sample_items = {
        "stocks": [
            {"title": "NVIDIA Corporation", "url": "NVDA", "symbol": "NVDA",
             "price": 228.38, "change_pct": 3.21, "currency": "USD"},
            {"title": "Tencent Holdings", "url": "0700.HK", "symbol": "0700.HK",
             "price": 655.0, "change_pct": -1.2, "currency": "HKD"},
        ],
        "ai-news": [{"title": "公开演示标题", "url": "https://example.com/t/1"}],
        "wool": [{"title": "公开演示标题", "url": "https://example.com/t/2"}],
        # games 两条覆盖两种字段形态:Epic 限免(price_text 直出)与 Steam
        # 特惠(无 price_text,模板退 final_price/100)
        "games": [
            {"title": "深埋之星", "url": "https://store.epicgames.com/zh-CN/p/buried-stars",
             "final_price": 0, "original_price": 11600, "discount_pct": 0, "price_text": "0"},
            {"title": "The Outlast Trials", "url": "https://store.steampowered.com/app/1304930",
             "final_price": 1360, "original_price": 13600, "discount_pct": 90},
        ],
    }[name]
    renderer = TemplateRenderer()
    expected_marker = {
        "stocks": "NVDA",
        "ai-news": "公开演示标题",
        "wool": "公开演示标题",
        "games": "深埋之星",
    }[name]
    # 值级标记(opt-in):钉住换算/退路的输出值,不只是「渲染不炸」。
    # games:Steam 条目无 price_text → 退 final_price/100,1360 分应渲染 13.6
    value_markers: dict[str, list[str]] = {"games": ["13.6"]}
    for push in config.push:
        if push.template is None:
            continue
        rendered = renderer.render(push.template, sample_items, _send_context())
        assert expected_marker in rendered
        for marker in value_markers.get(name, []):
            assert marker in rendered, f"{name}: 值级标记 {marker!r} 未渲染(单位换算/退路回归?)"
        assert rendered.strip(), "rendered card must not be blank"
        assert "#" not in rendered, "template comments leaked into the card"


# ---------------------------------------------------------------------------
# Extract configs against recorded-structure snippets (no network)
# ---------------------------------------------------------------------------


# Minimal snippets mirroring the markup verified on the live pages (2026-10).
_SNIPPETS = {
    ("stocks", "yahoo-chart"): {
        "json": {
            "chart": {"result": [{"meta": {
                "symbol": "NVDA", "longName": "NVIDIA Corporation",
                "regularMarketPrice": 228.38, "regularMarketChangePercent": 3.21,
                "currency": "USD",
            }}]},
        },
        # Documented workaround: the payload has no per-item URL, so the
        # schema-mandatory url field resolves to the stable symbol identity.
        "expect_url": "NVDA",
        "expect_title": "NVIDIA Corporation",
    },
    ("ai-news", "aihot"): {
        "html": '<article data-item-id="abc"><h3><a href="/items/abc">公开演示标题</a></h3></article>',
        "base": "https://aihot.news/",
        "expect_url": "https://aihot.news/items/abc",
        "expect_title": "公开演示标题",
    },
    ("ai-news", "cocoloop"): {
        "html": '<table><tr class="topic-list-item"><td><a class="title raw-topic-link" href="https://www.cocoloop.cn/t/topic/1">公开演示标题</a></td></tr></table>',
        "expect_url": "https://www.cocoloop.cn/t/topic/1",
        "expect_title": "公开演示标题",
    },
    ("wool", "linuxsb"): {
        "html": '<div class="post-body"><div class="post-title-row"><a class="post-title" href="/topic/1">公开演示标题</a></div></div>',
        "base": "https://linux.sb/",
        "expect_url": "https://linux.sb/topic/1",
        "expect_title": "公开演示标题",
    },
    ("wool", "bbsbtsb"): {
        "html": '<a class="min-w-0" href="/posts/abc"><h2>公开演示标题</h2></a>'
                '<a href="/posts/abc#comments"></a>',  # comment anchor must not yield a record
        "base": "https://bbs.bt.sb/",
        "expect_url": "https://bbs.bt.sb/posts/abc",
        "expect_title": "公开演示标题",
    },
    ("wool", "nodeloc"): {
        "html": '<table><tr class="topic-list-item"><td><a class="title raw-topic-link" href="https://www.nodeloc.com/t/topic/1">公开演示标题</a></td></tr></table>',
        "expect_url": "https://www.nodeloc.com/t/topic/1",
        "expect_title": "公开演示标题",
    },
    ("wool", "sbsb"): {
        "html": '<div class="post-body"><div class="post-title-row"><a class="post-title" href="/t/82/">公开演示标题</a></div></div>',
        "base": "https://sb.sb/",
        "expect_url": "https://sb.sb/t/82/",
        "expect_title": "公开演示标题",
    },
    # games (10-03-games): both JSON snippets are trimmed from the live
    # responses recorded on 2026-10-03 under
    # .trellis/tasks/10-03-games/evidence/ — the item URL is rendered by
    # extract.url_template (the payload has none), so these pin the D1
    # extraction-outlet rendering as well as the field mapping.
    ("games", "epic-free"): {
        # evidence/epic-free.json elements[9](深埋之星,限免形状:discountPrice 0
        # / discountPercentage 0)与 TerraScape(hash urlSlug 形状 + 无当前促销
        # 形状:promotionalOffers 空 → discount_pct 逐元素省略;URL 由
        # catalogNs.mappings[0].pageSlug 渲染,hash urlSlug 形态由此钉住消解)
        "json": {
            "data": {"Catalog": {"searchStore": {"elements": [
                {
                    "title": "深埋之星",
                    "urlSlug": "buried-stars",
                    "catalogNs": {"mappings": [
                        {"pageSlug": "buried-stars-d7c88c", "pageType": "productHome"},
                    ]},
                    "price": {"totalPrice": {
                        "discountPrice": 0, "originalPrice": 11600, "discount": 11600,
                        "currencyCode": "CNY",
                        "fmtPrice": {"originalPrice": "¥116.00", "discountPrice": "0",
                                     "intermediatePrice": "0"},
                    }},
                    "promotions": {"promotionalOffers": [{"promotionalOffers": [{
                        "startDate": "2026-10-01T15:00:00.000Z",
                        "endDate": "2026-10-08T15:00:00.000Z",
                        "discountSetting": {"discountType": "PERCENTAGE", "discountPercentage": 0},
                    }]}], "upcomingPromotionalOffers": []},
                },
                {
                    "title": "TerraScape",
                    "urlSlug": "f229ed53ddba40788e0ab62978e9eaf9",
                    "catalogNs": {"mappings": [
                        {"pageSlug": "terrascape-2b12b1", "pageType": "productHome"},
                    ]},
                    "price": {"totalPrice": {
                        "discountPrice": 5300, "originalPrice": 5300, "discount": 0,
                        "currencyCode": "CNY",
                        "fmtPrice": {"originalPrice": "¥53.00", "discountPrice": "¥53.00",
                                     "intermediatePrice": "¥53.00"},
                    }},
                    "promotions": {"promotionalOffers": [], "upcomingPromotionalOffers": []},
                },
            ]}}},
        },
        "expect_url": "https://store.epicgames.com/zh-CN/p/buried-stars-d7c88c",
        "expect_title": "深埋之星",
        # 第二形状:hash urlSlug 元素照样渲染出正规 pageSlug 链接;无促销元素
        # 的 discount_pct 逐元素省略(不错位、不补 None)
        "expect_second": {
            "url": "https://store.epicgames.com/zh-CN/p/terrascape-2b12b1",
            "absent": ["discount_pct"],
        },
    },
    ("games", "steam-specials"): {
        # evidence/steam-featured.json specials.items[0](The Outlast Trials,
        # 90% 大折扣形状)与 items[5](How to Fish,38% 不命中半价+ 形状)
        "json": {"specials": {"id": "specials", "name": "Specials", "items": [
            {
                "id": 1304930, "type": 0, "name": "The Outlast Trials", "discounted": True,
                "discount_percent": 90, "original_price": 13600, "final_price": 1360,
                "currency": "CNY", "windows_available": True, "mac_available": False,
                "linux_available": False, "discount_expiration": 1791478800,
                "controller_support": "full",
            },
            {
                "id": 4001890, "type": 0, "name": "How to Fish", "discounted": True,
                "discount_percent": 38, "original_price": 3300, "final_price": 2046,
                "currency": "CNY", "windows_available": True, "mac_available": False,
                "linux_available": False, "discount_expiration": 1791478800,
                "controller_support": "full",
            },
        ]}},
        "expect_url": "https://store.steampowered.com/app/1304930",
        "expect_title": "The Outlast Trials",
        # 第二形状:多元素字段不错位(items[1] 是 How to Fish 自己的 id/折扣),
        # expire(unix 秒)随 fields 入库
        "expect_second": {
            "url": "https://store.steampowered.com/app/4001890",
            "discount_pct": 38,
            "expire": 1791478800,
        },
    },
    # v2ex is parked (commented out in wool.yaml, challenge-gated until a
    # firecrawl backend exists) — its extract was never live-verified, so it
    # has no snippet here; re-add one when the source ships.
}


@pytest.mark.parametrize("plugin,source_name", sorted(_SNIPPETS))
def test_plugin_extract_matches_recorded_markup(plugin, source_name):
    """Each extract config parses the structure observed on the real pages."""
    config = _load(plugin)
    source = next(s for s in config.sources if s.name == source_name)
    assert source.extract is not None
    spec = _SNIPPETS[(plugin, source_name)]
    if "json" in spec:
        items = extract_json(spec["json"], source.extract)
    else:
        assert source.extract.type == "list", "snippet contract"
        items = extract_html(spec["html"], source.extract, base_url=spec.get("base", ""))
    assert items, f"{plugin}/{source_name}: extract found nothing"
    assert items[0]["url"] == spec["expect_url"]
    assert items[0].get("title") == spec["expect_title"]
    # 第二差异形状(opt-in):钉住逐元素提取不错位与字段省略行为,防 fixture
    # 付出的形状成本只被「首条断言」覆盖(games 质检 low 修复)
    second = spec.get("expect_second")
    if second is not None:
        assert len(items) > 1, f"{plugin}/{source_name}: second shape missing"
        for field in second.get("absent", []):
            assert field not in items[1], f"{plugin}/{source_name}: {field} 应逐元素省略"
        for key in ("url", "title", "discount_pct", "expire"):
            if key in second:
                assert items[1].get(key) == second[key], f"{plugin}/{source_name}: 第二形状 {key} 错位"


# ---------------------------------------------------------------------------
# Pipeline wiring (constructor fail-fast chain, still network-free)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", OFFICIAL_PLUGINS)
def test_plugin_builds_a_pipeline(name):
    """The category plugs into Pipeline (classify table, rules, routes, tz)."""
    pipeline = Pipeline(_load(name))
    assert pipeline.config.id == name


# ---------------------------------------------------------------------------
# PRD acceptance: 每插件二跑 —— 第二次 dedup 拦截,store 不重复入库
# (previously zero automated coverage; dedup drift degraded silently into
#  per-item dedup_key_error at run time)
# ---------------------------------------------------------------------------


# One representative live source per plugin; second-run payloads change the
# content (new price / new topic) so the fetch-level change fingerprint does
# NOT swallow the second run — the interception under test is the dedup stage.
def _epic_free_body(discount_price: int, fmt_price: str) -> dict:
    """One-element Epic freeGamesPromotions body(trimmed evidence 形状).

    Same catalogNs pageSlug both runs → same url_template 渲染 → 同一 {url}
    dedup 键;price 变化让内容指纹不吞掉第二次 run(拦截发生在 dedup 阶段)。
    """
    return {"data": {"Catalog": {"searchStore": {"elements": [{
        "title": "深埋之星",
        "urlSlug": "buried-stars",
        "catalogNs": {"mappings": [
            {"pageSlug": "buried-stars-d7c88c", "pageType": "productHome"},
        ]},
        "price": {"totalPrice": {
            "discountPrice": discount_price, "originalPrice": 11600,
            "discount": 11600 - discount_price, "currencyCode": "CNY",
            "fmtPrice": {"originalPrice": "¥116.00", "discountPrice": fmt_price},
        }},
        "promotions": {"promotionalOffers": [{"promotionalOffers": [{
            "startDate": "2026-10-01T15:00:00.000Z", "endDate": "2026-10-08T15:00:00.000Z",
            "discountSetting": {"discountType": "PERCENTAGE", "discountPercentage": 0},
        }]}], "upcomingPromotionalOffers": []},
    }]}}}}


_TWO_RUN_PLANS: dict[str, dict[str, Any]] = {
    "stocks": {
        "source": "yahoo-chart",
        "payloads": [
            {"chart": {"result": [{"meta": {
                "symbol": "NVDA", "longName": "NVIDIA Corporation",
                "regularMarketPrice": 100.0, "regularMarketChangePercent": 1.0,
                "currency": "USD",
            }}]}},
            {"chart": {"result": [{"meta": {
                "symbol": "NVDA", "longName": "NVIDIA Corporation",
                "regularMarketPrice": 101.5, "regularMarketChangePercent": 1.4,
                "currency": "USD",
            }}]}},
        ],
    },
    "ai-news": {
        "source": "aihot",
        "payloads": [
            '<article data-item-id="a"><h3><a href="/items/a">公开演示标题</a></h3></article>',
            '<article data-item-id="a"><h3><a href="/items/a">公开演示标题</a></h3></article>'
            '<article data-item-id="b"><h3><a href="/items/b">新话题标题</a></h3></article>',
        ],
    },
    "wool": {
        "source": "linuxsb",
        "payloads": [
            '<div class="post-body"><div class="post-title-row"><a class="post-title" href="/topic/1">公开演示标题</a></div></div>',
            '<div class="post-body"><div class="post-title-row"><a class="post-title" href="/topic/1">公开演示标题</a></div></div>'
            '<div class="post-body"><div class="post-title-row"><a class="post-title" href="/topic/2">新羊毛标题</a></div></div>',
        ],
    },
    "games": {
        "source": "epic-free",
        "payloads": [
            _epic_free_body(0, "0"),            # 限免价 0(实录形状)
            _epic_free_body(100, "¥1.00"),      # 促销价变了,URL 不变
        ],
    },
}


def _single_source_config(name: str):
    """The plugin narrowed to one representative source, classify bypassed and
    push stripped (the acceptance under test is the plugin's extract +
    dedup.key wiring — the real feishu target would need live credentials)."""
    config = _load(name)
    keep = _TWO_RUN_PLANS[name]["source"]
    source = next(s for s in config.sources if s.name == keep)
    if name == "stocks":
        data = source.model_dump()
        data["symbols"] = ["NVDA"]
        source = SourceConfig.model_validate(data)
    return config.model_copy(
        update={
            "sources": [source],
            "classify": ClassifyConfig(builtin=False, rules=[]),
            "push": [],
        }
    )


def _two_run_handler(plan: dict[str, Any]):
    """robots fail-open; first data request serves payload 1, later ones payload 2."""
    served = {"n": 0}
    payloads = plan["payloads"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/robots.txt"):
            return httpx.Response(404, text="")
        payload = payloads[min(served["n"], len(payloads) - 1)]
        served["n"] += 1
        if isinstance(payload, str):
            return httpx.Response(200, text=payload)
        return httpx.Response(200, json=payload)

    return handler


@pytest.mark.parametrize("name", OFFICIAL_PLUGINS)
def test_plugin_two_runs_dedup_blocks_and_no_duplicate_rows(name, tmp_path):
    """PRD 10-01-v01-plugins-official: 每插件二跑 —— 第二次 dedup 拦截,无重复入库。"""
    store = SQLiteStore(tmp_path / "dedup.db")
    client = httpx.AsyncClient(transport=httpx.MockTransport(_two_run_handler(_TWO_RUN_PLANS[name])))
    pipeline = Pipeline(_single_source_config(name), store=store, client=client)

    result1 = asyncio.run(pipeline.run())
    assert result1.status == "success", result1.stats_dict()
    assert result1.stage("dedup").items_out >= 1

    result2 = asyncio.run(pipeline.run())
    assert result2.status == "success", result2.stats_dict()
    assert result2.stage("dedup").skips.get("dedup_seen", 0) >= 1, (
        f"{name}: second run must be intercepted by the dedup registry"
    )
    rows = store.list_items()
    assert len(rows) == len({record.dedup_key for record in rows}), (
        f"{name}: store must not hold duplicate entries"
    )
    store.close()
    asyncio.run(client.aclose())


# ---------------------------------------------------------------------------
# Opt-in live smoke (真实源 smoke: skip-by-default, 本地可选跑;CI 零外网)
# ---------------------------------------------------------------------------


def test_aihot_live_extract_smoke():
    """MYIA_SMOKE_REAL=1: fetch the aihot root page and run its extract."""
    if not os.environ.get("MYIA_SMOKE_REAL"):
        pytest.skip("真实源 smoke 默认跳过(设置 MYIA_SMOKE_REAL=1 启用)")
    import httpx

    config = _load("ai-news")
    aihot = next(s for s in config.sources if s.name == "aihot")
    response = httpx.get(aihot.url, headers={"User-Agent": "MYIA/0.1 (smoke)"}, timeout=30)
    response.raise_for_status()
    items = extract_html(response.text, aihot.extract, base_url=aihot.url)  # type: ignore[arg-type]
    assert items, "aihot live extract returned nothing (markup drift?)"
    assert all(item.get("url") for item in items)


@pytest.mark.parametrize("source_name", ["linuxsb", "bbsbtsb", "nodeloc", "cocoloop", "sbsb"])
def test_wool_live_source_smoke(source_name):
    """MYIA_SMOKE_REAL=1: wool 各活源真实抓取 + extract ≥1 条(v2ex 已停放除外)。"""
    if not os.environ.get("MYIA_SMOKE_REAL"):
        pytest.skip("真实源 smoke 默认跳过(设置 MYIA_SMOKE_REAL=1 启用)")

    config = _load("wool")
    source = next(s for s in config.sources if s.name == source_name)
    response = httpx.get(source.url, headers={"User-Agent": "MYIA/0.1 (smoke)"}, timeout=30)
    response.raise_for_status()
    items = extract_html(response.text, source.extract, base_url=source.url)  # type: ignore[arg-type]
    assert items, f"wool/{source_name}: live extract returned nothing (markup drift?)"
    assert all(item.get("url") for item in items)


# ---------------------------------------------------------------------------
# 2026-10 复盘修复回归:目录内每个示例 YAML 都必须始终可加载
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(PLUGINS_DIR.glob("*.yaml")), ids=lambda p: p.name)
def test_every_top_level_plugin_yaml_loads(path):
    """回归(monitor.yaml 曾整文件拒载):plugins/ 下的示例是 agent 的 ground
    truth,yaml-schema 变更纪律要求它们永远跟 schema 同步——monitor 曾因 v0.3
    的 `plugin:` 节 + 扁平 keychain 名成为「唯一 keychain 示例却不可运行」。"""
    config = load_category_file(path)
    assert config.id
    assert config.sources, f"{path.name} 必须声明至少一个源"


def test_monitor_yaml_uses_canonical_keychain_namespace():
    """monitor 是唯一演示 keychain: 凭据位的官方示例,引用必须是规范名空间
    myia/<scope>/<name>(扁平旧名加载期放行、resolve 期必被拒)。"""
    config = _load("monitor")
    for source in config.sources:
        for key, value in source.headers.items():
            if value.startswith("keychain:"):
                name = value.split(":", 1)[1]
                assert name.startswith("myia/"), f"{key} 应为 keychain:myia/<scope>/<name>"
                assert name.count("/") == 2
