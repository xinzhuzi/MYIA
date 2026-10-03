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
v3 (task 10-03-games-v3) added the CheapShark multi-store source: its
response is a **top-level array** (``$[*]`` field prefixes), dealID arrives
pre-URL-encoded, and its dollar fields (sale_price/savings_pct) stay
un-normalized — no final_price, no baseline, digest-only routing.
wrap (task 10-03-games-wrap) added the GOG catalog free-source (fourth:
``$.products[*]`` prefix, storeLink is an absolute URL so the url field
wins over any template) and mounted the telegram push entry (v1 决议⑤:
same two-tier route, plain-text template variant — telegram sends user
templates without parse_mode).
dedup-fix (task 10-03-games-dedup-fix) made the games entry key carry
price state (``{url}-{final_price}``; CS/GOG gained a string-form
``final_price`` alias for it) and reversed :func:`item_metric_key` to
url-first so rotating entry keys no longer break price baselines.
news (task 10-03-news-rss) joined with the ``rss`` extract type: one
gcores feed source riding the static_html text path (feedparser entry
mapping — ``fields`` values are a closed entry-attribute whitelist,
``url`` maps ``link``), digest-only routing (资讯无 immediate 分层; the
sole immediate rule is the dormant v0.2 score slot), and templates that
stay out of the CDATA summary (标题+链接+日期 only).
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from myia.classify import rules_from_config
from myia.engines.fetch_base import extract_html, extract_json, extract_rss
from myia.pipeline import Pipeline
from myia.push.base import SendContext
from myia.push.route import resolve_route, routes_from_config
from myia.push.templates import TemplateRenderer, item_metric_key
from myia import secrets as secrets_store
from myia.schema import (
    ClassifyConfig,
    EnrichConfig,
    LoadError,
    SourceConfig,
    load_category,
    load_category_file,
)
from myia.secrets import InMemoryKeychainBackend
from myia.store import SQLiteStore

PLUGINS_DIR = Path(__file__).resolve().parents[1] / "plugins"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
# gpu-prices 补入(10-03-games-v2,grill 决议⑧):全套电池自 v0.4 起从未
# 覆盖过它;zol 源现被反爬检查页拦(2026-10-03 实测),无 _SNIPPETS 录制
# 样本(同 v2ex parked 先例),两跑计划用合成 zol 形状 markup。
# news 补入(10-03-news-rss):游戏资讯 RSS 品类(gcores 单源);snippet 用
# 实录裁剪 fixture tests/fixtures/news-gcores-rss.xml。
# exposure 补入(10-03-aipocket-fusion,Q12 决议进官方件电池):链外源引擎
# credhunter 品类(FOFA/Shodan 曝面 + 本地分诊双源);两跑计划走 scan lane
# (manual-triage 源,零网络零凭据——出网 lane 无 key 显式空态,不适合
# dedup 拦截用例的「有产出」前提)。
OFFICIAL_PLUGINS = ("stocks", "ai-news", "wool", "games", "gpu-prices", "news", "exposure")


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
    # 图片处理环官方示范(10-03-vision-pipeline 拍板⑨ → 10-03-vision-daily
    # A1 实开):活跃 list 源收图 URL(img@src → metadata["image"]),品类
    # images: 节 OCR + 本地 VL caption(vl: local 实配,不再只是注释示例)。
    assert "image" in aihot.extract.fields, "aihot 必须抽取卡片封面 img@src"
    assert config.images is not None and config.images.enabled
    assert config.images.vl == "local", "A1 落地:本地 VL caption 实配(vision.yaml local 节)"
    # enrich 第二层漏斗(10-03-vision-daily A2 实开):端点凭据双引用 —
    # base_url/api_key 均走钥匙链(10-03 桌面零 env 化:base_url 自 env:
    # MYIA_LLM_BASE_URL 迁 keychain:myia/llm/base_url,桌面 sidecar 无 shell
    # env,env: 引用会在桌面 run 构造期 fail-fast),api_key 是既有 GLM key
    # (vision.yaml cloud 同链)。
    assert config.enrich.enabled is True, "A2 落地:精评开通,image_ocr/image_caption 进精评 payload"
    assert config.enrich.base_url == "keychain:myia/llm/base_url", (
        "端点 base_url 必须是 keychain: 引用(桌面零 env;grill Q6 引用铁律不变)"
    )
    assert config.enrich.api_key == "keychain:myia/image/api_key", "key 走钥匙链既有 GLM 凭据"
    assert config.enrich.batch == 10, "实测批宽:glm-4-flash 单批 20 条超 60s completion 上限,10 条留余量"
    cocoloop = by_name["cocoloop"]
    assert cocoloop.engine == "static_html"
    assert cocoloop.pagination is not None and cocoloop.pagination.max_pages >= 1
    assert "{page}" in cocoloop.url


def test_ai_news_images_ring_local_hint_comment_ships_startup_guide():
    """拍板⑨ → 10-03-vision-daily A1:`vl: local` 从注释示例转实配,且注释
    仍必须带本地服务启动指引一句(games 教训:官方插件声明面的注释也是
    agent 的 ground truth——vl 开了之后,服务怎么起就是日常 run 的活文档)。"""
    text = (PLUGINS_DIR / "ai-news.yaml").read_text(encoding="utf-8")
    assert "\n  vl: local" in text, "vl: local 必须是实配行,不是注释示例"
    assert "mlx_vlm.server" in text, "本地 VL 服务启动指引必须在注释里"
    # A2 端点注记同规(10-03 桌面零 env 化后):keychain: 引用的录入方法必须
    # 在声明面留一句(myia secret set 指引,games 教训同源)
    assert "myia secret set myia/llm/base_url" in text, (
        "enrich.base_url 的 keychain: 引用必须注明凭据怎么录入"
    )


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
    """10-03-games D1/D6 + v3 + wrap:四源 direct_api + json_path。Epic/Steam/
    CS 三源响应无页面 URL(只有 urlSlug / 数字 id / 已编码 dealID),条目 URL
    全靠 url_template 渲染;GOG(wrap 第四源)是反向形态——storeLink 即绝对
    URL,url 字段直出免模板(schema 二选一的另一边)。dedup 键带价格态
    {url}-{final_price}(10-03-games-dedup-fix:is_seen 全期拦截下纯 {url} 键
    =条目一生只推一次,预告→免费的切换会被吞);baseline 价格历史按 url 存
    (item_metric_key url 优先),键随价格态轮换不断链。"""
    config = _load("games")
    assert {source.name for source in config.sources} == {
        "epic-free", "steam-specials", "cheapshark", "gog-free",
    }
    templated = {"epic-free", "steam-specials", "cheapshark"}
    for source in config.sources:
        assert source.engine == "direct_api"
        assert source.extract is not None and source.extract.type == "json_path"
        if source.name in templated:
            assert source.extract.url_template, "三源响应无页面 URL,url 必须来自模板渲染"
            assert "url" not in source.extract.fields
        else:  # gog-free:storeLink 绝对链接,url 字段胜出、无模板
            assert source.extract.url_template is None
            assert "url" in source.extract.fields
        # dedup 键价格态的硬前提(10-03-games-dedup-fix):make_key 对缺失
        # 字段抛 ValueError→条目按 dedup_key_error 丢弃,四源必须都产出
        # final_price(Epic/Steam 人民币分 int;CS/GOG 美元元字符串)。
        assert "final_price" in source.extract.fields, (
            f"{source.name}: dedup 键 {{url}}-{{final_price}} 缺 final_price 提取,条目会被整条丢弃"
        )
    assert config.dedup.key == "{url}-{final_price}"
    assert config.classify.builtin is False, "游戏标题不落七大类,内置扫描只会误杀"
    assert config.baseline is not None and config.baseline.enabled
    assert config.baseline.fields == ["final_price"], "人民币分单位可直接比;CS/GOG 美元元不归一(v3 决议④)"


def test_games_gog_source_declares_catalog_query_and_double_insurance():
    """10-03-games-wrap 第四源:catalog.gog.com/v1/catalog 是 gog.com 官网 SSR
    自用的公开 JSON 通道(探查证据 .trellis/tasks/10-03-games-wrap/evidence/
    gog-probe-*,summary 在 gog-probe-summary.json)。参数形态是实测坑:
    between:0,0 与 in:game,pack(横线/冒号形实测 404/静默清零)。条目级
    限免双保险=现价 0.00 且原价>0:原价同为 0.00 的永久免费游戏(实测 419
    条全如此)不命中、不刷 immediate;该端点无促销截止字段,判定只能靠
    价格形态。美元字符串循 CS 先例独立命名(sale_price/normal_price 同名
    同形态),不进 final_price 人民币分基线(v3 决议④口径)。"""
    config = _load("games")
    gog = next(s for s in config.sources if s.name == "gog-free")
    assert "catalog.gog.com/v1/catalog" in gog.url
    assert "price=between:0,0" in gog.url, "0 元锁定参数(横线形 between-0-0 实测 404)"
    assert "productType=in:game,pack" in gog.url, "集合参数正确形(冒号形 in:game:pack 静默清零)"
    assert "discounted=eq:true" in gog.url
    assert gog.extract is not None
    assert gog.extract.url_template is None, "storeLink 绝对 URL,url 字段直出"
    assert set(gog.extract.fields) == {
        "title", "url", "gog_id", "image", "sale_price", "normal_price", "final_price",
    }
    for name, path in gog.extract.fields.items():
        assert path.startswith("$.products[*]."), f"GOG 字段 {name} 应为 $.products[*] 前缀,当前 {path!r}"
    assert "discount_pct" not in gog.extract.fields
    # final_price(美元元字符串)= dedup 键价格态配套(10-03-games-dedup-fix):
    # 只作键成分,限免判定仍走 sale_price/normal_price 双保险析取,数值基线
    # 拒 str 不受污染(numeric_value 只收 int/float)。
    assert gog.extract.fields["final_price"] == "$.products[*].price.finalMoney.amount"
    assert gog.rate_limit is not None
    assert gog.rate_limit.respect_robots is True, "catalog.gog.com robots.txt 404(2026-10-03 实测),缺省即允许"

    rules = {
        rule.tag: rule
        for rule in rules_from_config([rule.model_dump() for rule in config.classify.rules])
    }
    giveaway = {
        "title": "BROK The InvestiGator - prologue",
        "url": "https://www.gog.com/en/game/brok_the_investigator_prologue",
        "sale_price": "0.00", "normal_price": "19.99",
    }
    permfree = {
        "title": "Whateverland: Prologue",
        "url": "https://www.gog.com/en/game/whateverland_prologue",
        "sale_price": "0.00", "normal_price": "0.00",
    }
    assert rules["限免"].evaluate(giveaway) is True, "现价 0 且原价>0 = 限免双保险命中"
    assert rules["限免"].evaluate(permfree) is False, "永久免费(原价 0)不是限免,不刷 immediate"
    # 路由两态:giveaway → immediate(两通道同款);permfree → 保守 digest
    for push in config.push:
        routes = routes_from_config(push.route)
        assert resolve_route(giveaway, routes).mode == "immediate"
        assert resolve_route(permfree, routes).mode == "digest"
    # None 守卫结构必需:GOG 条目无 final_price/discount_pct,裸 `>=` 比较会在
    # 轮到 GOG 析取前炸掉整条 BoolOp(or 的假值分支继续求值)——这里钉住
    # 守卫形不得回退成裸比较(回退则 giveaway 静默落 digest)。
    for push in config.push:
        immediate = next(r for r in push.route if r.mode == "immediate")
        assert "(discount_pct or 0)" in immediate.when
        assert '(sale_price or "-1")' in immediate.when


def test_games_telegram_push_entry_mounted_per_v1_decision5():
    """v1 决议⑤(wrap 落地):telegram 加挂 = 第二个 push 条目,同款两级路由
    (when 逐字与 feishu 条目一致,含 GOG 限免双保险析取),模板走精简纯文本
    变体——telegram 用户模板契约是渲染文本不带 parse_mode 直发
    (src/myia/push/telegram.py _compose),markdown 链接语法会原样露出。
    chat id 走 env:TELEGRAM_CHAT_ID;bot token 走渠道缺省
    env:TELEGRAM_BOT_TOKEN(DEFAULT_TOKEN_ENV_REF,无需声明)。"""
    config = _load("games")
    by_channel = {}
    for push in config.push:
        by_channel.setdefault(push.channel, []).append(push)
    assert set(by_channel) == {"feishu_card", "telegram"}, "双通道:feishu 起步 + telegram 加挂"
    (telegram,) = by_channel["telegram"]
    (feishu,) = by_channel["feishu_card"]
    assert telegram.target == "env:TELEGRAM_CHAT_ID"
    assert [r.when for r in telegram.route] == [r.when for r in feishu.route], "路由 when 与 feishu 条目同款"
    assert [r.mode for r in telegram.route] == ["immediate", "digest"]
    assert telegram.template is not None
    assert "](" not in telegram.template, "纯文本通道:markdown 链接语法会原样露出,必须精简变体"


def test_games_cheapshark_source_declares_three_stores_top_array_prefix():
    """10-03-games-v3 决议①③⑤:storeID=7,11,15(GOG/Humble/Fanatical,与
    Steam featured 零重叠);响应是**顶层数组**——字段全部同一 $[*] 前缀
    (extract_json 同前缀逐元素提取同样适用);URL 走 .com 域 redirect
    (dealID 已 URL-encoded 直拼);robots 有据推翻已批(循 stocks/Yahoo
    判例)→ respect_robots: false 是 deliberate override,不能是缺省漂移。"""
    config = _load("games")
    cs = next(s for s in config.sources if s.name == "cheapshark")
    assert "storeID=7,11,15" in cs.url
    assert "sortBy=Savings" in cs.url
    assert cs.extract is not None
    assert cs.extract.url_template == "https://www.cheapshark.com/redirect?dealID={deal_id}"
    # 顶层数组:每个字段路径都是同一 $[*] 前缀(Epic/Steam 是嵌套 $.data…[*])
    assert cs.extract.fields
    for name, path in cs.extract.fields.items():
        assert path.startswith("$[*]."), f"CS 顶层数组字段 {name} 应为 $[*] 前缀,当前 {path!r}"
    # 美元字段独立命名,不碰归一化字段(决议④:人民币分基线不容美元元);
    # final_price(= $.salePrice 字符串)是 dedup 键价格态配套
    # (10-03-games-dedup-fix),只作键成分不进显示/规则/数值基线。
    assert set(cs.extract.fields) == {"title", "deal_id", "sale_price", "savings_pct", "normal_price", "metacritic", "final_price"}
    assert cs.extract.fields["final_price"] == "$[*].salePrice"
    assert "discount_pct" not in cs.extract.fields
    assert cs.rate_limit is not None
    assert cs.rate_limit.respect_robots is False, "CS robots Disallow: /api/1.0/ → deliberate override(判例注释在 YAML)"


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

    # wrap 后双通道(feishu_card + telegram):路由两态对每个条目都成立
    for push in config.push:
        routes = routes_from_config(push.route)
        assert resolve_route(free, routes).mode == "immediate"
        assert resolve_route(discount, routes).mode == "digest"


def test_games_upcoming_free_hits_tag_rule_and_stays_digest():
    """10-03-games-v2 决议②:Epic「下周免费」预告走**字段融合**——upcoming_pct
    并入同一条目。10-03-games-dedup-fix 勘误:原「dedup 稳定 {url}、切换碰撞
    由槽位抑制消解」不成立(is_seen 全期拦截,预告日推过的同 url 到免费日
    被吞);修法=dedup 键带价格态 {url}-{final_price},免费日价格变 0 即新键
    入库,immediate 照常落地。预告不是现在能领的,必须留在 digest、绝不
    immediate。"""
    config = _load("games")
    rules = {
        rule.tag: rule
        for rule in rules_from_config([rule.model_dump() for rule in config.classify.rules])
    }
    upcoming = {
        "title": "合成预告", "url": "https://store.epicgames.com/zh-CN/p/synthetic",
        "final_price": 5300, "discount_pct": 0, "upcoming_pct": 100,
        "upcoming_start": "2026-10-08T15:00:00.000Z",
    }
    assert rules["下周免费"].evaluate(upcoming) is True
    assert rules["限免"].evaluate(upcoming) is False, "预告不是当前限免"
    for push in config.push:
        assert resolve_route(upcoming, routes_from_config(push.route)).mode == "digest"


def test_games_cheapshark_deal_hits_rule_and_stays_digest():
    """10-03-games-v3 决议④:合成 CS 条目(美元字符串形态,裁自 evidence/
    cs-multi.json Unclaimed World)——`float(savings_pct) >= 50` 白名单转换
    命中「多店半价+」;Epic/Steam 条目无 savings_pct,float(None) 求值失败
    按不命中(缺字段让路,天然无感);路由零改动:CS 条目无 final_price/
    discount_pct,两级 when 均不命中走保守缺省 digest——immediate 仍只属限免
    (digest 无 items 排序,顺序=源到达序,CS 条目落日报尾部)。"""
    config = _load("games")
    rules = {
        rule.tag: rule
        for rule in rules_from_config([rule.model_dump() for rule in config.classify.rules])
    }
    cs_hit = {
        "title": "Unclaimed World",
        "url": "https://www.cheapshark.com/redirect?dealID=hV1uGbDuy%2FdMUfxYZb%2BPCBj345sgqPwRWHlLgtuAxAk%3D",
        "sale_price": "0.50", "normal_price": "16.99", "savings_pct": "97.057092",
    }
    assert rules["多店半价+"].evaluate(cs_hit) is True
    # 边界:恰好半价命中(>= 50,非严格大于)
    assert rules["多店半价+"].evaluate({**cs_hit, "savings_pct": "50.0"}) is True
    assert rules["多店半价+"].evaluate({**cs_hit, "savings_pct": "49.999"}) is False
    # 缺字段让路:两源条目(无 savings_pct)对该规则零影响
    epic = {"title": "深埋之星", "url": "https://store.epicgames.com/zh-CN/p/x", "final_price": 0}
    steam = {"title": "The Outlast Trials", "url": "https://store.steampowered.com/app/1",
             "final_price": 1360, "discount_pct": 90}
    assert rules["多店半价+"].evaluate(epic) is False
    assert rules["多店半价+"].evaluate(steam) is False
    # 两源既有规则对 CS 条目同样让路(字段互不污染)
    assert rules["限免"].evaluate(cs_hit) is False
    assert rules["半价+"].evaluate(cs_hit) is False

    for push in config.push:
        assert resolve_route(cs_hit, routes_from_config(push.route)).mode == "digest", (
            "CS 条目必须落 digest(immediate 只属限免,v3 决议④路由零改动)"
        )


def test_news_uses_static_html_rss_with_whitelisted_entry_fields():
    """10-03-news-rss:游戏资讯 = 独立 news.yaml 品类(资讯无价格字段,games
    规则面全是价格语义,不并入——决议②);唯一源 gcores-rss 挂 static_html
    文本通路 + extract.type rss(fields 值=feedparser entry 属性白名单,
    schema 装载期拼错即拒;direct_api 保持 JSON-only 是引擎层词表分工)。
    路由全 digest(资讯无 immediate 分层):唯一 immediate 是 dormant v0.2
    score 槽位,enrich 关闭时条目无分数永不命中,未命中走保守缺省 digest。
    模板只有标题+链接+日期,不进 summary/CDATA(design R3)。"""
    config = _load("news")
    (source,) = config.sources
    assert source.name == "gcores-rss"
    assert source.engine == "static_html", "rss 只挂 static_html 文本通路(design R4)"
    assert source.extract is not None and source.extract.type == "rss"
    assert source.extract.fields == {"title": "title", "url": "link", "published": "published"}
    assert "url" in source.extract.fields, "rss 条目 url=entry.link,去重键根基(url 必填同规)"
    assert source.extract.url_template is None
    assert source.rate_limit is not None and source.rate_limit.respect_robots is True
    assert config.dedup.key == "{url}", "RSS 条目 url 稳定唯一,无价格态可带"
    assert config.enrich.enabled is False, "零 token 路线:标题+链接+日期自解释"
    assert config.classify.builtin is False, "资讯标题不落七大类,内置扫描只会误杀"
    # 双通道(同 games wrap 先例):feishu_card + telegram
    assert {push.channel for push in config.push} == {"feishu_card", "telegram"}
    for push in config.push:
        assert [rule.mode for rule in push.route] == ["immediate", "digest"]
        assert "score" in push.route[0].when, "immediate 槽位是 dormant score 规则(全 digest 决议)"
        assert push.template is not None
        assert "summary" not in push.template, "模板不进 CDATA summary(design R3)"
    # 全 digest 语义钉住:无分数条目 → dormant immediate 跳过 → 缺省 digest
    item = {"title": "合成条目", "url": "https://www.gcores.com/articles/1"}
    for push in config.push:
        assert resolve_route(item, routes_from_config(push.route)).mode == "digest"


# ---------------------------------------------------------------------------
# Security / policy red lines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", OFFICIAL_PLUGINS)
def test_plugin_push_targets_are_credential_references(name):
    """No plaintext targets: every push target is an env:/keychain: reference.

    stdout 通道例外(10-03-aipocket-fusion 起 exposure 品类走 stdout——
    Q9 私有情报不出本机):schema 强制其 target 为 None,不携带寻址。"""
    config = _load(name)
    for push in config.push:
        if push.channel == "stdout":
            assert push.target is None, "stdout 通道不带 target(schema 强制)"
            continue
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


# ---------------------------------------------------------------------------
# item_metric_key(10-03-games-dedup-fix:url 优先,退 dedup_key)
# ---------------------------------------------------------------------------


def test_item_metric_key_prefers_url_over_stateful_dedup_key():
    """url≠dedup_key 时取 url:条目键带状态后(games ``{url}-{final_price}``),
   价格基线的稳定身份是商品 url——键随价格态轮换,昨日/上周快照才不断链,
    vs_yesterday 对「这游戏今天又降了」才有基线可比。"""
    games_view = {
        "url": "https://store.epicgames.com/zh-CN/p/buried-stars",
        "dedup_key": "https://store.epicgames.com/zh-CN/p/buried-stars-0",
        "final_price": 0,
    }
    assert item_metric_key(games_view) == "https://store.epicgames.com/zh-CN/p/buried-stars"
    # stocks 同理:dedup 键 {symbol}-{date}-{slot} 每槽轮换,旧序(键优先)
    # 会让每日快照落在不同键上,vs_yesterday 恒空——反转后按稳定 url 存。
    stocks_view = {"url": "NVDA", "dedup_key": "NVDA-2026-10-03-am"}
    assert item_metric_key(stocks_view) == "NVDA"


def test_item_metric_key_falls_back_to_dedup_key_without_url():
    """无 url(或空串)退 dedup_key;两者皆无 → None(条目不进数值基线)。"""
    assert item_metric_key({"dedup_key": "k-1"}) == "k-1"
    assert item_metric_key({"url": "", "dedup_key": "k-2"}) == "k-2"
    assert item_metric_key({"url": "https://example.com/a"}) == "https://example.com/a"
    assert item_metric_key({"url": "", "dedup_key": ""}) is None
    assert item_metric_key({}) is None
    # 非字符串形态(引擎脏值)不算可用身份
    assert item_metric_key({"url": 42, "dedup_key": None}) is None


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
        # games 条目覆盖四种字段形态:Epic 限免(price_text 直出)、Steam
        # 特惠(无 price_text,模板退 final_price/100)、CS 多店折扣(美元
        # 字符串段 $sale_price(原价 normal_price)+ savings |float|round|int)、
        # GOG 限免(wrap:美元字段与 CS 同名同形态,同一段美元段自动复用)
        "games": [
            {"title": "深埋之星", "url": "https://store.epicgames.com/zh-CN/p/buried-stars",
             "final_price": 0, "original_price": 11600, "discount_pct": 0, "price_text": "0"},
            {"title": "The Outlast Trials", "url": "https://store.steampowered.com/app/1304930",
             "final_price": 1360, "original_price": 13600, "discount_pct": 90},
            # v2 决议②:「下周免费」预告徽标(ISO 切 [:10] 出生效日)
            {"title": "TerraScape 预告", "url": "https://store.epicgames.com/zh-CN/p/terrascape-2b12b1",
             "final_price": 5300, "original_price": 5300, "upcoming_pct": 100,
             "upcoming_start": "2026-10-08T15:00:00.000Z"},
            # v3 决议④:CS 美元形态(sale_price/normal_price/savings_pct 全字符串;
            # URL 已含 URL-encoded dealID,钉不二次编码)。final_price 美元字符串
            # 是 dedup 键价格态配套(10-03-games-dedup-fix)——同时钉模板 elif
            # 链 sale_price 先于 final_price:美元条目走 $ 段,final_price/100
            # 分支(对 str 会 TypeError)永不触达。
            {"title": "Unclaimed World",
             "url": "https://www.cheapshark.com/redirect?dealID=hV1uGbDuy%2FdMUfxYZb%2BPCBj345sgqPwRWHlLgtuAxAk%3D",
             "sale_price": "0.50", "normal_price": "16.99", "savings_pct": "97.057092",
             "final_price": "0.50"},
            # wrap 第四源 GOG 限免双保险形状(裁自 evidence/
            # gog-probe-catalog-freegames-p1.json [1],价格调整为 giveaway 形态
            # ——2026-10-03 无在途限免可实录,同 CS savings 合成先例);
            # final_price 美元字符串同 CS(dedup 键价格态配套)
            {"title": "BROK The InvestiGator - prologue",
             "url": "https://www.gog.com/en/game/brok_the_investigator_prologue",
             "sale_price": "0.00", "normal_price": "19.99", "final_price": "0.00"},
        ],
        # gpu-prices:vs_msrp/vs_* 在无 msrp/trends 上下文时渲染空串(契约),
        # keyword_trends 缺省空列表——模板必须裸渲染存活
        "gpu-prices": [
            {"title": "iGame RTX 5080 公开演示", "url": "https://detail.zol.com.cn/vga/1.html",
             "price": 8999},
        ],
        # news(10-03-news-rss):实录裁剪形状(fixture 同源条目 220477);
        # published 是 RFC 822 串,模板切 [:16] 显示;第二条钉逐条目缺
        # published 省略形态(is defined 守卫下裸渲染存活,不炸 StrictUndefined)
        "news": [
            {"title": "《恶魔城：贝尔蒙特的诅咒》试玩版今日上线",
             "url": "https://www.gcores.com/articles/220477",
             "published": "Fri, 02 Oct 2026 20:01:17 +0800"},
            {"title": "无日期条目形状", "url": "https://www.gcores.com/articles/220478"},
        ],
        # exposure(10-03-aipocket-fusion):掩码-only 命中物形状(title 即
        # 含前 8 后 4 掩码,与 manual-triage 源的合成示例键同源;第二条是
        # 曝面 finding 形状,缺省字段裸渲染存活)
        "exposure": [
            {"title": "openai 疑似泄露凭证 sk-4f1c9…MASKED…abcd(manual)",
             "url": "https://example.com/triage/config.env",
             "provider": "openai", "apikey_masked": "sk-4f1c9…MASKED…abcd",
             "source_type": "manual"},
            {"title": "litellm 未授权读暴露(203.0.113.7)",
             "url": "https://203.0.113.7/v1/models", "host": "203.0.113.7",
             "product": "litellm", "vuln_class": "unauth_read", "risk": 0},
        ],
    }[name]
    renderer = TemplateRenderer()
    expected_marker = {
        "stocks": "NVDA",
        "ai-news": "公开演示标题",
        "wool": "公开演示标题",
        "games": "深埋之星",
        "gpu-prices": "iGame RTX 5080",
        "news": "《恶魔城：贝尔蒙特的诅咒》试玩版今日上线",
        "exposure": "sk-4f1c9…MASKED…abcd",
    }[name]
    # 值级标记(opt-in):钉住换算/退路的输出值,不只是「渲染不炸」。
    # games:Steam 条目无 price_text → 退 final_price/100,1360 分应渲染 13.6;
    # 预告徽标 📅<日期>起免费(v2 决议②);CS 美元段 + 字符串 savings 取整
    # 徽标 $0.50(原价 16.99) -97%(v3 决议④);GOG 限免复用同款美元段
    # $0.00(原价 19.99)(wrap;feishu/telegram 两模板同钓)
    value_markers: dict[str, list[str]] = {
        "games": ["13.6", "📅2026-10-08起免费", "$0.50(原价 16.99)", "-97%", "$0.00(原价 19.99)"],
        # news:RFC 822 published 切 [:16] 出 "Fri, 02 Oct 2026";缺
        # published 条目(逐条目省略形态)裸渲染存活
        "news": ["Fri, 02 Oct 2026", "无日期条目形状"],
    }
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
        # 10-03-vision-pipeline 拍板⑨:aihot 抽 img@src(封面相对路径经
        # urljoin 绝对化)——snippet 同步钉住 image 字段的真实解析形状。
        "html": '<article data-item-id="abc"><h3><a href="/items/abc">公开演示标题</a></h3>'
                '<img src="/assets/cover-abc.jpg" alt="封面"></article>',
        "base": "https://aihot.news/",
        "expect_url": "https://aihot.news/items/abc",
        "expect_title": "公开演示标题",
        "expect_image": "https://aihot.news/assets/cover-abc.jpg",
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
                    # 实录形状:TerraScape 带 upcoming 0% 促销(10-08 起)——
                    # 钉「有预告但非 100%」形态(v2 决议② upcoming_pct 提取)
                    "promotions": {"promotionalOffers": [], "upcomingPromotionalOffers": [
                        {"promotionalOffers": [{
                            "startDate": "2026-10-08T15:00:00.000Z",
                            "endDate": "2026-10-15T15:00:00.000Z",
                            "discountSetting": {"discountType": "PERCENTAGE",
                                                "discountPercentage": 0},
                        }]},
                    ]},
                },
            ]}}},
        },
        "expect_url": "https://store.epicgames.com/zh-CN/p/buried-stars-d7c88c",
        "expect_title": "深埋之星",
        # 第二形状:hash urlSlug 元素照样渲染出正规 pageSlug 链接;无促销元素
        # 的 discount_pct 逐元素省略(不错位、不补 None)
        # 第二差异形状(opt-in)补充:TerraScape 带 upcoming 0% 预告(非 100%
        # 不命中「下周免费」,字段提取本身要钉住)
        "expect_second": {
            "url": "https://store.epicgames.com/zh-CN/p/terrascape-2b12b1",
            "absent": ["discount_pct"],
            "upcoming_pct": 0,
            "upcoming_start": "2026-10-08T15:00:00.000Z",
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
    # games v3 (10-03-games-v3): CheapShark deals 是**顶层数组**(字段同一
    # $[*] 前缀);dealID 已 URL-encoded,url_template 直拼不再编码。两个
    # 元素裁自 evidence/cs-multi.json(2026-10-03 实录,Humble storeID=11):
    # [0] Unclaimed World 97% 命中形状;[1] The Book of Legends ——唯一的人
    # 为调整是 savings 改 "45.0"(非命中形状):三店 top-Savings 页实测
    # 全 ≥50%(top-60 全查无 sub-50),无实录非命中元素可裁,同 gpu-prices
    # zol 合成 markup 先例;其余字段(dealID/价格/storeID)逐字实录。
    ("games", "cheapshark"): {
        "json": [
            {
                "internalName": "UNCLAIMEDWORLD", "title": "Unclaimed World",
                "dealID": "hV1uGbDuy%2FdMUfxYZb%2BPCBj345sgqPwRWHlLgtuAxAk%3D",
                "storeID": "11", "gameID": "107953",
                "salePrice": "0.50", "normalPrice": "16.99", "isOnSale": "1",
                "savings": "97.057092", "metacriticScore": "0",
                "steamAppID": "284100", "releaseDate": 1475539200,
                "lastChange": 1790536189, "dealRating": "7.3",
            },
            {
                "internalName": "THEBOOKOFLEGENDS", "title": "The Book of Legends",
                "dealID": "nlp3qDJuphBKbhB375Uh1maMvh4fQqKur9BHFfgLZqI%3D",
                "storeID": "11", "gameID": "106360",
                "salePrice": "0.59", "normalPrice": "14.99", "isOnSale": "1",
                "savings": "45.0", "metacriticScore": "0",
                "steamAppID": "277470", "releaseDate": 1329523200,
                "lastChange": 1759396067, "dealRating": "0.0",
            },
        ],
        "expect_url": "https://www.cheapshark.com/redirect?dealID=hV1uGbDuy%2FdMUfxYZb%2BPCBj345sgqPwRWHlLgtuAxAk%3D",
        "expect_title": "Unclaimed World",
        # 第二形状(非命中):多元素不错位——items[1] 是自己的 dealID 链接与
        # 美元字段(savings 45.0 < 50,规则不命中);URL-encoded dealID 直拼
        # 钉「不二次编码」(%2F/%3D 原样)
        "expect_second": {
            "url": "https://www.cheapshark.com/redirect?dealID=nlp3qDJuphBKbhB375Uh1maMvh4fQqKur9BHFfgLZqI%3D",
            "sale_price": "0.59",
            "final_price": "0.59",
            "savings_pct": "45.0",
        },
    },
    # games wrap (10-03-games-wrap): GOG catalog 响应是嵌套 $.products[*](区别
    # 于 CS 顶层数组);storeLink 即绝对 URL,url 字段直出(url_template 为
    # None 的反向形态)。两个元素裁自 evidence/gog-probe-catalog-freegames-p1.json
    # (2026-10-03 实录):[0] Whateverland: Prologue 永久免费形状(final=base=
    # "0.00"、discount null)逐字实录;[1] BROK prologue 唯一人为调整是价格改
    # giveaway 双保险形(sale "0.00"/base "19.99")——当日无在途限免可实录
    # (price=between:0,0 全量 419 条皆 base=0,四路交叉验证见
    # gog-probe-summary.json),同 CS savings "45.0" 合成先例;其余字段
    # (id/slug/storeLink/封面)逐字实录。
    ("games", "gog-free"): {
        "json": {"productCount": 2, "pages": 1, "products": [
            {
                "id": "1096877296", "slug": "whateverland_prologue",
                "productType": "game", "title": "Whateverland: Prologue",
                "coverVertical": "https://images.gog-statics.com/fc8da75e14fecef86f1e045e8def81652824570c8d8e25434d7a0e01496f0321.jpg",
                "price": {
                    "final": "$0.00", "base": "$0.00", "discount": None,
                    "finalMoney": {"amount": "0.00", "currency": "USD", "discount": "0.00"},
                    "baseMoney": {"amount": "0.00", "currency": "USD"},
                },
                "storeLink": "https://www.gog.com/en/game/whateverland_prologue",
            },
            {
                "id": "1129298921", "slug": "brok_the_investigator_prologue",
                "productType": "game", "title": "BROK The InvestiGator - prologue",
                "coverVertical": "https://images.gog-statics.com/9c14ad41ca4ae00e80badd6326324603ec495a91d5554688bb872f315b0d9bd6.jpg",
                "price": {
                    "final": "$0.00", "base": "$19.99", "discount": "$19.99",
                    "finalMoney": {"amount": "0.00", "currency": "USD", "discount": "19.99"},
                    "baseMoney": {"amount": "19.99", "currency": "USD"},
                },
                "storeLink": "https://www.gog.com/en/game/brok_the_investigator_prologue",
            },
        ]},
        "expect_url": "https://www.gog.com/en/game/whateverland_prologue",
        "expect_title": "Whateverland: Prologue",
        # 第二形状(限免命中形):storeLink 直出免拼接、美元字符串逐元素不错位
        # (giveaway:sale "0.00"/base "19.99" → 限免双保险;首条 base "0.00"
        # 永久免费形由 expect_second 之外的双保险测试钉住)
        "expect_second": {
            "url": "https://www.gog.com/en/game/brok_the_investigator_prologue",
            "gog_id": "1129298921",
            "image": "https://images.gog-statics.com/9c14ad41ca4ae00e80badd6326324603ec495a91d5554688bb872f315b0d9bd6.jpg",
            "sale_price": "0.00",
            "normal_price": "19.99",
            "final_price": "0.00",
        },
    },
    # news (10-03-news-rss): gcores RSS 实录裁剪 fixture(2026-10-03 探查
    # 取证,逐字裁自 archive/2026-10/10-03-games-wrap/evidence/
    # news-probe-gcores-rss.xml;CDATA description 只留首个 img+p 段保形状)。
    # rss 的 fields 值=entry 属性白名单:首条 220477 全字段形态;第二形状
    # 220471 钉多条目不错位(标题/链接/日期各归各)。
    ("news", "gcores-rss"): {
        "xml": FIXTURES / "news-gcores-rss.xml",
        "expect_url": "https://www.gcores.com/articles/220477",
        "expect_title": "《恶魔城：贝尔蒙特的诅咒》试玩版今日上线",
        "expect_second": {
            "url": "https://www.gcores.com/articles/220471",
            "title": "搜打撤游戏《绝地求生：黑域撤离》宣布中止开发",
            "published": "Fri, 02 Oct 2026 17:03:23 +0800",
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
    elif "xml" in spec:
        # rss snippet(10-03-news-rss):fixture 喂 extract_rss(feedparser 通路)
        assert source.extract.type == "rss", "snippet contract"
        items = extract_rss(spec["xml"].read_text(encoding="utf-8"), source.extract)
    else:
        assert source.extract.type == "list", "snippet contract"
        items = extract_html(spec["html"], source.extract, base_url=spec.get("base", ""))
    assert items, f"{plugin}/{source_name}: extract found nothing"
    assert items[0]["url"] == spec["expect_url"]
    assert items[0].get("title") == spec["expect_title"]
    # 图 URL 抽取(opt-in,10-03-vision-pipeline):img@src 经 urljoin 绝对化
    # 后落 metadata["image"],是图片处理环的候选来源之一。
    if "expect_image" in spec:
        assert items[0].get("image") == spec["expect_image"], (
            f"{plugin}/{source_name}: image 字段(img@src)解析形状漂移"
        )
    # 第二差异形状(opt-in):钉住逐元素提取不错位与字段省略行为,防 fixture
    # 付出的形状成本只被「首条断言」覆盖(games 质检 low 修复)
    second = spec.get("expect_second")
    if second is not None:
        assert len(items) > 1, f"{plugin}/{source_name}: second shape missing"
        for field in second.get("absent", []):
            assert field not in items[1], f"{plugin}/{source_name}: {field} 应逐元素省略"
        # 断言键集随源字段形态扩(v3:CS 美元字符串字段;wrap:GOG 同款美元
        # 字段 + id/image 直出;dedup-fix:CS/GOG final_price 美元字符串;
        # news:RSS RFC 822 published 串)
        for key in ("url", "title", "discount_pct", "expire", "upcoming_pct", "upcoming_start",
                    "sale_price", "savings_pct", "normal_price", "gog_id", "image", "final_price",
                    "published"):
            if key in second:
                assert items[1].get(key) == second[key], f"{plugin}/{source_name}: 第二形状 {key} 错位"


# ---------------------------------------------------------------------------
# Pipeline wiring (constructor fail-fast chain, still network-free)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", OFFICIAL_PLUGINS)
def test_plugin_builds_a_pipeline(name):
    """The category plugs into Pipeline (classify table, rules, routes, tz).

    10-03-vision-daily A2:ai-news 实开 enrich 后,构造期就要解析端点双引用
    (keychain:myia/llm/base_url + keychain:myia/image/api_key,Pipeline
    fail-fast 契约;10-03 桌面零 env 化后 base_url 也走钥匙链,env 注入
    不再需要)。这里注入内存钥匙链——构造面契约是「引用可解析」,
    真端点连通由 task 的真跑验收负责,battery 保持 network-free 且不依赖
    宿主机是否真有那把钥匙(CI/无钥匙链机器照常过)。"""
    backend = InMemoryKeychainBackend()
    backend.set_password(secrets_store.SECRET_SERVICE, "myia/image/api_key", "placeholder")
    backend.set_password(
        secrets_store.SECRET_SERVICE, "myia/llm/base_url", "https://open.bigmodel.cn/api/paas/v4"
    )
    secrets_store.set_backend(backend)
    try:
        pipeline = Pipeline(_load(name))
    finally:
        secrets_store.reset_backend()
    assert pipeline.config.id == name


# ---------------------------------------------------------------------------
# PRD acceptance: 每插件二跑 —— 第二次 dedup 拦截,store 不重复入库
# (previously zero automated coverage; dedup drift degraded silently into
#  per-item dedup_key_error at run time)
# ---------------------------------------------------------------------------


# One representative live source per plugin; second-run payloads change the
# content (new price / new topic) so the fetch-level change fingerprint does
# NOT swallow the second run — the interception under test is the dedup stage.
# (games 例外:键带价格态后,拦截用例要求第二跑提取内容不变——只搅动未被
# extract 的 endDate 来骗过指纹;变价=新键的反向语义由专测钉住。)
def _epic_free_body(
    discount_price: int, fmt_price: str, *, promo_end: str = "2026-10-08T15:00:00.000Z"
) -> dict:
    """One-element Epic freeGamesPromotions body(trimmed evidence 形状).

    Same catalogNs pageSlug → same url_template 渲染;``promo_end`` 搅动**未被
    extract 的字段**(促销窗 endDate),让两跑 body 哈希不同、fetch 层指纹放行
    ——games 键带价格态后(10-03-games-dedup-fix),「拦截」用例的第二跑还必须
    同 ``discount_price``(同 url 同价=同键),变价=新键=入库,由专门测试钉。
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
            "startDate": "2026-10-01T15:00:00.000Z", "endDate": promo_end,
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
        # 键含价后(10-03-games-dedup-fix)「拦截」用例须同 url 同价:两跑
        # discountPrice 都是 0(限免实录形状)→ 同键 {url}-0;endDate 搅动
        # 让 fetch 指纹放行,拦截点钉在 dedup 阶段。变价=新键=入库的语义
        # 由 test_games_two_runs_price_change_creates_new_key 单独钉。
        "payloads": [
            _epic_free_body(0, "0"),
            _epic_free_body(0, "0", promo_end="2026-10-09T15:00:00.000Z"),
        ],
    },
    # gpu-prices(10-03-games-v2):zol 形状合成 markup(真实页被反爬检查页
    # 拦,无法录制——同 v2ex parked 口径);同 URL 换价,拦截点在 dedup
    "gpu-prices": {
        "source": "zol",
        "payloads": [
            '<div class="list-item"><h3><a href="https://detail.zol.com.cn/vga/1.html">'
            'iGame RTX 5080 公开演示</a></h3><span class="price-type">8999</span></div>',
            '<div class="list-item"><h3><a href="https://detail.zol.com.cn/vga/1.html">'
            'iGame RTX 5080 公开演示</a></h3><span class="price-type">9099</span></div>',
        ],
    },
    # news(10-03-news-rss):gcores 形状 XML(实录条目裁剪);第二跑换条目
    # (A → A+B),同 url 条目被 dedup {url} seen 全期拦截,新条目照常入库。
    "news": {
        "source": "gcores-rss",
        "payloads": [
            "<rss version='2.0'><channel><title>机核</title>"
            "<item><title>《恶魔城：贝尔蒙特的诅咒》试玩版今日上线</title>"
            "<link>https://www.gcores.com/articles/220477</link>"
            "<pubDate>Fri, 02 Oct 2026 20:01:17 +0800</pubDate></item>"
            "</channel></rss>",
            "<rss version='2.0'><channel><title>机核</title>"
            "<item><title>《恶魔城：贝尔蒙特的诅咒》试玩版今日上线</title>"
            "<link>https://www.gcores.com/articles/220477</link>"
            "<pubDate>Fri, 02 Oct 2026 20:01:17 +0800</pubDate></item>"
            "<item><title>草蜢工作室宣布与网易游戏分道扬镳</title>"
            "<link>https://www.gcores.com/articles/220435</link>"
            "<pubDate>Thu, 01 Oct 2026 09:55:00 +0800</pubDate></item>"
            "</channel></rss>",
        ],
    },
    # exposure(10-03-aipocket-fusion):scan lane 的 manual-triage 源零网络
    # 零凭据(items 由 engine_options.documents 本地装配,payloads 不被
    # 消费——handler 形状保持一致仅为基建复用);两跑同文档 → dedup {url}
    # seen 拦截,钉链外源引擎进 dedup 全链的接线语义。
    "exposure": {
        "source": "manual-triage",
        "payloads": [],
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
            # 10-03-vision-daily A2:ai-news 的 enrich 已实开;本电池的验收面是
            # extract + dedup.key 接线,精评走真端点(自带 client,不吃注入的
            # MockTransport)——这里显式关掉,保持电池 network-free。
            "enrich": EnrichConfig(enabled=False),
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


def test_games_two_runs_price_change_creates_new_key(tmp_path):
    """10-03-games-dedup-fix 新语义:键 {url}-{final_price} 下**变价=新键**——
    同 url 第二跑价格变化(0 → 100)不被 seen 拦截,新键入库(限免 immediate
    在正式免费日落地的机制基础);同键唯一性仍由上面的通用两跑测试钉
    (同 url 同价=拦截)。这是 v2 决议②缺陷的回归钉:旧纯 {url} 键下,
    预告日推过的条目到免费日被 is_seen 全期吞掉。"""
    store = SQLiteStore(tmp_path / "dedup-price.db")
    plan = {
        "payloads": [
            _epic_free_body(0, "0"),            # 预告期/限免价 0 → 键 {url}-0
            _epic_free_body(100, "¥1.00"),      # 变价 100 → 新键 {url}-100
        ],
    }
    client = httpx.AsyncClient(transport=httpx.MockTransport(_two_run_handler(plan)))
    pipeline = Pipeline(_single_source_config("games"), store=store, client=client)

    result1 = asyncio.run(pipeline.run())
    assert result1.status == "success", result1.stats_dict()
    assert result1.stage("dedup").items_out == 1

    result2 = asyncio.run(pipeline.run())
    assert result2.status == "success", result2.stats_dict()
    # 变价=新键:第二跑不被 seen 拦截(0 skips),照常入库
    assert result2.stage("dedup").skips.get("dedup_seen", 0) == 0, (
        "变价后的条目是新键,不得被旧价格的 seen 记录拦截"
    )
    assert result2.stage("dedup").items_out == 1

    rows = store.list_items()
    assert len(rows) == 2, f"两价格态应各入库一行,当前 {len(rows)}"
    keys = {record.dedup_key for record in rows}
    url = "https://store.epicgames.com/zh-CN/p/buried-stars-d7c88c"
    assert keys == {f"{url}-0", f"{url}-100"}, f"键应带价格态,当前 {keys}"
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
