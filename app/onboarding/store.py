"""SQLite access for onboarding. Tests point app.db.DB_PATH at a temporary file."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app import db
from app.onboarding.migrate import apply

STATUSES = (
    "not_started",
    "constraints_done",
    "profile_confirmed",
    "reflection_done",
    "complete",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value, fallback):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _connect() -> sqlite3.Connection:
    path = db.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    apply(connection)
    return connection


def _item_payload(item: dict) -> str:
    return json.dumps(
        {
            "writer": item.get("writer") or "",
            "proposed_weight": item.get("proposed_weight", 0.7),
            "polarity": item.get("polarity") or "prefer",
            "experience_ref": item.get("experience_ref") or "",
            "edited": bool(item.get("edited")),
        }
    )


def _item_row(row: sqlite3.Row) -> dict:
    payload = _loads(row["payload"], {})
    weight = row["weight"]
    return {
        "key": row["item_key"],
        "kind": row["kind"],
        "name": row["name"],
        "level": row["level"] or "",
        "confidence": row["confidence"],
        "evidence": _loads(row["evidence"], []),
        "status": row["status"],
        "locked": bool(row["locked"]),
        "weight": weight,
        "source": row["source"],
        "document_backed": bool(row["document_backed"]),
        "writer": payload.get("writer") or "",
        "proposed_weight": payload.get("proposed_weight", 0.7),
        "polarity": payload.get("polarity") or "prefer",
        "experience_ref": payload.get("experience_ref") or "",
        "edited": bool(payload.get("edited")),
    }


def _question_row(row: sqlite3.Row) -> dict:
    return {
        "question_key": row["question_key"],
        "experience_ref": row["experience_ref"],
        "experience_kind": row["experience_kind"],
        "prompt": row["prompt"],
        "polarity": row["polarity"],
        "chips": _loads(row["chips"], []),
    }


def _answer_row(row: sqlite3.Row) -> dict:
    return {
        "question_key": row["question_key"],
        "choice": row["choice"],
        "skipped": bool(row["skipped"]),
        "experience_ref": row["experience_ref"],
        "polarity": row["polarity"],
    }


def _version_row(row: sqlite3.Row) -> dict:
    return {
        "version": int(row["version"]),
        "data": _loads(row["data"], {}),
        "major_change": bool(row["major_change"]),
        "reason": row["reason"],
        "created_at": row["created_at"],
    }


def get_constraints() -> dict:
    db.init()
    with db._LOCK, _connect() as connection:
        row = connection.execute("SELECT * FROM constraints WHERE id = 1").fetchone()
    status = row["status"] if row and row["status"] in STATUSES else "not_started"
    return {
        "status": status,
        "filters": _loads(row["data"], {}) if row else {},
        "actions_override": (row["actions_override"] if row else "") or "",
        "inference_note": (row["inference_note"] if row else "") or "",
    }


def save_filters(filters: dict, *, status: str | None = None) -> None:
    db.init()
    current = get_constraints()
    next_status = status if status in STATUSES else current["status"]
    with db._LOCK, _connect() as connection:
        connection.execute(
            """
            UPDATE constraints
            SET data = ?, status = ?, updated_at = ?
            WHERE id = 1
            """,
            (json.dumps(filters), next_status, _now()),
        )


def set_status(status: str) -> None:
    if status not in STATUSES:
        raise ValueError("Unknown onboarding status.")
    db.init()
    with db._LOCK, _connect() as connection:
        connection.execute(
            "UPDATE constraints SET status = ?, updated_at = ? WHERE id = 1",
            (status, _now()),
        )


def set_inference_note(note: str) -> None:
    db.init()
    with db._LOCK, _connect() as connection:
        connection.execute(
            "UPDATE constraints SET inference_note = ?, updated_at = ? WHERE id = 1",
            (note, _now()),
        )


def set_override(value: str) -> None:
    if value not in {"", "allow", "deny"}:
        raise ValueError("Override must be allow, deny, or cleared.")
    db.init()
    with db._LOCK, _connect() as connection:
        connection.execute(
            "UPDATE constraints SET actions_override = ?, updated_at = ? WHERE id = 1",
            (value, _now()),
        )


def list_items() -> list[dict]:
    db.init()
    with db._LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM profile_items ORDER BY id ASC"
        ).fetchall()
    return [_item_row(row) for row in rows]


def get_item(key: str) -> dict | None:
    db.init()
    with db._LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT * FROM profile_items WHERE item_key = ?",
            (key,),
        ).fetchone()
    return _item_row(row) if row else None


def upsert_item(item: dict) -> None:
    db.init()
    with db._LOCK, _connect() as connection:
        connection.execute(
            """
            INSERT INTO profile_items (
                item_key, kind, name, level, confidence, evidence, status, locked,
                weight, source, document_backed, payload, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_key) DO UPDATE SET
                kind = excluded.kind,
                name = excluded.name,
                level = excluded.level,
                confidence = excluded.confidence,
                evidence = excluded.evidence,
                status = excluded.status,
                locked = excluded.locked,
                weight = excluded.weight,
                source = excluded.source,
                document_backed = excluded.document_backed,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                item["key"],
                item["kind"],
                item["name"],
                item.get("level") or "",
                item.get("confidence"),
                json.dumps(item.get("evidence") or []),
                item["status"],
                1 if item.get("locked") else 0,
                item.get("weight"),
                item["source"],
                1 if item.get("document_backed") else 0,
                _item_payload(item),
                _now(),
            ),
        )


def delete_proposed(source: str) -> None:
    db.init()
    with db._LOCK, _connect() as connection:
        connection.execute(
            "DELETE FROM profile_items WHERE status = 'proposed' AND source = ?",
            (source,),
        )


def list_questions() -> list[dict]:
    db.init()
    with db._LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM reflection_questions ORDER BY id ASC"
        ).fetchall()
    return [_question_row(row) for row in rows]


def replace_questions(questions: list[dict]) -> None:
    db.init()
    with db._LOCK, _connect() as connection:
        connection.execute("DELETE FROM reflection_questions")
        connection.execute("DELETE FROM reflection_answers")
        for question in questions:
            connection.execute(
                """
                INSERT INTO reflection_questions (
                    question_key, experience_ref, experience_kind, prompt, polarity, chips, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    question["question_key"],
                    question["experience_ref"],
                    question["experience_kind"],
                    question["prompt"],
                    question["polarity"],
                    json.dumps(question["chips"]),
                    _now(),
                ),
            )


def list_answers() -> list[dict]:
    db.init()
    with db._LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM reflection_answers ORDER BY id ASC"
        ).fetchall()
    return [_answer_row(row) for row in rows]


def replace_answers(answers: list[dict]) -> None:
    db.init()
    with db._LOCK, _connect() as connection:
        connection.execute("DELETE FROM reflection_answers")
        for answer in answers:
            connection.execute(
                """
                INSERT INTO reflection_answers (
                    question_key, choice, skipped, experience_ref, polarity, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    answer["question_key"],
                    answer.get("choice") or "",
                    1 if answer.get("skipped") else 0,
                    answer.get("experience_ref") or "",
                    answer.get("polarity") or "",
                    _now(),
                ),
            )


def list_versions() -> list[dict]:
    db.init()
    with db._LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM preference_profile ORDER BY version ASC"
        ).fetchall()
    return [_version_row(row) for row in rows]


def latest_version() -> dict | None:
    versions = list_versions()
    return versions[-1] if versions else None


def insert_version(data: dict, *, major_change: bool, reason: str) -> dict:
    db.init()
    with db._LOCK, _connect() as connection:
        row = connection.execute("SELECT MAX(version) AS version FROM preference_profile").fetchone()
        version = int(row["version"] or 0) + 1
        document = dict(data)
        document["version"] = version
        created_at = _now()
        connection.execute(
            """
            INSERT INTO preference_profile (version, data, major_change, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (version, json.dumps(document), 1 if major_change else 0, reason, created_at),
        )
    saved = latest_version()
    assert saved is not None
    return saved
