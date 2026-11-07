"""Search, resume save, and a template CV work when no model is configured."""

from fastapi.testclient import TestClient

from app import db
from app.llm import NO_MODEL
from app.main import app

RESUME = "Samira Rahman\nsamira@example.com\nSkills\nPython, Django\n"


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobfinder.db")
    return TestClient(app)


def test_empty_settings_say_no_model_is_configured(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        state = client.get("/api/state")
        assert state.status_code == 200
        settings = state.json()["settings"]
        assert settings["configured"] is False
        assert settings["model"] == ""
        assert settings["base_url"] == ""
        assert settings["has_key"] is False
        assert settings["model_note"] == NO_MODEL
        assert "api_key" not in settings

        chat = client.post("/api/chat", json={"message": "I want Python work in Dhaka."})
        assert chat.status_code == 200
        body = chat.json()
        assert body["configured"] is False
        assert body["reply"] == NO_MODEL
        assert body["messages"] == []

        probed = client.post("/api/settings/test")
        assert probed.status_code == 200
        assert probed.json() == {"ok": False, "configured": False, "reply": NO_MODEL}


def test_resume_search_and_template_cv_without_a_model(tmp_path, monkeypatch):
    async def fake_collect(_keywords):
        return (
            [
                {
                    "id": "job-1",
                    "source": "Remote OK",
                    "title": "Python developer",
                    "company": "Example",
                    "location": "Dhaka",
                    "salary": "",
                    "description": "Python and Django",
                    "board_url": "https://example.com/jobs/1",
                    "company_url": "https://example.com/apply",
                    "posted_at": "2026-01-01",
                    "is_remote": True,
                    "location_note": "",
                }
            ],
            [],
        )

    monkeypatch.setattr("app.main.collect", fake_collect)
    with _client(tmp_path, monkeypatch) as client:
        saved = client.post("/api/resume", json={"text": RESUME})
        assert saved.status_code == 200
        assert saved.json()["name"] == "Samira Rahman"
        assert "Python" in saved.json()["must_have"]

        found = client.post(
            "/api/search",
            json={
                "resume_text": RESUME,
                "name": "Samira Rahman",
                "contact": "samira@example.com",
                "titles": ["Python developer"],
                "must_have": ["Python"],
                "city": "Dhaka",
                "work_mode": "both",
            },
        )
        assert found.status_code == 200
        assert found.json()["matched"] == 1

        cv = client.post("/api/jobs/job-1/cv")
        assert cv.status_code == 200
        document = cv.json()
        assert "Applying for the Python developer role" in document["tailored_cv"]
        assert "Hello Example team" in document["cover_note"]
