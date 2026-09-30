import os
from pathlib import Path

Path("data").mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = "sqlite:///./data/test_app.db"
os.environ["MEDIA_ROOT"] = "./data/test_media"
os.environ["SECURE_COOKIES"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, Preview, Project, SessionLocal, User, engine
from app.main import app
from app.security import hash_password
from app.providers import ModelResult


def test_gemini_overload_uses_recorded_fallback(monkeypatch):
    from app import providers

    calls = []

    class Overloaded(Exception):
        status_code = 503

    def fake_call(phase, prompt, media, **kwargs):
        calls.append(kwargs.get("model_override") or providers.MODEL_IDS[phase])
        if len(calls) < 4:
            raise Overloaded()
        return ModelResult('{"ok":true}', input_tokens=10, output_tokens=5)

    monkeypatch.setattr(providers, "_call_model_once", fake_call)
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)
    result = providers.call_model("camera_visuals", "test")
    assert calls == ["gemini-3.8-flash"] * 3 + ["gemini-3.5-flash"]
    assert result.model == "gemini-3.5-flash" and result.attempts == 4


def test_second_gemini_fallback_after_overload(monkeypatch):
    from app import providers

    calls = []

    class Overloaded(Exception):
        status_code = 503

    def fake_call(phase, prompt, media, **kwargs):
        calls.append(kwargs.get("model_override") or providers.MODEL_IDS[phase])
        if len(calls) < 5:
            raise Overloaded()
        return ModelResult('{"ok":true}')

    monkeypatch.setattr(providers, "_call_model_once", fake_call)
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)
    result = providers.call_model("camera_visuals", "test")
    assert calls[-2:] == ["gemini-3.5-flash", "gemini-3.5-flash-lite"]
    assert result.model == "gemini-3.5-flash-lite" and result.attempts == 5


def setup_function():
    from app.security import _attempts

    _attempts.clear()
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



def test_project_urls_do_not_grant_access_to_other_accounts():
    with SessionLocal() as db:
        db.add(User(email="creator@example.test", name="Creator", password_hash=hash_password("strong-creator-passphrase"), is_admin=False))
        db.add(User(email="other@example.test", name="Other", password_hash=hash_password("strong-other-passphrase"), is_admin=False))
        db.commit()

    with TestClient(app) as owner:
        csrf = signin(owner, "creator@example.test", "strong-creator-passphrase")
        response = owner.post("/api/projects", json={"title": "Private film", "brief": "A fox crosses a snowy field."}, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 200, response.text
        project_id = response.json()["id"]
        assert owner.get(f"/api/projects/{project_id}").status_code == 200

    for email, password in [("other@example.test", "strong-other-passphrase"), ("admin@example.test", "strong-admin-passphrase")]:
        with TestClient(app) as visitor:
            signin(visitor, email, password)
            response = visitor.get(f"/api/projects/{project_id}")
            assert response.status_code == 404
            csrf = visitor.get("/api/auth/me").json()["csrf"]
            update = visitor.put(f"/api/projects/{project_id}", json={"title": "Changed", "brief": "Another scene."}, headers={"X-CSRF-Token": csrf})
            assert update.status_code == 404



def test_storyboard_zero_reference_means_no_reference_but_unknown_id_fails():
    with TestClient(app) as client:
        csrf = signin(client)
        project = client.post("/api/projects", json={"title": "End card", "brief": "End with a title card."}, headers={"X-CSRF-Token": csrf}).json()
        project_id = project["id"]
        shots = [{"id": "07_endcard", "time_window": "6-8s", "visible_action": "Show the end card.", "reference_media_ids": [0]}]
        response = client.put(f"/api/projects/{project_id}/storyboard", json={"shots": shots, "open_questions": []}, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 200, response.text
        assert response.json()["storyboard"]["shots"][0]["reference_media_ids"] == []
        with SessionLocal() as db:
            stored = db.get(Project, project_id)
            stored.storyboard = {"shots": shots, "open_questions": []}
            db.commit()
        approved = client.post(f"/api/projects/{project_id}/storyboard/approve", headers={"X-CSRF-Token": csrf})
        assert approved.status_code == 200, approved.text
        assert approved.json()["storyboard"]["shots"][0]["reference_media_ids"] == []

        shots[0]["reference_media_ids"] = [999]
        response = client.put(f"/api/projects/{project_id}/storyboard", json={"shots": shots, "open_questions": []}, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 200
        assert client.post(f"/api/projects/{project_id}/storyboard/approve", headers={"X-CSRF-Token": csrf}).status_code == 422


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


def test_preview_rejection_removes_only_current_project_file():
    with TestClient(app) as client:
        csrf = signin(client)
        project = client.post("/api/projects", json={"title": "Preview", "brief": "A test shot."},
                              headers={"X-CSRF-Token": csrf}).json()
        path = Path("data/test_media/rejected-preview.png")
        path.write_bytes(b"preview")
        with SessionLocal() as db:
            item = Preview(project_id=project["id"], shot_id="shot-1", storyboard_version=0,
                           storage_key=path.name, mime="image/png", model="test", approved=False)
            db.add(item)
            db.commit()
            preview_id = item.id
        assert client.delete(f"/api/previews/{preview_id}", headers={"X-CSRF-Token": csrf}).status_code == 409
        with SessionLocal() as db:
            db.get(Preview, preview_id).storyboard_version = 1
            db.commit()
        client.put(f"/api/projects/{project['id']}/storyboard",
                   json={"shots": [{"id": "shot-1", "time_window": "0-8s", "visible_action": "A test shot."}]},
                   headers={"X-CSRF-Token": csrf})
        with SessionLocal() as db:
            db.get(Preview, preview_id).storyboard_version = 1
            db.commit()
        assert client.delete(f"/api/previews/{preview_id}", headers={"X-CSRF-Token": csrf}).status_code == 200
        assert not path.exists()
        assert client.get(f"/api/previews/{preview_id}/content").status_code == 404


def test_serious_finding_requires_reviewed_revision(monkeypatch):
    rounds = []

    def fake_model(phase, prompt, media, **kwargs):
        if phase == "prompt_draft":
            return ModelResult('{"prompt":"A fox walks across snow."}')
        if phase == "action_timing":
            rounds.append(phase)
            return ModelResult('{"findings":[{"severity":"major","message":"Timing is missing"}]}' if len(rounds) == 1 else '{"findings":[]}')
        if phase in {"camera_visuals", "audio_dialogue", "continuity", "challenge_review"}:
            return ModelResult('{"findings":[]}')
        if phase == "supervisor":
            text = "A fox walks across snow in 0–8 seconds."
            return ModelResult('{"prompt":' + __import__("json").dumps(text) + ',"unresolved_critical":false,"needs_recheck":false,"risks":[]}')
        raise AssertionError(phase)

    monkeypatch.setattr("app.workflow.call_model", fake_model)
    with TestClient(app) as client:
        csrf = signin(client)
        project = client.post("/api/projects", json={"title": "Review", "brief": "A fox on snow."},
                              headers={"X-CSRF-Token": csrf}).json()
        pid = project["id"]
        client.put(f"/api/projects/{pid}/storyboard",
                   json={"shots": [{"id": "shot-1", "time_window": "0-8s", "visible_action": "A fox walks across snow."}]},
                   headers={"X-CSRF-Token": csrf})
        client.post(f"/api/projects/{pid}/storyboard/approve", headers={"X-CSRF-Token": csrf})
        response = client.post(f"/api/projects/{pid}/reviews", json={"budget_usd": 5}, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 200
        current = client.get(f"/api/projects/{pid}").json()
        assert current["review"]["status"] == "ready_for_approval"
        assert current["review"]["findings"]["rounds"] == 2


def test_persistent_critical_finding_blocks_approval_and_escalates(monkeypatch):
    called = []

    def fake_model(phase, prompt, media, **kwargs):
        called.append(phase)
        if phase == "prompt_draft":
            return ModelResult('{"prompt":"A fox changes identity mid-shot."}')
        if phase == "continuity":
            return ModelResult('{"findings":[{"severity":"critical","message":"Identity changes mid-shot"}]}')
        if phase in {"action_timing", "camera_visuals", "audio_dialogue", "challenge_review"}:
            return ModelResult('{"findings":[]}')
        if phase == "supervisor":
            return ModelResult('{"prompt":"A fox changes identity mid-shot.","unresolved_critical":true,"needs_recheck":true,"risks":["Identity conflict"]}')
        if phase == "critical_escalation":
            return ModelResult('{"resolved":false,"reason":"Creator decision required","risks":["Identity conflict"]}')
        raise AssertionError(phase)

    monkeypatch.setattr("app.workflow.call_model", fake_model)
    with TestClient(app) as client:
        csrf = signin(client)
        project = client.post("/api/projects", json={"title": "Critical", "brief": "A fox in snow."},
                              headers={"X-CSRF-Token": csrf}).json()
        pid = project["id"]
        client.put(f"/api/projects/{pid}/storyboard",
                   json={"shots": [{"id": "shot-1", "time_window": "0-8s", "visible_action": "A fox in snow."}]},
                   headers={"X-CSRF-Token": csrf})
        client.post(f"/api/projects/{pid}/storyboard/approve", headers={"X-CSRF-Token": csrf})
        assert client.post(f"/api/projects/{pid}/reviews", json={"budget_usd": 5}, headers={"X-CSRF-Token": csrf}).status_code == 200
        current = client.get(f"/api/projects/{pid}").json()
        assert current["review"]["status"] == "needs_changes"
        assert current["review"]["findings"]["rounds"] == 1
        assert called.count("continuity") == 1
        assert called.count("critical_escalation") == 1
        assert client.post(f"/api/projects/{pid}/prompt/approve", headers={"X-CSRF-Token": csrf}).status_code == 409


def test_invalid_model_json_is_retried_and_both_calls_are_recorded(monkeypatch):
    from app.db import Project, Usage
    from app.workflow import record_call

    prompts = []

    def fake_model(phase, prompt, media, **kwargs):
        prompts.append(prompt)
        body = '{"findings":[' if len(prompts) == 1 else '{"findings":[],"verdict":"clear"}'
        return ModelResult(body, input_tokens=100, output_tokens=40, model="claude-opus-5-5")

    monkeypatch.setattr("app.workflow.call_model", fake_model)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.test"))
        project = Project(owner_id=user.id, title="JSON retry", brief="A simple scene")
        db.add(project)
        db.commit()
        result = record_call(db, project, "challenge_review", "Review this prompt", [])
        rows = db.scalars(select(Usage).where(Usage.project_id == project.id).order_by(Usage.id)).all()
    assert result == {"findings": [], "verdict": "clear"}
    assert len(prompts) == 2 and "parsed as JSON" in prompts[1]
    assert [row.status for row in rows] == ["invalid_json", "completed"]
    assert all(row.estimated_cost_usd is not None for row in rows)



def test_gemini_specialist_json_retry_has_reasoning_headroom(monkeypatch):
    from app import workflow
    from app.db import Project, Usage
    from app.workflow import record_call

    limits = []

    def fake_model(phase, prompt, media, **kwargs):
        limits.append(kwargs["max_output_tokens"])
        body = '{"findings":[' if len(limits) == 1 else '{"findings":[],"verdict":"clear"}'
        return ModelResult(body, input_tokens=100, output_tokens=40, reasoning_tokens=2000,
                           model="gemini-3.8-flash")

    monkeypatch.setattr("app.workflow.call_model", fake_model)
    monkeypatch.setitem(workflow.MODEL_IDS, "camera_visuals", "gemini-3.8-flash")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.test"))
        project = Project(owner_id=user.id, title="Gemini JSON", brief="A simple scene")
        db.add(project)
        db.commit()
        result = record_call(db, project, "camera_visuals", "Review this prompt", [])
        rows = db.scalars(select(Usage).where(Usage.project_id == project.id).order_by(Usage.id)).all()
    assert result == {"findings": [], "verdict": "clear"}
    assert limits == [16384, 16384]
    assert [row.status for row in rows] == ["invalid_json", "completed"]




def test_gemini_max_tokens_retry_increases_output_allowance(monkeypatch):
    from app import workflow
    from app.db import Project
    from app.workflow import record_call

    limits = []

    def fake_model(phase, prompt, media, **kwargs):
        limits.append(kwargs["max_output_tokens"])
        if len(limits) == 1:
            return ModelResult('{"findings":[', model="gemini-3.8-flash", finish_reason="MAX_TOKENS")
        return ModelResult('{"findings":[],"verdict":"clear"}', model="gemini-3.8-flash")

    monkeypatch.setattr("app.workflow.call_model", fake_model)
    monkeypatch.setitem(workflow.MODEL_IDS, "audio_dialogue", "gemini-3.8-flash")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.test"))
        project = Project(owner_id=user.id, title="Gemini cutoff", brief="A simple scene")
        db.add(project)
        db.commit()
        result = record_call(db, project, "audio_dialogue", "Review this prompt", [])
    assert result == {"findings": [], "verdict": "clear"}
    assert limits == [16384, 32768]


def test_gemini_specialists_use_low_thinking(monkeypatch):
    from types import SimpleNamespace
    from google.genai import types
    from app import providers

    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text='{"findings":[]}', usage_metadata=None, candidates=[])

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(providers.genai, "Client", lambda **kwargs: SimpleNamespace(models=FakeModels()))
    providers._call_model_once("audio_dialogue", "Review the audio", [], max_output_tokens=16384)
    assert captured["config"].thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert captured["config"].max_output_tokens == 16384


def test_claude_specialist_retry_has_full_json_allowance(monkeypatch):
    from app import workflow
    from app.db import Project, Usage
    from app.workflow import record_call

    limits = []

    def fake_model(phase, prompt, media, **kwargs):
        limits.append(kwargs["max_output_tokens"])
        body = '{"findings":[' if len(limits) == 1 else '{"findings":[],"verdict":"clear"}'
        return ModelResult(body, input_tokens=16000, output_tokens=2200,
                           model="claude-sonnet-5-5", finish_reason="max_tokens" if len(limits) == 1 else "end_turn")

    monkeypatch.setattr("app.workflow.call_model", fake_model)
    monkeypatch.setitem(workflow.MODEL_IDS, "continuity", "claude-sonnet-5-5")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.test"))
        project = Project(owner_id=user.id, title="Claude JSON", brief="A simple scene")
        db.add(project)
        db.commit()
        result = record_call(db, project, "continuity", "Review this prompt", [])
        rows = db.scalars(select(Usage).where(Usage.project_id == project.id).order_by(Usage.id)).all()
    assert result == {"findings": [], "verdict": "clear"}
    assert limits == [8192, 16384]
    assert [row.status for row in rows] == ["invalid_json", "completed"]



def test_failed_review_reuses_completed_first_round_steps(monkeypatch):
    import json
    calls = []
    first_continuity = True
    prompt = "A fox crosses a snowy field in one wide shot, from 0 to 8 seconds."

    def fake_model(phase, model_prompt, media, **kwargs):
        nonlocal first_continuity
        calls.append(phase)
        if phase == "continuity" and first_continuity:
            first_continuity = False
            raise RuntimeError("Temporary continuity failure")
        if phase == "prompt_draft":
            return ModelResult(json.dumps({"prompt": prompt}))
        if phase == "supervisor":
            return ModelResult(json.dumps({
                "prompt": prompt, "unresolved_critical": False, "needs_recheck": False,
                "risks": [], "decisions": [],
            }))
        if phase in {"action_timing", "camera_visuals", "audio_dialogue", "continuity", "challenge_review"}:
            return ModelResult('{"findings":[],"verdict":"clear"}')
        raise AssertionError(phase)

    monkeypatch.setattr("app.workflow.call_model", fake_model)
    with TestClient(app) as client:
        csrf = signin(client)
        headers = {"X-CSRF-Token": csrf}
        project = client.post("/api/projects", json={"title": "Resume review", "brief": "A fox crosses a snowy field."}, headers=headers).json()
        pid = project["id"]
        saved = client.put(f"/api/projects/{pid}/storyboard", headers=headers, json={
            "shots": [{"id": "shot-1", "time_window": "0-8s", "visible_action": "Fox crosses field.", "reference_media_ids": []}],
            "open_questions": [],
        })
        assert saved.status_code == 200, saved.text
        assert client.post(f"/api/projects/{pid}/storyboard/approve", headers=headers).status_code == 200
        assert client.post(f"/api/projects/{pid}/reviews", headers=headers).status_code == 200
        failed = client.get(f"/api/projects/{pid}").json()
        assert failed["review"]["status"] == "failed"
        assert "_checkpoint" not in failed["review"]["findings"]
        assert client.post(f"/api/projects/{pid}/reviews", headers=headers).status_code == 200
        resumed = client.get(f"/api/projects/{pid}").json()
        assert resumed["review"]["status"] == "ready_for_approval"
        assert calls.count("prompt_draft") == 1
        assert calls.count("action_timing") == 1
        assert calls.count("camera_visuals") == 1
        assert calls.count("audio_dialogue") == 1
        assert calls.count("continuity") == 2


def test_preview_progress_cost_and_restart_recovery(monkeypatch):
    from app import main as app_main
    from app.db import Project, Usage

    monkeypatch.setattr(app_main, "_preview_task", lambda *args: None)
    with TestClient(app) as client:
        csrf = signin(client)
        headers = {"X-CSRF-Token": csrf}
        project = client.post("/api/projects", json={"title": "Preview progress", "brief": "Two simple shots."}, headers=headers).json()
        pid = project["id"]
        saved = client.put(f"/api/projects/{pid}/storyboard", headers=headers, json={
            "shots": [{"id": "shot-1", "visible_action": "A light appears."},
                      {"id": "shot-2", "visible_action": "The light moves."}],
            "open_questions": [],
        })
        assert saved.status_code == 200
        started = client.post(f"/api/projects/{pid}/previews", headers=headers,
                              json={"shot_ids": ["shot-1", "shot-2"]})
        assert started.status_code == 200
        current = client.get(f"/api/projects/{pid}").json()
        assert current["preview_progress"]["total"] == 2
        assert current["preview_progress"]["completed"] == 0
        assert "_preview_job" not in current["storyboard"]
        with SessionLocal() as db:
            record = db.get(Project, pid)
            app_main._update_preview_progress(db, record, completed=1, current_index=2,
                                              current_shot="shot-2", completed_shot_ids=["shot-1"])
            db.add(Usage(project_id=pid, phase="preview", model="gemini-3.1-flash-image",
                         status="running", estimated_cost_usd=0.067))
            db.commit()
        updated = client.get(f"/api/projects/{pid}").json()
        assert updated["preview_progress"]["completed"] == 1
        assert updated["preview_progress"]["current_shot"] == "shot-2"
        assert updated["total_estimated_cost_usd"] == 0.067
    with TestClient(app) as client:
        signin(client)
        interrupted = client.get(f"/api/projects/{pid}").json()
        assert interrupted["status"] == "storyboard_draft"
        assert interrupted["preview_progress"]["status"] == "interrupted"
