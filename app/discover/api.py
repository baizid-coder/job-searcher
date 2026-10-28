"""HTTP routes for Discover. Pages and JSON live under /discover and /api/discover."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.discover.flags import gate
from app.discover.service import DiscoverError
from app.discover import service
from app.sources import collect

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "discover.html"
TOKENS = ROOT / "static" / "discover-tokens.css"
SCRIPT = ROOT / "static" / "discover.js"

# One pass through the existing collectors, with a keyword for each role family.
FAMILY_KEYWORDS = [
    "data analyst",
    "machine learning",
    "business analyst",
    "operations",
    "product manager",
    "it officer",
]

router = APIRouter()


class VerdictIn(BaseModel):
    posting_id: str
    verdict: str
    reason_chips: list[str] = Field(default_factory=list)


class LineIn(BaseModel):
    key: str


class EditIn(BaseModel):
    key: str
    text: str = ""


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except DiscoverError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _run_async(fn, *args, **kwargs):
    try:
        return await fn(*args, **kwargs)
    except DiscoverError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def fetch_postings() -> tuple[list[dict], list[str]]:
    return await collect(FAMILY_KEYWORDS)


@router.get("/discover")
def discover_page():
    return FileResponse(PAGE)


@router.get("/discover/discover-tokens.css")
def discover_tokens():
    return FileResponse(TOKENS, media_type="text/css")


@router.get("/discover/discover.js")
def discover_script():
    return FileResponse(SCRIPT, media_type="application/javascript")


@router.get("/api/discover/feed")
async def feed():
    try:
        jobs, warnings = await fetch_postings()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="The job boards did not respond.") from exc
    batch = service.next_batch(jobs)
    batch["warnings"] = warnings
    return batch


@router.post("/api/discover/verdicts")
async def verdicts(body: VerdictIn):
    return await _run_async(service.record_verdict, body.posting_id, body.verdict, body.reason_chips)


@router.get("/api/discover/profile")
def profile():
    return _run(service.profile)


@router.post("/api/discover/lines/confirm")
def confirm_line(body: LineIn):
    return _run(service.confirm_line, body.key)


@router.post("/api/discover/lines/reject")
def reject_line(body: LineIn):
    return _run(service.reject_line, body.key)


@router.post("/api/discover/lines/edit")
def edit_line(body: EditIn):
    return _run(service.edit_line, body.key, body.text)


@router.post("/api/discover/lines/unlock")
def unlock_line(body: LineIn):
    return _run(service.unlock_line, body.key)


@router.get("/api/discover/actions")
def actions():
    return gate(service.preferences_stable())


def _blocked_entry(name: str):
    status = gate(service.preferences_stable())
    if not status["entry_allowed"]:
        if status["reason"] == "not_stable":
            detail = f"{name} stays off until preferences are stable."
        else:
            detail = f"{name} stays off until the Discover action flag is turned on."
        raise HTTPException(status_code=403, detail=detail)
    return {
        **status,
        "performed": False,
        "detail": "Discover does not edit resumes or apply to jobs.",
    }


@router.post("/api/discover/resume-edit")
def resume_edit_entry():
    """Entry point only. This does not change a resume."""
    return _blocked_entry("Resume editing")


@router.post("/api/discover/auto-apply")
def auto_apply_entry():
    """Entry point only. This does not submit an application."""
    return _blocked_entry("Auto-apply")
