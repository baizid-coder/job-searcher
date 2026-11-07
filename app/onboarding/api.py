"""HTTP routes for onboarding and My Profile."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.onboarding import service
from app.onboarding.batch import first_batch
from app.resume import extract_pdf_text

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
MAX_RESUME_PDF_BYTES = 8 * 1024 * 1024
PDF_CONTENT_TYPES = {"", "application/pdf", "application/x-pdf", "application/octet-stream"}

router = APIRouter()


class LanguageIn(BaseModel):
    name: str = ""
    level: str = ""


class AuthIn(BaseModel):
    country: str = ""
    notes: str = ""


class ConstraintsIn(BaseModel):
    locations: list[str] = Field(default_factory=list)
    willing_to_relocate: str = ""
    remote_preference: str = ""
    employment_types: list[str] = Field(default_factory=list)
    weekly_hours: float | int | None = None
    min_salary: float | int | None = None
    salary_currency: str = ""
    salary_flexible: bool = False
    languages: list[LanguageIn] = Field(default_factory=list)
    work_authorization: list[AuthIn] = Field(default_factory=list)
    exclude_industries: list[str] = Field(default_factory=list)
    exclude_companies: list[str] = Field(default_factory=list)
    earliest_start: str = ""


class ResumeIn(BaseModel):
    text: str = ""


class KeyIn(BaseModel):
    key: str


class ItemEditIn(BaseModel):
    key: str
    name: str = ""


class AnswerIn(BaseModel):
    question_key: str
    choice: str = ""
    skipped: bool = False


class AnswersIn(BaseModel):
    answers: list[AnswerIn] = Field(default_factory=list)


class SwipeIn(BaseModel):
    verdict: str = ""
    posting: dict = Field(default_factory=dict)


class BatchIn(BaseModel):
    jobs: list[dict] = Field(default_factory=list)
    target: int = 20


class GateIn(BaseModel):
    override: str = ""


class LockIn(BaseModel):
    key: str
    locked: bool = True


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.OnboardingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _run_async(fn, *args, **kwargs):
    try:
        return await fn(*args, **kwargs)
    except service.OnboardingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _page(name: str):
    path = STATIC / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="That page is missing.")
    return FileResponse(path)


def _asset(name: str, media_type: str):
    path = STATIC / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="That file is missing.")
    return FileResponse(path, media_type=media_type)


@router.get("/onboarding")
def onboarding_page():
    return _page("onboarding.html")


@router.get("/profile")
def profile_page():
    return _page("profile.html")


@router.get("/onboarding/discover-tokens.css")
@router.get("/profile/discover-tokens.css")
def tokens_css():
    return _asset("discover-tokens.css", "text/css")


@router.get("/onboarding/onboarding.css")
@router.get("/profile/onboarding.css")
def onboarding_css():
    return _asset("onboarding.css", "text/css")


@router.get("/onboarding/onboarding.js")
def onboarding_script():
    return _asset("onboarding.js", "application/javascript")


@router.get("/profile/profile.js")
def profile_script():
    return _asset("profile.js", "application/javascript")


@router.get("/api/onboarding/state")
def state():
    return _run(service.snapshot)


@router.put("/api/onboarding/constraints")
def save_constraints(body: ConstraintsIn):
    return _run(service.save_constraints, body.model_dump())


@router.post("/api/onboarding/resume")
def save_resume(body: ResumeIn):
    return _run(service.save_resume_text, body.text)


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


@router.post("/api/onboarding/resume/pdf")
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
    return _run(service.save_resume_text, text)


@router.post("/api/onboarding/infer")
async def infer():
    return await _run_async(service.infer)


@router.post("/api/onboarding/items/confirm")
def confirm_item(body: KeyIn):
    return _run(service.decide_item, body.key, "confirmed")


@router.post("/api/onboarding/items/reject")
def reject_item(body: KeyIn):
    return _run(service.decide_item, body.key, "rejected")


@router.post("/api/onboarding/items/edit")
def edit_item(body: ItemEditIn):
    return _run(service.edit_item, body.key, body.name)


@router.post("/api/onboarding/items/unlock")
def unlock_item(body: KeyIn):
    return _run(service.unlock_item, body.key)


@router.post("/api/onboarding/review/continue")
def continue_review():
    return _run(service.continue_review)


@router.post("/api/onboarding/reflection")
def reflection():
    return _run(service.prepare_reflection)


@router.post("/api/onboarding/reflection/answers")
def reflection_answers(body: AnswersIn):
    return _run(service.submit_reflection, [answer.model_dump() for answer in body.answers])


@router.post("/api/onboarding/complete")
def complete():
    return _run(service.finish)


@router.get("/api/onboarding/profile")
def profile():
    return _run(service.snapshot)


@router.get("/api/onboarding/profile/history")
def profile_history():
    body = _run(service.snapshot)
    return {"ok": True, "history": body["history"], "version": None if body["profile"] is None else body["profile"]["version"]}


@router.post("/api/onboarding/profile/fields/edit")
def edit_field(body: ItemEditIn):
    return _run(service.edit_profile_field, body.key, body.name)


@router.post("/api/onboarding/profile/fields/lock")
def lock_field(body: LockIn):
    return _run(service.lock_profile_field, body.key, body.locked)


@router.post("/api/onboarding/first-batch")
def batch(body: BatchIn):
    target = min(50, max(1, int(body.target)))
    try:
        return service.first_batch_for(body.jobs, target)
    except service.OnboardingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/api/onboarding/swipes")
def swipes(body: SwipeIn):
    return _run(service.record_swipe, body.verdict, body.posting)


@router.get("/api/onboarding/gate")
def gate():
    body = _run(service.snapshot)
    return body["gate"]


@router.put("/api/onboarding/gate")
def update_gate(body: GateIn):
    return _run(service.set_gate, body.override)


@router.post("/api/onboarding/actions/match")
def action_match():
    return _run(service.run_action, "match")


@router.post("/api/onboarding/actions/resume-tailor")
def action_tailor():
    return _run(service.run_action, "resume-tailor")


@router.post("/api/onboarding/actions/auto-prepare")
def action_prepare():
    return _run(service.run_action, "auto-prepare")


def select_first_batch(jobs: list[dict], target: int = 20) -> dict:
    """Public hand-off. Hard-filters, then keeps about 70% near and 30% different."""
    return first_batch(jobs, service.store.get_constraints()["filters"], _fields(), target=target)


def _fields() -> list[dict]:
    latest = service.store.latest_version()
    if latest is None:
        return []
    return list((latest.get("data") or {}).get("fields") or [])
