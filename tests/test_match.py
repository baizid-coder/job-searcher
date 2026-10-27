import unittest

from app.match import rank_jobs, search_block_reason


def _job(title, company="Northwind", description="", source="Remote OK", remote=True, posted="2026-01-01"):
    return {
        "title": title,
        "company": company,
        "description": description,
        "location": "Worldwide",
        "source": source,
        "is_remote": remote,
        "posted_at": posted,
        "location_note": "",
        "salary": "",
    }


PROFILE = {
    "resume_text": "Backend work with Python.",
    "titles": ["Backend Engineer"],
    "must_have": ["Python"],
    "nice_have": ["Django"],
    "exclude": ["Acme"],
    "work_mode": "both",
    "city": "Dhaka",
    "salary_floor_bdt": 80000,
}


class RankTests(unittest.TestCase):
    def test_close_match_stays_above_unrelated_listing(self):
        close = _job("Python Developer", description="Build APIs with Python and Django.")
        title_only = _job("Backend Engineer", company="Contoso", description="Own the services.")
        unrelated = _job("School Teacher", company="Fabrikam", description="Teach classes and grade papers.")
        ranked = rank_jobs([unrelated, title_only, close], PROFILE)
        self.assertEqual([job["title"] for job in ranked], ["Python Developer", "Backend Engineer", "School Teacher"])
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])
        self.assertGreater(ranked[1]["score"], ranked[2]["score"])
        self.assertIn("Other open listing", ranked[2]["reasons"])
        self.assertTrue(any(reason.startswith("Skills:") for reason in ranked[0]["reasons"]))

    def test_hard_exclusions_still_drop_jobs(self):
        banned = _job("Python Developer", company="Acme", description="Python services.")
        onsite = _job("Retail Associate", company="Local Shop", source="Bdjobs", remote=False, description="In store.")
        remote_profile = {**PROFILE, "work_mode": "remote"}
        ranked = rank_jobs([banned, onsite, _job("School Teacher", description="Teach classes.")], remote_profile)
        self.assertEqual([job["title"] for job in ranked], ["School Teacher"])

    def test_salary_floor_does_not_hide_a_listing(self):
        low = _job("School Teacher", description="Teach classes.")
        low["salary"] = "20000"
        ranked = rank_jobs([low], PROFILE)
        self.assertEqual(len(ranked), 1)

    def test_search_still_requires_a_resume(self):
        self.assertEqual(search_block_reason({}), "Paste or upload a resume before searching.")
        self.assertIsNone(search_block_reason({"resume_text": "Built tools with Python."}))
