"""SQLite for Discover verdicts, summaries, and line decisions.

The file is separate from data/jobfinder.db. Tests point DB_PATH at a temp file.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "discover.db"
_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS discover_postings (
    posting_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    role_family TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discover_verdicts (
    posting_id TEXT PRIMARY KEY,
    verdict TEXT NOT NULL,
    reason_chips TEXT NOT NULL,
    created_at TEXT NOT NULL,
    posting_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discover_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verdict_count INTEGER NOT NULL UNIQUE,
    source TEXT NOT NULL,
    label TEXT NOT NULL,
    lines_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discover_line_decisions (
    line_key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    locked INTEGER NOT NULL DEFAULT 0,
    edited INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL,
    dimension TEXT NOT NULL,
    polarity TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value, fallback):
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed


def _connect() -> sqlite3.Connection:
    path = DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def upsert_posting(features: dict) -> None:
    with _LOCK, _connect() as connection:
        connection.execute(
            """
            INSERT INTO discover_postings (posting_id, payload, role_family, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(posting_id) DO UPDATE SET
                payload = excluded.payload,
                role_family = excluded.role_family,
                updated_at = excluded.updated_at
            """,
            (features["posting_id"], json.dumps(features), features["role_family"], _now()),
        )


def get_posting(posting_id: str) -> dict | None:
    with _LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM discover_postings WHERE posting_id = ?",
            (posting_id,),
        ).fetchone()
    if row is None:
        return None
    payload = _loads(row["payload"], None)
    return payload if isinstance(payload, dict) else None


def judged_ids() -> set[str]:
    with _LOCK, _connect() as connection:
        rows = connection.execute("SELECT posting_id FROM discover_verdicts").fetchall()
    return {row["posting_id"] for row in rows}


def add_verdict(posting_id: str, verdict: str, chips: list[str], posting: dict) -> dict:
    created_at = _now()
    with _LOCK, _connect() as connection:
        try:
            connection.execute(
                """
                INSERT INTO discover_verdicts (posting_id, verdict, reason_chips, created_at, posting_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (posting_id, verdict, json.dumps(chips), created_at, json.dumps(posting)),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateVerdict(posting_id) from exc
    return {
        "posting_id": posting_id,
        "verdict": verdict,
        "reason_chips": list(chips),
        "created_at": created_at,
    }


def get_verdict(posting_id: str) -> dict | None:
    with _LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT * FROM discover_verdicts WHERE posting_id = ?",
            (posting_id,),
        ).fetchone()
    return _verdict_row(row) if row else None


def count_verdicts() -> int:
    with _LOCK, _connect() as connection:
        row = connection.execute("SELECT COUNT(*) AS n FROM discover_verdicts").fetchone()
    return int(row["n"])


def verdict_pairs() -> list[dict]:
    with _LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM discover_verdicts ORDER BY created_at ASC, posting_id ASC"
        ).fetchall()
    pairs = []
    for row in rows:
        verdict = _verdict_row(row)
        posting = _loads(row["posting_json"], {})
        if isinstance(posting, dict):
            pairs.append({"verdict": verdict, "posting": posting})
    return pairs


def insert_summary(verdict_count: int, source: str, label: str, lines: list[dict]) -> dict:
    created_at = _now()
    with _LOCK, _connect() as connection:
        existing = connection.execute(
            "SELECT * FROM discover_summaries WHERE verdict_count = ?",
            (verdict_count,),
        ).fetchone()
        if existing is not None:
            return _summary_row(existing)
        cursor = connection.execute(
            """
            INSERT INTO discover_summaries (verdict_count, source, label, lines_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (verdict_count, source, label, json.dumps(lines), created_at),
        )
        row = connection.execute(
            "SELECT * FROM discover_summaries WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return _summary_row(row)


def update_summary_lines(summary_id: int, lines: list[dict]) -> dict | None:
    with _LOCK, _connect() as connection:
        connection.execute(
            "UPDATE discover_summaries SET lines_json = ? WHERE id = ?",
            (json.dumps(lines), summary_id),
        )
        row = connection.execute(
            "SELECT * FROM discover_summaries WHERE id = ?",
            (summary_id,),
        ).fetchone()
    return _summary_row(row) if row else None


def list_summaries() -> list[dict]:
    with _LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM discover_summaries ORDER BY id ASC"
        ).fetchall()
    return [_summary_row(row) for row in rows]


def latest_summary() -> dict | None:
    summaries = list_summaries()
    return summaries[-1] if summaries else None


def save_decision(line: dict, *, edited: bool | None = None) -> None:
    current = get_decision(line["key"])
    if edited is None:
        edited = bool(current and current["edited"])
    with _LOCK, _connect() as connection:
        connection.execute(
            """
            INSERT INTO discover_line_decisions (
                line_key, status, locked, edited, text, dimension, polarity, value, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(line_key) DO UPDATE SET
                status = excluded.status,
                locked = excluded.locked,
                edited = excluded.edited,
                text = excluded.text,
                dimension = excluded.dimension,
                polarity = excluded.polarity,
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (
                line["key"],
                line["status"],
                1 if line.get("locked") else 0,
                1 if edited else 0,
                line.get("text") or "",
                line.get("dimension") or "",
                line.get("polarity") or "",
                line.get("value") or "",
                _now(),
            ),
        )


def get_decision(line_key: str) -> dict | None:
    with _LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT * FROM discover_line_decisions WHERE line_key = ?",
            (line_key,),
        ).fetchone()
    return _decision_row(row) if row else None


def all_decisions() -> dict[str, dict]:
    with _LOCK, _connect() as connection:
        rows = connection.execute("SELECT * FROM discover_line_decisions").fetchall()
    return {row["line_key"]: _decision_row(row) for row in rows}


class DuplicateVerdict(Exception):
    def __init__(self, posting_id: str):
        super().__init__(posting_id)
        self.posting_id = posting_id


def _verdict_row(row: sqlite3.Row) -> dict:
    chips = _loads(row["reason_chips"], [])
    return {
        "posting_id": row["posting_id"],
        "verdict": row["verdict"],
        "reason_chips": chips if isinstance(chips, list) else [],
        "created_at": row["created_at"],
    }


def _summary_row(row: sqlite3.Row) -> dict:
    lines = _loads(row["lines_json"], [])
    return {
        "id": row["id"],
        "verdict_count": row["verdict_count"],
        "source": row["source"],
        "label": row["label"],
        "lines": lines if isinstance(lines, list) else [],
        "created_at": row["created_at"],
    }


def _decision_row(row: sqlite3.Row) -> dict:
    return {
        "key": row["line_key"],
        "status": row["status"],
        "locked": bool(row["locked"]),
        "edited": bool(row["edited"]),
        "text": row["text"],
        "dimension": row["dimension"],
        "polarity": row["polarity"],
        "value": row["value"],
        "updated_at": row["updated_at"],
    }
