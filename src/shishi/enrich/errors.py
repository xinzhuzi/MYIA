"""Structured enrich-layer errors (error-handling spec: 字段路径+错误类+原因).

Subclasses :class:`ValueError` so the CLI's config-error path (exit code 1)
catches it: an unusable enrich configuration (missing/plaintext endpoint
reference, unresolved env var, malformed prompt data file) is a *startup*
failure in the same family as a refused YAML — fail fast, never half-run
with a broken second funnel. One deliberate exception to the startup
timing: a missing optional dependency (``dependency_missing``, the
``openai`` package) surfaces at the **first LLM call**, not at construction
— the client imports it lazily, and enrich/aggregate isolate that failure
per batch like any other (降级不中断).

Runtime problems (LLM 超时/响应不可解析/单批失败) deliberately do NOT use this
class: they are isolated per batch inside :meth:`LLMEnricher.enrich` and
surface as structured failure records on :class:`EnrichOutcome` — the run
degrades to keyword-only filtering instead of dying.
"""

from __future__ import annotations

from typing import Any

__all__ = ["EnrichConfigError"]


class EnrichConfigError(ValueError):
    """Enrich configuration refused (startup / fail-fast; CLI exit code 1).

    Attributes:
        code: machine-readable failure class —
            ``missing_base_url`` / ``missing_api_key`` (no reference given),
            ``credential_plaintext`` (endpoint/key configured as plaintext
            instead of an ``env:``/``keychain:`` reference, grill Q6),
            ``credential_unresolved`` (the referenced env var is unset or the
            keychain is not yet supported),
            ``invalid_base_url`` (resolved endpoint is not an http(s) URL),
            ``dependency_missing`` (the optional ``openai`` package, extras
            ``shishi[llm]``, is not installed — raised at the first LLM call,
            the import is lazy, never at startup),
            ``prompt_invalid`` (the external prompt data file is malformed).
        details: structured context (field path / reference name), consumed
            by ``myia doctor`` (JSON) and repairing agents. Never carries
            resolved credential *values* — reference names only.
    """

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form for ``myia doctor`` (JSON) and agents."""
        return {"error_type": self.code, "message": str(self), **self.details}
