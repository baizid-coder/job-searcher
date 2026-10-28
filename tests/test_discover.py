"""Discover feed, verdicts, summaries, and the stable-preference gate."""

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.discover import store
from app.discover.classify import FAMILIES
from app.llm import ModelError
from app.main import app

ROOT = Path(__file__).resolve().parents[1]

SPECS = {
    "data_analyst": (
        "Data Analyst",
        "Build dashboards with SQL, Excel, and Tableau for weekly reporting.",
    ),
    "ml_mlops": (
        "MLOps Engineer",
        "Deploy machine learning models with Python, Docker, and Kubernetes.",
    ),
    "business_analyst": (
        "Business Analyst",
        "Gather requirements and write user stories with the team.",
    ),
    "operations": (
        "Operations Manager",
        "Run supply chain, logistics, and vendor schedules.",
    ),
    "product": (
        "Product Manager",
        "Own the product roadmap and discovery with the design team.",
    ),
    "it_bank_tech": (
        "IT Officer",
        "Support core banking systems, Linux servers, and the bank network.",
    ),
}


def _posting(posting_id, title, description, company, location, salary, remote):
    return {
        "id": posting_id,
        "title": title,
        "company": company,
        "location": location,
        "salary": salary,
        "description": description,
        "is_remote": remote,
        "source": "Fixture",
    }


def _family_jobs(family, count, prefix):
    title, description = SPECS[family]
    jobs = []
    for index in range(count):
        jobs.append(
            _posting(
                f"{prefix}-{family}-{index}",
                title,
                description,
                f"{family} {prefix} {index}",
                "Dhaka",
                "80000 BDT",
                True,
            )
        )
    return jobs


def _diverse_jobs(per_family=6):
    jobs = []
    for family in FAMILIES:
        jobs.extend(_family_jobs(family, per_family, "mix"))
    return jobs


def _wave_jobs(wave):
    jobs = []
    for index in range(15):
        jobs.append(
            _posting(
                f"da-{wave}-{index}",
                "Junior Data Analyst",
                "Use SQL and Excel for banking reports.",
                f"Northwind {wave}-{index}",
                "Dhaka, Bangladesh",
                "90000 BDT",
                True,
            )
        )
    for index in range(10):
        jobs.append(
            _posting(
                f"ops-{wave}-{index}",
                "Operations Manager",
                "Run supply chain and logistics for a retail company.",
                f"Contoso {wave}-{index}",
                "Chittagong",
                "",
                False,
            )
        )
    return jobs


def _client(tmp_path, monkeypatch, name="discover.db"):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / f"job-{name}")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / name)
    holder = {"jobs": []}

    async def fake_fetch():
        return list(holder["jobs"]), []

    monkeypatch.setattr("app.discover.api.fetch_postings", fake_fetch)
    client = TestClient(app)
    client.jobs = holder
    return client


def _load(client, jobs):
    client.jobs["jobs"] = jobs
    response = client.get("/api/discover/feed")
    assert response.status_code == 200, response.text
    return response.json()


def _judge(client, posting_id, verdict, chips=None):
    return client.post(
        "/api/discover/verdicts",
        json={"posting_id": posting_id, "verdict": verdict, "reason_chips": chips or []},
    )


def _judge_wave(client, wave):
    jobs = _wave_jobs(wave)
    _load(client, jobs)
    last = None
    for job in jobs:
        if job["id"].startswith("da-"):
            last = _judge(client, job["id"], "yes", [])
        else:
            last = _judge(client, job["id"], "no", ["location"])
        assert last.status_code == 200, last.text
    return last


def _confirm_open(client):
    profile = client.get("/api/discover/profile")
    assert profile.status_code == 200, profile.text
    body = profile.json()
    for line in body["summary"]["lines"]:
        if line["status"] != "confirmed":
            saved = client.post("/api/discover/lines/confirm", json={"key": line["key"]})
            assert saved.status_code == 200, saved.text
    return client.get("/api/discover/profile").json()


def _reach_stable(client):
    _judge_wave(client, 0)
    _confirm_open(client)
    _judge_wave(client, 1)
    confirmed = _confirm_open(client)
    assert confirmed["summary_count"] == 2
    assert confirmed["stable"] is False
    _judge_wave(client, 2)
    profile = client.get("/api/discover/profile").json()
    open_lines = [line["key"] for line in profile["summary"]["lines"] if line["status"] != "confirmed"]
    assert open_lines == []
    return profile


def test_judged_posting_is_not_shown_again(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        first = _load(client, _diverse_jobs())
        posting_id = first["postings"][0]["posting_id"]
        saved = _judge(client, posting_id, "no", [])
        assert saved.status_code == 200, saved.text
        again = _load(client, _diverse_jobs())
        assert posting_id not in {posting["posting_id"] for posting in again["postings"]}
        duplicate = _judge(client, posting_id, "yes", [])
        assert duplicate.status_code == 409


def test_batch_aims_for_diversity_across_families(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        body = _load(client, _diverse_jobs())
        families = [posting["role_family"] for posting in body["postings"]]
        assert len(body["postings"]) == 20
        assert set(families) == set(FAMILIES)
        counts = Counter(families)
        assert max(counts.values()) - min(counts.values()) <= 1

        jobs = []
        for family in FAMILIES:
            if family == "product":
                continue
            jobs.extend(_family_jobs(family, 10, "thin"))
        thin = _load(client, jobs)
        assert thin["thin_families"] == ["product"]
        assert len(thin["postings"]) == 20
        assert all(posting["role_family"] != "product" for posting in thin["postings"])
        assert {posting["role_family"] for posting in thin["postings"]} == set(FAMILIES) - {"product"}


def test_verdict_stores_chips_and_timestamp(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        feed = _load(client, _diverse_jobs(1))
        posting = next(item for item in feed["postings"] if item["salary"])
        before = datetime.now(timezone.utc)
        saved = _judge(client, posting["posting_id"], "yes", ["salary"])
        after = datetime.now(timezone.utc)
        assert saved.status_code == 200, saved.text
        verdict = saved.json()["verdict"]
        assert verdict["reason_chips"] == ["salary"]
        stamp = datetime.fromisoformat(verdict["created_at"])
        assert before <= stamp <= after
        stored = store.get_verdict(posting["posting_id"])
        assert stored["reason_chips"] == ["salary"]
        assert stored["created_at"] == verdict["created_at"]

        other = next(item for item in feed["postings"] if item["posting_id"] != posting["posting_id"])
        maybe = _judge(client, other["posting_id"], "maybe", ["salary"])
        assert maybe.status_code == 200, maybe.text
        assert maybe.json()["verdict"]["reason_chips"] == []
        assert store.get_verdict(other["posting_id"])["reason_chips"] == []


def test_summary_updates_after_25_verdicts(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        jobs = _wave_jobs(0)
        extra = _posting(
            "da-extra",
            "Junior Data Analyst",
            "Use SQL and Excel for banking reports.",
            "Northwind extra",
            "Dhaka, Bangladesh",
            "90000 BDT",
            True,
        )
        _load(client, jobs + [extra])
        for job in jobs[:24]:
            verdict = "yes" if job["id"].startswith("da-") else "no"
            chips = [] if verdict == "yes" else ["location"]
            saved = _judge(client, job["id"], verdict, chips)
            assert saved.status_code == 200, saved.text
            assert saved.json()["summary"] is None
        waiting = client.get("/api/discover/profile").json()
        assert waiting["summary"] is None
        assert waiting["summary_count"] == 0

        last = jobs[24]
        verdict = "yes" if last["id"].startswith("da-") else "no"
        chips = [] if verdict == "yes" else ["location"]
        created = _judge(client, last["id"], verdict, chips)
        assert created.status_code == 200, created.text
        summary = created.json()["summary"]
        assert summary["source"] == "verdicts"
        assert summary["label"] == "Computed from verdicts, not from a model."
        assert summary["lines"]
        assert any(line["dimension"] == "role_family" and "confidence" in line and line["counts"]["yes"] >= 0 for line in summary["lines"])
        profile = client.get("/api/discover/profile").json()
        assert profile["summary_count"] == 1

        extra_saved = _judge(client, extra["id"], "yes", [])
        assert extra_saved.status_code == 200
        assert extra_saved.json()["summary"] is None
        assert client.get("/api/discover/profile").json()["summary_count"] == 1


def test_summary_falls_back_when_the_model_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("app.discover.summary.configured", lambda settings: True)

    async def boom(*args, **kwargs):
        raise ModelError("The model did not answer.")

    monkeypatch.setattr("app.discover.summary.complete", boom)
    with _client(tmp_path, monkeypatch) as client:
        saved = _judge_wave(client, 9)
        summary = saved.json()["summary"]
        assert summary["source"] == "verdicts"
        assert "not from a model" in summary["label"]


def test_confirmed_lines_lock(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _judge_wave(client, 3)
        profile = client.get("/api/discover/profile").json()
        line = profile["summary"]["lines"][0]
        key = line["key"]
        confirmed = client.post("/api/discover/lines/confirm", json={"key": key})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "confirmed"
        assert confirmed.json()["locked"] is True
        assert client.post("/api/discover/lines/confirm", json={"key": key}).status_code == 409
        assert client.post("/api/discover/lines/reject", json={"key": key}).status_code == 409

        edited = client.post(
            "/api/discover/lines/edit",
            json={"key": key, "text": "Keep data analyst roles on the short list."},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "confirmed"
        assert edited.json()["locked"] is True
        assert edited.json()["text"] == "Keep data analyst roles on the short list."

        unlocked = client.post("/api/discover/lines/unlock", json={"key": key})
        assert unlocked.status_code == 200, unlocked.text
        assert unlocked.json()["locked"] is False
        assert unlocked.json()["status"] == "confirmed"
        rejected = client.post("/api/discover/lines/reject", json={"key": key})
        assert rejected.status_code == 200, rejected.text
        remaining = client.get("/api/discover/profile").json()["summary"]["lines"]
        assert key not in {item["key"] for item in remaining}


def test_next_batch_prefers_low_confidence_families(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        jobs = _family_jobs("ml_mlops", 16, "old") + _family_jobs("data_analyst", 9, "old")
        _load(client, jobs)
        for index, job in enumerate(jobs):
            if "ml_mlops" in job["id"]:
                verdict = "yes" if index < 8 else "no"
            else:
                verdict = "yes"
            saved = _judge(client, job["id"], verdict, [])
            assert saved.status_code == 200, saved.text
        profile = client.get("/api/discover/profile").json()
        key = "role_family:data_analyst"
        assert any(line["key"] == key for line in profile["summary"]["lines"])
        confirmed = client.post("/api/discover/lines/confirm", json={"key": key})
        assert confirmed.status_code == 200, confirmed.text

        fresh = _family_jobs("ml_mlops", 20, "new") + _family_jobs("data_analyst", 20, "new")
        batch = _load(client, fresh)
        counts = Counter(posting["role_family"] for posting in batch["postings"])
        assert counts["ml_mlops"] > counts["data_analyst"]
        assert counts["data_analyst"] > 0


def test_stable_badge_only_after_three_unchanged_confirmed_summaries(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, "early.db") as client:
        for wave in range(3):
            _judge_wave(client, wave)
        early = client.get("/api/discover/profile").json()
        assert early["summary_count"] == 3
        assert early["stable"] is False

    with _client(tmp_path, monkeypatch, "stable.db") as client:
        profile = _reach_stable(client)
        assert profile["summary_count"] == 3
        assert profile["stable"] is True, [line["key"] for line in profile["summary"]["lines"]]
        assert any(line["dimension"] == "role_family" and line["value"] == "data_analyst" for line in profile["summary"]["lines"])
        assert any(line["dimension"] == "location" for line in profile["summary"]["lines"])
        dislike = next(line for line in profile["summary"]["lines"] if line["dimension"] == "dislike")
        client.post("/api/discover/lines/unlock", json={"key": dislike["key"]})
        rejected = client.post("/api/discover/lines/reject", json={"key": dislike["key"]})
        assert rejected.status_code == 200, rejected.text
        assert client.get("/api/discover/profile").json()["stable"] is False


def test_feature_flag_blocks_resume_edit_and_auto_apply_until_stable(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCOVER_ACTIONS_ENABLED", "1")
    with _client(tmp_path, monkeypatch) as client:
        blocked = client.get("/api/discover/actions").json()
        assert blocked["resume_edit"] is False
        assert blocked["auto_apply"] is False
        assert blocked["blocked_until_stable"] is True
        assert client.post("/api/discover/resume-edit").status_code == 403
        assert client.post("/api/discover/auto-apply").status_code == 403

        profile = _reach_stable(client)
        assert profile["stable"] is True
        opened = client.get("/api/discover/actions").json()
        assert opened["resume_edit"] is True
        assert opened["auto_apply"] is True
        edit = client.post("/api/discover/resume-edit")
        apply = client.post("/api/discover/auto-apply")
        assert edit.status_code == 200, edit.text
        assert apply.status_code == 200, apply.text
        assert edit.json()["performed"] is False
        assert apply.json()["performed"] is False
        assert "does not edit resumes" in edit.json()["detail"]

        monkeypatch.setenv("DISCOVER_ACTIONS_ENABLED", "0")
        shut = client.get("/api/discover/actions").json()
        assert shut["stable"] is True
        assert shut["resume_edit"] is False
        assert shut["auto_apply"] is False
        assert client.post("/api/discover/auto-apply").status_code == 403

        page = client.get("/discover").text.lower()
        assert "auto-apply" not in page
        assert "auto apply" not in page


def test_discover_page_and_profile_states(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        page = client.get("/discover")
        assert page.status_code == 200
        assert "/discover/discover-tokens.css" in page.text
        assert client.get("/discover/discover-tokens.css").status_code == 200
        assert client.get("/discover/discover.js").status_code == 200
        empty = client.get("/api/discover/profile").json()
        assert empty["ok"] is True
        assert empty["summary"] is None
        assert empty["stable"] is False
        quiet = _load(client, [])
        assert quiet["ok"] is True
        assert quiet["postings"] == []

        async def broken():
            raise RuntimeError("boards down")

        monkeypatch.setattr("app.discover.api.fetch_postings", broken)
        failed = client.get("/api/discover/feed")
        assert failed.status_code == 502


def test_summary_stays_within_three_lines():
    from app.discover.classify import three_line_summary

    short = three_line_summary("Build dashboards with SQL.")
    assert short == "Build dashboards with SQL."
    long = three_line_summary(" ".join(f"sentence{index}." for index in range(80)))
    lines = long.splitlines()
    assert len(lines) == 3
    assert all(len(line) <= 100 for line in lines)
    assert lines[-1].endswith("…")


def test_discover_page_css_uses_tokens_only():
    html = (ROOT / "app/static/discover.html").read_text()
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    assert "discover-tokens.css" in html
    assert "px" not in style
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", style)
    tokens = (ROOT / "app/static/discover-tokens.css").read_text()
    for name in (
        "--discover-color-bg",
        "--discover-color-surface",
        "--discover-color-border",
        "--discover-color-text",
        "--discover-color-muted",
        "--discover-color-primary",
        "--discover-color-danger",
        "--discover-color-success",
        "--discover-page-max",
        "--discover-radius",
        "--discover-control-height",
        "--discover-space-1",
        "--discover-font-size-1",
    ):
        assert name in tokens
    assert "1100px" in tokens
    assert "768px" in tokens
