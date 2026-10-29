"""SQLite tables for the master's tracker, in the same file as the job finder."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app import db

VAULT_PATH: Path | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS academic_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    gpa REAL,
    gpa_scale REAL,
    degree TEXT NOT NULL DEFAULT '',
    graduation_date TEXT NOT NULL DEFAULT '',
    age INTEGER,
    language_levels TEXT NOT NULL DEFAULT '{}',
    publications INTEGER,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS programs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    university TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    degree TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    intake TEXT NOT NULL DEFAULT '',
    application_url TEXT NOT NULL DEFAULT '',
    portal_url TEXT NOT NULL DEFAULT '',
    application_fee REAL,
    language_requirement TEXT NOT NULL DEFAULT '',
    gpa_cutoff REAL,
    age_limit INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    research_areas TEXT NOT NULL DEFAULT '',
    requirements_text TEXT NOT NULL DEFAULT '',
    sop_word_limit INTEGER,
    sop_page_limit INTEGER,
    proposal_word_limit INTEGER,
    proposal_page_limit INTEGER,
    verified INTEGER NOT NULL DEFAULT 0,
    verified_at TEXT,
    status TEXT NOT NULL DEFAULT 'shortlisted',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scholarships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    amount TEXT NOT NULL DEFAULT '',
    eligibility TEXT NOT NULL DEFAULT '',
    deadline TEXT,
    requires_admission INTEGER NOT NULL DEFAULT 0,
    language_requirement TEXT NOT NULL DEFAULT '',
    can_combine_with TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deadlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id INTEGER,
    scholarship_id INTEGER,
    type TEXT NOT NULL,
    date TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE,
    FOREIGN KEY (scholarship_id) REFERENCES scholarships(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deadline_id INTEGER NOT NULL,
    offset_days INTEGER NOT NULL,
    remind_on TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'in_app',
    status TEXT NOT NULL DEFAULT 'pending',
    email_id INTEGER,
    FOREIGN KEY (deadline_id) REFERENCES deadlines(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    is_master INTEGER NOT NULL DEFAULT 0,
    uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checklist_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    country TEXT NOT NULL DEFAULT '',
    program_type TEXT NOT NULL DEFAULT '',
    items_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checklist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'todo',
    document_id INTEGER,
    lor_count INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS writings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    program_id INTEGER,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    word_limit INTEGER,
    page_limit INTEGER,
    status TEXT NOT NULL DEFAULT 'draft',
    origin TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS recommenders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    institution TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommender_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommender_id INTEGER NOT NULL,
    program_id INTEGER NOT NULL,
    requested_at TEXT,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'not_asked',
    reminder_log TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY (recommender_id) REFERENCES recommenders(id) ON DELETE CASCADE,
    FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS professors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    professor TEXT NOT NULL,
    lab TEXT NOT NULL DEFAULT '',
    university TEXT NOT NULL DEFAULT '',
    research_area TEXT NOT NULL DEFAULT '',
    program_id INTEGER,
    status TEXT NOT NULL DEFAULT 'identified',
    followup_after_days INTEGER NOT NULL DEFAULT 7,
    emailed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS scholarship_links (
    scholarship_id INTEGER NOT NULL,
    program_id INTEGER NOT NULL,
    PRIMARY KEY (scholarship_id, program_id),
    FOREIGN KEY (scholarship_id) REFERENCES scholarships(id) ON DELETE CASCADE,
    FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_type TEXT NOT NULL,
    test_date TEXT,
    scores_json TEXT NOT NULL DEFAULT '{}',
    expiry TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS costs (
    program_id INTEGER PRIMARY KEY,
    application_fee REAL NOT NULL DEFAULT 0,
    test_fees REAL NOT NULL DEFAULT 0,
    tuition REAL NOT NULL DEFAULT 0,
    living_cost REAL NOT NULL DEFAULT 0,
    funding_amount REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    to_address TEXT NOT NULL DEFAULT '',
    program_id INTEGER,
    related_type TEXT NOT NULL DEFAULT '',
    related_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approved_at TEXT,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS packages (
    program_id INTEGER PRIMARY KEY,
    sop_variant_id INTEGER,
    proposal_variant_id INTEGER,
    status TEXT NOT NULL DEFAULT 'draft',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def vault_dir() -> Path:
    path = VAULT_PATH or (Path(__file__).resolve().parents[2] / "data" / "vault")
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect():
    path = db.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init() -> None:
    db.init()
    with db._LOCK, connect() as connection:
        connection.executescript(SCHEMA)
        row = connection.execute("SELECT id FROM academic_profile WHERE id = 1").fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO academic_profile (id, gpa, gpa_scale, degree, graduation_date, age, language_levels, publications, updated_at) "
                "VALUES (1, NULL, NULL, '', '', NULL, '{}', NULL, ?)",
                (now(),),
            )
        count = connection.execute("SELECT COUNT(*) AS n FROM checklist_templates").fetchone()["n"]
        if count == 0:
            for template in default_templates():
                connection.execute(
                    "INSERT INTO checklist_templates (name, country, program_type, items_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (template["name"], template["country"], template["program_type"], json.dumps(template["items"]), now()),
                )


def default_templates() -> list[dict]:
    general = [
        {"kind": "transcript", "label": "Transcript", "required": True},
        {"kind": "cv", "label": "CV", "required": True},
        {"kind": "sop", "label": "Statement of purpose", "required": True},
        {"kind": "research_proposal", "label": "Research proposal", "required": False},
        {"kind": "lor", "label": "Letters of recommendation", "required": True, "lor_count": 2},
        {"kind": "test_score", "label": "Test score report", "required": False},
        {"kind": "passport", "label": "Passport", "required": True},
        {"kind": "photo", "label": "Photo", "required": False},
        {"kind": "fee_paid", "label": "Application fee paid", "required": True},
    ]
    japan = [dict(item) for item in general]
    for item in japan:
        if item["kind"] == "research_proposal":
            item["required"] = True
    usa = [dict(item) for item in general]
    for item in usa:
        if item["kind"] == "test_score":
            item["required"] = True
        if item["kind"] == "lor":
            item["lor_count"] = 3
    germany = [dict(item) for item in general]
    for item in germany:
        if item["kind"] == "test_score":
            item["required"] = True
            item["label"] = "Language test score"
    uk = [dict(item) for item in general]
    return [
        {"name": "General master's", "country": "", "program_type": "master", "items": general},
        {"name": "Japan master's", "country": "Japan", "program_type": "master", "items": japan},
        {"name": "United States master's", "country": "United States", "program_type": "master", "items": usa},
        {"name": "Germany master's", "country": "Germany", "program_type": "master", "items": germany},
        {"name": "United Kingdom master's", "country": "United Kingdom", "program_type": "master", "items": uk},
    ]


def _loads(value, fallback):
    try:
        parsed = json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed


def _program(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["verified"] = bool(item["verified"])
    return item


def _scholarship(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["requires_admission"] = bool(item["requires_admission"])
    return item


def _checklist(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["required"] = bool(item["required"])
    return item


def _document(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["is_master"] = bool(item["is_master"])
    return item


def _writing(row: sqlite3.Row) -> dict:
    return dict(row)


def _email(row: sqlite3.Row) -> dict:
    return dict(row)


def _professor(row: sqlite3.Row) -> dict:
    return dict(row)


def _assignment(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["reminder_log"] = _loads(item.get("reminder_log"), [])
    return item


def _test(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["scores"] = _loads(item.pop("scores_json"), {})
    return item


def _template(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["items"] = _loads(item.pop("items_json"), [])
    return item


def get_academic_profile() -> dict:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM academic_profile WHERE id = 1").fetchone()
    item = dict(row)
    item["language_levels"] = _loads(item["language_levels"], {})
    return item


def save_academic_profile(data: dict) -> dict:
    init()
    with db._LOCK, connect() as connection:
        connection.execute(
            """
            UPDATE academic_profile SET
                gpa = ?, gpa_scale = ?, degree = ?, graduation_date = ?, age = ?,
                language_levels = ?, publications = ?, updated_at = ?
            WHERE id = 1
            """,
            (
                data["gpa"],
                data["gpa_scale"],
                data["degree"],
                data["graduation_date"],
                data["age"],
                json.dumps(data["language_levels"]),
                data["publications"],
                now(),
            ),
        )
    return get_academic_profile()


def insert_program(fields: dict) -> dict:
    init()
    stamp = now()
    with db._LOCK, connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO programs (
                university, department, degree, country, intake, application_url, portal_url,
                application_fee, language_requirement, gpa_cutoff, age_limit, notes, source_url,
                research_areas, requirements_text, sop_word_limit, sop_page_limit,
                proposal_word_limit, proposal_page_limit, verified, verified_at, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fields["university"],
                fields["department"],
                fields["degree"],
                fields["country"],
                fields["intake"],
                fields["application_url"],
                fields["portal_url"],
                fields["application_fee"],
                fields["language_requirement"],
                fields["gpa_cutoff"],
                fields["age_limit"],
                fields["notes"],
                fields["source_url"],
                fields["research_areas"],
                fields["requirements_text"],
                fields["sop_word_limit"],
                fields["sop_page_limit"],
                fields["proposal_word_limit"],
                fields["proposal_page_limit"],
                1 if fields["verified"] else 0,
                fields["verified_at"],
                fields["status"],
                stamp,
                stamp,
            ),
        )
        program_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO packages (program_id, sop_variant_id, proposal_variant_id, status, updated_at) VALUES (?, NULL, NULL, 'draft', ?)",
            (program_id, stamp),
        )
        connection.execute(
            "INSERT INTO costs (program_id, application_fee, test_fees, tuition, living_cost, funding_amount) VALUES (?, ?, 0, 0, 0, 0)",
            (program_id, fields["application_fee"] or 0),
        )
        row = connection.execute("SELECT * FROM programs WHERE id = ?", (program_id,)).fetchone()
        return _program(row)


def update_program_row(program_id: int, fields: dict) -> dict | None:
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with db._LOCK, connect() as connection:
        connection.execute(
            f"UPDATE programs SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), now(), program_id),
        )
        row = connection.execute("SELECT * FROM programs WHERE id = ?", (program_id,)).fetchone()
    return _program(row)


def delete_program_row(program_id: int) -> None:
    with db._LOCK, connect() as connection:
        connection.execute("DELETE FROM programs WHERE id = ?", (program_id,))


def get_program_row(program_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM programs WHERE id = ?", (program_id,)).fetchone()
    return _program(row)


def list_program_rows() -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        rows = connection.execute("SELECT * FROM programs ORDER BY id").fetchall()
    return [_program(row) for row in rows]


def insert_deadline(fields: dict) -> dict:
    with db._LOCK, connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO deadlines (program_id, scholarship_id, type, date, timezone, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fields.get("program_id"),
                fields.get("scholarship_id"),
                fields["type"],
                fields["date"],
                fields.get("timezone") or "UTC",
                fields.get("status") or "open",
                now(),
            ),
        )
        deadline_id = cursor.lastrowid
        _replace_reminders(connection, deadline_id, fields["date"])
        row = connection.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
        return dict(row)


def update_deadline_row(deadline_id: int, fields: dict) -> dict | None:
    with db._LOCK, connect() as connection:
        if fields:
            assignments = ", ".join(f"{key} = ?" for key in fields)
            connection.execute(
                f"UPDATE deadlines SET {assignments} WHERE id = ?",
                (*fields.values(), deadline_id),
            )
        row = connection.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
        if row is None:
            return None
        if "date" in fields:
            _replace_reminders(connection, deadline_id, fields["date"])
        return dict(row)


def delete_deadline_row(deadline_id: int) -> None:
    with db._LOCK, connect() as connection:
        connection.execute("DELETE FROM deadlines WHERE id = ?", (deadline_id,))


def get_deadline_row(deadline_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
    return dict(row) if row else None


def list_deadline_rows() -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        rows = connection.execute("SELECT * FROM deadlines").fetchall()
    return [dict(row) for row in rows]


def _replace_reminders(connection: sqlite3.Connection, deadline_id: int, date_text: str) -> None:
    from datetime import date, timedelta

    connection.execute("DELETE FROM reminders WHERE deadline_id = ?", (deadline_id,))
    anchor = date.fromisoformat(date_text[:10])
    for offset in (30, 14, 7, 3, 1):
        remind_on = (anchor - timedelta(days=offset)).isoformat()
        connection.execute(
            """
            INSERT INTO reminders (deadline_id, offset_days, remind_on, channel, status, email_id)
            VALUES (?, ?, ?, 'in_app', 'pending', NULL)
            """,
            (deadline_id, offset, remind_on),
        )


def list_reminder_rows(deadline_id: int | None = None) -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        if deadline_id is None:
            rows = connection.execute("SELECT * FROM reminders ORDER BY remind_on, offset_days").fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM reminders WHERE deadline_id = ? ORDER BY offset_days DESC",
                (deadline_id,),
            ).fetchall()
    return [dict(row) for row in rows]


def set_reminder_email(reminder_id: int, email_id: int) -> None:
    with db._LOCK, connect() as connection:
        connection.execute(
            "UPDATE reminders SET email_id = ?, status = 'drafted' WHERE id = ?",
            (email_id, reminder_id),
        )


def get_reminder_row(reminder_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    return dict(row) if row else None


def list_templates() -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        rows = connection.execute("SELECT * FROM checklist_templates ORDER BY id").fetchall()
    return [_template(row) for row in rows]


def get_template(template_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM checklist_templates WHERE id = ?", (template_id,)).fetchone()
    return _template(row) if row else None


def insert_template(fields: dict) -> dict:
    init()
    with db._LOCK, connect() as connection:
        cursor = connection.execute(
            "INSERT INTO checklist_templates (name, country, program_type, items_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (fields["name"], fields["country"], fields["program_type"], json.dumps(fields["items"]), now()),
        )
        row = connection.execute("SELECT * FROM checklist_templates WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _template(row)


def list_checklist(program_id: int) -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        rows = connection.execute(
            "SELECT * FROM checklist_items WHERE program_id = ? ORDER BY sort_order, id",
            (program_id,),
        ).fetchall()
    return [_checklist(row) for row in rows]


def get_checklist_item(item_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM checklist_items WHERE id = ?", (item_id,)).fetchone()
    return _checklist(row) if row else None


def replace_checklist(program_id: int, items: list[dict]) -> list[dict]:
    with db._LOCK, connect() as connection:
        connection.execute("DELETE FROM checklist_items WHERE program_id = ?", (program_id,))
        for index, item in enumerate(items):
            connection.execute(
                """
                INSERT INTO checklist_items (
                    program_id, kind, label, required, status, document_id, lor_count, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    program_id,
                    item["kind"],
                    item["label"],
                    1 if item.get("required", True) else 0,
                    item.get("status") or "todo",
                    item.get("document_id"),
                    item.get("lor_count"),
                    index,
                ),
            )
    return list_checklist(program_id)


def insert_checklist_item(fields: dict) -> dict:
    with db._LOCK, connect() as connection:
        order = connection.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM checklist_items WHERE program_id = ?",
            (fields["program_id"],),
        ).fetchone()["n"]
        cursor = connection.execute(
            """
            INSERT INTO checklist_items (
                program_id, kind, label, required, status, document_id, lor_count, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fields["program_id"],
                fields["kind"],
                fields["label"],
                1 if fields.get("required", True) else 0,
                fields.get("status") or "todo",
                fields.get("document_id"),
                fields.get("lor_count"),
                order,
            ),
        )
        row = connection.execute("SELECT * FROM checklist_items WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _checklist(row)


def update_checklist_item(item_id: int, fields: dict) -> dict | None:
    if not fields:
        return get_checklist_item(item_id)
    stored = dict(fields)
    if "required" in stored:
        stored["required"] = 1 if stored["required"] else 0
    assignments = ", ".join(f"{key} = ?" for key in stored)
    with db._LOCK, connect() as connection:
        connection.execute(
            f"UPDATE checklist_items SET {assignments} WHERE id = ?",
            (*stored.values(), item_id),
        )
        row = connection.execute("SELECT * FROM checklist_items WHERE id = ?", (item_id,)).fetchone()
    return _checklist(row) if row else None


def delete_checklist_item(item_id: int) -> None:
    with db._LOCK, connect() as connection:
        connection.execute("DELETE FROM checklist_items WHERE id = ?", (item_id,))


def next_document_version(doc_type: str) -> int:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS n FROM documents WHERE doc_type = ?",
            (doc_type,),
        ).fetchone()
    return int(row["n"])


def insert_document(fields: dict) -> dict:
    with db._LOCK, connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO documents (
                doc_type, version, original_name, stored_name, content_type, size_bytes, is_master, uploaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fields["doc_type"],
                fields["version"],
                fields["original_name"],
                fields["stored_name"],
                fields["content_type"],
                fields["size_bytes"],
                1 if fields["is_master"] else 0,
                fields["uploaded_at"],
            ),
        )
        row = connection.execute("SELECT * FROM documents WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _document(row)


def list_documents() -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        rows = connection.execute("SELECT * FROM documents ORDER BY uploaded_at DESC, id DESC").fetchall()
    return [_document(row) for row in rows]


def get_document(document_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    return _document(row) if row else None


def delete_document_row(document_id: int) -> dict | None:
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            return None
        connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    return _document(row)


def programs_for_document(document_id: int) -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT p.* FROM programs p
            JOIN checklist_items c ON c.program_id = p.id
            WHERE c.document_id = ?
            ORDER BY p.university, p.id
            """,
            (document_id,),
        ).fetchall()
    return [_program(row) for row in rows]


def list_writings(kind: str | None = None, program_id: int | None = None, scope: str | None = None) -> list[dict]:
    init()
    clauses = []
    params: list = []
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if program_id is not None:
        clauses.append("program_id = ?")
        params.append(program_id)
    if scope:
        clauses.append("scope = ?")
        params.append(scope)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db._LOCK, connect() as connection:
        rows = connection.execute(f"SELECT * FROM writings {where} ORDER BY id", params).fetchall()
    return [_writing(row) for row in rows]


def get_writing(writing_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM writings WHERE id = ?", (writing_id,)).fetchone()
    return _writing(row) if row else None


def insert_writing(fields: dict) -> dict:
    stamp = now()
    with db._LOCK, connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO writings (
                kind, scope, program_id, title, body, word_limit, page_limit, status, origin, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fields["kind"],
                fields["scope"],
                fields.get("program_id"),
                fields.get("title") or "",
                fields.get("body") or "",
                fields.get("word_limit"),
                fields.get("page_limit"),
                fields.get("status") or "draft",
                fields.get("origin") or "user",
                stamp,
                stamp,
            ),
        )
        row = connection.execute("SELECT * FROM writings WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _writing(row)


def update_writing(writing_id: int, fields: dict) -> dict | None:
    if not fields:
        return get_writing(writing_id)
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with db._LOCK, connect() as connection:
        connection.execute(
            f"UPDATE writings SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), now(), writing_id),
        )
        row = connection.execute("SELECT * FROM writings WHERE id = ?", (writing_id,)).fetchone()
    return _writing(row) if row else None


def insert_recommender(fields: dict) -> dict:
    with db._LOCK, connect() as connection:
        cursor = connection.execute(
            "INSERT INTO recommenders (name, title, institution, email, created_at) VALUES (?, ?, ?, ?, ?)",
            (fields["name"], fields["title"], fields["institution"], fields["email"], now()),
        )
        row = connection.execute("SELECT * FROM recommenders WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


def update_recommender(recommender_id: int, fields: dict) -> dict | None:
    if not fields:
        return get_recommender(recommender_id)
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with db._LOCK, connect() as connection:
        connection.execute(
            f"UPDATE recommenders SET {assignments} WHERE id = ?",
            (*fields.values(), recommender_id),
        )
        row = connection.execute("SELECT * FROM recommenders WHERE id = ?", (recommender_id,)).fetchone()
    return dict(row) if row else None


def get_recommender(recommender_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM recommenders WHERE id = ?", (recommender_id,)).fetchone()
    return dict(row) if row else None


def list_recommenders() -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        rows = connection.execute("SELECT * FROM recommenders ORDER BY name, id").fetchall()
    return [dict(row) for row in rows]


def delete_recommender(recommender_id: int) -> None:
    with db._LOCK, connect() as connection:
        connection.execute("DELETE FROM recommenders WHERE id = ?", (recommender_id,))


def insert_assignment(fields: dict) -> dict:
    with db._LOCK, connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO recommender_assignments (
                recommender_id, program_id, requested_at, due_date, status, reminder_log
            ) VALUES (?, ?, ?, ?, ?, '[]')
            """,
            (
                fields["recommender_id"],
                fields["program_id"],
                fields.get("requested_at"),
                fields.get("due_date"),
                fields.get("status") or "not_asked",
            ),
        )
        row = connection.execute(
            """
            SELECT a.*, r.name, r.title, r.institution, r.email
            FROM recommender_assignments a
            JOIN recommenders r ON r.id = a.recommender_id
            WHERE a.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return _assignment(row)


def update_assignment(assignment_id: int, fields: dict) -> dict | None:
    stored = dict(fields)
    if "reminder_log" in stored and not isinstance(stored["reminder_log"], str):
        stored["reminder_log"] = json.dumps(stored["reminder_log"])
    if not stored:
        return get_assignment(assignment_id)
    assignments = ", ".join(f"{key} = ?" for key in stored)
    with db._LOCK, connect() as connection:
        connection.execute(
            f"UPDATE recommender_assignments SET {assignments} WHERE id = ?",
            (*stored.values(), assignment_id),
        )
    return get_assignment(assignment_id)


def get_assignment(assignment_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute(
            """
            SELECT a.*, r.name, r.title, r.institution, r.email
            FROM recommender_assignments a
            JOIN recommenders r ON r.id = a.recommender_id
            WHERE a.id = ?
            """,
            (assignment_id,),
        ).fetchone()
    return _assignment(row) if row else None


def list_assignments(program_id: int | None = None) -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        if program_id is None:
            rows = connection.execute(
                """
                SELECT a.*, r.name, r.title, r.institution, r.email
                FROM recommender_assignments a
                JOIN recommenders r ON r.id = a.recommender_id
                ORDER BY a.id
                """
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT a.*, r.name, r.title, r.institution, r.email
                FROM recommender_assignments a
                JOIN recommenders r ON r.id = a.recommender_id
                WHERE a.program_id = ?
                ORDER BY a.id
                """,
                (program_id,),
            ).fetchall()
    return [_assignment(row) for row in rows]


def insert_professor(fields: dict) -> dict:
    stamp = now()
    with db._LOCK, connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO professors (
                professor, lab, university, research_area, program_id, status,
                followup_after_days, emailed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fields["professor"],
                fields["lab"],
                fields["university"],
                fields["research_area"],
                fields.get("program_id"),
                fields.get("status") or "identified",
                fields.get("followup_after_days") if fields.get("followup_after_days") is not None else 7,
                fields.get("emailed_at"),
                stamp,
                stamp,
            ),
        )
        row = connection.execute("SELECT * FROM professors WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _professor(row)


def update_professor(professor_id: int, fields: dict) -> dict | None:
    if not fields:
        return get_professor(professor_id)
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with db._LOCK, connect() as connection:
        connection.execute(
            f"UPDATE professors SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), now(), professor_id),
        )
        row = connection.execute("SELECT * FROM professors WHERE id = ?", (professor_id,)).fetchone()
    return _professor(row) if row else None


def get_professor(professor_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM professors WHERE id = ?", (professor_id,)).fetchone()
    return _professor(row) if row else None


def list_professors(program_id: int | None = None) -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        if program_id is None:
            rows = connection.execute("SELECT * FROM professors ORDER BY id").fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM professors WHERE program_id = ? ORDER BY id",
                (program_id,),
            ).fetchall()
    return [_professor(row) for row in rows]


def delete_professor(professor_id: int) -> None:
    with db._LOCK, connect() as connection:
        connection.execute("DELETE FROM professors WHERE id = ?", (professor_id,))


def insert_scholarship(fields: dict) -> dict:
    stamp = now()
    with db._LOCK, connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO scholarships (
                name, provider, amount, eligibility, deadline, requires_admission,
                language_requirement, can_combine_with, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fields["name"],
                fields["provider"],
                fields["amount"],
                fields["eligibility"],
                fields.get("deadline"),
                1 if fields.get("requires_admission") else 0,
                fields["language_requirement"],
                fields["can_combine_with"],
                stamp,
                stamp,
            ),
        )
        row = connection.execute("SELECT * FROM scholarships WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _scholarship(row)


def update_scholarship(scholarship_id: int, fields: dict) -> dict | None:
    stored = dict(fields)
    if "requires_admission" in stored:
        stored["requires_admission"] = 1 if stored["requires_admission"] else 0
    if not stored:
        return get_scholarship(scholarship_id)
    assignments = ", ".join(f"{key} = ?" for key in stored)
    with db._LOCK, connect() as connection:
        connection.execute(
            f"UPDATE scholarships SET {assignments}, updated_at = ? WHERE id = ?",
            (*stored.values(), now(), scholarship_id),
        )
        row = connection.execute("SELECT * FROM scholarships WHERE id = ?", (scholarship_id,)).fetchone()
    return _scholarship(row) if row else None


def get_scholarship(scholarship_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM scholarships WHERE id = ?", (scholarship_id,)).fetchone()
    return _scholarship(row) if row else None


def list_scholarships() -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        rows = connection.execute("SELECT * FROM scholarships ORDER BY deadline, id").fetchall()
    return [_scholarship(row) for row in rows]


def delete_scholarship(scholarship_id: int) -> None:
    with db._LOCK, connect() as connection:
        connection.execute("DELETE FROM scholarships WHERE id = ?", (scholarship_id,))


def link_scholarship(scholarship_id: int, program_id: int) -> None:
    with db._LOCK, connect() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO scholarship_links (scholarship_id, program_id) VALUES (?, ?)",
            (scholarship_id, program_id),
        )


def unlink_scholarship(scholarship_id: int, program_id: int) -> None:
    with db._LOCK, connect() as connection:
        connection.execute(
            "DELETE FROM scholarship_links WHERE scholarship_id = ? AND program_id = ?",
            (scholarship_id, program_id),
        )


def scholarship_program_ids(scholarship_id: int) -> list[int]:
    init()
    with db._LOCK, connect() as connection:
        rows = connection.execute(
            "SELECT program_id FROM scholarship_links WHERE scholarship_id = ? ORDER BY program_id",
            (scholarship_id,),
        ).fetchall()
    return [row["program_id"] for row in rows]


def linked_scholarships(program_id: int) -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        rows = connection.execute(
            """
            SELECT s.* FROM scholarships s
            JOIN scholarship_links l ON l.scholarship_id = s.id
            WHERE l.program_id = ?
            ORDER BY s.deadline, s.id
            """,
            (program_id,),
        ).fetchall()
    return [_scholarship(row) for row in rows]


def find_scholarship_deadline(scholarship_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute(
            "SELECT * FROM deadlines WHERE scholarship_id = ? AND type = 'scholarship' ORDER BY id LIMIT 1",
            (scholarship_id,),
        ).fetchone()
    return dict(row) if row else None


def insert_test(fields: dict) -> dict:
    with db._LOCK, connect() as connection:
        cursor = connection.execute(
            "INSERT INTO tests (test_type, test_date, scores_json, expiry, created_at) VALUES (?, ?, ?, ?, ?)",
            (fields["test_type"], fields.get("test_date"), json.dumps(fields["scores"]), fields.get("expiry"), now()),
        )
        row = connection.execute("SELECT * FROM tests WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _test(row)


def update_test(test_id: int, fields: dict) -> dict | None:
    stored = dict(fields)
    if "scores" in stored:
        stored["scores_json"] = json.dumps(stored.pop("scores"))
    if not stored:
        return get_test(test_id)
    assignments = ", ".join(f"{key} = ?" for key in stored)
    with db._LOCK, connect() as connection:
        connection.execute(f"UPDATE tests SET {assignments} WHERE id = ?", (*stored.values(), test_id))
        row = connection.execute("SELECT * FROM tests WHERE id = ?", (test_id,)).fetchone()
    return _test(row) if row else None


def get_test(test_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM tests WHERE id = ?", (test_id,)).fetchone()
    return _test(row) if row else None


def list_tests() -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        rows = connection.execute("SELECT * FROM tests ORDER BY expiry, id").fetchall()
    return [_test(row) for row in rows]


def delete_test(test_id: int) -> None:
    with db._LOCK, connect() as connection:
        connection.execute("DELETE FROM tests WHERE id = ?", (test_id,))


def get_cost(program_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM costs WHERE program_id = ?", (program_id,)).fetchone()
    return dict(row) if row else None


def save_cost(program_id: int, fields: dict) -> dict:
    with db._LOCK, connect() as connection:
        connection.execute(
            """
            INSERT INTO costs (program_id, application_fee, test_fees, tuition, living_cost, funding_amount)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(program_id) DO UPDATE SET
                application_fee = excluded.application_fee,
                test_fees = excluded.test_fees,
                tuition = excluded.tuition,
                living_cost = excluded.living_cost,
                funding_amount = excluded.funding_amount
            """,
            (
                program_id,
                fields["application_fee"],
                fields["test_fees"],
                fields["tuition"],
                fields["living_cost"],
                fields["funding_amount"],
            ),
        )
        row = connection.execute("SELECT * FROM costs WHERE program_id = ?", (program_id,)).fetchone()
    return dict(row)


def insert_email(fields: dict) -> dict:
    stamp = now()
    with db._LOCK, connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO emails (
                kind, subject, body, status, to_address, program_id, related_type, related_id,
                created_at, updated_at, approved_at, sent_at
            ) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                fields["kind"],
                fields.get("subject") or "",
                fields.get("body") or "",
                fields.get("to_address") or "",
                fields.get("program_id"),
                fields.get("related_type") or "",
                fields.get("related_id"),
                stamp,
                stamp,
            ),
        )
        row = connection.execute("SELECT * FROM emails WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _email(row)


def update_email(email_id: int, fields: dict) -> dict | None:
    if not fields:
        return get_email(email_id)
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with db._LOCK, connect() as connection:
        connection.execute(
            f"UPDATE emails SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), now(), email_id),
        )
        row = connection.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    return _email(row) if row else None


def get_email(email_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    return _email(row) if row else None


def list_emails(status: str | None = None) -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        if status:
            rows = connection.execute(
                "SELECT * FROM emails WHERE status = ? ORDER BY id DESC",
                (status,),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM emails ORDER BY id DESC").fetchall()
    return [_email(row) for row in rows]


def get_package(program_id: int) -> dict | None:
    init()
    with db._LOCK, connect() as connection:
        row = connection.execute("SELECT * FROM packages WHERE program_id = ?", (program_id,)).fetchone()
    return dict(row) if row else None


def update_package(program_id: int, fields: dict) -> dict | None:
    if not fields:
        return get_package(program_id)
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with db._LOCK, connect() as connection:
        connection.execute(
            f"UPDATE packages SET {assignments}, updated_at = ? WHERE program_id = ?",
            (*fields.values(), now(), program_id),
        )
        row = connection.execute("SELECT * FROM packages WHERE program_id = ?", (program_id,)).fetchone()
    return dict(row) if row else None


def add_audit(actor: str, action: str, subject_type: str, subject_id: str, detail: str = "") -> dict:
    with db._LOCK, connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO audit_log (actor, action, subject_type, subject_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (actor, action, subject_type, str(subject_id), detail, now()),
        )
        row = connection.execute("SELECT * FROM audit_log WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


def list_audit(limit: int = 50) -> list[dict]:
    init()
    with db._LOCK, connect() as connection:
        rows = connection.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
