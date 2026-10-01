"""Default SQLite storage: single file, zero external dependencies.

Planned tables:
- items           — intelligence entries flowing through pipelines
- dedup_registry  — seen dedup keys (see myia.dedup)
- baselines       — v1.7 change fingerprints / value history per source & field
- feedback        — v1.7 valuable/not-valuable push feedback loop
- runs            — one row per pipeline execution (status, counts, timing)
"""

from __future__ import annotations

import sqlite3


class SQLiteStore:
    def __init__(self, sqlite_path: str) -> None:
        self.sqlite_path = sqlite_path
        self.conn = sqlite3.connect(sqlite_path)

    def init_schema(self) -> None:
        """Create the minimal v0.1 tables (items, runs)."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT,
                scores TEXT,
                metadata TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin TEXT NOT NULL,
                started_at TEXT NOT NULL,
                status TEXT NOT NULL,
                stats TEXT
            );
            """
        )
        self.conn.commit()

    def purge_expired(self, retention_days: int) -> int:
        """Delete items older than the retention window; returns row count (v0.2)."""
        raise NotImplementedError
