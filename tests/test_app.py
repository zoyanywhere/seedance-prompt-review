import os
from pathlib import Path

Path("data").mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = "sqlite:///./data/test_app.db"
os.environ["MEDIA_ROOT"] = "./data/test_media"
os.environ["SECURE_COOKIES"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, SessionLocal, User, engine
from app.main import app
from app.security import hash_password
from app.providers import ModelResult


def setup_function():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Path("data/test_media").mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        db.add(User(email="admin@example.test", name="Admin", password_hash=hash_password("strong-admin-passphrase"), is_admin=True))
        db.commit()


def signin(client, email="admin@example.test", password="strong-admin-passphrase"):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["csrf"]


def test_invitation_and_project_isolation():
    with TestClient(app) as client:
        csrf = signin(client)
        invite = client.post("/api/admin/invitations", json={"email": "creator@example.test"}, headers={"X-CSRF-Token": csrf})
        assert invite.status_code == 200
        link = invite.json()["link"]
        secret = link.split("token=")[1]
        registration = client.post("/api/auth/register", json={"token": secret, "name": "Creator", "password": "strong-creator-passphrase"})
        assert registration.status_code == 200
        assert client.post("/api/auth/register", json={"token": secret, "name": "Again", "password": "strong-creator-passphrase"}).status_code == 400
        creator_csrf = registration.json()["csrf"]
        project = client.post("/api/projects", json={"title": "First film", "brief": "A fox crosses a snowy field."}, headers={"X-CSRF-Token": creator_csrf})
        assert project.status_code == 200, project.text
        project_id = project.json()["id"]
        assert client.post("/api/projects", json={"title": "Bad", "brief": "test"}).status_code == 403
        client.post("/api/auth/logout", headers={"X-CSRF-Token": creator_csrf})
        assert client.get(f"/api/projects/{project_id}").status_code == 401


def test_storyboard_and_review_gate(monkeypatch):
    def fake_model(phase, prompt, media, **kwargs):
        if phase == "brief_intake":
            return ModelResult('{"intent":"Fox","must_haves":[],"uncertainties":[],"reference_roles":[]}')
        if phase == "storyboard":
            return ModelResult('{"shots":[{"id":"shot-1","time_window":"0-8s","visible_action":"Fox crosses field","reference_media_ids":[]}],"open_questions":[]}')
        if phase == "prompt_draft":
            return ModelResult('{"prompt":"An orange fox crosses a snowy field in one wide shot, 0–8 seconds."}')
        if phase in {"action_timing", "camera_visuals", "audio_dialogue", "continuity", "challenge_review"}:
            return ModelResult('{"findings":[],"verdict":"clear"}')
        if phase == "supervisor":
            return ModelResult('{"prompt":"An orange fox crosses a snowy field in one wide shot, 0–8 seconds.","unresolved_critical":false,"needs_recheck":false,"risks":[],"decisions":[]}')
        raise AssertionError(phase)
    monkeypatch.setattr("app.workflow.call_model", fake_model)
    with TestClient(app) as client:
        csrf = signin(client)
        project = client.post("/api/projects", json={"title": "Fox", "brief": "A fox crosses a snowy field."}, headers={"X-CSRF-Token": csrf}).json()
        pid = project["id"]
        assert client.post(f"/api/projects/{pid}/reviews", json={}, headers={"X-CSRF-Token": csrf}).status_code == 409
        generated = client.post(f"/api/projects/{pid}/storyboard/generate", headers={"X-CSRF-Token": csrf})
        assert generated.status_code == 200
        current = client.get(f"/api/projects/{pid}").json()
        assert current["status"] == "storyboard_draft"
        approved = client.post(f"/api/projects/{pid}/storyboard/approve", headers={"X-CSRF-Token": csrf})
        assert approved.status_code == 200
        reviewed = client.post(f"/api/projects/{pid}/reviews", json={"budget_usd": 5}, headers={"X-CSRF-Token": csrf})
        assert reviewed.status_code == 200
        current = client.get(f"/api/projects/{pid}").json()
        assert current["review"]["status"] == "ready_for_approval", current["review"]
        assert len(current["usage"]) == 9
        assert client.post(f"/api/projects/{pid}/prompt/approve", headers={"X-CSRF-Token": csrf}).status_code == 200
        assert client.get(f"/api/projects/{pid}").json()["prompt_approved"] is True


def test_task_specific_settings_rejected():
    with TestClient(app) as client:
        csrf = signin(client)
        response = client.post("/api/projects", json={"title": "Edit", "brief": "Change the first shot.", "task_type": "edit", "duration": 8, "ratio": "16:9"}, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 422
        assert "adaptive" in str(response.json())
