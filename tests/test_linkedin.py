import tempfile
import unittest
from pathlib import Path

import httpx

from app.sources.linkedin import EXTERNAL_NOTE, extract_urls, fetch_jobs, lead_from_html, looks_like_secret

PUBLIC_JOB = """
<html>
<head>
  <title>Backend Engineer | LinkedIn</title>
  <script type="application/ld+json">
  {"@context":"https://schema.org","@type":"JobPosting","title":"Backend Engineer",
   "description":"<p>Build APIs with Python.</p>",
   "hiringOrganization":{"@type":"Organization","name":"Northwind"},
   "jobLocation":{"@type":"Place","address":{"addressLocality":"Dhaka","addressCountry":"Bangladesh"}}}
  </script>
</head>
<body>
  <a href="https://www.linkedin.com/jobs/view/4382199999">View job</a>
  <a href="https://boards.greenhouse.io/northwind/jobs/999">Apply on company website</a>
  <button>Easy Apply</button>
</body>
</html>
"""

EASY_APPLY = """
<html>
<head>
  <script type="application/ld+json">
  {"@type":"JobPosting","title":"Support Associate","description":"<p>Answer customers.</p>",
   "hiringOrganization":{"name":"Contoso"}}
  </script>
</head>
<body><button>Easy Apply</button></body>
</html>
"""

LOGIN_WALL = """
<html><title>Sign In | LinkedIn</title>
<body>
  <div class="authwall">Join LinkedIn to see this job. Sign in to view the apply button.</div>
</body></html>
"""

JSON_APPLY = """
<html><head>
<script type="application/ld+json">
{"@type":"JobPosting","title":"Data Analyst","description":"<p>SQL and Excel.</p>","hiringOrganization":{"name":"Fabrikam"}}
</script>
</head><body>
<script>{"companyApplyUrl":"https:\\/\\/jobs.lever.co\\/fabrikam\\/abc-123"}</script>
</body></html>
"""


class ParseTests(unittest.TestCase):
    def test_public_page_keeps_external_apply_link(self):
        job = lead_from_html(PUBLIC_JOB, "https://www.linkedin.com/jobs/view/4382199999")
        self.assertEqual(job["source"], "LinkedIn")
        self.assertEqual(job["title"], "Backend Engineer")
        self.assertEqual(job["company"], "Northwind")
        self.assertEqual(job["id"], "linkedin-4382199999")
        self.assertEqual(job["board_url"], "https://www.linkedin.com/jobs/view/4382199999")
        self.assertEqual(job["company_url"], "https://boards.greenhouse.io/northwind/jobs/999")
        self.assertEqual(job["official_url"], job["company_url"])
        self.assertNotIn(EXTERNAL_NOTE, job["reasons"])

    def test_easy_apply_keeps_linkedin_link(self):
        job = lead_from_html(EASY_APPLY, "https://www.linkedin.com/jobs/view/10000001")
        self.assertEqual(job["company_url"], "")
        self.assertEqual(job["official_url"], "https://www.linkedin.com/jobs/view/10000001")
        self.assertIn(EXTERNAL_NOTE, job["reasons"])
        self.assertIn(EXTERNAL_NOTE, job["description"])

    def test_login_wall_is_still_a_lead(self):
        job = lead_from_html(LOGIN_WALL, "https://www.linkedin.com/jobs/view/10000002", status=999)
        self.assertEqual(job["board_url"], "https://www.linkedin.com/jobs/view/10000002")
        self.assertEqual(job["company_url"], "")
        self.assertIn(EXTERNAL_NOTE, job["location_note"])
        self.assertNotIn("li_at", job["description"])

    def test_escaped_company_apply_url(self):
        job = lead_from_html(JSON_APPLY, "https://www.linkedin.com/jobs/view/10000003")
        self.assertEqual(job["company_url"], "https://jobs.lever.co/fabrikam/abc-123")

    def test_linkedin_apply_url_is_not_external(self):
        page = """
        <html><head>
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Data Analyst","description":"<p>SQL and Excel.</p>","hiringOrganization":{"name":"Fabrikam"}}
        </script>
        </head><body>
        <script>{"applyUrl":"https://www.linkedin.com/jobs/view/10000003"}</script>
        <a href="https://www.linkedin.com/jobs/view/10000003">Easy Apply</a>
        </body></html>
        """
        job = lead_from_html(page, "https://www.linkedin.com/jobs/view/10000003")
        self.assertEqual(job["company_url"], "")

    def test_extract_urls_dedupes_and_ignores_other_sites(self):
        text = """
        https://www.linkedin.com/jobs/view/backend-engineer-at-northwind-4382199999?trk=public
        https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4382199999
        www.linkedin.com/jobs/view/10000004
        https://example.com/jobs/1
        https://lnkd.in/abcDEF
        """
        urls, warnings = extract_urls(text)
        self.assertEqual(warnings, [])
        self.assertEqual(
            urls,
            [
                "https://www.linkedin.com/jobs/view/4382199999",
                "https://www.linkedin.com/jobs/view/10000004",
                "https://lnkd.in/abcDEF",
            ],
        )

    def test_rejects_secret_material(self):
        self.assertTrue(looks_like_secret("li_at=abc"))
        self.assertFalse(looks_like_secret("https://www.linkedin.com/jobs/view/10000004"))


PUBLIC_TOP_CARD = """
<html>
<head><title>Platform Engineer | LinkedIn Jobs</title></head>
<body>
  <h2>Sign in to set job alerts</h2>
  <h1>Platform Engineer</h1>
  <a class="topcard__org-name-link topcard__flavor--black-link" href="https://www.linkedin.com/company/northwind">Northwind</a>
  <span class="topcard__flavor topcard__flavor--bullet">Dhaka, Bangladesh</span>
  <div class="show-more-less-html__markup">Build Python services for the hiring team.</div>
  <button data-tracking-control-name="public_jobs_apply-link-onsite">Apply</button>
  <a href="https://www.linkedin.com/signup/cold-join?session_redirect=https%3A%2F%2Fwww.linkedin.com%2Fjobs%2Fview%2F10000005" data-tracking-control-name="public_jobs_apply-link-offsite">Apply</a>
</body>
</html>
"""

OFFSITE_LINK = """
<html><body>
  <h1>Analyst</h1>
  <a class="topcard__org-name-link" href="https://www.linkedin.com/company/northwind">Northwind</a>
  <a class="apply-button" data-tracking-control-name="public_jobs_apply-link-offsite" href="https://boards.greenhouse.io/northwind/jobs/15">Apply</a>
</body></html>
"""


class TopCardTests(unittest.TestCase):
    def test_public_top_card_without_external_link(self):
        job = lead_from_html(PUBLIC_TOP_CARD, "https://www.linkedin.com/jobs/view/10000005")
        self.assertEqual(job["title"], "Platform Engineer")
        self.assertEqual(job["company"], "Northwind")
        self.assertEqual(job["location"], "Dhaka, Bangladesh")
        self.assertIn("Python services", job["description"])
        self.assertEqual(job["company_url"], "")
        self.assertIn(EXTERNAL_NOTE, job["reasons"])

    def test_offsite_anchor_is_kept(self):
        job = lead_from_html(OFFSITE_LINK, "https://www.linkedin.com/jobs/view/10000006")
        self.assertEqual(job["company_url"], "https://boards.greenhouse.io/northwind/jobs/15")
        self.assertEqual(job["official_url"], job["company_url"])


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_uses_public_html(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=PUBLIC_JOB)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            jobs, warnings = await fetch_jobs(
                ["https://www.linkedin.com/jobs/view/4382199999"],
                client=client,
            )
        self.assertEqual(warnings, [])
        self.assertEqual(jobs[0]["company_url"], "https://boards.greenhouse.io/northwind/jobs/999")

    async def test_offsite_redirect_is_the_apply_link(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "boards.greenhouse.io":
                raise AssertionError("off-LinkedIn redirects are not followed")
            return httpx.Response(302, headers={"location": "https://boards.greenhouse.io/northwind/jobs/999"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            jobs, warnings = await fetch_jobs(
                ["https://www.linkedin.com/jobs/view/4382199999"],
                client=client,
            )
        self.assertEqual(jobs[0]["company_url"], "https://boards.greenhouse.io/northwind/jobs/999")
        self.assertEqual(warnings, [])


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import app.db as db

        self.db = db
        self.old = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "jobfinder.db"

    def tearDown(self):
        self.db.DB_PATH = self.old
        self.tmp.cleanup()

    def test_import_is_idempotent_and_survives_search(self):
        job = lead_from_html(PUBLIC_JOB, "https://www.linkedin.com/jobs/view/4382199999")
        self.db.upsert_imported([job])
        self.db.update_job(job["id"], tailored_cv="Kept CV", cover_note="Kept note")
        self.db.upsert_imported([job])
        stored = self.db.get_job(job["id"])
        self.assertEqual(stored["tailored_cv"], "Kept CV")
        self.assertEqual(stored["status"], "new")
        self.assertEqual(stored["company_url"], job["company_url"])
        self.assertEqual(len(self.db.list_jobs(["new", "saved"])), 1)
        self.db.store_search(
            [
                {
                    "id": "remotive-1",
                    "source": "Remotive",
                    "title": "Other",
                    "company": "Other Co",
                    "score": 3,
                    "reasons": [],
                }
            ]
        )
        ids = {row["id"] for row in self.db.list_jobs(["new", "saved"])}
        self.assertIn(job["id"], ids)
        self.assertIn("remotive-1", ids)


if __name__ == "__main__":
    unittest.main()
