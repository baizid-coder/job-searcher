"""HTTP routes for the master's tracker. Routes call the service rules; they do not skip them."""

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from app.masters import service
from app.masters.service import MastersError

PAGE = Path(__file__).resolve().parents[1] / "static" / "masters.html"
router = APIRouter()


class Payload(BaseModel):
    model_config = ConfigDict(extra="allow")


class StatusIn(BaseModel):
    status: str


class IdsIn(BaseModel):
    program_ids: list[int]


def _data(body: Payload) -> dict:
    return body.model_dump()


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except MastersError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


async def _run_async(fn, *args, **kwargs):
    try:
        return await fn(*args, **kwargs)
    except MastersError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


async def _read_limited(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = await file.read(256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > service.MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=400, detail="That file is larger than 8 MB.")
            chunks.append(chunk)
    finally:
        await file.close()
    return b"".join(chunks)


def _flag(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@router.get("/masters")
def masters_page():
    return FileResponse(PAGE)


@router.get("/api/masters/dashboard")
def dashboard():
    return _run(service.dashboard)


@router.get("/api/masters/profile")
def get_profile():
    return _run(service.get_academic_profile)


@router.put("/api/masters/profile")
def save_profile(body: Payload):
    return _run(service.save_academic_profile, _data(body))


@router.get("/api/masters/programs")
def list_programs(
    country: str = "",
    intake: str = "",
    status: str = "",
    deadline_from: str = "",
    deadline_to: str = "",
):
    return _run(
        service.list_programs,
        {
            "country": country,
            "intake": intake,
            "status": status,
            "deadline_from": deadline_from,
            "deadline_to": deadline_to,
        },
    )


@router.post("/api/masters/programs")
def create_program(body: Payload):
    return _run(service.create_program, _data(body))


@router.post("/api/masters/programs/import")
async def import_program(body: Payload):
    return await _run_async(service.import_program_url, str(_data(body).get("url") or ""))


@router.get("/api/masters/programs/{program_id}")
def program_detail(program_id: int):
    return _run(service.program_detail, program_id)


@router.put("/api/masters/programs/{program_id}")
def update_program(program_id: int, body: Payload):
    return _run(service.update_program, program_id, _data(body))


@router.delete("/api/masters/programs/{program_id}")
def delete_program(program_id: int):
    _run(service.delete_program, program_id)
    return {"ok": True}


@router.post("/api/masters/programs/{program_id}/status")
def set_status(program_id: int, body: StatusIn):
    return _run(service.set_status, program_id, body.status)


@router.post("/api/masters/programs/{program_id}/mark-submitted")
def mark_submitted(program_id: int):
    return _run(service.mark_submitted, program_id)


@router.post("/api/masters/programs/{program_id}/verify")
def verify_program(program_id: int):
    return _run(service.verify_program, program_id)


@router.get("/api/masters/deadlines")
def list_deadlines():
    return _run(service.list_deadlines)


@router.post("/api/masters/programs/{program_id}/deadlines")
def add_deadline(program_id: int, body: Payload):
    return _run(service.add_deadline, program_id, _data(body))


@router.put("/api/masters/deadlines/{deadline_id}")
def update_deadline(deadline_id: int, body: Payload):
    return _run(service.update_deadline, deadline_id, _data(body))


@router.delete("/api/masters/deadlines/{deadline_id}")
def delete_deadline(deadline_id: int):
    _run(service.delete_deadline, deadline_id)
    return {"ok": True}


@router.get("/api/masters/reminders")
def list_reminders(deadline_id: int | None = None):
    return _run(service.list_reminders, deadline_id)


@router.post("/api/masters/reminders/{reminder_id}/email-draft")
def draft_reminder(reminder_id: int):
    return _run(service.draft_reminder_email, reminder_id)


@router.get("/api/masters/templates")
def list_templates():
    return _run(service.list_templates)


@router.post("/api/masters/templates")
def create_template(body: Payload):
    return _run(service.create_template, _data(body))


@router.post("/api/masters/programs/{program_id}/checklist/apply")
def apply_template(program_id: int, body: Payload):
    return _run(service.apply_template, program_id, int(_data(body)["template_id"]))


@router.post("/api/masters/programs/{program_id}/checklist")
def add_checklist_item(program_id: int, body: Payload):
    return _run(service.add_checklist_item, program_id, _data(body))


@router.put("/api/masters/checklist/{item_id}")
def update_checklist_item(item_id: int, body: Payload):
    return _run(service.update_checklist_item, item_id, _data(body))


@router.delete("/api/masters/checklist/{item_id}")
def delete_checklist_item(item_id: int):
    _run(service.delete_checklist_item, item_id)
    return {"ok": True}


@router.get("/api/masters/documents")
def list_documents():
    return _run(service.list_documents)


@router.post("/api/masters/documents")
async def upload_document(
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    is_master: str = Form("false"),
):
    data = await _read_limited(file)
    return _run(service.save_document, file.filename or "document", data, file.content_type or "", doc_type, _flag(is_master))


@router.get("/api/masters/documents/{document_id}")
def document_detail(document_id: int):
    return _run(service.document_view, document_id)


@router.get("/api/masters/documents/{document_id}/file")
def document_file(document_id: int):
    document, path = _run(service.document_file, document_id)
    return FileResponse(path, media_type=document["content_type"], filename=document["original_name"])


@router.delete("/api/masters/documents/{document_id}")
def delete_document(document_id: int):
    _run(service.delete_document, document_id)
    return {"ok": True}


@router.get("/api/masters/writing")
def writing(program_id: int | None = None):
    return _run(service.writing_bundle, program_id)


@router.put("/api/masters/writing/master/{kind}")
def save_master(kind: str, body: Payload):
    data = _data(body)
    return _run(service.save_master, kind, data.get("body") or "", data.get("title"))


@router.post("/api/masters/programs/{program_id}/writing/{kind}")
async def generate_variant(program_id: int, kind: str):
    return await _run_async(service.generate_variant, program_id, kind)


@router.put("/api/masters/writing/{writing_id}")
def update_writing(writing_id: int, body: Payload):
    return _run(service.update_writing, writing_id, _data(body))


@router.post("/api/masters/writing/{writing_id}/approve")
def approve_writing(writing_id: int, body: Payload | None = None):
    data = _data(body) if body else {}
    return _run(service.approve_writing, writing_id, data.get("body"), data.get("title"))


@router.post("/api/masters/writing/{writing_id}/reject")
def reject_writing(writing_id: int):
    return _run(service.reject_writing, writing_id)


@router.get("/api/masters/recommenders")
def list_recommenders():
    return _run(service.list_recommenders)


@router.post("/api/masters/recommenders")
def create_recommender(body: Payload):
    return _run(service.create_recommender, _data(body))


@router.put("/api/masters/recommenders/{recommender_id}")
def update_recommender(recommender_id: int, body: Payload):
    return _run(service.update_recommender, recommender_id, _data(body))


@router.delete("/api/masters/recommenders/{recommender_id}")
def delete_recommender(recommender_id: int):
    _run(service.delete_recommender, recommender_id)
    return {"ok": True}


@router.post("/api/masters/recommenders/{recommender_id}/assignments")
def assign_recommender(recommender_id: int, body: Payload):
    return _run(service.assign_recommender, recommender_id, _data(body))


@router.put("/api/masters/recommender-assignments/{assignment_id}")
def update_assignment(assignment_id: int, body: Payload):
    return _run(service.update_assignment, assignment_id, _data(body))


@router.post("/api/masters/recommender-assignments/{assignment_id}/mark-submitted")
def mark_recommender_submitted(assignment_id: int):
    return _run(service.mark_recommender_submitted, assignment_id)


@router.post("/api/masters/recommender-assignments/{assignment_id}/draft/{kind}")
async def draft_recommender(assignment_id: int, kind: str):
    return await _run_async(service.draft_recommender_email, assignment_id, kind)


@router.get("/api/masters/professors")
def list_professors():
    return _run(service.list_professors)


@router.post("/api/masters/professors")
def create_professor(body: Payload):
    return _run(service.create_professor, _data(body))


@router.put("/api/masters/professors/{professor_id}")
def update_professor(professor_id: int, body: Payload):
    return _run(service.update_professor, professor_id, _data(body))


@router.delete("/api/masters/professors/{professor_id}")
def delete_professor(professor_id: int):
    _run(service.delete_professor, professor_id)
    return {"ok": True}


@router.post("/api/masters/professors/{professor_id}/draft/{kind}")
async def draft_professor(professor_id: int, kind: str):
    return await _run_async(service.draft_professor_email, professor_id, kind)


@router.get("/api/masters/scholarships")
def list_scholarships():
    return _run(service.list_scholarships)


@router.post("/api/masters/scholarships")
def create_scholarship(body: Payload):
    return _run(service.create_scholarship, _data(body))


@router.put("/api/masters/scholarships/{scholarship_id}")
def update_scholarship(scholarship_id: int, body: Payload):
    return _run(service.update_scholarship, scholarship_id, _data(body))


@router.delete("/api/masters/scholarships/{scholarship_id}")
def delete_scholarship(scholarship_id: int):
    _run(service.delete_scholarship, scholarship_id)
    return {"ok": True}


@router.post("/api/masters/scholarships/{scholarship_id}/programs/{program_id}")
def link_scholarship(scholarship_id: int, program_id: int):
    return _run(service.link_scholarship, scholarship_id, program_id)


@router.delete("/api/masters/scholarships/{scholarship_id}/programs/{program_id}")
def unlink_scholarship(scholarship_id: int, program_id: int):
    return _run(service.unlink_scholarship, scholarship_id, program_id)


@router.get("/api/masters/tests")
def list_tests():
    return _run(service.list_tests)


@router.post("/api/masters/tests")
def create_test(body: Payload):
    return _run(service.create_test, _data(body))


@router.put("/api/masters/tests/{test_id}")
def update_test(test_id: int, body: Payload):
    return _run(service.update_test, test_id, _data(body))


@router.delete("/api/masters/tests/{test_id}")
def delete_test(test_id: int):
    _run(service.delete_test, test_id)
    return {"ok": True}


@router.get("/api/masters/programs/{program_id}/costs")
def get_cost(program_id: int):
    return _run(service.cost_sheet, program_id)


@router.put("/api/masters/programs/{program_id}/costs")
def save_cost(program_id: int, body: Payload):
    return _run(service.save_cost, program_id, _data(body))


@router.post("/api/masters/costs/compare")
def compare_costs(body: IdsIn):
    return _run(service.compare_costs, body.program_ids)


@router.get("/api/masters/programs/{program_id}/package")
def package_view(program_id: int):
    return _run(service.package_view, program_id)


@router.post("/api/masters/programs/{program_id}/package/attach")
def attach_variant(program_id: int, body: Payload):
    return _run(service.attach_variant, program_id, int(_data(body)["writing_id"]))


@router.post("/api/masters/programs/{program_id}/package/approve")
def approve_package(program_id: int):
    return _run(service.approve_package, program_id)


@router.post("/api/masters/programs/{program_id}/package/reject")
def reject_package(program_id: int):
    return _run(service.reject_package, program_id)


@router.get("/api/masters/approvals")
def approvals():
    return _run(service.approval_queue)


@router.get("/api/masters/emails")
def list_emails():
    return _run(service.list_emails)


@router.post("/api/masters/emails/{email_id}/approve")
def approve_email(email_id: int, body: Payload | None = None):
    data = _data(body) if body else {}
    return _run(service.approve_email, email_id, data.get("subject"), data.get("body"))


@router.post("/api/masters/emails/{email_id}/reject")
def reject_email(email_id: int):
    return _run(service.reject_email, email_id)


@router.post("/api/masters/emails/{email_id}/send")
def send_email(email_id: int):
    return _run(service.send_email, email_id)


@router.get("/api/masters/audit")
def audit():
    return _run(service.list_audit)
