"""skill/SKILL.md ↔ src/myia/schema.py anti-drift tests (task 10-01-v03-agent-skill).

SKILL.md is the agent-facing 12-section quick reference; the PRD requires it to
stay field-for-field identical to the pydantic models in ``myia.schema`` and to
teach YAML an agent can actually load. Three mechanisms lock that:

1. **Field tables** — every ``### <title>(<Model>)`` header in SKILL.md is
   followed by a markdown table documenting that model's fields as
   ``| `field` | `default` | semantics |``. The test regex-extracts the field
   names and default tokens and asserts, per section:
   documented fields == ``Model.model_fields`` (both directions — no missing,
   no stale), and each documented default equals the live pydantic default.
2. **Enum table** — the ``### 2.1`` vocabulary table maps schema constants
   (``ENGINES``, ``PUSH_CHANNELS``, …) to their values; token sets must equal
   the module-level tuples exactly.
3. **Example YAML blocks** — every ```yaml fenced block in SKILL.md (and in
   docs/write-a-plugin.md) must load through the real entry point
   :func:`myia.schema.load_category`.

docs/write-a-plugin.md is additionally pinned to the same 12-section vocabulary
(single-source-of-truth rule: the docs site and SKILL.md may paraphrase, never
disagree), and both documents must keep cross-referencing each other.

These tests make doc drift a red test instead of a silent agent-misleading doc.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from myia import schema
from myia.schema import (
    CategoryConfig,
    ClassifyConfig,
    ClassifyRuleConfig,
    DedupConfig,
    EnrichConfig,
    ExtractConfig,
    PaginationConfig,
    PushConfig,
    RateLimitConfig,
    RouteRuleConfig,
    SourceConfig,
    StorageConfig,
    WatchlistConfig,
    load_category,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / "skill" / "SKILL.md"
WRITE_A_PLUGIN_MD = REPO_ROOT / "docs" / "write-a-plugin.md"

#: SKILL.md section header -> pydantic model. The header format
#: ``### <title>(<ModelName>)`` is the parse anchor; ``###`` headers whose
#: parenthesized suffix is not a schema model name are ignored on purpose.
SECTION_MODELS: dict[str, type[BaseModel]] = {
    "CategoryConfig": CategoryConfig,
    "SourceConfig": SourceConfig,
    "PaginationConfig": PaginationConfig,
    "ExtractConfig": ExtractConfig,
    "RateLimitConfig": RateLimitConfig,
    "WatchlistConfig": WatchlistConfig,
    "ClassifyConfig": ClassifyConfig,
    "ClassifyRuleConfig": ClassifyRuleConfig,
    "DedupConfig": DedupConfig,
    "EnrichConfig": EnrichConfig,
    "PushConfig": PushConfig,
    "RouteRuleConfig": RouteRuleConfig,
    "StorageConfig": StorageConfig,
}

#: Models whose own default is a nested section instance: SKILL.md documents
#: those cells as ``见 <section> 节`` instead of repeating the nested table.
MODEL_DEFAULT_SECTION: dict[str, str] = {
    "RateLimitConfig": "rate_limit",
    "WatchlistConfig": "watchlist",
    "ClassifyConfig": "classify",
    "DedupConfig": "dedup",
    "EnrichConfig": "enrich",
    "StorageConfig": "storage",
}

#: The nine fail-fast vocabularies SKILL.md's enum table must mirror exactly.
ENUM_CONSTANTS = (
    "ENGINES",
    "PAGINATION_MODES",
    "EXTRACT_TYPES",
    "BACKOFF_POLICIES",
    "PUSH_CHANNELS",
    "ROUTE_MODES",
    "ENRICH_SCORES",
    "VACUUM_CADENCES",
    "CREDENTIAL_KEY_SUFFIXES",
)

_SECTION_HEADER_RE = re.compile(r"^###\s+.*\((?P<model>[A-Z][A-Za-z]+)\)\s*$")
_HEADER_RE = re.compile(r"^#{1,6}\s")
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")

#: 各通道凭据约定的缺省 env 引用(与 src/myia/push/*.py 的 DEFAULT_*_ENV_REF
#: 及 SKILL.md §2.13 同源;docs/write-a-plugin.md 的「各通道凭据约定」指向它)。
_CHANNEL_CREDENTIAL_ENV_REFS = (
    "env:FEISHU_CHAT_ID",
    "env:FEISHU_BOT_TOKEN",
    "env:TELEGRAM_CHAT_ID",
    "env:TELEGRAM_BOT_TOKEN",
    "env:MYIA_WEBHOOK_URL",
)


# ---------------------------------------------------------------------------
# Parsing helpers (markdown is a fixture format here — kept strict on purpose)
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _first_code_span(cell: str) -> str | None:
    match = _CODE_SPAN_RE.search(cell)
    return match.group(1) if match else None


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _parse_field_table(text: str, model_name: str) -> list[tuple[str, str]]:
    """Rows of the first table after the ``### …(model_name)`` header.

    Returns ``(field, default_token)`` pairs; raises AssertionError when the
    anchor header or its table is missing (document structure drift).
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        match = _SECTION_HEADER_RE.match(line)
        if match is not None and match.group("model") == model_name:
            start = index + 1
            break
    assert start is not None, (
        f"SKILL.md 缺少 {model_name} 的速查表锚点标题: ### …({model_name})"
    )
    rows: list[tuple[str, str]] = []
    in_table = False
    for line in lines[start:]:
        if _HEADER_RE.match(line):
            break
        if not line.startswith("|"):
            if in_table:
                break
            continue
        in_table = True
        cells = _split_row(line)
        if set(cells[0]) <= {"-", " ", ":"}:  # separator row
            continue
        if cells[0].startswith("字段"):  # header row
            continue
        field = _first_code_span(cells[0])
        token = _first_code_span(cells[1]) if len(cells) > 1 else None
        assert field is not None, f"{model_name} 表格行缺字段名代码段: {line!r}"
        assert token is not None, f"{model_name}.{field} 行缺缺省值代码段: {line!r}"
        rows.append((field, token))
    assert rows, f"SKILL.md 的 {model_name} 表格没有解析到数据行"
    return rows


def _format_value(value: Any) -> str:
    """Render a live schema default exactly the way SKILL.md writes it."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return '""' if value == "" else value
    if isinstance(value, BaseModel):
        section = MODEL_DEFAULT_SECTION.get(type(value).__name__)
        assert section is not None, f"未登记的嵌套模型缺省: {type(value).__name__}"
        return f"见 {section} 节"
    if isinstance(value, dict):
        return "{}"
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _expected_default(model: type[BaseModel], field_name: str) -> str:
    info = model.model_fields[field_name]
    if info.is_required():
        return "必填"
    return _format_value(info.get_default(call_default_factory=True))


def _yaml_blocks(text: str) -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", text, flags=re.DOTALL)


# ---------------------------------------------------------------------------
# 1. Field tables ↔ pydantic models (the core anti-drift check)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_name", sorted(SECTION_MODELS))
def test_skill_field_table_matches_model_fields_and_defaults(model_name: str):
    """Documented fields == model fields (both ways) and defaults match live values."""
    model = SECTION_MODELS[model_name]
    rows = _parse_field_table(_read(SKILL_MD), model_name)

    documented = [field for field, _ in rows]
    duplicated = {name for name in documented if documented.count(name) > 1}
    assert not duplicated, f"{model_name} 表格重复字段: {sorted(duplicated)}"

    missing = set(model.model_fields) - set(documented)
    assert not missing, (
        f"{model_name} 表格缺少 schema 字段(改 SKILL.md): {sorted(missing)}"
    )
    stale = set(documented) - set(model.model_fields)
    assert not stale, (
        f"{model_name} 表格含 schema 已不存在的字段(改 SKILL.md): {sorted(stale)}"
    )

    wrong = {
        field: {"documented": token, "actual": _expected_default(model, field)}
        for field, token in rows
        if token != _expected_default(model, field)
    }
    assert not wrong, f"{model_name} 缺省值与 schema.py 不一致(改 SKILL.md): {wrong}"


def test_skill_documents_every_section_model():
    """All 13 section models have a quick-reference table (12 节 = 11 根字段 + route)."""
    text = _read(SKILL_MD)
    headers = {
        _SECTION_HEADER_RE.match(line).group("model")
        for line in text.splitlines()
        if _SECTION_HEADER_RE.match(line)
    }
    assert headers == set(SECTION_MODELS), (
        f"SKILL.md 速查表锚点与模型集不一致: 多出 {sorted(headers - set(SECTION_MODELS))},"
        f" 缺少 {sorted(set(SECTION_MODELS) - headers)}"
    )


# ---------------------------------------------------------------------------
# 2. Enum vocabulary ↔ schema constants
# ---------------------------------------------------------------------------


def test_skill_enum_table_matches_schema_constants():
    """The vocabulary table mirrors every schema constant value-for-value."""
    documented: dict[str, set[str]] = {}
    for line in _read(SKILL_MD).splitlines():
        if not line.startswith("|"):
            continue
        cells = _split_row(line)
        if len(cells) < 2:
            continue
        constant = _first_code_span(cells[0])
        if constant in ENUM_CONSTANTS:
            tokens = set(_CODE_SPAN_RE.findall(cells[1]))
            assert tokens, f"枚举表 {constant} 行没有取值: {line!r}"
            assert constant not in documented, f"枚举表重复行: {constant}"
            documented[constant] = tokens

    missing = set(ENUM_CONSTANTS) - set(documented)
    assert not missing, f"SKILL.md 枚举表缺少常量: {sorted(missing)}"

    for name in ENUM_CONSTANTS:
        actual = set(getattr(schema, name))
        assert documented[name] == actual, (
            f"schema.{name} 与 SKILL.md 枚举表不一致: "
            f"文档多出 {sorted(documented[name] - actual)}, 文档缺失 {sorted(actual - documented[name])}"
        )


# ---------------------------------------------------------------------------
# 3. Example YAML blocks load through the real schema entry point
# ---------------------------------------------------------------------------


def test_skill_yaml_examples_load_through_load_category():
    """Every ```yaml block in SKILL.md is a complete, schema-valid category config.

    PRD acceptance: an agent with no repo context must be able to lift these
    blocks verbatim and get a loadable plugin — so each block must stand alone
    (fragments are forbidden; illustrate fields in tables instead).
    """
    blocks = _yaml_blocks(_read(SKILL_MD))
    assert len(blocks) >= 2, "SKILL.md 至少要有一个完整示例与一个最小示例两个 yaml 块"
    for index, block in enumerate(blocks, start=1):
        data = yaml.safe_load(block)
        assert isinstance(data, dict), f"SKILL.md 第 {index} 个 yaml 块不是顶层映射"
        config = load_category(data, source=f"SKILL.md 示例 #{index}")
        assert config.id and config.sources, f"SKILL.md 示例 #{index} 加载后不完整"


def test_skill_yaml_examples_have_no_plaintext_credentials():
    """Credential-like keys in the examples only ever carry env:/keychain: refs.

    Structural check on the parsed blocks (the prose must be free to *teach*
    the credential syntax); load_category already refuses plaintext, this
    names the offending key so the fix is obvious.
    """
    from myia.schema import is_credential_key

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                if isinstance(key, str) and is_credential_key(key):
                    assert isinstance(value, str) and re.match(
                        r"^(?:\S+ )?(?:env:|keychain:)", value
                    ), f"SKILL.md 示例凭据位明文: {child}={value!r}"
                walk(value, child)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")

    for index, block in enumerate(_yaml_blocks(_read(SKILL_MD)), start=1):
        walk(yaml.safe_load(block), f"示例 #{index}")


# ---------------------------------------------------------------------------
# 4. docs/write-a-plugin.md stays on the same vocabulary (single source)
# ---------------------------------------------------------------------------


def test_write_a_plugin_section_table_matches_schema_vocabulary():
    """The 12 numbered sections in write-a-plugin.md == the schema vocabulary."""
    text = _read(WRITE_A_PLUGIN_MD)
    sections: list[str] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = _split_row(line)
        if len(cells) < 3 or not re.fullmatch(r"\d+", cells[0].strip()):
            continue
        name = _first_code_span(cells[1])
        assert name is not None, f"write-a-plugin.md 表格行缺节名: {line!r}"
        sections.append(name)

    expected = [
        "id",
        "name",
        "schedule",
        "timezone",
        "sources",
        "watchlist",
        "classify",
        "dedup",
        "enrich",
        "push",
        "push.route",
        "storage",
    ]
    assert sections == expected, (
        f"write-a-plugin.md 的 12 节清单与 schema 不一致: 实际 {sections}"
    )


def test_write_a_plugin_mentions_only_real_push_channels():
    """Channel values named in write-a-plugin.md are all in PUSH_CHANNELS."""
    text = _read(WRITE_A_PLUGIN_MD)
    mentioned = set(_CODE_SPAN_RE.findall(text)) & set(schema.PUSH_CHANNELS)
    assert mentioned == set(schema.PUSH_CHANNELS), (
        f"write-a-plugin.md 通道词表与 schema 不一致: 缺少 {sorted(set(schema.PUSH_CHANNELS) - mentioned)}"
    )


def test_write_a_plugin_example_yaml_loads():
    """The minimal example in write-a-plugin.md stays schema-valid."""
    blocks = _yaml_blocks(_read(WRITE_A_PLUGIN_MD))
    assert blocks, "write-a-plugin.md 缺少示例 yaml 块"
    for index, block in enumerate(blocks, start=1):
        load_category(yaml.safe_load(block), source=f"write-a-plugin.md 示例 #{index}")


# ---------------------------------------------------------------------------
# 5. Cross references (docs site ↔ skill, PRD: 互相引用不许两份漂移)
# ---------------------------------------------------------------------------


def test_skill_and_docs_cross_reference_each_other():
    skill_text = _read(SKILL_MD)
    plugin_text = _read(WRITE_A_PLUGIN_MD)
    assert "docs/write-a-plugin.md" in skill_text, (
        "SKILL.md 应引用详细版 docs/write-a-plugin.md"
    )
    assert "skill/SKILL.md" in plugin_text, "write-a-plugin.md 应回引 skill/SKILL.md"


# ---------------------------------------------------------------------------
# 6. The workflow commands SKILL.md teaches must exist in the CLI
# ---------------------------------------------------------------------------


def test_skill_technique_commands_exist_in_cli():
    """Every `myia …` command line quoted in SKILL.md uses a real subcommand/option."""
    from myia.cli import build_parser

    parser = build_parser()
    command_action = next(
        action for action in parser._actions if action.dest == "command"
    )
    choices = getattr(command_action, "choices", {})
    options_by_command: dict[str, set[str]] = {
        name: {
            option
            for sub_action in sub._actions
            for option in sub_action.option_strings
            if option.startswith("--")
        }
        for name, sub in choices.items()
    }

    for expected in ("run", "test", "doctor", "init", "secret"):
        assert expected in options_by_command, (
            f"SKILL.md 教学的子命令 {expected} 不在 CLI 中"
        )

    for line in re.findall(r"`myia [^`]+`", _read(SKILL_MD)):
        parts = line.strip("`").split()
        if len(parts) < 2 or parts[1] not in options_by_command:
            continue  # 泛指用法(如 myia doctor 出现在标题里)由上面的子命令断言覆盖
        command, option_tokens = parts[1], parts[2:]
        for token in option_tokens:
            if token.startswith("--"):
                assert token in options_by_command[command], (
                    f"SKILL.md 引用了不存在的选项: {line!r}"
                )


# ---------------------------------------------------------------------------
# 7. Runtime-shaped prose: run --json stage list & per-channel credentials
#    (v1.1 low backlog #1/#2 防漂移回归)
# ---------------------------------------------------------------------------


def test_skill_run_json_stage_list_matches_executed_stages():
    """§4.3's ``stages[]`` prose names exactly ``pipeline.EXECUTED_STAGES``, in order.

    Pinned against the live constant, not a copy: ``enrich`` never appears as
    a stage in ``result.stages`` (LLM 精评发生在 analyze 内), and the v0.4
    ``aggregate`` stage only exists when the category declares ``aggregate:``
    — the prose must say so instead of listing it as a standing stage.
    """
    from myia.pipeline import EXECUTED_STAGES

    text = _read(SKILL_MD)
    assert "/".join(EXECUTED_STAGES) in text, (
        "SKILL.md 的 run --json stages[] 清单应恰为 "
        f"{'/'.join(EXECUTED_STAGES)}(与 myia.pipeline.EXECUTED_STAGES 一致)"
    )
    assert "analyze/enrich/push" not in text, (
        "SKILL.md 又把 enrich 列进 run --json 的 stages[](执行链没有独立 enrich 阶段)"
    )
    lines = text.splitlines()
    stage_index = next(i for i, line in enumerate(lines) if "stages[]" in line)
    stage_context = "\n".join(lines[stage_index : stage_index + 4])
    assert "aggregate" in stage_context, (
        "SKILL.md 的 stages[] 说明应注明 aggregate 仅在品类声明 aggregate: 时条件性插入"
    )


def test_skill_documents_per_channel_credentials():
    """§2.13 names every channel's companion credential env ref.

    Single source for the conventions the docs site cites (docs/{zh,en,}/
    write-a-plugin.md 的「各通道凭据约定」都指向本节,本节漂移则三处同漂).
    The shared root guide (docs/write-a-plugin.md) must carry them too.
    """
    for path, label in ((SKILL_MD, "SKILL.md"), (WRITE_A_PLUGIN_MD, "write-a-plugin.md")):
        text = _read(path)
        missing = [ref for ref in _CHANNEL_CREDENTIAL_ENV_REFS if ref not in text]
        assert not missing, f"{label} 缺少各通道凭据约定: {missing}"
