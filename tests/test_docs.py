"""Bilingual docs (docs/zh + docs/en) ↔ schema anti-drift tests (10-01-v10-docs-bilingual).

The bilingual docs site is AI input: an agent lifts YAML examples verbatim
and follows the taught workflow, so drift is not a cosmetic problem. Three
mechanisms lock the pages to the current code:

1. **Example YAML blocks** — every ```yaml fenced block anywhere under
   ``docs/`` (zh tree, en tree, and the shared ``docs/write-a-plugin.md``)
   must be a *complete* category config and load through the real entry
   point :func:`myia.schema.load_category`. Fragments are forbidden:
   fragments would fail the moment an agent copies them (field details
   belong in tables, not in broken examples).
2. **zh/en structural alignment** — the two trees carry the same page set,
   and each zh/en pair shares one heading-level skeleton, one fence-language
   profile, and one example-per-block structural shape. Neither language can
   silently grow a section, lose an example, or reshuffle the chapter flow
   without a red test.
3. **Red lines** — examples carry zero plaintext credentials (structural
   walk over the parsed blocks, mirroring tests/test_skill_doc.py), and
   every relative link in the docs resolves to a repo file, so the published
   site has no dead internal links.

Scope note: ``docs/demo/**`` is v1.0 演示物料 (PRD 10-01-v10-demo: 视频脚本 /
分镜 / 素材清单), not schema-teaching documentation — those pages are exempt
from the per-page "must carry a category example" rule and may link media
artifacts (``.srt``/``.gif``). But any ```yaml block they contain is still
validated through :func:`load_category`, and every relative link they carry
must resolve to a real repo file — no protection is dropped.

These tests make docs drift a red test instead of a silently misleading doc.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from myia.schema import is_credential_key, load_category

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
ZH_DIR = DOCS_DIR / "zh"
EN_DIR = DOCS_DIR / "en"
DEMO_DIR = DOCS_DIR / "demo"

#: 双语页面清单:两棵树必须同时存在这五页,并逐页结构对齐。
BILINGUAL_PAGES = (
    "getting-started.md",
    "write-a-plugin.md",
    "schema.md",
    "faq.md",
    "zero-cost.md",
)

_YAML_BLOCK_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
_FENCED_RE = re.compile(r"^```.*?^```", re.DOTALL | re.MULTILINE)
_FENCE_LINE_RE = re.compile(r"^```", re.MULTILINE)
_HEADING_RE = re.compile(r"^(#{1,6})\s", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
#: 凭据位合法值:可选认证 scheme 前缀 + env:/keychain: 引用(与 test_skill_doc 同款)。
_SECRET_REF_VALUE_RE = re.compile(r"^(?:\S+ )?(?:env:|keychain:)")

#: 各通道凭据约定的缺省 env 引用(与 skill/SKILL.md §2.13 及
#: src/myia/push/*.py 的 DEFAULT_*_ENV_REF 同源)。schema.md 的「各通道凭据
#: 约定见 write-a-plugin」指向双语文指南,该节内容由此清单锁住不悬空。
_CHANNEL_CREDENTIAL_ENV_REFS = (
    "env:FEISHU_CHAT_ID",
    "env:FEISHU_BOT_TOKEN",
    "env:TELEGRAM_CHAT_ID",
    "env:TELEGRAM_BOT_TOKEN",
    "env:NTFY_TOKEN",
    "env:DINGTALK_WEBHOOK_URL",
    "env:WECOM_TUSER",
    "env:WECOM_CORPID",
    "env:WECOM_CORPSECRET",
    "env:WECOM_AGENTID",
    "env:MYIA_WEBHOOK_URL",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks so bash ``#`` comments never read as headings."""
    return _FENCED_RE.sub("", text)


def _yaml_blocks(text: str) -> list[str]:
    return _YAML_BLOCK_RE.findall(text)


def _heading_levels(text: str) -> list[int]:
    return [len(level) for level in _HEADING_RE.findall(_strip_code_blocks(text))]


def _all_docs_markdown_files() -> list[Path]:
    """Every markdown file anywhere under docs/ (documentation + demo material)."""
    return sorted(DOCS_DIR.rglob("*.md"))


def _is_doc_page(path: Path) -> bool:
    """Schema-teaching documentation page (bilingual-PRD scope): the shared
    root pages (``docs/*.md``) plus the zh/en trees. Demo material
    (``docs/demo/**``) is repo material, not AI-input documentation — exempt
    from the per-page example rule and the docs-only link-type gate, but its
    yaml blocks are still fully validated and its links must still resolve."""
    resolved = path.resolve()
    if resolved.is_relative_to(DEMO_DIR.resolve()):
        return False
    return (
        path.parent == DOCS_DIR
        or resolved.is_relative_to(ZH_DIR.resolve())
        or resolved.is_relative_to(EN_DIR.resolve())
    )


def _shape(node: Any) -> Any:
    """Language-independent structural shape of a parsed YAML example.

    Mappings/lists keep their structure (list lengths must match); every
    scalar collapses to ``"scalar"`` so 中文 and English values compare equal
    while keys, nesting and arity cannot drift between the two trees.
    """
    if isinstance(node, dict):
        return {str(key): _shape(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_shape(item) for item in node]
    return "scalar"


# ---------------------------------------------------------------------------
# 1. Page inventory: both trees carry the same, complete page set
# ---------------------------------------------------------------------------


def test_docs_trees_have_identical_page_sets():
    zh_pages = {path.name for path in ZH_DIR.glob("*.md")}
    en_pages = {path.name for path in EN_DIR.glob("*.md")}
    assert zh_pages == set(BILINGUAL_PAGES), f"docs/zh 页面集漂移: {sorted(zh_pages)}"
    assert en_pages == set(BILINGUAL_PAGES), f"docs/en 页面集漂移: {sorted(en_pages)}"


# ---------------------------------------------------------------------------
# 2. Example YAML blocks load through the real schema entry point
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", _all_docs_markdown_files(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_docs_yaml_block_loads_through_load_category(path: Path):
    """Every ```yaml block anywhere under docs/ is a complete, schema-valid
    category config; documentation pages (zh/en/共享根页) must each carry at
    least one example (demo material pages are exempt from the must-carry rule
    but not from block validation)."""
    blocks = _yaml_blocks(_read(path))
    if _is_doc_page(path):
        assert blocks, (
            f"{path.relative_to(REPO_ROOT)} 缺少示例 yaml 块(文档即 AI 输入,至少一个完整示例)"
        )
    for index, block in enumerate(blocks, start=1):
        data = yaml.safe_load(block)
        assert isinstance(data, dict), (
            f"{path} 第 {index} 个 yaml 块不是顶层映射(禁止片段示例)"
        )
        config = load_category(
            data, source=f"{path.relative_to(REPO_ROOT)} 示例 #{index}"
        )
        assert config.id and config.sources, f"{path} 示例 #{index} 加载后不完整"


@pytest.mark.parametrize(
    "path", _all_docs_markdown_files(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_docs_yaml_examples_have_no_plaintext_credentials(path: Path):
    """Credential-like keys in doc examples only ever carry env:/keychain: refs
    (global sweep — demo material 的 yaml 块同样受凭据红线约束)."""
    for index, block in enumerate(_yaml_blocks(_read(path)), start=1):
        data = yaml.safe_load(block)
        for finding in _plaintext_findings(data, f"{path.name} 示例 #{index}"):
            raise AssertionError(finding)


def _plaintext_findings(node: Any, where: str, path: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if (
                isinstance(key, str)
                and is_credential_key(key)
                and not (isinstance(value, str) and _SECRET_REF_VALUE_RE.match(value))
            ):
                findings.append(f"{where} 凭据位明文: {child}={value!r}")
            findings.extend(_plaintext_findings(value, where, child))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            findings.extend(_plaintext_findings(item, where, f"{path}[{index}]"))
    return findings


# ---------------------------------------------------------------------------
# 3. zh/en structural alignment (same skeleton, same examples)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", BILINGUAL_PAGES)
def test_bilingual_pair_heading_skeletons_align(page: str):
    """zh/en heading-level sequences are identical (chapters cannot drift)."""
    zh_levels = _heading_levels(_read(ZH_DIR / page))
    en_levels = _heading_levels(_read(EN_DIR / page))
    assert zh_levels, f"docs/zh/{page} 没有解析到任何标题"
    assert zh_levels == en_levels, (
        f"zh/en 章节结构不对齐({page}):zh 标题层级 {zh_levels} vs en {en_levels}"
    )


@pytest.mark.parametrize("page", BILINGUAL_PAGES)
def test_bilingual_pair_fence_profiles_align(page: str):
    """zh/en carry the same number of fences per language (examples stay paired)."""
    zh_text, en_text = _read(ZH_DIR / page), _read(EN_DIR / page)

    zh_yaml, en_yaml = len(_yaml_blocks(zh_text)), len(_yaml_blocks(en_text))
    assert zh_yaml == en_yaml, (
        f"zh/en yaml 示例数不对齐({page}): {zh_yaml} vs {en_yaml}"
    )
    assert zh_yaml >= 1, f"docs/zh/{page} 缺少示例 yaml 块"

    zh_fences, en_fences = (
        len(_FENCE_LINE_RE.findall(zh_text)),
        len(_FENCE_LINE_RE.findall(en_text)),
    )
    assert zh_fences == en_fences, (
        f"zh/en 代码块总数不对齐({page}): {zh_fences} vs {en_fences}"
    )


@pytest.mark.parametrize("page", BILINGUAL_PAGES)
def test_bilingual_pair_yaml_examples_share_structure(page: str):
    """Block-by-block, zh/en examples are the same config in two languages."""
    zh_blocks, en_blocks = (
        _yaml_blocks(_read(ZH_DIR / page)),
        _yaml_blocks(_read(EN_DIR / page)),
    )
    assert len(zh_blocks) == len(en_blocks)
    for index, (zh_block, en_block) in enumerate(zip(zh_blocks, en_blocks), start=1):
        zh_shape, en_shape = (
            _shape(yaml.safe_load(zh_block)),
            _shape(yaml.safe_load(en_block)),
        )
        assert zh_shape == en_shape, (
            f"zh/en 示例 #{index} 结构不一致({page}):"
            f"两棵树必须是同一份配置的两种语言(键/嵌套/列表长度不同)"
        )


# ---------------------------------------------------------------------------
# 3b. Per-channel credential conventions (v1.1 low backlog #2 anti-drift)
# ---------------------------------------------------------------------------


def test_bilingual_write_a_plugin_documents_channel_credentials():
    """Both zh/en plugin guides name every channel's credential env ref.

    docs/{zh,en}/schema.md 教「各通道凭据约定见 write-a-plugin」——目标页没有
    该内容时引用即悬空(agent 被指到一页没有答案的文字)。此清单与
    skill/SKILL.md §2.13 同源,双语两页必须同时携带,缺一即红。
    """
    for tree_dir in (ZH_DIR, EN_DIR):
        text = _read(tree_dir / "write-a-plugin.md")
        missing = [ref for ref in _CHANNEL_CREDENTIAL_ENV_REFS if ref not in text]
        assert not missing, (
            f"docs/{tree_dir.name}/write-a-plugin.md 缺少各通道凭据约定: {missing}"
            "(schema.md 的「凭据约定见 write-a-plugin」指向本页,不许悬空)"
        )


# ---------------------------------------------------------------------------
# 4. Navigation: every relative *.md / *.yaml link resolves to a repo file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", _all_docs_markdown_files(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_docs_relative_links_resolve(path: Path):
    for link in _MD_LINK_RE.findall(_read(path)):
        if link.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = link.split("#", 1)[0]
        if not target:
            continue  # 纯页内锚点(#section)由渲染器保证,这里只查文件级链接
        if not _is_doc_page(path):
            # 演示物料可链接 .srt/.gif 等媒体资产:只防死链,不限文档类型
            resolved = (path.parent / target).resolve()
            assert resolved.exists(), f"{path} 的相对链接失效: {link!r} -> {resolved}"
            continue
        assert target.endswith((".md", ".yaml")), f"{path} 链接目标非文档文件: {link!r}"
        resolved = (path.parent / target).resolve()
        assert resolved.exists(), f"{path} 的相对链接失效: {link!r} -> {resolved}"
