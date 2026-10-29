import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.llm import ModelError
from app.main import app
from app.onboarding import service, store
from app.onboarding.batch import first_batch
from app.onboarding.documents import extract_experiences
from app.onboarding.filters import passes_constraints
from app.onboarding.infer import ground_items, infer_from_source
from app.onboarding.profile import is_stable
from app.onboarding.reflect import build_questions
from app.onboarding.schema import schema_errors

SAMPLE = """Amina Rahman
amina@example.com

Experience
Data Analyst Intern | Northwind
2024
- Built dashboards in Excel
- Cleaned survey data
- Wrote weekly SQL reports
- Presented findings to the team

Projects
Flood Watch
- Mapped rainfall data
- Built a Python scraper
- Designed alert rules
- Tested SMS notices

Education
BSc Geography
- Remote sensing course
- GIS lab
- Field methods
- Statistics
"""

SETTINGS = {"base_url": "http://127.0.0.1:9", "model": "test", "api_key": "test-key"}
QUOTE = "Built APIs with Python as an Intern."


def valid_payload():
    return {
        "role_families": [{"name": "Python", "confidence": 0.9, "evidence": ["Python"]}],
        "skills": [
            {"name": "Python", "level": "fluent", "evidence": ["Python"]},
            {"name": "Cobol", "level": "fluent", "evidence": ["Cobol expert"]},
        ],
        "industries": [],
        "seniority_guess": {"name": "Intern", "confidence": 0.4, "evidence": ["Intern"]},
        "strengths": [],
        "gaps": [],
    }


class FakeComplete:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def __call__(self, settings, messages, timeout=None):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _python_field():
    return {"key": "skill:python", "kind": "skill", "name": "Python", "weight": 0.7, "source": "inferred"}


def _job(job_id, title, company, description, **extra):
    job = {
        "id": job_id,
        "title": title,
        "company": company,
        "description": description,
        "location": "Dhaka",
    }
    job.update(extra)
    return job


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "onboarding.db"
        self._client = TestClient(app)
        self.client = self._client.__enter__()

    def tearDown(self):
        self._client.__exit__(None, None, None)
        db.DB_PATH = self.previous
        self.tmp.cleanup()

    def test_migration_is_idempotent(self):
        db.init()
        db.init()
        connection = sqlite3.connect(db.DB_PATH)
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        connection.close()
        for table in (
            "constraints",
            "preference_profile",
            "profile_items",
            "reflection_questions",
            "reflection_answers",
        ):
            self.assertIn(table, names)

    def test_hard_filter_and_empty_constraint(self):
        banned = _job("1", "Python Developer", "Acme", "Build services with Python", salary="40000")
        kept = _job("2", "Python Developer", "Northwind", "Build services with Python", salary="120000")
        self.assertFalse(passes_constraints(banned, {"exclude_companies": ["Acme"]}))
        self.assertTrue(passes_constraints(kept, {"exclude_companies": ["Acme"]}))
        self.assertTrue(passes_constraints(banned, {}))

        low = _job("3", "Analyst", "Northwind", "Reports", salary="40000")
        self.assertFalse(passes_constraints(low, {"min_salary": 80000, "salary_currency": "BDT"}))
        self.assertTrue(passes_constraints(low, {"min_salary": 80000, "salary_currency": "BDT", "salary_flexible": True}))
        self.assertTrue(passes_constraints(low, {}))

        away = _job("4", "Analyst", "Northwind", "Reports", location="Chattogram")
        self.assertFalse(passes_constraints(away, {"locations": ["Dhaka"], "willing_to_relocate": "no"}))
        self.assertTrue(passes_constraints(away, {"locations": ["Dhaka"], "willing_to_relocate": "yes"}))
        self.assertTrue(passes_constraints(away, {}))

        onsite = _job("5", "Office Assistant", "Northwind", "Work on site in Dhaka", is_remote=False)
        self.assertFalse(passes_constraints(onsite, {"remote_preference": "remote"}))
        self.assertTrue(passes_constraints(onsite, {}))
        self.assertTrue(passes_constraints(onsite, {"remote_preference": "no_preference"}))

        long_week = _job("6", "Nurse", "Care", "Ward work", weekly_hours=40)
        self.assertFalse(passes_constraints(long_week, {"weekly_hours": 20}))
        self.assertTrue(passes_constraints(long_week, {}))

        language = _job(
            "7",
            "Guide",
            "Northwind",
            "Tours",
            required_languages=[{"name": "Japanese", "level": "fluent"}],
        )
        self.assertFalse(passes_constraints(language, {"languages": [{"name": "Japanese", "level": "basic"}]}))
        self.assertTrue(passes_constraints(language, {}))

        early = _job("8", "Analyst", "Northwind", "Reports", start_date="2026-01-01")
        self.assertFalse(passes_constraints(early, {"earliest_start": "2026-06-01"}))
        self.assertTrue(passes_constraints(early, {}))

        japan = _job("9", "Guide", "Northwind", "Tours", location="Tokyo, Japan")
        self.assertFalse(
            passes_constraints(
                japan,
                {"work_authorization": [{"country": "Japan", "notes": "Not authorized to work"}]},
            )
        )
        self.assertTrue(
            passes_constraints(japan, {"work_authorization": [{"country": "Japan", "notes": "Citizen"}]})
        )
        self.assertTrue(passes_constraints(japan, {}))

        hospital = _job("10", "Nurse", "Care", "Ward work", industry="Healthcare")
        self.assertFalse(passes_constraints(hospital, {"exclude_industries": ["Healthcare"]}))
        self.assertTrue(passes_constraints(hospital, {}))

        intern = _job("11", "Intern", "Northwind", "Summer internship")
        self.assertFalse(passes_constraints(intern, {"employment_types": ["full-time"]}))
        self.assertTrue(passes_constraints(intern, {"employment_types": ["internship"]}))
        self.assertTrue(passes_constraints(intern, {}))

    def test_items_without_evidence_are_dropped(self):
        parsed = valid_payload()
        parsed["skills"].append({"name": "Ghost", "level": "fluent", "evidence": []})
        self.assertTrue(schema_errors({"skills": []}))
        items = ground_items(parsed, QUOTE)
        names = {item["name"].casefold() for item in items}
        self.assertIn("python", names)
        self.assertNotIn("cobol", names)
        self.assertNotIn("ghost", names)
        for item in items:
            for quote in item["evidence"]:
                self.assertIn(quote.casefold(), QUOTE.casefold())

    def test_invalid_model_output_retries_once_then_falls_back(self):
        failed = FakeComplete(["not json", "still nope"])
        result = asyncio.run(infer_from_source(SAMPLE, SETTINGS, extract_experiences(SAMPLE), failed))
        self.assertEqual(failed.calls, 2)
        self.assertEqual(result["writer"], "document")
        self.assertIn("Document-backed", result["label"])
        self.assertTrue(result["items"])
        self.assertTrue(all(item["document_backed"] for item in result["items"]))
        for item in result["items"]:
            self.assertTrue(item["evidence"])
            for quote in item["evidence"]:
                self.assertIn(quote.casefold(), SAMPLE.casefold())

        recovered = FakeComplete([ModelError("down"), json.dumps(valid_payload())])
        result = asyncio.run(infer_from_source(QUOTE, SETTINGS, [], recovered))
        self.assertEqual(recovered.calls, 2)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["writer"], "model")
        names = {item["name"].casefold() for item in result["items"]}
        self.assertIn("python", names)
        self.assertNotIn("cobol", names)

        once = FakeComplete([json.dumps(valid_payload())])
        result = asyncio.run(infer_from_source(QUOTE, SETTINGS, [], once))
        self.assertEqual(once.calls, 1)
        self.assertEqual(result["writer"], "model")

        skipped = FakeComplete([json.dumps(valid_payload())])
        result = asyncio.run(infer_from_source(SAMPLE, {}, extract_experiences(SAMPLE), skipped))
        self.assertEqual(skipped.calls, 0)
        self.assertEqual(result["writer"], "document")
        self.assertTrue(all(item["document_backed"] for item in result["items"]))

    def test_confirmation_weights_and_resume_mid_flow(self):
        saved = self.client.put(
            "/api/onboarding/constraints",
            json={"locations": ["Dhaka"], "willing_to_relocate": "no"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["status"], "constraints_done")
        again = self.client.get("/api/onboarding/state")
        self.assertEqual(again.json()["status"], "constraints_done")
        self.assertEqual(again.json()["constraints"]["locations"], ["Dhaka"])

        db.save_profile({"resume_text": SAMPLE, "name": "Amina Rahman"})
        body = asyncio.run(service.infer())
        python = next(item for item in body["items"] if item["kind"] == "skill" and item["name"].casefold() == "python")
        excel = next(item for item in body["items"] if item["kind"] == "skill" and item["name"].casefold() == "excel")
        sql = next(item for item in body["items"] if item["kind"] == "skill" and item["name"].casefold() == "sql")
        self.assertEqual(python["status"], "proposed")
        service.decide_item(python["key"], "confirmed")
        service.decide_item(excel["key"], "rejected")
        confirmed = store.get_item(python["key"])
        rejected = store.get_item(excel["key"])
        self.assertTrue(confirmed["locked"])
        self.assertAlmostEqual(confirmed["weight"], 0.7)
        self.assertLess(rejected["weight"], 0)

        reviewed = service.continue_review()
        fields = {field["name"].casefold(): field for field in reviewed["profile"]["fields"]}
        self.assertAlmostEqual(fields["python"]["weight"], 0.7)
        self.assertTrue(fields["python"]["locked"])
        self.assertEqual(fields["python"]["source"], "inferred")
        self.assertLess(fields["excel"]["weight"], 0)
        self.assertNotIn(sql["name"].casefold(), fields)
        self.assertTrue(all(field["status"] != "proposed" for field in reviewed["profile"]["fields"]))
        self.assertEqual(reviewed["status"], "profile_confirmed")

        service.prepare_reflection()
        questions = store.list_questions()
        self.assertEqual(len(questions), 6)
        prompts = [question["prompt"] for question in questions]
        self.assertEqual(sum("which part would you want more of" in prompt for prompt in prompts), 3)
        self.assertEqual(sum("which part would you avoid" in prompt for prompt in prompts), 3)
        self.assertFalse(any("tell me about yourself" in prompt.casefold() for prompt in prompts))
        for question in questions:
            self.assertIn("none / not sure", question["chips"])
            self.assertTrue(question["prompt"].startswith("In "))
            for chip in question["chips"]:
                if chip == "none / not sure":
                    continue
                self.assertIn(chip, SAMPLE)

        more = next(question for question in questions if "Flood Watch" in question["prompt"] and question["polarity"] == "more")
        avoid = next(question for question in questions if "Flood Watch" in question["prompt"] and question["polarity"] == "avoid")
        chip = more["chips"][0]
        service.submit_reflection(
            [
                {"question_key": more["question_key"], "choice": chip, "skipped": False},
                {"question_key": avoid["question_key"], "choice": "none / not sure", "skipped": False},
            ]
        )
        reflected = [item for item in service.snapshot()["items"] if item["source"] == "reflection"]
        self.assertEqual(len(reflected), 1)
        self.assertEqual(reflected[0]["status"], "proposed")
        self.assertTrue(any(answer["skipped"] for answer in service.snapshot()["answers"]))
        names = {field["name"].casefold() for field in service.snapshot()["profile"]["fields"]}
        self.assertNotIn(chip.casefold(), names)
        service.decide_item(reflected[0]["key"], "confirmed")
        names = {field["name"].casefold() for field in service.snapshot()["profile"]["fields"]}
        self.assertNotIn(chip.casefold(), names)
        finished = service.finish()
        match = next(field for field in finished["profile"]["fields"] if field["name"].casefold() == chip.casefold())
        self.assertAlmostEqual(match["weight"], 0.7)
        self.assertTrue(match["locked"])
        self.assertEqual(match["source"], "reflection")
        self.assertEqual(finished["status"], "complete")
        self.assertEqual(self.client.get("/api/onboarding/state").json()["status"], "complete")

    def test_first_batch_is_about_70_30_and_hard_filtered(self):
        near = [
            _job(f"n{index}", "Python Developer", f"Good{index}", "Build services with Python")
            for index in range(10)
        ]
        far = [
            _job(f"f{index}", "Nurse", f"Care{index}", "Patient care and ward rounds")
            for index in range(10)
        ]
        blocked = [
            _job(f"b{index}", "Python Developer", "Acme", "Build services with Python")
            for index in range(2)
        ]
        result = first_batch(near + far + blocked, {"exclude_companies": ["Acme"]}, [_python_field()], target=10)
        self.assertEqual(len(result["jobs"]), 10)
        self.assertEqual(result["near_count"], 7)
        self.assertEqual(result["explore_count"], 3)
        self.assertTrue(all(job["company"] != "Acme" for job in result["jobs"]))
        self.assertEqual(result["excluded_count"], 2)

        open_filter = first_batch(blocked, {}, [_python_field()], target=10)
        self.assertEqual(len(open_filter["jobs"]), 2)
        self.assertTrue(all(job["company"] == "Acme" for job in open_filter["jobs"]))

    def test_discover_first_batch_uses_onboarding_profile(self):
        from app.discover.batch import select_postings
        from app.discover.classify import extract
        from app.onboarding.batch import is_near, select_for_discover

        near = [
            _job(f"n{index}", "Python Developer", f"Good{index}", "Build services with Python")
            for index in range(10)
        ]
        far = [
            _job(f"f{index}", "Nurse", f"Care{index}", "Patient care and ward rounds")
            for index in range(10)
        ]
        blocked = [
            _job(f"b{index}", "Python Developer", "Acme", "Build services with Python")
            for index in range(2)
        ]
        postings = []
        for job in near + far + blocked:
            features = extract(job)
            self.assertIsNotNone(features)
            postings.append(features)
        self.assertIsNone(select_for_discover(postings, 10))

        service.save_constraints({"exclude_companies": ["Acme"]})
        store.insert_version(
            {"fields": [_python_field()], "swipes": [], "onboarding_status": "complete"},
            major_change=True,
            reason="test",
        )
        selected, _thin = select_postings(postings, {}, set(), target=10, has_verdicts=False)
        self.assertEqual(len(selected), 10)
        self.assertTrue(all(item["company"] != "Acme" for item in selected))
        near_count = sum(1 for item in selected if is_near(item, [_python_field()]))
        self.assertEqual(near_count, 7)
        self.assertEqual(len(selected) - near_count, 3)

    def test_swipe_bumps_version_and_gate_blocks_until_override_or_stable(self):
        service.save_constraints({})
        db.save_profile({"resume_text": SAMPLE, "name": "Amina Rahman"})
        body = asyncio.run(service.infer())
        python = next(item for item in body["items"] if item["kind"] == "skill" and item["name"].casefold() == "python")
        service.decide_item(python["key"], "confirmed")
        reviewed = service.continue_review()
        self.assertEqual(reviewed["profile"]["version"], 1)
        self.assertFalse(reviewed["stable"])

        blocked = self.client.post("/api/onboarding/actions/match")
        self.assertEqual(blocked.status_code, 403)
        self.assertIn("stable", blocked.json()["detail"].casefold())

        allowed = self.client.put("/api/onboarding/gate", json={"override": "allow"})
        self.assertEqual(allowed.status_code, 200)
        opened = self.client.post("/api/onboarding/actions/resume-tailor")
        self.assertEqual(opened.status_code, 200)
        self.assertFalse(opened.json()["performed"])
        prepared = self.client.post("/api/onboarding/actions/auto-prepare")
        self.assertEqual(prepared.status_code, 200)
        self.assertFalse(prepared.json()["performed"])

        self.client.put("/api/onboarding/gate", json={"override": ""})
        self.assertEqual(self.client.post("/api/onboarding/actions/match").status_code, 403)

        before = store.latest_version()["version"]
        swipe = self.client.post(
            "/api/onboarding/swipes",
            json={"verdict": "yes", "posting": {"id": "hobby-0", "title": "Side path", "skills": ["Hobby 0"]}},
        )
        self.assertEqual(swipe.status_code, 200, swipe.text)
        self.assertEqual(swipe.json()["version"], before + 1)
        self.assertFalse(swipe.json()["major_change"])

        for index in range(1, 4):
            if self.client.get("/api/onboarding/gate").json()["stable"]:
                break
            extra = self.client.post(
                "/api/onboarding/swipes",
                json={"verdict": "yes", "posting": {"id": f"hobby-{index}", "title": "Side path", "skills": [f"Hobby {index}"]}},
            )
            self.assertEqual(extra.status_code, 200, extra.text)
        gate = self.client.get("/api/onboarding/gate").json()
        self.assertTrue(gate["stable"])
        self.assertTrue(is_stable(store.list_versions()))
        opened = self.client.post("/api/onboarding/actions/match")
        self.assertEqual(opened.status_code, 200)
        self.assertFalse(opened.json()["performed"])

    def test_questions_are_not_generic_when_resume_has_no_experiences(self):
        self.assertEqual(build_questions([]), [])

    def test_pages_and_job_finder_still_load(self):
        onboarding = self.client.get("/onboarding")
        profile = self.client.get("/profile")
        tokens = self.client.get("/onboarding/discover-tokens.css")
        home = self.client.get("/")
        self.assertEqual(onboarding.status_code, 200)
        self.assertIn("Loading onboarding", onboarding.text)
        self.assertIn("Here's what I see", onboarding.text)
        self.assertEqual(profile.status_code, 200)
        self.assertIn("Loading profile", profile.text)
        self.assertEqual(tokens.status_code, 200)
        self.assertIn("--discover-page-max: 1100px", tokens.text)
        self.assertIn("768px", tokens.text)
        self.assertEqual(home.status_code, 200)
        self.assertIn("Job finder", home.text)
