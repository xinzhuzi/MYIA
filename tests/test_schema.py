"""Tests for myia.schema — 12-section category YAML models and loading.

Mirrors src/myia/schema.py. Covers the PRD (10-01-v01-yaml-schema) acceptance
criteria: the stocks.yaml showcase loads with correct section semantics,
plaintext credentials / illegal cron / unknown fields are refused, every
section's defaults are asserted, and load errors are structured
(field path + error type + reason).
"""

from pathlib import Path

import pytest

from myia.schema import (
    CategoryConfig,
    CredentialResolveError,
    LoadError,
    load_category,
    load_category_file,
    resolve_credential,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
STOCKS_FIXTURE = FIXTURES / "stocks.yaml"


# ---------------------------------------------------------------------------
# Helpers (local to this module: no cross-test shared state)
# ---------------------------------------------------------------------------


def _minimal_data() -> dict:
    """Smallest valid category config; optional sections left out."""
    return {
        "id": "demo",
        "name": "Demo",
        "schedule": "0 9 * * *",
        "sources": [{"name": "example", "url": "https://example.com/list?page={page}"}],
    }


def _load_error(data) -> LoadError:
    """Load `data` and return the raised LoadError (fails the test otherwise)."""
    with pytest.raises(LoadError) as excinfo:
        load_category(data)
    return excinfo.value


def _error_of_type(load_error: LoadError, error_type: str) -> object:
    """The first detail with `error_type`; asserts at least one exists."""
    matches = [d for d in load_error.errors if d.error_type == error_type]
    assert matches, f"expected error_type={error_type!r}, got {load_error.errors}"
    return matches[0]


# ---------------------------------------------------------------------------
# Acceptance: the stocks.yaml showcase loads with correct section semantics
# ---------------------------------------------------------------------------


class TestStocksFixture:
    def test_load_category_file_with_stocks_fixture_succeeds(self):
        cfg = load_category_file(STOCKS_FIXTURE)

        assert cfg.id == "stocks"
        assert cfg.name == "股票情报"
        assert cfg.schedule == "0 9,15 * * 1-5"
        assert cfg.timezone == "Asia/Shanghai"

    def test_stocks_fixture_source_semantics(self):
        cfg = load_category_file(STOCKS_FIXTURE)
        source = cfg.sources[0]

        assert source.name == "yahoo"
        assert source.engine == "auto"
        assert source.url == (
            "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&page={page}"
        )
        assert source.method == "GET"
        # keychain: is valid credential SYNTAX at load; resolution is v0.2.
        assert source.headers["Cookie"] == "keychain:linuxsb_cookie"
        assert source.pagination is not None
        assert source.pagination.mode == "template"
        assert source.pagination.max_pages == 3
        assert source.extract is not None
        assert source.extract.type == "list"
        assert source.extract.item == "div.topic-list tr"
        assert source.extract.fields == {
            "title": "a.title",
            "url": "a.title@href",
            "time": "td.time",
            "symbol": "td.symbol",
        }
        assert source.rate_limit.qps == 0.5
        assert source.rate_limit.jitter == 2.0
        assert source.rate_limit.backoff == "exponential"
        assert source.rate_limit.respect_robots is True
        assert source.proxy == "direct"
        assert source.retry == 3
        # 源级扩展参数 pass through to the engine layer.
        assert source.extra_params == {"symbols": ["NVDA", "AAPL", "TSLA", "0700.HK"]}

    def test_stocks_fixture_section_semantics(self):
        cfg = load_category_file(STOCKS_FIXTURE)

        assert cfg.watchlist.keywords == ["NVDA", "AI芯片", "美股"]
        assert cfg.watchlist.mute == ["广告", "中奖"]
        assert cfg.classify.builtin is True
        assert len(cfg.classify.rules) == 1
        rule = cfg.classify.rules[0]
        assert rule.name == "异动"
        assert rule.when == "abs(change_pct) >= 3"
        assert rule.tag == "大涨/大跌"
        assert cfg.dedup.key == "{symbol}-{date}"
        assert cfg.enrich.enabled is True
        assert cfg.enrich.model == "glm-4-flash"
        assert cfg.enrich.scores == ["value", "relevance", "credibility"]
        assert cfg.enrich.batch == 20
        assert cfg.enrich.cache is True
        assert cfg.enrich.budget_per_run == 50000
        assert len(cfg.push) == 1
        push = cfg.push[0]
        assert push.channel == "feishu_card"
        assert push.target == "env:FEISHU_CHAT_ID"
        assert [r.mode for r in push.route] == ["immediate", "digest", "archive"]
        assert push.template is not None and "{% for item in items %}" in push.template
        assert cfg.storage.retention == "90d"
        assert cfg.storage.retention_days == 90
        assert cfg.storage.vacuum == "monthly"


# ---------------------------------------------------------------------------
# Acceptance: every section's defaults are covered
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_minimal_config_defaults_for_optional_sections(self):
        cfg = load_category(_minimal_data())

        assert cfg.timezone is None  # 缺省跟系统时区
        assert cfg.watchlist.keywords == []
        assert cfg.watchlist.mute == []
        assert cfg.classify.builtin is True
        assert cfg.classify.rules == []
        assert cfg.dedup.key == "{url}"
        assert cfg.enrich.enabled is False
        assert cfg.enrich.model == "glm-4-flash"
        assert cfg.enrich.scores == ["value", "relevance", "credibility"]
        assert cfg.enrich.batch == 20
        assert cfg.enrich.cache is True
        assert cfg.enrich.budget_per_run == 50000
        assert cfg.push == []
        assert cfg.storage.retention == "90d"
        assert cfg.storage.vacuum == "monthly"

    def test_source_defaults_when_fields_omitted(self):
        cfg = load_category(_minimal_data())
        source = cfg.sources[0]

        assert source.engine == "auto"
        assert source.method == "GET"
        assert source.post_body is None
        assert source.headers == {}
        assert source.pagination is None  # 单页,不翻页
        assert source.extract is None  # L3 自动结构化兜底
        assert source.rate_limit.qps == 0.5
        assert source.rate_limit.jitter == 0.0
        assert source.rate_limit.backoff == "exponential"
        assert source.rate_limit.respect_robots is True
        assert source.proxy == "direct"
        assert source.retry == 3
        assert source.extra_params == {}

    def test_rate_limit_jitter_normalizes_duration_strings(self):
        data = _minimal_data()
        source = data["sources"][0]
        source["rate_limit"] = {"jitter": "2s"}
        assert load_category(data).sources[0].rate_limit.jitter == 2.0

        data["sources"] = [dict(source, rate_limit={"jitter": "500ms"})]
        assert load_category(data).sources[0].rate_limit.jitter == pytest.approx(0.5)

        data["sources"] = [dict(source, rate_limit={"jitter": "1m"})]
        assert load_category(data).sources[0].rate_limit.jitter == 60.0

        data["sources"] = [dict(source, rate_limit={"jitter": 3})]
        assert load_category(data).sources[0].rate_limit.jitter == 3.0

    def test_storage_retention_parses_days_and_weeks(self):
        data = _minimal_data()
        data["storage"] = {"retention": "2w"}
        cfg = load_category(data)
        assert cfg.storage.retention_days == 14

    def test_timezone_defaults_resolve_later_but_validate_when_given(self):
        data = _minimal_data()
        data["timezone"] = "Asia/Shanghai"
        assert load_category(data).timezone == "Asia/Shanghai"


# ---------------------------------------------------------------------------
# Acceptance: plaintext credentials / illegal cron / unknown fields refused
# ---------------------------------------------------------------------------


class TestCredentialRefusal:
    def test_source_with_plaintext_cookie_header_rejected(self):
        data = _minimal_data()
        data["sources"][0]["headers"] = {"Cookie": "sessionid=abc123; theme=light"}

        error = _load_error(data)
        detail = _error_of_type(error, "credential_plaintext")
        assert detail.path == "$.sources[0].headers.Cookie"
        assert "禁明文" in detail.message

    def test_source_with_plaintext_api_key_header_rejected(self):
        data = _minimal_data()
        data["sources"][0]["headers"] = {"X-Api-Key": "sk-live-9f8e7d6c"}

        error = _load_error(data)
        detail = _error_of_type(error, "credential_plaintext")
        assert detail.path == "$.sources[0].headers.X-Api-Key"

    def test_source_with_auth_scheme_but_no_reference_rejected(self):
        # "Bearer <plaintext>" is still plaintext — the ref itself is missing.
        data = _minimal_data()
        data["sources"][0]["headers"] = {"Authorization": "Bearer sk-abc123"}

        error = _load_error(data)
        _error_of_type(error, "credential_plaintext")

    def test_source_with_mixed_plaintext_and_ref_rejected(self):
        # A ref next to plaintext cookie pairs would leak the plaintext half.
        data = _minimal_data()
        data["sources"][0]["headers"] = {"Cookie": "sessionid=abc; token=env:TOKEN"}

        error = _load_error(data)
        _error_of_type(error, "credential_plaintext")

    def test_source_with_env_and_keychain_refs_loads(self):
        data = _minimal_data()
        data["sources"][0]["headers"] = {
            "Authorization": "Bearer env:AIPOCKET_TOKEN",
            "Cookie": "keychain:linuxsb_cookie",
        }
        cfg = load_category(data)
        assert cfg.sources[0].headers["Authorization"] == "Bearer env:AIPOCKET_TOKEN"

    def test_push_target_with_plaintext_rejected(self):
        data = _minimal_data()
        data["push"] = [
            {"channel": "feishu_card", "target": "https://open.feishu.cn/hook/abc123"}
        ]

        error = _load_error(data)
        detail = _error_of_type(error, "credential_plaintext")
        assert detail.path == "$.push[0].target"

    def test_push_target_with_bearer_scheme_rejected(self):
        # Targets are pure refs; an auth-scheme prefix only makes sense in headers.
        data = _minimal_data()
        data["push"] = [{"channel": "telegram", "target": "Bearer env:BOT_TOKEN"}]

        error = _load_error(data)
        detail = _error_of_type(error, "invalid_credential_ref")
        assert detail.path == "$.push[0].target"

    def test_push_with_keychain_target_loads(self):
        # keychain: is valid syntax at load; only resolution is v0.2-gated.
        data = _minimal_data()
        data["push"] = [{"channel": "telegram", "target": "keychain:myia_bot_token"}]
        assert load_category(data).push[0].target == "keychain:myia_bot_token"


class TestCronAndTimezoneRefusal:
    def test_config_with_illegal_cron_rejected(self):
        data = _minimal_data()
        data["schedule"] = "not-a-cron"

        error = _load_error(data)
        detail = _error_of_type(error, "invalid_cron")
        assert detail.path == "$.schedule"
        assert "cron" in detail.message

    def test_config_with_out_of_range_cron_field_rejected(self):
        data = _minimal_data()
        data["schedule"] = "0 25 * * *"  # hour 25 does not exist

        error = _load_error(data)
        _error_of_type(error, "invalid_cron")

    def test_config_with_invalid_engine_enum_rejected(self):
        data = _minimal_data()
        data["sources"][0]["engine"] = "fircrawl"  # typo of a real engine name

        error = _load_error(data)
        detail = _error_of_type(error, "invalid_value")
        assert detail.path == "$.sources[0].engine"

    def test_config_with_invalid_timezone_rejected(self):
        data = _minimal_data()
        data["timezone"] = "Mars/Olympus"

        error = _load_error(data)
        detail = _error_of_type(error, "invalid_timezone")
        assert detail.path == "$.timezone"


class TestUnknownFieldRefusal:
    def test_config_with_unknown_top_level_field_rejected(self):
        data = _minimal_data()
        data["notify"] = {"email": "a@b.c"}

        error = _load_error(data)
        detail = _error_of_type(error, "unknown_field")
        assert detail.path == "$.notify"

    def test_config_with_unknown_section_field_rejected(self):
        # Strictness is not top-level only: typos inside a section are caught too.
        data = _minimal_data()
        data["enrich"] = {"enabled": False, "modle": "glm-4-flash"}

        error = _load_error(data)
        detail = _error_of_type(error, "unknown_field")
        assert detail.path == "$.enrich.modle"

    def test_config_with_unknown_source_field_is_kept_for_engine(self):
        # 源级扩展参数 (e.g. symbols) are the deliberate exception: pass-through.
        data = _minimal_data()
        data["sources"][0]["symbols"] = ["NVDA"]
        cfg = load_category(data)
        assert cfg.sources[0].extra_params == {"symbols": ["NVDA"]}


# ---------------------------------------------------------------------------
# Cross-field semantics (fail-fast on AI-generation quality issues)
# ---------------------------------------------------------------------------


class TestCrossFieldRules:
    def test_post_source_without_post_body_rejected(self):
        data = _minimal_data()
        data["sources"][0]["method"] = "POST"

        error = _load_error(data)
        detail = _error_of_type(error, "missing_post_body")
        assert detail.path == "$.sources[0].post_body"

    def test_get_source_with_post_body_rejected(self):
        data = _minimal_data()
        data["sources"][0]["post_body"] = {"q": "gpu"}

        error = _load_error(data)
        _error_of_type(error, "post_body_on_get")

    def test_template_pagination_without_page_placeholder_rejected(self):
        data = _minimal_data()
        data["sources"][0]["url"] = "https://example.com/list"  # no {page}
        data["sources"][0]["pagination"] = {"mode": "template", "max_pages": 3}

        error = _load_error(data)
        detail = _error_of_type(error, "missing_page_placeholder")
        assert detail.path == "$.sources[0].url"

    def test_selector_pagination_without_selector_rejected(self):
        data = _minimal_data()
        data["sources"][0]["url"] = "https://example.com/latest"
        data["sources"][0]["pagination"] = {"mode": "selector"}

        error = _load_error(data)
        detail = _error_of_type(error, "missing_pagination_selector")
        assert detail.path == "$.sources[0].pagination.selector"

    def test_extract_list_without_item_selector_rejected(self):
        data = _minimal_data()
        data["sources"][0]["extract"] = {
            "type": "list",
            "fields": {"title": "a.title", "url": "a.title@href"},
        }

        error = _load_error(data)
        detail = _error_of_type(error, "missing_extract_item")
        assert detail.path == "$.sources[0].extract.item"

    def test_extract_list_without_url_field_rejected(self):
        # Dedup keys rely on the URL; a list extraction without it is unusable.
        data = _minimal_data()
        data["sources"][0]["extract"] = {
            "type": "list",
            "item": "div.post",
            "fields": {"title": "a.title"},
        }

        error = _load_error(data)
        detail = _error_of_type(error, "missing_url_field")
        assert detail.path == "$.sources[0].extract.fields"

    def test_dedup_key_with_title_fingerprint_rejected(self):
        data = _minimal_data()
        data["dedup"] = {"key": "{title}"}

        error = _load_error(data)
        detail = _error_of_type(error, "title_fingerprint_forbidden")
        assert detail.path == "$.dedup.key"

    def test_dedup_key_without_placeholder_rejected(self):
        data = _minimal_data()
        data["dedup"] = {"key": "constant-key"}

        error = _load_error(data)
        _error_of_type(error, "invalid_dedup_key")

    def test_enrich_enabled_without_scores_rejected(self):
        data = _minimal_data()
        data["enrich"] = {"enabled": True, "scores": []}

        error = _load_error(data)
        detail = _error_of_type(error, "missing_scores")
        assert detail.path == "$.enrich.scores"

    def test_source_url_without_http_scheme_rejected(self):
        data = _minimal_data()
        data["sources"][0]["url"] = "ftp://example.com/list"

        error = _load_error(data)
        detail = _error_of_type(error, "invalid_url")
        assert detail.path == "$.sources[0].url"

    def test_source_with_invalid_proxy_rejected(self):
        data = _minimal_data()
        data["sources"][0]["proxy"] = "pool:"

        error = _load_error(data)
        detail = _error_of_type(error, "invalid_proxy")
        assert detail.path == "$.sources[0].proxy"

    def test_empty_sources_list_rejected(self):
        data = _minimal_data()
        data["sources"] = []

        error = _load_error(data)
        detail = _error_of_type(error, "too_short")
        assert detail.path == "$.sources"

    def test_storage_retention_with_invalid_format_rejected(self):
        data = _minimal_data()
        data["storage"] = {"retention": "forever"}

        error = _load_error(data)
        detail = _error_of_type(error, "invalid_retention")
        assert detail.path == "$.storage.retention"

    def test_stdout_push_with_target_rejected(self):
        data = _minimal_data()
        data["push"] = [{"channel": "stdout", "target": "env:WHATEVER"}]

        error = _load_error(data)
        _error_of_type(error, "unexpected_target")

    def test_channel_push_without_target_rejected(self):
        data = _minimal_data()
        data["push"] = [{"channel": "telegram"}]

        error = _load_error(data)
        detail = _error_of_type(error, "missing_target")
        assert detail.path == "$.push[0].target"


class TestExtractUrlTemplate:
    """D1(task 10-03-games):extract.url_template 微扩展 —— url 字段或模板二选一。

    背景见 plugins/stocks.yaml 记录的 open issue:「json_path cannot express
    "item URL = f(field)"」;响应只有 slug/appid 的 API 源(Epic/Steam)靠
    url_template 渲染出可点链接。占位语法沿用 dedup.key 的 {field} 纯占位。
    """

    def test_json_path_with_url_template_instead_of_url_field_loads(self):
        data = _minimal_data()
        data["sources"][0]["extract"] = {
            "type": "json_path",
            "url_template": "https://store.example.com/p/{slug}",
            "fields": {"title": "$.elements[*].title", "slug": "$.elements[*].slug"},
        }
        extract = load_category(data).sources[0].extract
        assert extract is not None
        assert extract.url_template == "https://store.example.com/p/{slug}"

    def test_url_template_without_placeholder_rejected(self):
        data = _minimal_data()
        data["sources"][0]["extract"] = {
            "type": "json_path",
            "url_template": "https://store.example.com/p/free-now",
            "fields": {"url": "$[*].u", "slug": "$[*].slug"},
        }

        error = _load_error(data)
        detail = _error_of_type(error, "invalid_url_template")
        assert detail.path == "$.sources[0].extract.url_template"

    def test_url_template_placeholder_not_in_fields_rejected(self):
        """占位符-字段交叉校验(同 dedup.key 先例):拼错的占位符装载即拒——
        否则运行期整源条目 url 恒渲染为空串、被管线逐条拒成 invalid_item,
        AI 写错字段名 = 静默整源全灭。逐条目缺「值」(urlSlug=null 元素)不受
        影响,那是渲染层的正常路径。"""
        data = _minimal_data()
        data["sources"][0]["extract"] = {
            "type": "json_path",
            "url_template": "https://store.example.com/p/{wrong_field}",
            "fields": {"title": "$[*].title", "slug": "$[*].slug"},
        }

        error = _load_error(data)
        detail = _error_of_type(error, "invalid_url_template")
        assert detail.path == "$.sources[0].extract.url_template"
        assert "wrong_field" in detail.message

        # 占位符全部落在 fields 里则装载通过(games.yaml 双源即此形态)
        data["sources"][0]["extract"] = {
            "type": "json_path",
            "url_template": "https://store.example.com/p/{slug}",
            "fields": {"title": "$[*].title", "slug": "$[*].slug"},
        }
        assert load_category(data).sources[0].extract is not None

    def test_url_field_and_template_are_either_or(self):
        """矩阵:只有 url ✓ / 只有 template ✓ / 都缺 ✗(旧 yaml 零影响)。"""
        base = {"type": "json_path"}
        only_url = dict(base, fields={"url": "$[*].u"})
        only_template = dict(base, url_template="https://x/{slug}", fields={"slug": "$[*].s"})

        data = _minimal_data()
        data["sources"][0]["extract"] = only_url
        assert load_category(data).sources[0].extract is not None

        data["sources"][0]["extract"] = only_template
        assert load_category(data).sources[0].extract is not None

        data["sources"][0]["extract"] = dict(base, fields={"title": "$[*].t"})
        error = _load_error(data)
        detail = _error_of_type(error, "missing_url_field")
        assert detail.path == "$.sources[0].extract.fields"

    def test_url_field_wins_when_both_declared(self):
        """定死(implement 步骤 1):都有 = url 字段胜出,template 静默不用 ——
        不为此新增报错分支(引擎侧同样让 url 字段值胜出)。"""
        data = _minimal_data()
        data["sources"][0]["extract"] = {
            "type": "json_path",
            "url_template": "https://x/{slug}",
            "fields": {"url": "$[*].u", "slug": "$[*].s"},
        }
        cfg = load_category(data)  # 不拒载
        assert cfg.sources[0].extract is not None
        assert "url" in cfg.sources[0].extract.fields

    def test_item_type_rejects_url_template(self):
        """单页源(item)条目 url 即请求 URL,配模板即拒(与 item 选择器语义对齐)。"""
        data = _minimal_data()
        data["sources"][0]["extract"] = {
            "type": "item",
            "url_template": "https://x/{slug}",
            "fields": {"title": "h1"},
        }

        error = _load_error(data)
        detail = _error_of_type(error, "unexpected_url_template")
        assert detail.path == "$.sources[0].extract.url_template"

    def test_list_type_accepts_url_template(self):
        data = _minimal_data()
        data["sources"][0]["extract"] = {
            "type": "list",
            "item": "div.post",
            "url_template": "https://x/{slug}",
            "fields": {"title": "a.title", "slug": "span.slug"},
        }
        extract = load_category(data).sources[0].extract
        assert extract is not None and extract.url_template is not None


class TestExtractRss:
    """10-03-news-rss:extract.type=rss —— fields 值=feedparser entry 属性白名单。

    RSS 源现状(探查实证):static_html 引擎硬接 RSS 会因 ``<link>`` void 元素
    拿不到条目 URL 而静默零产出,故加专用提取类型。白名单( title/link/
    published/updated/summary/author)封闭:拼错属性名装载即拒——同
    url_template 占位符交叉校验的动机,AI 写错 = 加载期可检出,不是运行期
    整源静默缺失。url 必填沿 list/json_path 同规(条目 url=entry.link,
    去重键根基);item 选择器与 url_template 对 rss 均互斥。
    """

    def test_rss_valid_whitelisted_fields_load(self):
        data = _minimal_data()
        data["sources"][0]["extract"] = {
            "type": "rss",
            "fields": {"title": "title", "url": "link", "published": "published"},
        }
        extract = load_category(data).sources[0].extract
        assert extract is not None
        assert extract.type == "rss"
        assert extract.item is None and extract.url_template is None

    def test_rss_field_outside_whitelist_rejected(self):
        """拼错属性名(pubdate→应为 published)装载即拒,path 指到 fields.<键>。"""
        data = _minimal_data()
        data["sources"][0]["extract"] = {
            "type": "rss",
            "fields": {"title": "title", "url": "link", "published": "pubdate"},
        }

        error = _load_error(data)
        detail = _error_of_type(error, "invalid_rss_field")
        assert detail.path == "$.sources[0].extract.fields.published"
        assert "pubdate" in detail.message

    def test_rss_missing_url_field_rejected(self):
        # 条目 url=entry.link;没有 url 字段的 rss 提取喂不动去重键。
        data = _minimal_data()
        data["sources"][0]["extract"] = {
            "type": "rss",
            "fields": {"title": "title", "published": "published"},
        }

        error = _load_error(data)
        detail = _error_of_type(error, "missing_url_field")
        assert detail.path == "$.sources[0].extract.fields"

    def test_rss_rejects_item_selector(self):
        # item 选择器是 CSS 语义,rss 沿用 list 外一律互斥的现有结构。
        data = _minimal_data()
        data["sources"][0]["extract"] = {
            "type": "rss",
            "item": "div.entry",
            "fields": {"title": "title", "url": "link"},
        }

        error = _load_error(data)
        detail = _error_of_type(error, "unexpected_extract_item")
        assert detail.path == "$.sources[0].extract.item"

    def test_rss_rejects_url_template(self):
        # rss 条目 url 由 fields.url←entry.link 映射,模板无消费出口,配即拒
        # (防「装载通过但运行期模板静默不用」的半支持路径)。
        data = _minimal_data()
        data["sources"][0]["extract"] = {
            "type": "rss",
            "url_template": "https://x/{title}",
            "fields": {"title": "title", "url": "link"},
        }

        error = _load_error(data)
        detail = _error_of_type(error, "unexpected_url_template")
        assert detail.path == "$.sources[0].extract.url_template"


# ---------------------------------------------------------------------------
# Acceptance: structured load errors (field path + reason) for doctor/agents
# ---------------------------------------------------------------------------


class TestStructuredErrors:
    def test_load_error_details_are_structured(self):
        data = _minimal_data()
        data["sources"][0]["headers"] = {"Cookie": "sessionid=abc"}

        error = _load_error(data)
        detail = error.errors[0]
        assert detail.path == "$.sources[0].headers.Cookie"
        assert detail.error_type == "credential_plaintext"
        assert detail.message  # human-readable Chinese reason

        as_dict = error.to_dict()
        assert as_dict["source"] is None
        assert as_dict["errors"][0]["path"] == "$.sources[0].headers.Cookie"
        assert as_dict["errors"][0]["error_type"] == "credential_plaintext"

    def test_load_error_collects_multiple_errors_at_once(self):
        data = _minimal_data()
        data["unknown_top"] = 1
        data["sources"][0]["headers"] = {"Cookie": "sessionid=abc"}

        error = _load_error(data)
        types = {d.error_type for d in error.errors}
        assert {"unknown_field", "credential_plaintext"} <= types

    def test_load_error_message_is_human_readable_chinese(self):
        data = _minimal_data()
        data["sources"][0]["headers"] = {"Cookie": "sessionid=abc"}

        error = _load_error(data)
        text = str(error)
        assert "品类 YAML 校验失败" in text
        assert "$.sources[0].headers.Cookie" in text

    def test_load_category_with_non_mapping_root_rejected(self):
        error = _load_error(["just", "a", "list"])
        detail = error.errors[0]
        assert detail.path == "$"
        assert detail.error_type == "invalid_root"

    def test_load_category_file_with_yaml_syntax_error_raises_load_error(self, tmp_path):
        bad_file = tmp_path / "broken.yaml"
        bad_file.write_text("id: [unclosed\n  name: oops\n", encoding="utf-8")

        with pytest.raises(LoadError) as excinfo:
            load_category_file(bad_file)
        detail = excinfo.value.errors[0]
        assert detail.error_type == "yaml_parse_error"
        assert excinfo.value.source == str(bad_file)

    def test_load_category_file_with_missing_file_raises_load_error(self, tmp_path):
        missing = tmp_path / "nope.yaml"

        with pytest.raises(LoadError) as excinfo:
            load_category_file(missing)
        assert excinfo.value.errors[0].error_type == "file_not_found"


# ---------------------------------------------------------------------------
# env: / keychain: resolution (env only in v0.1; keychain reports v0.2)
# ---------------------------------------------------------------------------


class TestCredentialResolution:
    def test_resolve_credential_with_env_ref_returns_env_value(self, monkeypatch):
        monkeypatch.setenv("MYIA_TEST_TOKEN", "secret-value-123")

        assert resolve_credential("env:MYIA_TEST_TOKEN") == "secret-value-123"

    def test_resolve_credential_with_bearer_env_ref_keeps_scheme(self, monkeypatch):
        monkeypatch.setenv("MYIA_TEST_TOKEN", "secret-value-123")

        assert resolve_credential("Bearer env:MYIA_TEST_TOKEN") == "Bearer secret-value-123"

    def test_resolve_credential_with_missing_env_var_raises(self, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_TOKEN", raising=False)

        with pytest.raises(CredentialResolveError) as excinfo:
            resolve_credential("env:MYIA_TEST_TOKEN")
        assert excinfo.value.code == "env_var_missing"
        assert "MYIA_TEST_TOKEN" in str(excinfo.value)

    def test_resolve_credential_with_noncanonical_keychain_name_raises(self):
        """keychain: 已在 v0.2 实装;非 myia/<scope>/<name> 的旧扁平名在触碰
        钥匙链之前就被拒绝(确定性,零真实钥匙串触碰)。"""
        with pytest.raises(CredentialResolveError) as excinfo:
            resolve_credential("keychain:linuxsb_cookie")
        assert excinfo.value.code == "invalid_secret_name"
        assert "myia/<scope>/<name>" in str(excinfo.value)  # 指明规范名空间

    def test_resolve_credential_with_plain_value_raises(self):
        with pytest.raises(CredentialResolveError) as excinfo:
            resolve_credential("sessionid=abc123")
        assert excinfo.value.code == "invalid_credential_ref"


# ---------------------------------------------------------------------------
# Model sanity (public API surface used by later modules)
# ---------------------------------------------------------------------------


def test_category_config_validates_into_typed_model():
    assert callable(CategoryConfig.model_validate)
    cfg = load_category(_minimal_data())
    assert isinstance(cfg, CategoryConfig)


# ---------------------------------------------------------------------------
# images: sidecar 节(10-03-vision-pipeline 图片处理环)
# ---------------------------------------------------------------------------


class TestImagesSection:
    """照 aggregate/baseline 同门:PrivateAttr sidecar,$.images 前缀结构化错误。"""

    def test_absent_section_leaves_config_images_none(self):
        assert load_category(_minimal_data()).images is None

    def test_null_section_treated_as_absent(self):
        data = _minimal_data()
        data["images"] = None
        assert load_category(data).images is None

    def test_defaults_match_pinned_decisions(self):
        """缺省:false / 3 / 30 / 10KB / off / 引擎按 vision.yaml(拍板③④)。"""
        data = _minimal_data()
        data["images"] = {"enabled": True}
        images = load_category(data).images
        assert images is not None
        assert images.enabled is True
        assert images.max_images == 3
        assert images.max_per_run == 30
        assert images.min_bytes == 10_240
        assert images.vl == "off"
        assert images.ocr_engine is None

    def test_detail_defaults_off_with_max_ten(self):
        """详情页追拍缺省零进入(10-03-detail-images AC1):false / 10。"""
        data = _minimal_data()
        data["images"] = {"enabled": True}
        images = load_category(data).images
        assert images is not None
        assert images.detail_fetch is False
        assert images.detail_max_items == 10

    def test_detail_full_pair_loads(self):
        data = _minimal_data()
        data["images"] = {"enabled": True, "detail_fetch": True, "detail_max_items": 25}
        images = load_category(data).images
        assert images is not None
        assert images.detail_fetch is True
        assert images.detail_max_items == 25

    def test_detail_max_items_range_enforced(self):
        """1-50 越界拒载,错误路径钉到 $.images.detail_max_items。"""
        for bad in (0, 51):
            data = _minimal_data()
            data["images"] = {"enabled": True, "detail_max_items": bad}
            errors = _load_error(data).errors
            assert errors, "detail_max_items 越界必须拒载"
            assert errors[0].path == "$.images.detail_max_items"

    def test_full_section_loads(self):
        data = _minimal_data()
        data["images"] = {
            "enabled": True,
            "max_images": 5,
            "max_per_run": 60,
            "min_bytes": 2048,
            "vl": "local",
            "ocr_engine": "rapidocr",
        }
        images = load_category(data).images
        assert images is not None
        assert (images.max_images, images.max_per_run, images.min_bytes) == (5, 60, 2048)
        assert images.vl == "local"
        assert images.ocr_engine == "rapidocr"

    def test_non_mapping_section_rejected_with_path(self):
        data = _minimal_data()
        data["images"] = "yes"
        detail = _error_of_type(_load_error(data), "invalid_images_section")
        assert detail.path == "$.images"

    def test_unknown_field_rejected_with_path(self):
        data = _minimal_data()
        data["images"] = {"enabled": True, "oops": 1}
        detail = _error_of_type(_load_error(data), "unknown_field")
        assert detail.path == "$.images.oops"

    def test_vl_enum_rejected_with_path(self):
        data = _minimal_data()
        data["images"] = {"enabled": True, "vl": "remote"}
        detail = _error_of_type(_load_error(data), "invalid_value")
        assert detail.path == "$.images.vl"

    def test_ocr_engine_enum_rejected_with_path(self):
        data = _minimal_data()
        data["images"] = {"enabled": True, "ocr_engine": "tesseract"}
        detail = _error_of_type(_load_error(data), "invalid_value")
        assert detail.path == "$.images.ocr_engine"

    def test_max_images_range_enforced(self):
        for bad in (0, 11):
            data = _minimal_data()
            data["images"] = {"enabled": True, "max_images": bad}
            errors = _load_error(data).errors
            assert errors, "max_images 越界必须拒载"
            assert errors[0].path == "$.images.max_images"

    def test_min_bytes_range_enforced(self):
        data = _minimal_data()
        data["images"] = {"enabled": True, "min_bytes": 0}
        assert _load_error(data).errors[0].path == "$.images.min_bytes"

    def test_images_errors_merge_with_section_errors(self):
        """sidecar 错误与 12 节错误同报(与 plugin/baseline/aggregate 同一契约)。"""
        data = _minimal_data()
        data["schedule"] = "not-cron"
        data["images"] = {"vl": "bogus"}
        load_error = _load_error(data)
        paths = {detail.path for detail in load_error.errors}
        assert "$.schedule" in paths
        assert "$.images.vl" in paths
