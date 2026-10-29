import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "jobfinder.db"
_LOCK = threading.Lock()

DEFAULT_PROFILE = {
    "resume_text": "",
    "name": "",
    "contact": "",
    "titles": [],
    "seeking": [],
    "must_have": [],
    "nice_have": [],
    "city": "Dhaka",
    "salary_floor_bdt": None,
    "work_mode": "both",
    "exclude": [],
}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def init() -> None:
    with _LOCK, _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                company TEXT,
                location TEXT,
                salary TEXT,
                description TEXT,
                board_url TEXT,
                company_url TEXT,
                posted_at TEXT,
                is_remote INTEGER NOT NULL DEFAULT 0,
                location_note TEXT,
                score INTEGER NOT NULL DEFAULT 0,
                reasons TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'new',
                tailored_cv TEXT,
                cover_note TEXT,
                posting_text TEXT,
                posting_label TEXT,
                official_url TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cache (
                cache_key TEXT PRIMARY KEY,
                body TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );
            """
        )
        row = connection.execute("SELECT data FROM profile WHERE id = 1").fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO profile (id, data) VALUES (1, ?)",
                (json.dumps(DEFAULT_PROFILE),),
            )
        from app.onboarding.migrate import apply

        apply(connection)


def _loads(value, fallback):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def split_terms(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value).replace("\n", ",").split(",")
    terms: list[str] = []
    for item in raw_items:
        term = str(item).strip()
        if term and term.lower() not in {existing.lower() for existing in terms}:
            terms.append(term)
    return terms


def normalize_profile(data: dict) -> dict:
    salary = data.get("salary_floor_bdt")
    if salary in ("", None):
        salary_value = None
    else:
        try:
            salary_value = int(float(salary))
        except (TypeError, ValueError):
            salary_value = None
    work_mode = data.get("work_mode") if data.get("work_mode") in {"both", "remote"} else "both"
    city = str(data.get("city") or "").strip() or "Dhaka"
    return {
        "resume_text": str(data.get("resume_text") or ""),
        "name": str(data.get("name") or "").strip(),
        "contact": str(data.get("contact") or "").strip(),
        "titles": split_terms(data.get("titles")),
        "seeking": split_terms(data.get("seeking")),
        "must_have": split_terms(data.get("must_have")),
        "nice_have": split_terms(data.get("nice_have")),
        "city": city,
        "salary_floor_bdt": salary_value,
        "work_mode": work_mode,
        "exclude": split_terms(data.get("exclude")),
    }


def get_profile() -> dict:
    init()
    with _LOCK, _connect() as connection:
        row = connection.execute("SELECT data FROM profile WHERE id = 1").fetchone()
    return normalize_profile(_loads(row["data"], DEFAULT_PROFILE))


def save_profile(data: dict) -> dict:
    profile = normalize_profile({**get_profile(), **data})
    with _LOCK, _connect() as connection:
        connection.execute("UPDATE profile SET data = ? WHERE id = 1", (json.dumps(profile),))
    return profile


def get_settings() -> dict:
    init()
    with _LOCK, _connect() as connection:
        rows = connection.execute("SELECT key, value FROM settings").fetchall()
    stored = {row["key"]: row["value"] for row in rows}
    return {
        "base_url": stored.get("base_url", ""),
        "model": stored.get("model", ""),
        "api_key": stored.get("api_key", ""),
        "preset_provider": stored.get("preset_provider", ""),
        "preset_model": stored.get("preset_model", ""),
        "use_preset": stored.get("use_preset", "1") == "1",
    }


def public_settings() -> dict:
    from app import model_presets
    
    settings = get_settings()
    use_preset = settings.get("use_preset", False)
    key = settings.get("api_key", "")
    
    # If using preset, resolve it and combine with user's key
    if use_preset:
        provider_id = settings.get("preset_provider", "")
        model_id = settings.get("preset_model", "")
        
        if provider_id and model_id:
            resolved = model_presets.resolve_preset(provider_id, model_id)
            if resolved:
                # Preset resolved, check if user has entered a key
                ready = bool(key)
                note = ""
                if not ready:
                    from app.llm import NO_MODEL
                    note = NO_MODEL
                
                return {
                    "use_preset": True,
                    "preset_provider": provider_id,
                    "preset_model": model_id,
                    "base_url": resolved["base_url"],
                    "model": resolved["model"],
                    "has_key": bool(key),
                    "key_hint": f"····{key[-4:]}" if len(key) >= 4 else ("saved" if key else ""),
                    "configured": ready,
                    "model_note": note,
                }
    
    # Using advanced/custom settings or no preset selected
    base_url = settings.get("base_url", "")
    model = settings.get("model", "")
    ready = bool(base_url and model and key)
    note = ""
    if not ready:
        from app.llm import NO_MODEL
        note = NO_MODEL
    
    return {
        "use_preset": False,
        "preset_provider": "",
        "preset_model": "",
        "base_url": base_url,
        "model": model,
        "has_key": bool(key),
        "key_hint": f"····{key[-4:]}" if len(key) >= 4 else ("saved" if key else ""),
        "configured": ready,
        "model_note": note,
    }


def _write_settings(values: dict[str, str]) -> None:
    init()
    with _LOCK, _connect() as connection:
        for name, value in values.items():
            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (name, value),
            )


def save_settings(base_url: str, model: str, api_key: str | None, clear_key: bool) -> dict:
    current = get_settings()
    if clear_key:
        key = ""
    elif api_key is None or api_key == "":
        key = current["api_key"]
    else:
        key = api_key.strip()
    _write_settings({
        "base_url": base_url.strip().rstrip("/"),
        "model": model.strip(),
        "api_key": key,
        "use_preset": "0",  # Using advanced/custom settings
        "preset_provider": "",
        "preset_model": "",
    })
    return public_settings()


def save_preset_settings(provider_id: str, model_id: str, api_key: str | None = None, clear_key: bool = False) -> dict:
    """Save preset provider and model selection, optionally with API key."""
    from app import model_presets
    
    resolved = model_presets.resolve_preset(provider_id, model_id)
    if not resolved:
        # Invalid preset
        return public_settings()
    
    current = get_settings()
    if clear_key:
        key = ""
    elif api_key is not None and api_key != "":
        key = api_key.strip()
    else:
        key = current.get("api_key", "")
    
    _write_settings({
        "preset_provider": provider_id,
        "preset_model": model_id,
        "use_preset": "1",
        "base_url": resolved["base_url"],
        "model": resolved["model"],
        "api_key": key,
    })
    return public_settings()


def get_feed_settings() -> dict:
    init()
    with _LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT key, value FROM settings WHERE key IN ('auto_refresh', 'last_feed_at', 'last_feed_added')"
        ).fetchall()
    stored = {row["key"]: row["value"] for row in rows}
    raw_added = stored.get("last_feed_added", "")
    try:
        added = int(raw_added) if raw_added != "" else None
    except ValueError:
        added = None
    return {
        "auto_refresh": stored.get("auto_refresh", "1") != "0",
        "last_feed_at": stored.get("last_feed_at", ""),
        "last_feed_added": added,
    }


def set_auto_refresh(enabled: bool) -> None:
    _write_settings({"auto_refresh": "1" if enabled else "0"})


def mark_feed_refreshed(added: int) -> None:
    _write_settings({
        "last_feed_at": _now(),
        "last_feed_added": str(int(added)),
    })


def add_message(role: str, content: str) -> None:
    init()
    with _LOCK, _connect() as connection:
        connection.execute(
            "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, _now()),
        )


def list_messages(limit: int = 20) -> list[dict]:
    init()
    with _LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT role, content, created_at FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def cache_get(key: str, ttl_hours: int = 6) -> str | None:
    init()
    with _LOCK, _connect() as connection:
        row = connection.execute("SELECT body, fetched_at FROM cache WHERE cache_key = ?", (key,)).fetchone()
    if row is None:
        return None
    fetched = datetime.fromisoformat(row["fetched_at"])
    if datetime.now(timezone.utc) - fetched > timedelta(hours=ttl_hours):
        return None
    return row["body"]


def cache_set(key: str, body: str) -> None:
    init()
    with _LOCK, _connect() as connection:
        connection.execute(
            "INSERT INTO cache (cache_key, body, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET body = excluded.body, fetched_at = excluded.fetched_at",
            (key, body, _now()),
        )


def store_search(jobs: list[dict]) -> int:
    """Insert listings and refresh details for ones already stored.

    Leads that are missing from this batch stay on the board. Status, the edited
    resume, and the cover note are left as they are. Returns how many rows were new.
    """
    init()
    now = _now()
    added = 0
    with _LOCK, _connect() as connection:
        for job in jobs:
            existing = connection.execute("SELECT id FROM jobs WHERE id = ?", (job["id"],)).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO jobs (
                        id, source, title, company, location, salary, description, board_url,
                        company_url, posted_at, is_remote, location_note, score, reasons, status,
                        tailored_cv, cover_note, posting_text, posting_label, official_url, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', '', '', '', '', ?)
                    """,
                    _job_values(job, now),
                )
                added += 1
            else:
                connection.execute(
                    """
                    UPDATE jobs SET
                        source = ?, title = ?, company = ?, location = ?, salary = ?, description = ?,
                        board_url = ?, company_url = ?, posted_at = ?, is_remote = ?, location_note = ?,
                        score = ?, reasons = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        job["source"],
                        job["title"],
                        job.get("company") or "",
                        job.get("location") or "",
                        job.get("salary") or "",
                        job.get("description") or "",
                        job.get("board_url") or "",
                        job.get("company_url") or "",
                        job.get("posted_at") or "",
                        1 if job.get("is_remote") else 0,
                        job.get("location_note") or "",
                        int(job.get("score") or 0),
                        json.dumps(job.get("reasons") or []),
                        now,
                        job["id"],
                    ),
                )
    return added


def job_ids() -> set[str]:
    init()
    with _LOCK, _connect() as connection:
        rows = connection.execute("SELECT id FROM jobs").fetchall()
    return {row["id"] for row in rows}


def upsert_imported(jobs: list[dict]) -> None:
    """Insert pasted leads without dropping other rows.

    Pasting the same link again refreshes the posting and keeps status and any CV.
    """
    init()
    now = _now()
    with _LOCK, _connect() as connection:
        for job in jobs:
            existing = connection.execute("SELECT id FROM jobs WHERE id = ?", (job["id"],)).fetchone()
            posting_text = job.get("posting_text") or job.get("description") or ""
            posting_label = job.get("posting_label") or ""
            official_url = job.get("official_url") or job.get("company_url") or job.get("board_url") or ""
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO jobs (
                        id, source, title, company, location, salary, description, board_url,
                        company_url, posted_at, is_remote, location_note, score, reasons, status,
                        tailored_cv, cover_note, posting_text, posting_label, official_url, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', '', ?, ?, ?, ?)
                    """,
                    (
                        job["id"],
                        job["source"],
                        job["title"],
                        job.get("company") or "",
                        job.get("location") or "",
                        job.get("salary") or "",
                        job.get("description") or "",
                        job.get("board_url") or "",
                        job.get("company_url") or "",
                        job.get("posted_at") or "",
                        1 if job.get("is_remote") else 0,
                        job.get("location_note") or "",
                        int(job.get("score") or 0),
                        json.dumps(job.get("reasons") or []),
                        posting_text,
                        posting_label,
                        official_url,
                        now,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE jobs SET
                        source = ?, title = ?, company = ?, location = ?, salary = ?, description = ?,
                        board_url = ?, company_url = ?, posted_at = ?, is_remote = ?, location_note = ?,
                        score = ?, reasons = ?, posting_text = ?, posting_label = ?, official_url = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        job["source"],
                        job["title"],
                        job.get("company") or "",
                        job.get("location") or "",
                        job.get("salary") or "",
                        job.get("description") or "",
                        job.get("board_url") or "",
                        job.get("company_url") or "",
                        job.get("posted_at") or "",
                        1 if job.get("is_remote") else 0,
                        job.get("location_note") or "",
                        int(job.get("score") or 0),
                        json.dumps(job.get("reasons") or []),
                        posting_text,
                        posting_label,
                        official_url,
                        now,
                        job["id"],
                    ),
                )


def list_jobs(statuses: list[str]) -> list[dict]:
    init()
    marks = ",".join("?" for _ in statuses)
    with _LOCK, _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY score DESC, posted_at DESC",
            statuses,
        ).fetchall()
    return [_row_job(row) for row in rows]


def count_jobs(status: str) -> int:
    init()
    with _LOCK, _connect() as connection:
        row = connection.execute("SELECT COUNT(*) AS n FROM jobs WHERE status = ?", (status,)).fetchone()
    return int(row["n"])


def get_job(job_id: str) -> dict | None:
    init()
    with _LOCK, _connect() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_job(row) if row else None


def update_job(job_id: str, **fields) -> dict | None:
    current = get_job(job_id)
    if current is None:
        return None
    allowed = {
        "status", "tailored_cv", "cover_note", "posting_text", "posting_label",
        "official_url", "company_url",
    }
    updates = {key: value for key, value in fields.items() if key in allowed and value is not None}
    if not updates:
        return current
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with _LOCK, _connect() as connection:
        connection.execute(
            f"UPDATE jobs SET {assignments}, updated_at = ? WHERE id = ?",
            (*updates.values(), _now(), job_id),
        )
    return get_job(job_id)


def _job_values(job: dict, now: str) -> tuple:
    return (
        job["id"],
        job["source"],
        job["title"],
        job.get("company") or "",
        job.get("location") or "",
        job.get("salary") or "",
        job.get("description") or "",
        job.get("board_url") or "",
        job.get("company_url") or "",
        job.get("posted_at") or "",
        1 if job.get("is_remote") else 0,
        job.get("location_note") or "",
        int(job.get("score") or 0),
        json.dumps(job.get("reasons") or []),
        now,
    )


def _row_job(row: sqlite3.Row) -> dict:
    job = dict(row)
    job["is_remote"] = bool(job["is_remote"])
    job["reasons"] = _loads(job["reasons"], [])
    return job


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
