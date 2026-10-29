import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import db, feeds
from app import main as app_main


def listing(job_id: str, title: str = "Python developer", score: int = 10) -> dict:
    return {
        "id": job_id,
        "source": "Bdjobs",
        "title": title,
        "company": "Northwind",
        "location": "Dhaka, Bangladesh",
        "salary": "",
        "description": "Python work in Dhaka",
        "board_url": "https://example.test/" + job_id,
        "company_url": "",
        "posted_at": "2026-10-01",
        "is_remote": False,
        "location_note": "",
        "score": score,
        "reasons": ["Skills: Python"],
    }


def raw_listing(job_id: str, title: str = "Python developer") -> dict:
    job = listing(job_id, title)
    job.pop("score")
    job.pop("reasons")
    return job


PROFILE = {
    "resume_text": "Python developer based in Dhaka.",
    "name": "Ada",
    "contact": "ada@example.com",
    "titles": ["Python developer"],
    "must_have": ["Python"],
    "nice_have": [],
    "city": "Dhaka",
    "salary_floor_bdt": None,
    "work_mode": "both",
    "exclude": [],
}


class TempDb(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self._old_path = db.DB_PATH
        db.DB_PATH = Path(self._dir.name) / "jobfinder.db"
        self.addCleanup(setattr, db, "DB_PATH", self._old_path)
        feeds._retry_after = None
        self.addCleanup(setattr, feeds, "_retry_after", None)
        db.init()


class StoreTests(TempDb):
    def test_later_search_keeps_jobs_missing_from_the_batch(self):
        first = db.store_search([listing("old"), listing("kept")])
        self.assertEqual(first, 2)
        db.update_job("kept", status="applied", tailored_cv="Edited CV", cover_note="Note")

        added = db.store_search([listing("kept", title="Python lead", score=40), listing("fresh")])

        self.assertEqual(added, 1)
        stored = {job["id"]: job for job in db.list_jobs(["new", "applied", "saved", "skipped"])}
        self.assertEqual(set(stored), {"old", "kept", "fresh"})
        self.assertEqual(stored["kept"]["status"], "applied")
        self.assertEqual(stored["kept"]["tailored_cv"], "Edited CV")
        self.assertEqual(stored["kept"]["cover_note"], "Note")
        self.assertEqual(stored["kept"]["title"], "Python lead")
        self.assertEqual(stored["kept"]["score"], 40)
        self.assertEqual(stored["old"]["status"], "new")
        self.assertEqual(db.store_search([]), 0)
        self.assertEqual(set(db.job_ids()), {"old", "kept", "fresh"})

    def test_feed_switch_defaults_on_and_can_be_turned_off(self):
        self.assertTrue(db.get_feed_settings()["auto_refresh"])
        self.assertIsNone(db.get_feed_settings()["last_feed_added"])
        db.set_auto_refresh(False)
        self.assertFalse(db.get_feed_settings()["auto_refresh"])
        db.set_auto_refresh(True)
        db.mark_feed_refreshed(4)
        stored = db.get_feed_settings()
        self.assertTrue(stored["auto_refresh"])
        self.assertEqual(stored["last_feed_added"], 4)
        self.assertTrue(stored["last_feed_at"])
        status = feeds.public_feed_status()
        self.assertEqual(status["interval_hours"], 8)
        last = datetime.fromisoformat(status["last_refresh_at"])
        nxt = datetime.fromisoformat(status["next_refresh_at"])
        self.assertEqual(nxt - last, timedelta(hours=8))
        db.set_auto_refresh(False)
        self.assertEqual(feeds.public_feed_status()["next_refresh_at"], "")


class ScheduleTests(unittest.TestCase):
    def test_due_times(self):
        now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(feeds.seconds_until_due("", now), 0)
        self.assertEqual(feeds.seconds_until_due("not-a-date", now), 0)
        self.assertEqual(feeds.seconds_until_due((now - timedelta(hours=8)).isoformat(), now), 0)
        self.assertEqual(feeds.seconds_until_due((now - timedelta(hours=9)).isoformat(), now), 0)
        self.assertEqual(
            feeds.seconds_until_due((now - timedelta(hours=7, minutes=30)).isoformat(), now),
            30 * 60,
        )

    def test_next_delay(self):
        now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        self.assertIsNone(feeds.next_delay(True, "", now))
        self.assertEqual(feeds.next_delay(False, "", now), 30)
        self.assertEqual(feeds.next_delay(True, recent, now), 60)
        almost = (now - timedelta(hours=7, minutes=59, seconds=50)).isoformat()
        self.assertEqual(feeds.next_delay(True, almost, now), 10)
        self.assertIsNone(feeds.next_delay(True, (now - timedelta(hours=8)).isoformat(), now))
        self.assertEqual(feeds.next_delay(True, "", now, now + timedelta(seconds=20)), 20)
        self.assertEqual(feeds.next_delay(True, "", now, now + timedelta(minutes=10)), 60)
        self.assertIsNone(feeds.next_delay(True, "", now, now - timedelta(seconds=1)))


class SelectTests(unittest.TestCase):
    def test_new_jobs_are_capped_and_old_matches_are_kept(self):
        ranked = [listing(str(i), score=100 - i) for i in range(30)]
        chosen = feeds.select_for_store(ranked, {"0", "1"}, limit=25)
        ids = [job["id"] for job in chosen]
        self.assertEqual(ids[:2], ["0", "1"])
        self.assertEqual(len(ids), 27)
        self.assertNotIn("27", ids)
        self.assertIn("26", ids)


class RefreshTests(TempDb):
    def setUp(self):
        super().setUp()
        self._collect = feeds.collect
        self.addCleanup(setattr, feeds, "collect", self._collect)
        db.save_profile(PROFILE)

    def test_refresh_adds_jobs_and_leaves_missing_ones(self):
        db.store_search([listing("older", title="Python analyst")])
        db.update_job("older", tailored_cv="Stay", cover_note="Stay note")
        db.store_search([listing("old", title="Python developer")])
        db.update_job("old", status="saved", tailored_cv="Saved CV", cover_note="Saved note")

        async def fake_collect(_keywords):
            jobs = [raw_listing("old", "Python lead")]
            jobs.extend(raw_listing(f"n{i}") for i in range(30))
            return jobs, []

        feeds.collect = fake_collect
        result = asyncio.run(feeds.collect_and_store(db.get_profile()))

        self.assertEqual(result["added"], 30)
        self.assertEqual(result["matched"], 31)
        self.assertFalse(result["deferred"])
        stored = {job["id"]: job for job in db.list_jobs(["new", "saved"])}
        self.assertIn("older", stored)
        self.assertEqual(stored["older"]["tailored_cv"], "Stay")
        self.assertEqual(stored["older"]["cover_note"], "Stay note")
        self.assertEqual(stored["old"]["status"], "saved")
        self.assertEqual(stored["old"]["tailored_cv"], "Saved CV")
        self.assertEqual(stored["old"]["title"], "Python lead")
        self.assertEqual(db.get_job("n0")["status"], "new")
        self.assertIsNotNone(db.get_job("n29"))
        self.assertEqual(db.get_feed_settings()["last_feed_added"], 30)

        async def fake_again(_keywords):
            jobs = [raw_listing("old", "Python lead")]
            jobs.extend(raw_listing(f"n{i}") for i in range(30))
            return jobs, []

        feeds.collect = fake_again
        again = asyncio.run(feeds.collect_and_store(db.get_profile()))
        self.assertEqual(again["added"], 0)
        self.assertIsNotNone(db.get_job("older"))

    def test_failed_boards_do_not_drop_leads_or_reset_the_timer(self):
        db.store_search([listing("older")])

        async def fake_down(_keywords):
            return [], ["Bdjobs did not respond, so those listings are missing from this search."]

        feeds.collect = fake_down
        result = asyncio.run(feeds.collect_and_store(db.get_profile()))
        self.assertTrue(result["deferred"])
        self.assertEqual(result["added"], 0)
        self.assertIsNotNone(db.get_job("older"))
        self.assertEqual(db.get_feed_settings()["last_feed_at"], "")


class TickTests(TempDb):
    def setUp(self):
        super().setUp()
        self._refresh = feeds.refresh_saved_profile
        self.addCleanup(setattr, feeds, "refresh_saved_profile", self._refresh)

    async def _run(self, result):
        called = {"n": 0}

        async def fake():
            called["n"] += 1
            return result

        feeds.refresh_saved_profile = fake
        delay = await feeds.tick()
        return delay, called["n"]

    def test_off_and_recent_do_not_refresh(self):
        db.set_auto_refresh(False)
        delay, calls = asyncio.run(self._run({"deferred": False}))
        self.assertEqual(delay, 30)
        self.assertEqual(calls, 0)

        db.set_auto_refresh(True)
        db.mark_feed_refreshed(0)
        delay, calls = asyncio.run(self._run({"deferred": False}))
        self.assertEqual(delay, 60)
        self.assertEqual(calls, 0)

    def test_due_refresh_and_backoff(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat()
        db._write_settings({"auto_refresh": "1", "last_feed_at": old, "last_feed_added": "0"})
        delay, calls = asyncio.run(self._run({"deferred": False}))
        self.assertEqual(delay, 60)
        self.assertEqual(calls, 1)
        self.assertIsNone(feeds._retry_after)

        delay, calls = asyncio.run(self._run({"deferred": True}))
        self.assertEqual(delay, 15 * 60)
        self.assertEqual(calls, 1)
        self.assertIsNotNone(feeds._retry_after)


class ApiTests(TempDb):
    def test_toggle_and_search_keep_existing_leads(self):
        async def idle(stop):
            await stop.wait()

        original_loop = app_main.feed_loop
        original_collect = feeds.collect
        app_main.feed_loop = idle

        async def fake_collect(_keywords):
            return [raw_listing("fresh", "Python developer")], []

        feeds.collect = fake_collect
        try:
            from fastapi.testclient import TestClient

            db.store_search([listing("older", title="Python analyst")])
            db.update_job("older", status="saved", tailored_cv="Keep")
            with TestClient(app_main.app) as client:
                state = client.get("/api/state")
                self.assertEqual(state.status_code, 200)
                self.assertTrue(state.json()["feeds"]["auto_refresh"])
                self.assertEqual(state.json()["feeds"]["interval_hours"], 8)
                self.assertEqual({job["id"] for job in state.json()["leads"]}, {"older"})

                off = client.put("/api/feeds", json={"auto_refresh": False})
                self.assertEqual(off.status_code, 200)
                self.assertFalse(off.json()["auto_refresh"])
                self.assertEqual(off.json()["next_refresh_at"], "")
                self.assertFalse(client.get("/api/state").json()["feeds"]["auto_refresh"])

                on = client.put("/api/feeds", json={"auto_refresh": True})
                self.assertTrue(on.json()["auto_refresh"])

                searched = client.post("/api/search", json=PROFILE)
                self.assertEqual(searched.status_code, 200)
                body = searched.json()
                self.assertEqual(body["added"], 1)
                self.assertGreaterEqual(body["matched"], 1)
                ids = {job["id"] for job in body["leads"]}
                self.assertEqual(ids, {"older", "fresh"})
                older = next(job for job in body["leads"] if job["id"] == "older")
                self.assertEqual(older["status"], "saved")
                self.assertEqual(db.get_job("older")["tailored_cv"], "Keep")
                self.assertTrue(body["feeds"]["last_refresh_at"])
                self.assertEqual(body["feeds"]["last_added"], 1)
        finally:
            app_main.feed_loop = original_loop
            feeds.collect = original_collect


if __name__ == "__main__":
    unittest.main()
