import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.db as db
from app.llm import requirements_turn
from app.main import _chat_patch, app
from app.match import query_keywords, rank_jobs


def _job(job_id, title, description="", source="Remotive"):
    return {
        "id": job_id,
        "source": source,
        "title": title,
        "company": "Acme",
        "location": "Dhaka, Bangladesh",
        "salary": "",
        "description": description,
        "board_url": f"https://example.com/{job_id}",
        "company_url": "",
        "posted_at": "2026-10-01",
        "is_remote": True,
        "location_note": "",
    }


class RankingTests(unittest.TestCase):
    def test_named_role_drops_jobs_that_only_share_a_skill(self):
        profile = {
            "seeking": ["Data Analyst"],
            "must_have": ["Excel"],
            "titles": ["Support Officer"],
            "nice_have": [],
            "exclude": [],
            "city": "Dhaka",
            "work_mode": "both",
        }
        ranked = rank_jobs(
            [
                _job("analyst", "Senior Data Analyst", "Build Excel reports for the finance team."),
                _job("plain", "Data Analyst", "Prepare weekly reports."),
                _job("business", "Business Analyst", "Strong Excel skills for stakeholder reports."),
                _job("python", "Python Developer", "Excel automation and data scripts."),
                _job("risk", "Risk Analyst", "Review data quality with the analyst team using Excel."),
                _job("coordinator", "Project Coordinator", "Partner with the data analyst on Excel reports."),
            ],
            profile,
        )
        titles = [job["title"] for job in ranked]
        self.assertEqual(titles, ["Senior Data Analyst", "Data Analyst"])
        self.assertTrue(ranked[0]["reasons"][0].startswith("You asked for"))
        self.assertIn("Excel", ranked[0]["reasons"][1])

    def test_without_a_named_role_a_skill_match_still_shows(self):
        profile = {
            "seeking": [],
            "must_have": ["Excel"],
            "titles": ["Support Officer"],
            "nice_have": [],
            "exclude": [],
            "city": "Dhaka",
            "work_mode": "both",
        }
        ranked = rank_jobs([_job("business", "Business Analyst", "Strong Excel skills.")], profile)
        self.assertEqual([job["title"] for job in ranked], ["Business Analyst"])

    def test_query_uses_the_role_and_not_the_resume_skills(self):
        keywords = query_keywords(
            {
                "seeking": ["Junior Python", "Data Analyst"],
                "must_have": ["Excel", "Django", "Git"],
                "titles": ["Support Officer"],
            }
        )
        self.assertEqual(keywords[0], "Junior Python")
        self.assertIn("python", [item.lower() for item in keywords])
        self.assertIn("Data Analyst", keywords)
        self.assertNotIn("Excel", keywords)
        self.assertNotIn("excel", [item.lower() for item in keywords])

    def test_resume_keywords_stay_when_no_role_was_named(self):
        keywords = query_keywords({"seeking": [], "must_have": ["Python"], "titles": []})
        self.assertEqual([item.lower() for item in keywords], ["python"])


class ChatPatchTests(unittest.TestCase):
    def test_role_written_into_titles_becomes_seeking(self):
        cleaned = _chat_patch(
            {"titles": ["Junior Python"], "must_have": ["Django"]},
            {"titles": ["Support Officer"]},
        )
        self.assertEqual(cleaned["seeking"], ["Junior Python"])
        self.assertNotIn("titles", cleaned)
        self.assertEqual(cleaned["must_have"], ["Django"])

    def test_same_role_in_titles_does_not_erase_history(self):
        cleaned = _chat_patch(
            {"seeking": ["Data Analyst"], "titles": ["Data Analyst"]},
            {"titles": ["Support Officer"]},
        )
        self.assertEqual(cleaned["seeking"], ["Data Analyst"])
        self.assertNotIn("titles", cleaned)


class ChatFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "jobfinder.db"
        self.client = TestClient(app)
        self.client.__enter__()
        self.client.put("/api/settings", json={"base_url": "http://127.0.0.1:9/v1", "model": "test-model"})
        self.client.put(
            "/api/profile",
            json={
                "name": "Amina Rahman",
                "resume_text": "Amina Rahman\nSkills: Excel",
                "titles": ["Support Officer"],
                "must_have": ["Excel"],
                "seeking": [],
            },
        )

    async def asyncTearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    async def test_model_reply_is_shown_and_seeking_is_what_search_uses(self):
        async def fake_complete(_settings, _messages, timeout=None):
            return json.dumps(
                {
                    "reply": "I'll look for junior Python roles in Dhaka.",
                    "profile": {
                        "seeking": ["Junior Python"],
                        "must_have": ["Django", "Excel"],
                        "city": "Dhaka",
                        "work_mode": "both",
                    },
                }
            )

        captured = {}

        async def fake_collect(keywords):
            captured["keywords"] = keywords
            return [
                _job("python", "Junior Python Developer", "Django and Excel in Dhaka."),
                _job("excel", "Accounts Officer", "Advanced Excel for daily reports."),
            ], []

        with patch("app.llm.complete", fake_complete):
            chat = self.client.post("/api/chat", json={"message": "I want junior Python work in Dhaka. I know Django and Excel."})
        self.assertEqual(chat.status_code, 200, chat.text)
        body = chat.json()
        self.assertEqual(body["reply"], "I'll look for junior Python roles in Dhaka.")
        self.assertEqual(body["messages"][-1]["content"], body["reply"])
        self.assertEqual(body["messages"][-2]["content"], "I want junior Python work in Dhaka. I know Django and Excel.")
        self.assertEqual(body["profile"]["seeking"], ["Junior Python"])
        self.assertEqual(body["profile"]["titles"], ["Support Officer"])
        self.assertIn("Django", body["profile"]["must_have"])

        with patch("app.main.collect", fake_collect):
            found = self.client.post("/api/search", json=body["profile"])
        self.assertEqual(found.status_code, 200, found.text)
        payload = found.json()
        self.assertEqual(captured["keywords"][0], "Junior Python")
        self.assertEqual([job["title"] for job in payload["leads"]], ["Junior Python Developer"])
        self.assertIn("Junior Python", payload["leads"][0]["reasons"][0])
        self.assertIn("matched", payload)
        self.assertEqual(payload["matched"], 1)

    async def test_reply_that_only_fills_titles_still_steers_search(self):
        async def fake_turn(_settings, _history, _profile, _message):
            return "I'll look for data analyst roles.", {"titles": ["Data Analyst"], "must_have": ["Excel"]}

        with patch("app.main.requirements_turn", fake_turn):
            chat = self.client.post("/api/chat", json={"message": "I want data analyst jobs."})
        self.assertEqual(chat.status_code, 200, chat.text)
        profile = chat.json()["profile"]
        self.assertEqual(profile["seeking"], ["Data Analyst"])
        self.assertEqual(profile["titles"], ["Support Officer"])

    async def test_requirements_turn_reads_seeking_from_the_model(self):
        async def fake_complete(_settings, messages, timeout=None):
            self.assertIn("seeking", messages[0]["content"])
            return json.dumps(
                {
                    "reply": "I'll look for data analyst roles.",
                    "seeking": ["Data Analyst"],
                    "city": "Dhaka",
                }
            )

        with patch("app.llm.complete", fake_complete):
            reply, understood = await requirements_turn(
                {"base_url": "http://127.0.0.1:9/v1", "model": "test-model"},
                [],
                {"seeking": [], "titles": ["Support Officer"], "city": "Dhaka"},
                "I want data analyst jobs.",
            )
        self.assertEqual(reply, "I'll look for data analyst roles.")
        self.assertEqual(understood["seeking"], ["Data Analyst"])


if __name__ == "__main__":
    unittest.main()
