import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.masters import service, store


HTML_PAGE = """
<html>
  <head><title>MSc Computer Science | Example University</title></head>
  <body><p>Department of Computing</p><p>Apply for the master's program.</p></body>
</html>
"""


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._db = db.DB_PATH
        self._vault = store.VAULT_PATH
        db.DB_PATH = Path(self.tmp.name) / "jobfinder.db"
        store.VAULT_PATH = Path(self.tmp.name) / "vault"
        self.addCleanup(self._restore)
        store.init()

    def _restore(self):
        db.DB_PATH = self._db
        store.VAULT_PATH = self._vault

    def program(self, **extra):
        payload = {"university": "Northwind University", "degree": "MSc", "country": "Japan"}
        payload.update(extra)
        return service.create_program(payload, apply_template=False)


class ChecklistAndStatusTests(Isolated):
    def test_ready_for_review_requires_done_items_and_explicit_submit(self):
        program = self.program()
        item = service.add_checklist_item(program["id"], {
            "kind": "transcript",
            "label": "Transcript",
            "required": True,
        })
        with self.assertRaises(service.MastersError):
            service.set_status(program["id"], "ready_for_review")
        self.assertEqual(service.get_program(program["id"])["status"], "shortlisted")

        service.update_checklist_item(item["id"], {"status": "done"})
        ready = service.set_status(program["id"], "ready_for_review")
        self.assertEqual(ready["status"], "ready_for_review")

        with self.assertRaises(service.MastersError):
            service.set_status(program["id"], "submitted")
        with self.assertRaises(service.MastersError):
            service.update_program(program["id"], {"status": "submitted", "university": program["university"]})
        self.assertEqual(service.get_program(program["id"])["status"], "ready_for_review")

        submitted = service.mark_submitted(program["id"])
        self.assertEqual(submitted["status"], "submitted")

    def test_mark_submitted_is_refused_before_review(self):
        program = self.program()
        with self.assertRaises(service.MastersError):
            service.mark_submitted(program["id"])
        self.assertNotEqual(service.get_program(program["id"])["status"], "submitted")

    def test_only_an_approved_sop_variant_can_be_attached(self):
        program = self.program()
        item = service.add_checklist_item(program["id"], {"kind": "sop", "label": "Statement of purpose", "required": True})
        variant = asyncio.run(service.generate_variant(program["id"], "sop"))
        self.assertEqual(variant["status"], "draft")
        self.assertIn("TEMPLATE DRAFT", variant["body"])
        self.assertIn("Northwind University", variant["body"])

        with self.assertRaises(service.MastersError) as blocked:
            service.attach_variant(program["id"], variant["id"])
        self.assertIn("approved", str(blocked.exception).lower())

        service.update_checklist_item(item["id"], {"status": "done"})
        with self.assertRaises(service.MastersError):
            service.set_status(program["id"], "ready_for_review")

        approved = service.approve_writing(variant["id"])
        self.assertEqual(approved["status"], "approved")
        package = service.attach_variant(program["id"], approved["id"])
        self.assertEqual(package["sop_variant"]["id"], approved["id"])
        self.assertEqual(package["sop_variant"]["status"], "approved")

        ready = service.set_status(program["id"], "ready_for_review")
        self.assertEqual(ready["status"], "ready_for_review")
        self.assertEqual(service.package_view(program["id"])["sop_variant"]["id"], approved["id"])


class ExtractionAndStaleTests(Isolated):
    def test_extracted_fields_stay_unverified_until_the_user_verifies(self):
        program = asyncio.run(service.save_extracted("https://example.edu/msc", HTML_PAGE))
        self.assertFalse(program["verified"])
        self.assertIsNone(program["verified_at"])
        self.assertIn("Example University", program["university"])
        self.assertIn("MSc", program["degree"])
        self.assertIn("Department of Computing", program["department"])
        self.assertTrue(program["unverified"])

        verified = service.verify_program(program["id"])
        self.assertTrue(verified["verified"])
        self.assertIsNotNone(verified["verified_at"])
        self.assertFalse(verified["stale"])

    def test_stale_warning_when_verification_is_older_than_90_days(self):
        program = self.program()
        verified = service.verify_program(program["id"])
        moment = datetime.fromisoformat(verified["verified_at"])
        self.assertFalse(service.is_stale(verified, now=moment + timedelta(days=90)))
        self.assertTrue(service.is_stale(verified, now=moment + timedelta(days=90, seconds=1)))

        old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
        store.update_program_row(program["id"], {"verified": 1, "verified_at": old})
        stale = service.get_program(program["id"])
        self.assertTrue(stale["stale"])
        self.assertIn("90 days", stale["stale_message"])

        recent = (datetime.now(timezone.utc) - timedelta(days=89)).isoformat()
        store.update_program_row(program["id"], {"verified": 1, "verified_at": recent})
        self.assertFalse(service.get_program(program["id"])["stale"])


class EligibilityTests(Isolated):
    def test_pass_fail_unknown_and_blocked_badge(self):
        passed = service.evaluate_constraints(
            {"gpa_cutoff": 3.0, "age_limit": 35, "language_requirement": "IELTS 6.5"},
            {"gpa": 3.6, "age": 24, "language_levels": {"IELTS": 7.0}, "degree": "BSc", "graduation_date": "2024-06-01"},
        )
        self.assertEqual(passed["badge"], "eligible")
        self.assertFalse(passed["blocked"])
        self.assertEqual({rule["rule"]: rule["result"] for rule in passed["rules"]}["gpa"], "pass")

        failed = service.evaluate_constraints(
            {"gpa_cutoff": 3.8, "age_limit": 21, "language_requirement": "IELTS 7.5"},
            {"gpa": 3.2, "age": 24, "language_levels": {"IELTS": 6.5}, "degree": "BSc", "graduation_date": "2024-06-01"},
        )
        self.assertTrue(failed["blocked"])
        self.assertTrue(failed["badge"].startswith("blocked:"))
        self.assertIn("GPA", failed["badge"])
        self.assertIn("age", failed["badge"])
        self.assertIn("IELTS", failed["badge"])

        unknown = service.evaluate_constraints(
            {"gpa_cutoff": None, "age_limit": None, "language_requirement": ""},
            {"gpa": None, "age": None, "language_levels": {}, "degree": "", "graduation_date": ""},
        )
        self.assertEqual(unknown["badge"], "eligible")
        self.assertFalse(unknown["blocked"])
        compared = {rule["rule"]: rule["result"] for rule in unknown["rules"]}
        self.assertEqual(compared["gpa"], "unknown")
        self.assertEqual(compared["age"], "unknown")
        self.assertEqual(compared["language"], "unknown")

        gpa_only = service.evaluate_constraints(
            {"gpa_cutoff": 3.5, "age_limit": None, "language_requirement": "see the department"},
            {"gpa": 3.0, "age": None, "language_levels": {}, "degree": "BSc", "graduation_date": "2024-06-01"},
        )
        self.assertTrue(gpa_only["badge"].startswith("blocked:"))
        self.assertIn("GPA", gpa_only["badge"])
        self.assertNotIn("language", gpa_only["badge"].lower())
        results = {rule["rule"]: rule["result"] for rule in gpa_only["rules"]}
        self.assertEqual(results["language"], "unknown")
        self.assertEqual(results["age"], "unknown")

        program = self.program(gpa_cutoff=3.9, age_limit=30, language_requirement="IELTS 6.5")
        service.save_academic_profile({"gpa": 3.1, "age": 22, "language_levels": {"IELTS": 7}})
        listed = service.get_program(program["id"])
        self.assertTrue(listed["eligibility"]["badge"].startswith("blocked:"))
        self.assertIn("GPA", listed["eligibility"]["badge"])


class DeadlineAndScholarshipTests(Isolated):
    def test_deadlines_sort_by_nearest_date_and_create_in_app_reminders(self):
        program = self.program()
        service.add_deadline(program["id"], {"type": "documents", "date": "2026-12-01", "timezone": "UTC"})
        service.add_deadline(program["id"], {"type": "application", "date": "2026-05-02", "timezone": "Asia/Tokyo"})
        service.add_deadline(program["id"], {"type": "test_score", "date": "2026-08-15", "timezone": "UTC"})
        deadlines = service.list_deadlines()
        self.assertEqual([item["date"] for item in deadlines], ["2026-05-02", "2026-08-15", "2026-12-01"])

        application = next(item for item in deadlines if item["type"] == "application")
        self.assertEqual(sorted(item["offset_days"] for item in application["reminders"]), [1, 3, 7, 14, 30])
        self.assertTrue(all(item["channel"] == "in_app" for item in application["reminders"]))
        self.assertTrue(all(item["status"] != "sent" for item in application["reminders"]))
        reminder = service.draft_reminder_email(application["reminders"][0]["id"])
        self.assertEqual(reminder["status"], "draft")
        self.assertIsNone(reminder["sent_at"])

        later = self.program(university="Later University")
        service.add_deadline(later["id"], {"type": "application", "date": "2026-11-01", "timezone": "UTC"})
        ordered = [item["university"] for item in service.list_programs()]
        self.assertEqual(ordered[0], "Northwind University")

    def test_scholarship_deadline_earlier_than_application_is_flagged(self):
        program = self.program()
        service.add_deadline(program["id"], {"type": "application", "date": "2026-12-01", "timezone": "UTC"})
        early = service.create_scholarship({
            "name": "Early award",
            "deadline": "2026-10-01",
            "program_ids": [program["id"]],
            "requires_admission": True,
        })
        self.assertTrue(early["conflict"])
        self.assertTrue(early["conflicts"][0]["conflict"])
        self.assertIn("earlier than the application deadline", early["conflicts"][0]["reason"])

        late = service.create_scholarship({
            "name": "Later award",
            "deadline": "2027-01-15",
            "program_ids": [program["id"]],
        })
        self.assertFalse(late["conflict"])

        kinds = [item["type"] for item in service.list_deadlines()]
        self.assertEqual(kinds.count("scholarship"), 2)
        self.assertEqual(service.list_deadlines()[0]["date"], "2026-10-01")


class EmailApprovalTests(Isolated):
    def test_approving_an_email_does_not_send_it_or_mark_the_recommendation_submitted(self):
        program = self.program()
        person = service.create_recommender({
            "name": "Ada Lovelace",
            "title": "Professor",
            "institution": "Northwind",
            "email": "ada@example.edu",
        })
        assignment = service.assign_recommender(person["id"], {"program_id": program["id"], "due_date": "2026-11-01"})
        email = asyncio.run(service.draft_recommender_email(assignment["id"], "request"))
        self.assertEqual(email["status"], "draft")
        self.assertIsNone(email["sent_at"])
        self.assertIn("TEMPLATE DRAFT", email["body"])

        with self.assertRaises(service.MastersError) as denied:
            service.send_email(email["id"])
        self.assertIn("approval", str(denied.exception).lower())
        self.assertIsNone(service.get_email(email["id"])["sent_at"])
        self.assertEqual(service.get_email(email["id"])["status"], "draft")
        self.assertEqual(store.get_assignment(assignment["id"])["status"], "not_asked")

        approved = service.approve_email(email["id"], subject="Recommendation request", body=email["body"])
        self.assertEqual(approved["status"], "approved")
        self.assertIsNone(approved["sent_at"])
        self.assertFalse(approved["sent"])
        self.assertEqual(store.get_assignment(assignment["id"])["status"], "not_asked")

        with self.assertRaises(service.MastersError) as unsent:
            service.send_email(email["id"])
        self.assertIn("SMTP", str(unsent.exception))
        stored = service.get_email(email["id"])
        self.assertEqual(stored["status"], "approved")
        self.assertIsNone(stored["sent_at"])
        self.assertFalse(stored["sent"])
        self.assertNotEqual(stored["status"], "sent")

        submitted = service.mark_recommender_submitted(assignment["id"])
        self.assertEqual(submitted["status"], "submitted")


class HttpTests(Isolated):
    def test_pages_status_gate_and_vault_limits(self):
        with TestClient(app) as client:
            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn("Job finder", home.text)
            self.assertIn("/masters", home.text)
            page = client.get("/masters")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Master's applications", page.text)
            self.assertIn("band-overdue", page.text)
            self.assertIn("data-counter", page.text)
            state = client.get("/api/state")
            self.assertEqual(state.status_code, 200)

            created = client.post("/api/masters/programs", json={"university": "HTTP University", "degree": "MSc"})
            self.assertEqual(created.status_code, 200)
            program = created.json()
            self.assertFalse(program["verified"])
            self.assertIsNone(program["verified_at"])

            denied = client.post(f"/api/masters/programs/{program['id']}/status", json={"status": "submitted"})
            self.assertEqual(denied.status_code, 400)
            sneaky = client.put(
                f"/api/masters/programs/{program['id']}",
                json={"university": "HTTP University", "status": "submitted"},
            )
            self.assertEqual(sneaky.status_code, 400)
            review = client.post(
                f"/api/masters/programs/{program['id']}/status",
                json={"status": "ready_for_review"},
            )
            self.assertEqual(review.status_code, 400)
            marked = client.post(f"/api/masters/programs/{program['id']}/mark-submitted")
            self.assertEqual(marked.status_code, 400)
            current = client.get(f"/api/masters/programs/{program['id']}")
            self.assertNotEqual(current.json()["program"]["status"], "submitted")

            rejected = client.post(
                "/api/masters/documents",
                files={"file": ("notes.txt", b"not a pdf", "text/plain")},
                data={"doc_type": "other", "is_master": "false"},
            )
            self.assertEqual(rejected.status_code, 400)

            pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
            uploaded = client.post(
                "/api/masters/documents",
                files={"file": ("cv.pdf", pdf, "application/pdf")},
                data={"doc_type": "cv", "is_master": "true"},
            )
            self.assertEqual(uploaded.status_code, 200)
            saved = uploaded.json()
            self.assertTrue(saved["is_master"])
            self.assertEqual(saved["doc_type"], "cv")
            downloaded = client.get(f"/api/masters/documents/{saved['id']}/file")
            self.assertEqual(downloaded.status_code, 200)
            self.assertTrue(downloaded.content.startswith(b"%PDF"))

            oversized = b"%PDF" + b"0" * (8 * 1024 * 1024)
            too_big = client.post(
                "/api/masters/documents",
                files={"file": ("big.pdf", oversized, "application/pdf")},
                data={"doc_type": "transcript", "is_master": "false"},
            )
            self.assertEqual(too_big.status_code, 400)


if __name__ == "__main__":
    unittest.main()
