"""Idempotent SQLite migration for onboarding. No Alembic in this app."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS constraints (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL DEFAULT 'not_started',
    data TEXT NOT NULL,
    actions_override TEXT NOT NULL DEFAULT '',
    inference_note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preference_profile (
    version INTEGER PRIMARY KEY,
    data TEXT NOT NULL,
    major_change INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT '',
    confidence REAL,
    evidence TEXT NOT NULL,
    status TEXT NOT NULL,
    locked INTEGER NOT NULL DEFAULT 0,
    weight REAL,
    source TEXT NOT NULL,
    document_backed INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reflection_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_key TEXT NOT NULL UNIQUE,
    experience_ref TEXT NOT NULL,
    experience_kind TEXT NOT NULL,
    prompt TEXT NOT NULL,
    polarity TEXT NOT NULL,
    chips TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reflection_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_key TEXT NOT NULL UNIQUE,
    choice TEXT NOT NULL DEFAULT '',
    skipped INTEGER NOT NULL DEFAULT 0,
    experience_ref TEXT NOT NULL DEFAULT '',
    polarity TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

_COLUMNS = {
    "constraints": {
        "actions_override": "TEXT NOT NULL DEFAULT ''",
        "inference_note": "TEXT NOT NULL DEFAULT ''",
        "status": "TEXT NOT NULL DEFAULT 'not_started'",
    },
    "preference_profile": {
        "major_change": "INTEGER NOT NULL DEFAULT 0",
        "reason": "TEXT NOT NULL DEFAULT ''",
    },
    "profile_items": {
        "level": "TEXT NOT NULL DEFAULT ''",
        "document_backed": "INTEGER NOT NULL DEFAULT 0",
        "payload": "TEXT NOT NULL DEFAULT '{}'",
    },
    "reflection_answers": {
        "experience_ref": "TEXT NOT NULL DEFAULT ''",
        "polarity": "TEXT NOT NULL DEFAULT ''",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def apply(connection: sqlite3.Connection) -> None:
    """Create onboarding tables. Safe to run on every startup and in tests."""
    connection.executescript(SCHEMA)
    for table, columns in _COLUMNS.items():
        existing = _columns(connection, table)
        for name, ddl in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    row = connection.execute("SELECT id FROM constraints WHERE id = 1").fetchone()
    if row is None:
        connection.execute(
            """
            INSERT INTO constraints (id, status, data, actions_override, inference_note, updated_at)
            VALUES (1, 'not_started', ?, '', '', ?)
            """,
            (json.dumps({}), _now()),
        )
