"""Tests for myia.classify: gold set, dual-signal adjudication, safe rule eval."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from myia.classify import (
    ALL_CATEGORIES,
    CATEGORY_CHANNEL,
    SEVEN_CATEGORIES,
    ClassifyDataError,
    Rule,
    RuleConfigError,
    RuleSyntaxError,
    category_label,
    classify_item,
    classify_title,
    load_rules,
    load_table,
    rules_from_config,
)
from myia.classify.builtin import DEFAULT_TABLE_PATH

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "classify_gold.json"


def _gold_items() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["items"]


# ---------------------------------------------------------------------------
# gold set: real production titles, labels measured from production cat_of
# ---------------------------------------------------------------------------


def test_gold_set_size_within_40_to_60_titles():
    items = _gold_items()
    assert 40 <= len(items) <= 60


def test_gold_set_accuracy_at_least_90_percent():
    items = _gold_items()
    wrong = []
    for row in items:
        got = classify_title(row["title"]).category
        if got != row["expected"]:
            wrong.append({"title": row["title"], "expected": row["expected"], "got": got})
    accuracy = 1 - len(wrong) / len(items)
    assert accuracy >= 0.90, (
        f"金测集准确率 {accuracy:.1%} 低于 90% 目标, 共 {len(wrong)} 条不符: {wrong[:10]}"
    )


def test_gold_set_covers_every_category_including_discards():
    expected_ids = {row["expected"] for row in _gold_items()}
    assert expected_ids >= set(SEVEN_CATEGORIES) | {CATEGORY_CHANNEL, None}


# ---------------------------------------------------------------------------
# dual-signal adjudication: free vs paid conflicts (PRD acceptance item)
# ---------------------------------------------------------------------------


def test_classify_title_free_signal_beats_paid_signal_to_token():
    # 免费(送$)与付费(低至/0.13)撞车 → 免费优先, token/api 语境归 token
    title = "【富可敌国】Fluxion AI丨GPT Pro持续稳定供应，限时低至0.13｜L站新用户留言送$3丨真实产品评测$10"
    assert classify_title(title).category == "token"


def test_classify_title_paid_signal_without_free_signal_never_reaches_freebie():
    # 生产 2026-09-28 纠错回归用例: 付费中转广告绝不进羊毛/token
    title = "【顺风Token】GPT低至0.075x，CCMAX仅0.9x，中秋限时国模0.04x"
    assert classify_title(title).category == "channel"


def test_classify_title_paid_signal_without_free_signal_never_reaches_token():
    # 标题带 claude/中转 等 token 语汇, 但只有付费信号 → 渠道而非 token
    assert classify_title("求推荐claude低价中转").category == "channel"


def test_classify_title_free_signal_beats_primary_category_scan():
    # 免费+付费撞车且标题明显是节点代理 → 仍归羊毛(免费信号绝对优先)
    title = "🎉🎉B2Proxy福利！新用户免费试用 / 动态 $0.7/GB / 静态 $0.12/IP/天 / 不限量 $10/小时"
    result = classify_title(title)
    assert result.category == "freebie"
    tables = {e.table for e in result.matched}
    assert "category:freebie" in tables and "signals.paid" in tables


def test_classify_title_fallback_scan_routes_token_pattern_to_ai_news():
    # 生产语义钉子: 无免费信号时 token 关键词只触发 AI信息 兜底, 不产生 token
    assert classify_title("token额度没用完有点焦虑，哈哈").category == "ai-news"


def test_classify_title_paid_signal_with_release_word_routes_to_channel():
    # 0\.\d 数字价 + 发布 类词: 付费检查先于 AI信息 兜底(生产语义)
    assert classify_title("【webcodex】 v0.4.2 发布，功能上也许近乎完善了。").category == "channel"


# ---------------------------------------------------------------------------
# filters: dead / noise / registration lure
# ---------------------------------------------------------------------------


def test_classify_title_dead_keyword_discards_title():
    result = classify_title("Opus5.5 100刀，仅限开发，CC客户端调用（增加Astra）已耗尽")
    assert result.category is None
    assert any(e.table == "filters.dead" for e in result.matched)


def test_classify_title_noise_keyword_discards_title():
    result = classify_title("论坛版规与积分规则调整公告")
    assert result.category is None
    assert any(e.table == "filters.noise" for e in result.matched)


def test_classify_title_registration_without_direct_give_discards():
    # 生产主人指令: 含"注册"但无直接送钱信号 → 剔除(即使命中 oracle/中转)
    assert classify_title("muse ai/oracle龟壳注册经验分享").category is None
    assert classify_title("【富可敌国】面向小企业的中转站开放注册了").category is None


def test_classify_title_registration_with_direct_give_classifies():
    # 送5000 直接给钱信号豁免注册引流过滤 → 免费 + 额度 → token
    assert classify_title("羊毛速薅，注册送5000额度，签到1000到2000").category == "token"


# ---------------------------------------------------------------------------
# category coverage (one representative per category)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("bybit普通用户的100% AI订阅返现将在10.5取消", "credit-card"),
        ("cline 订阅不需要国外的卡，银联就成功了", "proxy-node"),
        ("之前找咸鱼代充GPT，全程都是自动化的，请问原理是这样的吗？", "buying-agent"),
        ("有没有头铁只用vps用claude没被封的", "server"),
        ("Claude 免费领100$", "token"),
        ("米哈游千亿进军大模型，力争进入第一梯队(附录音)", "ai-news"),
        ("青龙面板 夸克签到领取永久空间", "freebie"),
        ("bestbuy和giftly礼品卡充值", "channel"),
    ],
)
def test_classify_title_covers_every_category(title, expected):
    assert classify_title(title).category == expected


def test_classify_title_normalizes_whitespace_before_matching():
    result = classify_title("Claude   Pro、Max用户   免费领取$100、$250积分额度")
    assert result.category == "token"
    assert "  " not in result.normalized_title


# ---------------------------------------------------------------------------
# trace: 命中明细可追溯
# ---------------------------------------------------------------------------


def test_classify_title_records_matching_keyword_with_span():
    result = classify_title("bybit普通用户的100% AI订阅返现将在10.5取消")
    hits = [e for e in result.matched if e.value == "返现"]
    assert len(hits) == 1
    evidence = hits[0]
    assert evidence.table == "category:credit-card"
    assert evidence.kind == "keyword"
    assert result.normalized_title[evidence.start : evidence.end] == "返现"


def test_classify_title_records_pattern_hits_for_paid_signal():
    result = classify_title("【顺风Token】GPT低至0.075x，CCMAX仅0.9x，中秋限时国模0.04x")
    pattern_hits = [e for e in result.matched if e.kind == "pattern" and e.table == "signals.paid"]
    assert pattern_hits, "付费正则(0\\.\\d)命中应有追溯"
    assert result.category == "channel"


# ---------------------------------------------------------------------------
# table loading: data/code separation + structured validation errors
# ---------------------------------------------------------------------------


def _loaded_table_json() -> dict:
    return json.loads(DEFAULT_TABLE_PATH.read_text(encoding="utf-8"))


def test_load_table_missing_file_raises_structured_error():
    with pytest.raises(ClassifyDataError, match="文件不存在"):
        load_table("/nonexistent/keywords.json")


def test_load_table_unknown_top_field_raises():
    data = _loaded_table_json()
    data["unknown_section"] = {}
    with pytest.raises(ClassifyDataError, match="unknown_section"):
        load_table(_dump_to_tmp(data))


def test_load_table_duplicate_category_id_raises():
    data = _loaded_table_json()
    data["categories"].append(dict(data["categories"][1]))
    with pytest.raises(ClassifyDataError, match="id 重复"):
        load_table(_dump_to_tmp(data))


def test_load_table_two_free_targets_raises():
    data = _loaded_table_json()
    data["categories"][1]["stage"] = "free_target"
    with pytest.raises(ClassifyDataError, match="free_target"):
        load_table(_dump_to_tmp(data))


def test_load_table_invalid_regex_raises_with_field_path():
    data = _loaded_table_json()
    data["categories"][1]["patterns"].append("坏正则(?!未闭合")
    with pytest.raises(ClassifyDataError, match="categories\\[1\\].patterns"):
        load_table(_dump_to_tmp(data))


def test_load_table_token_context_unknown_target_raises():
    data = _loaded_table_json()
    data["signals"]["token_context"]["target_category"] = "no-such-category"
    with pytest.raises(ClassifyDataError, match="target_category"):
        load_table(_dump_to_tmp(data))


def test_load_table_adjudication_target_must_exist():
    data = _loaded_table_json()
    data["adjudication"]["fallback_target"] = "no-such-category"
    with pytest.raises(ClassifyDataError, match="fallback_target"):
        load_table(_dump_to_tmp(data))


def _dump_to_tmp(data: dict) -> str:
    import tempfile

    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(data, handle, ensure_ascii=False)
    handle.close()
    return handle.name


def test_classify_uses_new_keyword_from_data_file_without_code_change():
    # 数据与代码分离验收: AI/用户改 keywords.json 即生效, 不用改代码
    data = _loaded_table_json()
    server = next(c for c in data["categories"] if c["id"] == "server")
    server["keywords"].append("极速云主机")
    table = load_table(_dump_to_tmp(data))
    assert classify_title("便宜出极速云主机三台", table).category == "server"
    # 默认表不受临时表影响
    assert classify_title("便宜出极速云主机三台").category != "server"


# ---------------------------------------------------------------------------
# custom rules: whitelisted AST evaluation (never eval)
# ---------------------------------------------------------------------------


def test_rule_when_abs_change_pct_at_threshold_fires():
    rule = Rule(name="big-move", when="abs(change_pct) >= 3", tag="大波动")
    assert rule.evaluate({"change_pct": 3.0}) is True
    assert rule.evaluate({"change_pct": -3.5}) is True
    assert rule.evaluate({"change_pct": 2.9}) is False


def test_rule_when_string_in_operator_and_chained_compare():
    rule = Rule(name="gpt-related", when="1 <= change_pct <= 2 and 'gpt' in title", tag="gpt")
    assert rule.evaluate({"change_pct": 1.5, "title": "gpt 新价格"}) is True
    assert rule.evaluate({"change_pct": 3.0, "title": "gpt 新价格"}) is False


def test_rule_when_list_literal_and_whitelisted_functions():
    rule = Rule(name="multi", when="len(tags) >= 2 and max(scores) < min([10, 20])", tag="ok")
    assert rule.evaluate({"tags": ["a", "b"], "scores": [3, 5]}) is True
    assert rule.evaluate({"tags": ["a"], "scores": [30, 5]}) is False


def test_rule_when_guarded_missing_field_evaluates_false_without_raising():
    rule = Rule(name="guarded", when="change_pct and change_pct > 3", tag="x")
    assert rule.evaluate({"title": "没有涨跌幅字段"}) is False


def test_rule_when_missing_field_arithmetic_logs_warning_and_skips(caplog):
    rule = Rule(name="unguarded", when="abs(change_pct) >= 3", tag="x")
    with caplog.at_level(logging.WARNING, logger="myia.classify.custom"):
        assert rule.evaluate({"title": "缺字段的条目"}) is False
    assert any(record.levelname == "WARNING" and "unguarded" in record.message for record in caplog.records)


def test_rule_when_division_by_zero_skips_rule():
    rule = Rule(name="bad-div", when="10 / change_pct > 1", tag="x")
    assert rule.evaluate({"change_pct": 0}) is False


def test_rule_when_reads_object_attributes():
    rule = Rule(name="obj", when="change_pct >= 3", tag="x")
    assert rule.evaluate(SimpleNamespace(title="t", change_pct=4)) is True


def test_rule_fields_lists_referenced_names():
    rule = Rule(name="fields", when="abs(change_pct) >= 3 or change_pct <= -3", tag="x")
    assert rule.fields == ("change_pct",)


def test_rule_when_pow_blows_up_skips_instead_of_hanging():
    """``9**9**9``(7 字符)曾把进程挂死在分钟级+GB 内存;现在规模超限按不命中处理。"""
    rule = Rule(name="oom", when="9**9**9 == 1", tag="x")
    assert rule.evaluate({}) is False  # 若回归为无上界求值,这条断言永不返回


def test_evaluate_expression_pow_size_guard_is_structured():
    from myia.classify.custom import RuleEvalError, evaluate_expression

    with pytest.raises(RuleEvalError, match="幂运算规模超限"):
        evaluate_expression("9**9**9", {})


def test_rule_when_huge_sequence_repeat_skips_instead_of_oom():
    """``'a' * 10**14`` 曾以 MemoryError 打穿 Rule.evaluate 隔离;现在被规模轨拒绝。"""
    rule = Rule(name="repeat", when="'a' * (10**14) == 'x'", tag="x")
    assert rule.evaluate({}) is False


def test_eval_node_wraps_memory_error_into_rule_eval_error(monkeypatch):
    """资源类异常(MemoryError)同样包装,绝不逃出 Rule.evaluate 的隔离契约。"""
    import ast as _ast

    from myia.classify import custom as custom_module

    def boom(_left: object, _right: object) -> None:
        raise MemoryError("synthetic")

    monkeypatch.setitem(custom_module._BIN_OPS, _ast.Mult, boom)
    rule = Rule(name="mem", when="'a' * 3 == 'aaa'", tag="x")
    assert rule.evaluate({}) is False


@pytest.mark.parametrize(
    "when",
    [
        "__import__('os').system('true')",  # 任意调用
        "title.upper() == 'X'",  # 属性访问
        "title[0] == 'g'",  # 下标
        "lambda x: x",  # lambda
        "f'{title}'",  # f-string
        "open('/etc/passwd')",  # 非白名单函数
        "round(x, ndigits=2) > 1",  # 关键字参数
        "{'a': 1} == {}",  # 字典字面量
        "[x for x in title]",  # 推导式
        "'a' if title else 'b'",  # 三元
        "title := 'x'",  # 海象
        "",  # 空
    ],
)
def test_rule_when_disallowed_construct_raises_syntax_error(when):
    with pytest.raises(RuleSyntaxError):
        Rule(name="evil", when=when, tag="x")


def test_rule_when_oversized_expression_raises():
    with pytest.raises(RuleSyntaxError, match="超长"):
        Rule(name="long", when="1 + " * 600 + "1", tag="x")


def test_load_rules_unknown_field_fails_fast(tmp_path):
    yaml_file = tmp_path / "plugin.yaml"
    yaml_file.write_text(
        "classify:\n  rules:\n    - name: r1\n      when: \"change_pct >= 3\"\n      tag: t\n      priority: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(RuleConfigError, match="priority"):
        load_rules(str(yaml_file))


def test_load_rules_missing_when_fails_fast(tmp_path):
    yaml_file = tmp_path / "plugin.yaml"
    yaml_file.write_text("classify:\n  rules:\n    - name: r1\n      tag: t\n", encoding="utf-8")
    with pytest.raises(RuleConfigError, match="when"):
        load_rules(str(yaml_file))


def test_rules_from_config_defaults_name_and_tag():
    rules = rules_from_config([{"when": "change_pct >= 3", "tag": "大波动"}, {"name": "r2", "when": "1 > 2"}])
    assert [rule.name for rule in rules] == ["rule-0", "r2"]
    assert [rule.tag for rule in rules] == ["大波动", ""]
    assert rules[0].evaluate({"change_pct": 5}) is True


# ---------------------------------------------------------------------------
# glue: classify_item (category + tags + trace)
# ---------------------------------------------------------------------------


def test_classify_item_returns_category_and_fired_tags():
    rule = Rule(name="big-move", when="abs(change_pct) >= 3", tag="大波动")
    result = classify_item(
        {"title": "羊毛速薅，注册送5000额度，签到1000到2000", "change_pct": 5.0},
        rules=[rule],
    )
    assert result.category == "token"
    assert result.tags == ["大波动"]
    assert result.matched
    assert result.normalized_title.startswith("羊毛速薅")


def test_classify_item_rule_failure_does_not_break_classification(caplog):
    rule = Rule(name="broken", when="abs(change_pct) >= 3", tag="x")
    with caplog.at_level(logging.WARNING, logger="myia.classify.custom"):
        result = classify_item({"title": "Claude 免费领100$"}, rules=[rule])
    assert result.category == "token"
    assert result.tags == []


def test_classify_item_missing_title_raises_type_error():
    with pytest.raises(TypeError, match="title"):
        classify_item({"url": "https://example.com/a"})


# ---------------------------------------------------------------------------
# constants and labels
# ---------------------------------------------------------------------------


def test_seven_categories_constant_shape():
    assert len(SEVEN_CATEGORIES) == 7
    assert CATEGORY_CHANNEL not in SEVEN_CATEGORIES
    assert len(ALL_CATEGORIES) == 8


def test_category_label_returns_production_labels():
    assert category_label("freebie") == "🎁羊毛"
    assert category_label("channel") == "💰渠道"
    assert category_label("ai-news") == "🤖AI信息"
    assert category_label("no-such") is None


def test_classified_titles_reference_known_categories_only():
    known = set(ALL_CATEGORIES)
    for row in _gold_items():
        if row["expected"] is not None:
            assert row["expected"] in known, f"夹具含未知类目: {row['expected']}"


def test_fixture_titles_contain_no_urls_or_credentials():
    url_like = re.compile(r"https?://|\w+://|Bearer |api[_-]?key\s*[=:]|sk-", re.I)
    for row in _gold_items():
        assert not url_like.search(row["title"]), f"夹具标题疑似含 URL/凭据: {row['title']}"
