"""URL/composite-key dedup registry backed by stdlib sqlite3."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


class DedupRegistry:
    """Persistent registry of already-seen dedup keys."""

    def __init__(self, sqlite_path: str) -> None:
        self.conn = sqlite3.connect(sqlite_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dedup_registry (
                key TEXT PRIMARY KEY,
                first_seen TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def is_seen(self, key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM dedup_registry WHERE key = ?", (key,)
        ).fetchone()
        return row is not None

    def mark(self, key: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO dedup_registry (key, first_seen) VALUES (?, ?)",
            (key, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    @staticmethod
    def make_key(template: str, values: dict) -> str:
        """Render a dedup key from the plugin's `dedup.key` template.

        Composite keys only (e.g. "{symbol}-{date}" or "{url}") — never
        title fingerprints, per the plugin spec.
        """
        return template.format(**values)
