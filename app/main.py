import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import db
from app.cv import assemble_cv, extract_summary, template_documents, template_summary
from app.llm import ModelError, build_documents, configured, complete, requirements_turn
from app.match import query_keywords, rank_jobs, search_block_reason
from app.posting import _relevant, load_posting
from app.resume import extract_pdf_text, parse_resume
from app.sources import collect
from app.sources.linkedin import extract_urls, fetch_jobs, looks_like_secret

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "static" / "index.html"
MAX_RESUME_PDF_BYTES = 8 * 1024 * 1024
PDF_CONTENT_TYPES = {"", "application/pdf", "application/x-pdf", "application/octet-stream"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    yield


app = FastAPI(title="Job finder", lifespan=lifespan)


class ProfileIn(BaseModel):
    resume_text: str = ""
    name: str = ""
    contact: str = ""
    titles: list[str] | str = Field(default_factory=list)
    must_have: list[str] | str = Field(default_factory=list)
    nice_have: list[str] | str = Field(default_factory=list)
    city: str = "Dhaka"
    salary_floor_bdt: int | float | str | None = None
    work_mode: str = "both"
    exclude: list[str] | str = Field(default_factory=list)


class ResumeIn(BaseModel):
    text: str = ""


class ChatIn(BaseModel):
    message: str = ""


class SettingsIn(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str | None = None
    clear_key: bool = False


class DecideIn(BaseModel):
    status: str
    tailored_cv: str = ""
    cover_note: str = ""


class LinkedInIn(BaseModel):
    urls: str = Field(default="", max_length=8000)


def _lead(job: dict) -> dict:
    return {
        "id": job["id"],
        "source": job["source"],
        "title": job["title"],
        "company": job["company"],
        "location": job["location"],
        "salary": job["salary"],
        "score": job["score"],
        "reasons": job["reasons"],
        "board_url": job["board_url"],
        "official_url": job.get("official_url") or job.get("company_url") or job["board_url"],
        "status": job["status"],
        "snippet": (job.get("description") or "")[:280],
        "posting_label": job.get("posting_label") or "",
    }


def _board() -> dict:
    return {
        "leads": [_lead(job) for job in db.list_jobs(["new", "saved"])],
        "applied": [_lead(job) for job in db.list_jobs(["applied"])],
        "skipped_count": db.count_jobs("skipped"),
        "skipped": [_lead(job) for job in db.list_jobs(["skipped"])],
    }


def _chat_patch(patch: dict) -> dict:
    cleaned: dict = {}
    for key in ("titles", "must_have", "nice_have", "exclude"):
        if key in patch:
            terms = db.split_terms(patch[key])
            if terms:
                cleaned[key] = terms
    if patch.get("work_mode") in {"both", "remote"}:
        cleaned["work_mode"] = patch["work_mode"]
    city = str(patch.get("city") or "").strip()
    if city:
        cleaned["city"] = city
    if patch.get("salary_floor_bdt") not in (None, ""):
        cleaned["salary_floor_bdt"] = patch["salary_floor_bdt"]
    return cleaned


@app.get("/")
def index():
    return FileResponse(PAGE)


@app.get("/api/state")
def state():
    return {
        "profile": db.get_profile(),
        "settings": db.public_settings(),
        "messages": db.list_messages(30),
        **_board(),
    }


@app.put("/api/profile")
def save_profile(body: ProfileIn):
    return db.save_profile(body.model_dump())


def _apply_resume(text: str) -> dict:
    parsed = parse_resume(text)
    current = db.get_profile()
    update = {
        "resume_text": text,
        "name": parsed["name"],
        "contact": parsed["contact"],
        "titles": parsed["titles"],
        "must_have": parsed["skills"],
    }
    if not str(current.get("city") or "").strip():
        update["city"] = "Dhaka"
    if current.get("work_mode") not in {"both", "remote"}:
        update["work_mode"] = "both"
    return db.save_profile(update)


def _fill_blanks_from_resume(data: dict) -> dict:
    """Fill empty name, contact, skills, and titles from the resume.

    Typed skills and titles are kept. Titles only boost a listing. A title
    the person typed is not required, and an empty title box is not an error.
    """
    text = str(data.get("resume_text") or "").strip()
    filled = dict(data)
    if not text:
        return filled
    parsed = parse_resume(text)
    if not str(filled.get("name") or "").strip() and parsed["name"]:
        filled["name"] = parsed["name"]
    if not str(filled.get("contact") or "").strip() and parsed["contact"]:
        filled["contact"] = parsed["contact"]
    skills_blank = not db.split_terms(filled.get("must_have")) and not db.split_terms(filled.get("nice_have"))
    titles_blank = not db.split_terms(filled.get("titles"))
    if skills_blank and parsed["skills"]:
        filled["must_have"] = parsed["skills"]
    if titles_blank and parsed["titles"]:
        filled["titles"] = parsed["titles"]
    if skills_blank and titles_blank:
        if parsed.get("city"):
            filled["city"] = parsed["city"]
        if parsed.get("work_mode") == "remote":
            filled["work_mode"] = "remote"
    return filled


def _require_resume(profile: dict) -> None:
    resume = (profile.get("resume_text") or "").strip()
    name = (profile.get("name") or "").strip()
    if resume and name:
        return
    if resume:
        raise HTTPException(
            status_code=400,
            detail="Paste or upload a resume that includes your name. The CV is built from it.",
        )
    raise HTTPException(status_code=400, detail="Paste or upload a resume first. The CV is built from it.")


def _is_pdf_upload(file: UploadFile, data: bytes) -> bool:
    if b"%PDF" not in data[:1024]:
        return False
    name = (file.filename or "").strip().lower()
    if name and not name.endswith(".pdf"):
        return False
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    return content_type in PDF_CONTENT_TYPES


async def _read_upload(file: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = await file.read(256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise HTTPException(status_code=400, detail="That PDF is larger than 8 MB.")
            chunks.append(chunk)
    finally:
        await file.close()
    return b"".join(chunks)


@app.post("/api/resume")
def save_resume(body: ResumeIn):
    return _apply_resume(body.text)


@app.post("/api/resume/pdf")
async def upload_resume_pdf(file: UploadFile = File(...)):
    data = await _read_upload(file, MAX_RESUME_PDF_BYTES)
    if not _is_pdf_upload(file, data):
        raise HTTPException(status_code=400, detail="Upload a PDF file.")
    try:
        text = extract_pdf_text(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not text:
        raise HTTPException(
            status_code=400,
            detail="This PDF is a scan with no text layer, so it cannot be read. Paste the resume text instead.",
        )
    return _apply_resume(text)


@app.put("/api/settings")
def save_settings(body: SettingsIn):
    return db.save_settings(body.base_url, body.model, body.api_key, body.clear_key)


@app.post("/api/settings/test")
async def test_settings():
    settings = db.get_settings()
    if not configured(settings):
        raise HTTPException(status_code=400, detail="Add a base URL and a model name first.")
    try:
        reply = await complete(settings, [{"role": "user", "content": "Reply with the single word ready."}])
    except ModelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "reply": reply.strip()[:200]}


@app.post("/api/chat")
async def chat(body: ChatIn):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Write a message first.")
    settings = db.get_settings()
    if not configured(settings):
        raise HTTPException(
            status_code=400,
            detail="Connect a model in Settings. You can still fill the form and search without one.",
        )
    try:
        reply, patch = await requirements_turn(settings, db.list_messages(12), db.get_profile(), message)
    except ModelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="The model did not return requirements I could read. Try again.") from exc
    db.add_message("user", message)
    db.add_message("assistant", reply)
    profile = db.save_profile(_chat_patch(patch))
    return {"reply": reply, "profile": profile, "messages": db.list_messages(30)}


@app.post("/api/search")
async def search(body: ProfileIn):
    profile = db.save_profile(_fill_blanks_from_resume(body.model_dump()))
    reason = search_block_reason(profile)
    if reason:
        raise HTTPException(status_code=400, detail=reason)
    jobs, warnings = await collect(query_keywords(profile))
    ranked = rank_jobs(jobs, profile)[:25]
    warnings = list(warnings)
    sources_down = bool(warnings) and not ranked
    if not ranked and not warnings:
        warnings.append("No listings shared a skill or a past title with this resume.")
    if not sources_down:
        db.store_search(ranked)
    return {"matched": len(ranked), "warnings": warnings, "profile": profile, **_board()}


def _needs_cv(job: dict) -> bool:
    cv = job.get("tailored_cv") or ""
    title = (job.get("title") or "").strip()
    if not cv.strip() or "\nResume\n" in cv or "The overlap with this posting" in cv:
        return True
    return bool(title) and title.lower() not in cv.lower()


def _job_source(job: dict) -> dict:
    return {**job, "description": job.get("posting_text") or job.get("description") or ""}


async def _documents(profile: dict, job: dict) -> tuple[str, str]:
    try:
        return await build_documents(db.get_settings(), profile, _job_source(job))
    except Exception:
        return template_documents(profile, _job_source(job))


async def _posting_within(job: dict, seconds: float) -> dict | None:
    if _relevant(job.get("title") or "", job.get("posting_text") or ""):
        return None
    try:
        return await asyncio.wait_for(load_posting(job), timeout=seconds)
    except Exception:
        return None


@app.post("/api/linkedin")
async def import_linkedin(body: LinkedInIn):
    pasted = body.urls or ""
    if looks_like_secret(pasted):
        raise HTTPException(
            status_code=400,
            detail="Paste LinkedIn job links only. This app does not take your LinkedIn password or session cookie.",
        )
    urls, warnings = extract_urls(pasted)
    if not urls:
        raise HTTPException(
            status_code=400,
            detail="Paste one or more LinkedIn job links. This app does not use your LinkedIn password.",
        )
    jobs, fetch_warnings = await fetch_jobs(urls)
    warnings.extend(fetch_warnings)
    if jobs:
        db.upsert_imported(jobs)
    return {"imported": len(jobs), "warnings": warnings, **_board()}


@app.get("/api/jobs/{job_id}")
async def review(job_id: str):
    profile = db.get_profile()
    _require_resume(profile)
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="That lead is no longer here.")
    posting_task = asyncio.create_task(_posting_within(job, 8))
    generated = False
    cv = job.get("tailored_cv") or ""
    note = job.get("cover_note") or ""
    if _needs_cv(job):
        cv, note = await _documents(profile, job)
        generated = True
    posting = None
    if posting_task.done():
        try:
            posting = posting_task.result()
        except Exception:
            posting = None
    else:
        posting_task.cancel()
        try:
            await posting_task
        except (asyncio.CancelledError, Exception):
            posting = None
    fields: dict = {}
    if posting:
        fields.update(posting)
        if generated:
            source = {**job, **posting, "description": posting.get("posting_text") or job.get("description") or ""}
            summary = extract_summary(cv) or template_summary(profile, source)
            cv = assemble_cv(profile, source, summary)
    if generated:
        fields["tailored_cv"] = cv
        fields["cover_note"] = note
    if fields:
        job = db.update_job(job_id, **fields) or job
    if generated and job is not None:
        job["tailored_cv"] = cv
        job["cover_note"] = note
    return job


@app.post("/api/jobs/{job_id}/cv")
async def rewrite(job_id: str):
    profile = db.get_profile()
    _require_resume(profile)
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="That lead is no longer here.")
    cv, note = await _documents(profile, job)
    updated = db.update_job(job_id, tailored_cv=cv, cover_note=note)
    return updated


@app.post("/api/jobs/{job_id}/decide")
def decide(job_id: str, body: DecideIn):
    if body.status not in {"applied", "skipped", "saved"}:
        raise HTTPException(status_code=400, detail="Choose apply, skip, or save.")
    updated = db.update_job(
        job_id,
        status=body.status,
        tailored_cv=body.tailored_cv,
        cover_note=body.cover_note,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="That lead is no longer here.")
    return {"job": updated, **_board()}
