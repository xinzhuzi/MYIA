"""Structured storage-layer errors (PRD 10-01-v02-storage-hardening).

The store must never fail with a bare string: a corrupt database file or a
schema version the binary does not understand is a *startup* failure and has
to be machine-consumable (``myia doctor`` v0.2, repairing agents) — field
paths / codes / found-vs-expected values, Chinese human message.

Subclasses :class:`ValueError` so construction-time callers (``Pipeline(...)``)
and the CLI config-error family treat it uniformly — and ``myia run`` has an
explicit ``except StoreSchemaError`` handler around the run itself, because the
store opens lazily inside ``pipeline.run()`` (exit code 1, structured
``{"error": "store", ...}`` on stdout under ``--json``).
"""

from __future__ import annotations

from typing import Any

__all__ = ["StoreSchemaError"]


class StoreSchemaError(ValueError):
    """The database file cannot be used as-is at open time.

    Attributes:
        code: machine-readable failure class —
            ``store_corrupt`` (the file is not a usable SQLite database),
            ``schema_version_newer`` (the database was written by a newer
            MYIA; downgrading is refused instead of silently misreading),
            ``schema_version_mismatch`` (a version with no migration path).
        details: structured context (path / expected / found versions).
    """

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form for ``myia doctor`` (JSON) and agents."""
        return {"error_type": self.code, "message": str(self), **self.details}
