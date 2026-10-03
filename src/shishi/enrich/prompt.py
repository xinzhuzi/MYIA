"""External prompt template (data file), loaded once at enricher construction.

The templates live in ``data/`` (same data/code separation as the classifier
package's ``myia-classifier/myia_classifier/data/keywords.json``) so the
owner or an AI can tune the prompts —
the v0.3 feedback loop's tuning knob — without touching code:

- ``prompt.json`` — the scoring prompt (LLMEnricher; ``load_prompt``);
- ``dedupe_prompt.json`` — the event-dedup prompt (EventAggregator,
  PRD 10-01-v04-event-aggregation; ``load_dedupe_prompt``; the same
  feedback-tunable data-file mechanism, PRD Notes: prompt 进 enrich prompt
  模板体系).

Placeholders (plain token substitution, no str.format so JSON braces in the
prompt stay literal, no template engine so nothing can inject code):

- ``{{SCORES}}`` — the configured dimension list (e.g. ``value, relevance,
  credibility``)
- ``{{WATCHLIST}}`` — the watchlist keywords (relevance baseline)
- ``{{ITEMS_JSON}}`` — the batch's items as a JSON array
- ``{{WINDOW_HOURS}}`` — the aggregation time window (dedupe prompt only)
- ``{{GROUPS_JSON}}`` — the batch's candidate groups as a JSON array
  (dedupe prompt only)

``version`` fingerprints the template into the enrich-cache key: a prompt
change re-scores every URL (schema/template 变更才重评 semantics), and
``EnrichSettings.prompt_version`` / ``EnrichSettings.dedupe_prompt_version``
can force the same effect without editing the data file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shishi.enrich.errors import EnrichConfigError

__all__ = [
    "DEFAULT_DEDUPE_PROMPT_PATH",
    "DEFAULT_PROMPT_PATH",
    "DEDUPE_PROMPT_PLACEHOLDERS",
    "PROMPT_PLACEHOLDERS",
    "PromptTemplate",
    "load_dedupe_prompt",
    "load_prompt",
]

DEFAULT_PROMPT_PATH = Path(__file__).resolve().with_name("data") / "prompt.json"
DEFAULT_DEDUPE_PROMPT_PATH = (
    Path(__file__).resolve().with_name("data") / "dedupe_prompt.json"
)

#: Tokens the scoring user template may reference; an unknown ``{{X}}`` token
#: left in the rendered text means the data file drifts from this contract —
#: refused at load (fail fast) instead of silently confusing the model.
PROMPT_PLACEHOLDERS = ("SCORES", "WATCHLIST", "ITEMS_JSON")

#: Tokens the event-dedup user template may reference (同上 drift 契约).
DEDUPE_PROMPT_PLACEHOLDERS = ("WINDOW_HOURS", "GROUPS_JSON")


class PromptTemplate:
    """A validated prompt pair: ``system`` message + ``user`` template."""

    def __init__(self, system: str, user_template: str, version: int) -> None:
        self.system = system
        self.user_template = user_template
        self.version = version

    def render_user(self, *, scores: list[str], watchlist: list[str], items_json: str) -> str:
        """Fill the scoring template for one batch; unknown leftover tokens fail fast.

        Raises:
            EnrichConfigError: ``prompt_invalid`` — the template references a
                placeholder outside :data:`PROMPT_PLACEHOLDERS`.
        """
        return self._render(
            {
                "{{SCORES}}": ", ".join(scores),
                "{{WATCHLIST}}": ", ".join(watchlist) if watchlist else "(无)",
                "{{ITEMS_JSON}}": items_json,
            },
            allowed=PROMPT_PLACEHOLDERS,
        )

    def render_groups(self, *, window_hours: float, groups_json: str) -> str:
        """Fill the event-dedup template for one batch (v0.4 事件聚合).

        Raises:
            EnrichConfigError: ``prompt_invalid`` — the template references a
                placeholder outside :data:`DEDUPE_PROMPT_PLACEHOLDERS`.
        """
        return self._render(
            {
                "{{WINDOW_HOURS}}": str(window_hours),
                "{{GROUPS_JSON}}": groups_json,
            },
            allowed=DEDUPE_PROMPT_PLACEHOLDERS,
        )

    def _render(self, substitutions: dict[str, str], *, allowed: tuple[str, ...]) -> str:
        """Substitute tokens, then refuse leftovers outside ``allowed``."""
        rendered = self.user_template
        for token, value in substitutions.items():
            rendered = rendered.replace(token, value)
        leftover = _unknown_placeholder(rendered, allowed)
        if leftover is not None:
            raise EnrichConfigError(
                "prompt_invalid",
                f"prompt 模板含未知占位符 {{{{{leftover}}}}},允许的占位符: "
                f"{[f'{{{{{name}}}}}' for name in allowed]}",
                details={"field": "enrich.prompt", "placeholder": leftover},
            )
        return rendered


def load_prompt(path: str | Path | None = None) -> PromptTemplate:
    """Load and validate the scoring prompt data file (packaged default or override).

    Args:
        path: explicit prompt file (``EnrichSettings.prompt_path``); ``None``
            uses the bundled default.

    Returns:
        A validated :class:`PromptTemplate`.

    Raises:
        EnrichConfigError: ``prompt_invalid`` — unreadable file, invalid JSON,
            missing/empty/wrong-typed fields, or a template referencing an
            unknown placeholder.
    """
    prompt_path = Path(path) if path is not None else DEFAULT_PROMPT_PATH
    system, user_template, version = _load_prompt_file(prompt_path)
    template = PromptTemplate(system=system, user_template=user_template, version=version)
    # 提前演练一次渲染:模板引用未知占位符在加载期即拒(fail fast 于配置)。
    template.render_user(scores=["value"], watchlist=["demo"], items_json="[]")
    return template


def load_dedupe_prompt(path: str | Path | None = None) -> PromptTemplate:
    """Load and validate the event-dedup prompt data file (v0.4 事件聚合).

    与 :func:`load_prompt` 同一数据文件契约与同一 ``prompt_invalid`` 错误族;
    模板占位符词表是 :data:`DEDUPE_PROMPT_PLACEHOLDERS`。

    Args:
        path: explicit prompt file (``EnrichSettings.dedupe_prompt_path``);
            ``None`` uses the bundled default.

    Returns:
        A validated :class:`PromptTemplate` (render via :meth:`render_groups`).

    Raises:
        EnrichConfigError: ``prompt_invalid`` — same failure modes as
            :func:`load_prompt`.
    """
    prompt_path = Path(path) if path is not None else DEFAULT_DEDUPE_PROMPT_PATH
    system, user_template, version = _load_prompt_file(prompt_path)
    template = PromptTemplate(system=system, user_template=user_template, version=version)
    template.render_groups(window_hours=24, groups_json="[]")
    return template


def _load_prompt_file(path: Path) -> tuple[str, str, int]:
    """Read and validate one prompt data file; returns (system, user_template, version).

    Raises:
        EnrichConfigError: ``prompt_invalid`` — unreadable file, invalid JSON,
            missing/empty/wrong-typed fields (including a non-positive version).
    """
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EnrichConfigError(
            "prompt_invalid",
            f"无法读取 prompt 模板文件 {path}: {exc}",
            details={"field": "enrich.prompt", "path": str(path)},
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EnrichConfigError(
            "prompt_invalid",
            f"prompt 模板文件不是有效 JSON/UTF-8({path}): {exc}",
            details={"field": "enrich.prompt", "path": str(path)},
        ) from exc
    if not isinstance(raw, dict):
        raise EnrichConfigError(
            "prompt_invalid",
            f"prompt 模板文件必须是 JSON 对象,当前为 {type(raw).__name__}",
            details={"field": "enrich.prompt", "path": str(path)},
        )
    system = raw.get("system")
    user_template = raw.get("user_template")
    version = raw.get("version")
    for name, value in (("system", system), ("user_template", user_template)):
        if not isinstance(value, str) or not value.strip():
            raise EnrichConfigError(
                "prompt_invalid",
                f"prompt 模板字段 {name} 必须是非空字符串",
                details={"field": f"enrich.prompt.{name}", "path": str(path)},
            )
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise EnrichConfigError(
            "prompt_invalid",
            f"prompt 模板 version 必须是正整数(进缓存指纹),当前为 {version!r}",
            details={"field": "enrich.prompt.version", "path": str(path)},
        )
    return system, user_template, version


def _unknown_placeholder(text: str, allowed: tuple[str, ...]) -> str | None:
    """Return the first ``{{NAME}}`` token outside ``allowed``, if any."""
    for chunk in text.split("{{")[1:]:
        token = chunk.split("}}", 1)[0].strip()
        if token not in allowed:
            return token
    return None
