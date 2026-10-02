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


def audit_reply(prompt):
    import json
    prior = json.loads(prompt.split("PRIOR FINDINGS:\n", 1)[1].split("\nREPAIRS:", 1)[0])
    final = prompt.split("FINAL PROMPT:\n", 1)[1]
    fixed = "0" in final and "8" in final
    return ModelResult(json.dumps({"findings": [], "verdict": "checked", "resolutions": [
        {"finding_id": f["id"], "status": "resolved" if fixed else "open",
         "reason": "Checked the explicit time and identity constraint", "prompt_excerpt": final if fixed else ""}
        for f in prior]}))


def test_sol_61_cost_includes_discounted_cached_tokens():
    from app.providers import estimate_cost, preflight_estimate

    # 500k ordinary input, 500k cached input, 100k output.
    assert estimate_cost("gpt-6.1-sol", 1_000_000, 100_000, 500_000) == 2.05
    assert preflight_estimate("gpt-6.1-sol", "A short prompt", 1000) == 0.012


def test_provider_overload_retries_same_model_without_downgrade(monkeypatch):
    from app import providers
    import pytest
    calls = []
    class Overloaded(Exception):
        status_code = 503
    def fake_call(phase, prompt, media, **kwargs):
        calls.append(kwargs.get("model_override") or providers.MODEL_IDS[phase])
        raise Overloaded()
    monkeypatch.setattr(providers, "_call_model_once", fake_call)
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)
    with pytest.raises(Overloaded):
        providers.call_model("camera_visuals", "test")
    assert calls == ["gemini-3.8-flash"] * 3



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
        if phase == "final_verification" and "REPAIRS:" in prompt:
            return audit_reply(prompt)
        if phase == "brief_intake":
            return ModelResult('{"intent":"Fox","must_haves":[],"uncertainties":[],"reference_roles":[]}')
        if phase == "storyboard":
            return ModelResult('{"duration_seconds":8,"shots":[{"id":"shot-1","time_window":"0-8s","visible_action":"Fox crosses field","reference_media_ids":[]}],"open_questions":[]}')
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
        if phase == "final_verification" and "REPAIRS:" in prompt:
            return audit_reply(prompt)
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
        assert current["review"]["findings"]["rounds"] == 1


def test_persistent_critical_finding_blocks_approval_without_stalled_repetition(monkeypatch):
    called = []

    def fake_model(phase, prompt, media, **kwargs):
        if phase == "final_verification" and "REPAIRS:" in prompt:
            return audit_reply(prompt)
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
        assert called.count("supervisor") == 1
        assert "critical_escalation" not in called
        assert client.post(f"/api/projects/{pid}/prompt/approve", headers={"X-CSRF-Token": csrf}).status_code == 409


def test_blocked_review_can_be_revised_by_agents(monkeypatch):
    import json

    drafts = []
    old_prompt = "A fox crosses snow."
    new_prompt = "A fox crosses snow from 0 to 8 seconds, keeping its appearance."

    def fake_model(phase, prompt, media, **kwargs):
        if phase == "final_verification" and "REPAIRS:" in prompt:
            return audit_reply(prompt)
        if phase == "prompt_draft":
            drafts.append(prompt)
            return ModelResult(json.dumps({"prompt": old_prompt if len(drafts) == 1 else new_prompt}))
        if phase == "continuity" and len(drafts) == 1:
            return ModelResult('{"findings":[{"severity":"major","message":"Continuity needs timing"}]}')
        if phase in {"action_timing", "camera_visuals", "audio_dialogue", "continuity", "challenge_review"}:
            return ModelResult('{"findings":[]}')
        if phase == "supervisor":
            current = old_prompt if len(drafts) == 1 else new_prompt
            return ModelResult(json.dumps({"prompt": current, "unresolved_critical": False,
                                           "needs_recheck": False, "risks": [], "decisions": []}))
        raise AssertionError(phase)

    monkeypatch.setattr("app.workflow.call_model", fake_model)
    with TestClient(app) as client:
        csrf = signin(client)
        headers = {"X-CSRF-Token": csrf}
        project = client.post("/api/projects", json={"title": "Revision", "brief": "Fox crosses snow."}, headers=headers).json()
        pid = project["id"]
        client.put(f"/api/projects/{pid}/storyboard", headers=headers, json={
            "shots": [{"id": "shot-1", "time_window": "0-8s", "visible_action": "Fox crosses snow."}],
        })
        client.post(f"/api/projects/{pid}/storyboard/approve", headers=headers)
        assert client.post(f"/api/projects/{pid}/reviews", headers=headers).status_code == 200
        assert client.get(f"/api/projects/{pid}").json()["review"]["status"] == "needs_changes"
        assert client.post(f"/api/projects/{pid}/reviews", headers=headers).status_code == 200
        revised = client.get(f"/api/projects/{pid}").json()
        assert revised["review"]["status"] == "ready_for_approval"
        assert revised["prompt"] == new_prompt
        assert old_prompt in drafts[1]
        assert "Continuity needs timing" in drafts[1]


def test_last_round_revision_gets_final_audit_instead_of_stale_blocker(monkeypatch):
    import json
    from app import workflow
    from app.providers import MODEL_IDS

    assert MODEL_IDS["challenge_review"] == "gpt-6.1-sol"
    monkeypatch.setattr(workflow, "MAX_REVIEW_ROUNDS", 2)
    calls = []

    def fake_model(phase, prompt, media, **kwargs):
        if phase == "final_verification" and "REPAIRS:" in prompt:
            return audit_reply(prompt)
        calls.append(phase)
        if phase == "prompt_draft":
            return ModelResult('{"prompt":"A fox crosses snow."}')
        if phase == "action_timing":
            return ModelResult('{"findings":[{"severity":"major","message":"Timing missing"}]}')
        if phase in {"camera_visuals", "audio_dialogue", "continuity", "challenge_review", "final_verification"}:
            return ModelResult('{"findings":[],"verdict":"clear"}')
        if phase == "supervisor":
            number = calls.count("supervisor")
            return ModelResult(json.dumps({
                "prompt": f"A fox crosses snow from 0 to 8 seconds. Revision {number}.",
                "unresolved_critical": False, "needs_recheck": True, "risks": [],
            }))
        raise AssertionError(phase)

    monkeypatch.setattr(workflow, "call_model", fake_model)
    with TestClient(app) as client:
        csrf = signin(client)
        headers = {"X-CSRF-Token": csrf}
        project = client.post("/api/projects", json={"title": "Final audit", "brief": "Fox crosses snow."}, headers=headers).json()
        pid = project["id"]
        client.put(f"/api/projects/{pid}/storyboard", headers=headers, json={
            "shots": [{"id": "shot-1", "time_window": "0-8s", "visible_action": "Fox crosses snow."}],
        })
        client.post(f"/api/projects/{pid}/storyboard/approve", headers=headers)
        assert client.post(f"/api/projects/{pid}/reviews", headers=headers).status_code == 200
        review = client.get(f"/api/projects/{pid}").json()["review"]
        assert review["status"] == "ready_for_approval"
        assert review["findings"]["quality_gate"]["serious_findings"] == []
        assert len(review["findings"]["repair_history"]) == 1


def test_existing_last_round_blocker_uses_only_targeted_audit(monkeypatch):
    from app.db import Review

    calls = []

    def fake_model(phase, prompt, media, **kwargs):
        if phase == "final_verification" and "REPAIRS:" in prompt:
            return audit_reply(prompt)
        calls.append(phase)
        assert phase == "final_verification"
        assert "MCL40 front tires" in prompt
        return ModelResult('{"findings":[],"verdict":"Prior finding is fixed"}')

    monkeypatch.setattr("app.workflow.call_model", fake_model)
    with TestClient(app) as client:
        csrf = signin(client)
        headers = {"X-CSRF-Token": csrf}
        project = client.post("/api/projects", json={"title": "Old blocker", "brief": "MCL40 front tires."}, headers=headers).json()
        pid = project["id"]
        client.put(f"/api/projects/{pid}/storyboard", headers=headers, json={
            "shots": [{"id": "shot-1", "time_window": "0-8s", "visible_action": "Show MCL40 front tires."}],
        })
        client.post(f"/api/projects/{pid}/storyboard/approve", headers=headers)
        with SessionLocal() as db:
            saved = db.get(Project, pid)
            prior = Review(project_id=pid, storyboard_version=saved.storyboard_version,
                           status="needs_changes", final_prompt="Show MCL40 front tires from 0 to 8 seconds.",
                           findings={"rounds": 3, "supervisor": {"unresolved_critical": False},
                                     "quality_gate": {"must_recheck": True, "round_limit_reached": True},
                                     "challenge_review": {"findings": [{"severity": "major", "message": "Front tires missing"}]}})
            db.add(prior)
            saved.status = "needs_changes"
            db.commit()
        assert client.post(f"/api/projects/{pid}/reviews", headers=headers).status_code == 200
        result = client.get(f"/api/projects/{pid}").json()["review"]
        assert result["status"] == "ready_for_approval"
        assert result["findings"]["quality_gate"]["serious_findings"] == []
        assert calls == ["final_verification"]


def test_source_conflict_cannot_be_cleared_by_prompt_only_audit(monkeypatch):
    from app.db import Review

    monkeypatch.setattr("app.workflow.call_model", lambda *args, **kwargs: ModelResult(
        '{"findings":[],"verdict":"Prompt text is clear"}'))
    with TestClient(app) as client:
        csrf = signin(client)
        headers = {"X-CSRF-Token": csrf}
        project = client.post("/api/projects", json={"title": "Source conflict", "brief": "Car hits the kerb."}, headers=headers).json()
        pid = project["id"]
        client.put(f"/api/projects/{pid}/storyboard", headers=headers, json={
            "shots": [{"id": "shot-1", "time_window": "0-8s", "visible_action": "Car hits the kerb."}],
        })
        client.post(f"/api/projects/{pid}/storyboard/approve", headers=headers)
        with SessionLocal() as db:
            saved = db.get(Project, pid)
            prior = Review(project_id=pid, storyboard_version=saved.storyboard_version,
                           status="needs_changes", final_prompt="Car hits the kerb.",
                           findings={"rounds": 3, "supervisor": {"unresolved_critical": False},
                                     "quality_gate": {"must_recheck": True, "round_limit_reached": True},
                                     "camera_visuals": {"findings": [{"severity": "major",
                                         "message": "Reference video contains no kerb contact.",
                                         "source_conflict": True}]}})
            db.add(prior)
            saved.status = "needs_changes"
            db.commit()
        assert client.post(f"/api/projects/{pid}/reviews", headers=headers).status_code == 200
        current = client.get(f"/api/projects/{pid}").json()["review"]
        assert current["status"] == "needs_changes"
        assert current["findings"]["quality_gate"]["source_conflicts"]
        assert client.post(f"/api/projects/{pid}/reviews", headers=headers).status_code == 409
        client.put(f"/api/projects/{pid}/storyboard", headers=headers, json={
            "shots": [{"id": "shot-1", "time_window": "0-8s", "visible_action": "Car passes between two rivals."}],
        })
        revised_project = client.get(f"/api/projects/{pid}").json()
        assert revised_project["review"] is None
        client.post(f"/api/projects/{pid}/storyboard/approve", headers=headers)
        assert client.post(f"/api/projects/{pid}/reviews", headers=headers).status_code == 200


def test_invalid_model_json_is_retried_and_both_calls_are_recorded(monkeypatch):
    from app.db import Project, Usage
    from app.workflow import record_call

    prompts = []

    def fake_model(phase, prompt, media, **kwargs):
        if phase == "final_verification" and "REPAIRS:" in prompt:
            return audit_reply(prompt)
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
    assert len(prompts) == 2 and "invalid" in prompts[1]
    assert [row.status for row in rows] == ["invalid_json", "completed"]
    assert all(row.estimated_cost_usd is not None for row in rows)



def test_gemini_specialist_json_retry_has_reasoning_headroom(monkeypatch):
    from app import workflow
    from app.db import Project, Usage
    from app.workflow import record_call

    limits = []

    def fake_model(phase, prompt, media, **kwargs):
        if phase == "final_verification" and "REPAIRS:" in prompt:
            return audit_reply(prompt)
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
    assert limits == [16384, 32768]
    assert [row.status for row in rows] == ["invalid_json", "completed"]




def test_gemini_max_tokens_retry_increases_output_allowance(monkeypatch):
    from app import workflow
    from app.db import Project
    from app.workflow import record_call

    limits = []

    def fake_model(phase, prompt, media, **kwargs):
        if phase == "final_verification" and "REPAIRS:" in prompt:
            return audit_reply(prompt)
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


def test_gemini_specialists_use_high_thinking(monkeypatch):
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
    assert captured["config"].thinking_config.thinking_level == types.ThinkingLevel.HIGH
    assert captured["config"].max_output_tokens == 16384


def test_claude_specialist_retry_has_full_json_allowance(monkeypatch):
    from app import workflow
    from app.db import Project, Usage
    from app.workflow import record_call

    limits = []

    def fake_model(phase, prompt, media, **kwargs):
        if phase == "final_verification" and "REPAIRS:" in prompt:
            return audit_reply(prompt)
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
    assert limits == [16384, 32768]
    assert [row.status for row in rows] == ["invalid_json", "completed"]



def test_failed_review_reuses_completed_first_round_steps(monkeypatch):
    import json
    calls = []
    first_continuity = True
    prompt = "A fox crosses a snowy field in one wide shot, from 0 to 8 seconds."

    def fake_model(phase, model_prompt, media, **kwargs):
        if phase == "final_verification":
            return audit_reply(model_prompt)
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


def test_quality_specialists_run_concurrently_and_keep_usage(monkeypatch):
    import threading
    import json
    from app import workflow
    from app.db import Review, Usage
    barrier = threading.Barrier(4)
    def fake(phase, prompt, media, **kwargs):
        if phase in workflow.SPECIALISTS:
            barrier.wait(timeout=5)  # Sequential execution would break this barrier.
            return ModelResult('{"findings":[],"verdict":"clear"}', input_tokens=100, output_tokens=20)
        if phase == "prompt_draft":
            return ModelResult('{"prompt":"A fox crosses snow from 0 to 8 seconds."}')
        if phase == "supervisor":
            return ModelResult('{"prompt":"A fox crosses snow from 0 to 8 seconds.","unresolved_critical":false,"repairs":[]}')
        if phase == "final_verification":
            return ModelResult('{"findings":[],"resolutions":[],"verdict":"clear"}')
        raise AssertionError(phase)
    monkeypatch.setattr(workflow, "call_model", fake)
    with SessionLocal() as db:
        project, review = quality_project(db)
        workflow.run_review(db, project, [], review)
        assert review.status == "ready_for_approval", review.error
        rows = list(db.scalars(select(Usage).where(Usage.review_id == review.id)))
        assert len(rows) == 7
        assert all(x.effort == "high" and x.status == "completed" for x in rows)
        assert set(review.findings["specialist_results"]) == set(workflow.SPECIALISTS)


def quality_project(db):
    from app.db import Review, utcnow
    user = db.scalar(select(User).where(User.email == "admin@example.test"))
    project = Project(owner_id=user.id, title="Quality check", brief="A fox crosses snow.", duration=8,
                      storyboard={"shots":[{"id":"s1","time_window":"0–8","visible_action":"Fox crosses snow"}]},
                      storyboard_version=1, storyboard_approved_at=utcnow(), status="review_running")
    db.add(project); db.commit()
    review = Review(project_id=project.id, storyboard_version=1, budget_usd=5)
    db.add(review); db.commit()
    return project, review


def test_quality_batch_reserves_budget_before_any_specialist(monkeypatch):
    from app import workflow
    from app.db import Usage
    calls = []
    def fake(phase, *args, **kwargs):
        calls.append(phase)
        return ModelResult('{"prompt":"A fox crosses snow."}')
    monkeypatch.setattr(workflow, "call_model", fake)
    with SessionLocal() as db:
        project, review = quality_project(db)
        review.budget_usd = .22
        db.commit()
        workflow.run_review(db, project, [], review)
        assert review.status == "failed" and "Budget" in review.error
        assert calls == ["prompt_draft"]
        cancelled = list(db.scalars(select(Usage).where(Usage.review_id == review.id, Usage.status == "cancelled")))
        assert cancelled and all(x.estimated_cost_usd == 0 for x in cancelled)


def test_quality_clean_audit_cannot_silently_erase_major_finding(monkeypatch):
    from app import workflow
    def fake(phase, *args, **kwargs):
        if phase == "prompt_draft": return ModelResult('{"prompt":"A fox crosses snow."}')
        if phase == "action_timing": return ModelResult('{"findings":[{"severity":"major","message":"Timing missing"}]}')
        if phase == "supervisor": return ModelResult('{"prompt":"A fox crosses snow from 0 to 8 seconds.","unresolved_critical":false}')
        return ModelResult('{"findings":[],"resolutions":[],"verdict":"clear"}')
    monkeypatch.setattr(workflow, "call_model", fake)
    with SessionLocal() as db:
        project, review = quality_project(db)
        workflow.run_review(db, project, [], review)
        assert review.status == "needs_changes"
        assert review.findings["quality_gate"]["serious_findings"]
        assert review.findings["quality_gate"]["stalled"]


def test_quality_timeline_rejects_gaps_overruns_and_nan():
    import pytest
    from app.workflow import validate_storyboard, PIPELINE_VERSION, time_window
    project = Project(duration=8, storyboard={"_pipeline_version":PIPELINE_VERSION,"duration_seconds":8,"shots":[
        {"id":"s1","visible_action":"Walk","time_window":"0–4"},
        {"id":"s2","visible_action":"Stop","time_window":"5–8"}]})
    assert any("continuous" in x for x in validate_storyboard(project, []))
    project.storyboard["shots"][1]["time_window"] = "4–9"
    assert any("add up" in x for x in validate_storyboard(project, []))
    project.storyboard["shots"][1]["time_window"] = "4–8"
    assert validate_storyboard(project, []) == []
    with pytest.raises(ValueError): time_window("0–nan")
    assert time_window("00:12–00:14.134") == (12,14.134)


def test_quality_media_analysis_precedes_director_and_is_reused(monkeypatch):
    import json
    from types import SimpleNamespace
    from app import workflow
    from app.db import Usage
    calls=[]
    media=[SimpleNamespace(id=77,kind="image",mime="image/png",filename="ref.png",storage_key="test",size=100,role="identity",duration_seconds=None,width=100,height=100)]
    def fake(phase,prompt,inputs,**kwargs):
        calls.append(phase)
        if phase=="media_analysis": return ModelResult('{"sources":[{"media_id":77,"observations":[{"observation":"orange fur"}]}]}')
        if phase=="brief_intake": return ModelResult('{"intent":"fox"}')
        assert phase=="storyboard" and "orange fur" in prompt
        return ModelResult(json.dumps({"duration_seconds":12,"duration_rationale":"Readable motion and payoff","shots":[
            {"id":"s1","time_window":"0–12","visible_action":"Fox walks","reference_media_ids":[77]}]}))
    monkeypatch.setattr(workflow,"call_model",fake)
    with SessionLocal() as db:
        project,_=quality_project(db)
        project.duration_mode="agent"
        workflow.generate_storyboard(db,project,media)
        assert project.duration==12
        assert calls==["media_analysis","brief_intake","storyboard"]
        workflow.generate_storyboard(db,project,media)
        assert calls.count("media_analysis")==1
        media[0].role="lighting only"
        workflow.generate_storyboard(db,project,media)
        assert calls.count("media_analysis")==2
        rows=list(db.scalars(select(Usage).where(Usage.project_id==project.id)))
        assert any(x.phase=="media_analysis" for x in rows)


def test_quality_effort_sent_to_openai_and_anthropic(monkeypatch):
    from types import SimpleNamespace
    from app import providers
    captures={}
    def openai_call(**kwargs):
        captures['openai']=kwargs
        return SimpleNamespace(output_text='{}',usage=SimpleNamespace(),incomplete_details=None)
    def claude_call(**kwargs):
        captures['claude']=kwargs
        return SimpleNamespace(content=[],usage=SimpleNamespace(input_tokens=0,output_tokens=0),stop_reason='end_turn')
    monkeypatch.setenv('OPENAI_API_KEY','test')
    monkeypatch.setenv('ANTHROPIC_API_KEY','test')
    monkeypatch.setattr(providers,'OpenAI',lambda **kw:SimpleNamespace(responses=SimpleNamespace(create=openai_call)))
    monkeypatch.setattr(providers,'Anthropic',lambda **kw:SimpleNamespace(messages=SimpleNamespace(create=claude_call)))
    providers._call_model_once('supervisor','Return JSON',[],max_output_tokens=16384)
    providers._call_model_once('storyboard','Return JSON',[],max_output_tokens=16384)
    assert captures['openai']['model']=='gpt-6-astra'
    assert captures['openai']['reasoning']=={'effort':'high'}
    assert captures['claude']['model']=='claude-opus-5-5'
    assert captures['claude']['output_config']=={'effort':'high'}
    assert captures['claude']['thinking']=={'type':'adaptive'}


def test_quality_source_frames_are_timestamped_bounded_and_removed(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from app import evidence
    subprocess.run(['ffmpeg','-loglevel','error','-f','lavfi','-i','color=c=red:s=64x64:r=10','-t','0.5',str(tmp_path/'clip.mp4')],check=True)
    monkeypatch.setattr(evidence,'MEDIA_ROOT',tmp_path)
    media=[SimpleNamespace(id=1,kind='video',duration_seconds=.5,storage_key='clip.mp4',filename='clip.mp4',role='camera')]
    with evidence.reference_frames(media) as frames:
        assert 2<=len(frames)<=24
        assert '0.000s' in frames[0].filename
        paths=[tmp_path/f.storage_key for f in frames]
        assert all(p.exists() for p in paths)
    assert not any(p.exists() for p in paths)
    assert (tmp_path/'clip.mp4').exists()

# Production profiles: real workflow gates, isolated context and backward compatibility.
def production_prompt(audio='No background music. Dialogue and SFX only.'):
    from app.production import HEADINGS
    content = ['A fox crosses snow, fixed camera.', 'No external references.',
               '0-8s: the fox walks and settles.', audio, 'Grounded weight and contact.']
    return '\n\n'.join(h+'\n'+v for h,v in zip(HEADINGS,content))


def test_production_library_scope_and_conflicts():
    from app.production import production_context, library, contract_errors
    film=Project(production_settings={'profile':'film_scene','audio':'sfx_dialogue','style_id':'STYLE_12','camera_id':'CAM_01'})
    short=Project(production_settings={'profile':'short_ad','audio':'score','morphing':'allowed','style_id':'STYLE_12'})
    a=production_context(film,'audio_dialogue'); b=production_context(short,'audio_dialogue')
    assert not a['sound_examples'] and b['sound_examples']
    assert 'no background music' in a['audio_rule'].lower()
    assert 'transformation_rule' not in a and 'allowed' in b['transformation_rule']
    assert 'movement_examples' not in a
    camera=production_context(film,'camera_visuals')
    assert camera['movement_examples'][0]['id']=='CAM_01'
    assert 'shot' not in camera['style_examples'][0]['visual_traits'].lower()
    assert len(library()['movements'])==46
    assert {x['matching_visual_id'] for x in library()['audio']} <= {x['id'] for x in library()['styles']}
    assert contract_errors(short,production_prompt('No morphing. No background music.'),[])
    assert contract_errors(film,production_prompt('A piano score.'),[])
    assert not contract_errors(film,production_prompt(),[])


def test_production_five_blocks_aliases_and_placeholders():
    from app.production import contract_errors, HEADINGS
    from types import SimpleNamespace
    p=Project(production_settings={'profile':'short_ad'})
    media=[SimpleNamespace(id=9,kind='image'),SimpleNamespace(id=20,kind='video')]
    text=production_prompt().replace('No external references.','@Image1 identity; @Video1 camera and blocking.')
    assert not contract_errors(p,text,media)
    assert contract_errors(p,text.replace('@Video1','@Video2'),media)
    assert contract_errors(p,text.replace('Grounded weight','[ENTITY_A] weight'),media)
    assert contract_errors(p,text.replace(HEADINGS[1],HEADINGS[0]),media)
    assert contract_errors(p,text.replace(HEADINGS[4]+'\nGrounded weight and contact.',HEADINGS[4]),media)


def test_production_api_switch_requires_replan_and_preserves_legacy_fields():
    with TestClient(app) as client:
        csrf=signin(client); headers={'X-CSRF-Token':csrf}
        assert client.get('/api/production-library').status_code==200
        base={'title':'Film','brief':'A quiet forest scene','production_settings':{'profile':'film_scene','audio':'sfx_dialogue'}}
        p=client.post('/api/projects',json=base,headers=headers).json()
        assert p['production_settings']['profile']=='film_scene'
        invalid={**base,'production_settings':{'profile':'short_ad','camera_id':'NONEXISTENT'}}
        assert client.post('/api/projects',json=invalid,headers=headers).status_code==422
        with SessionLocal() as db:
            row=db.get(Project,p['id']); row.storyboard={'shots':[{'id':'s1','visible_action':'Walk','time_window':'0-8'}]}; row.prompt='Old prompt'; db.commit()
        changed=client.put(f"/api/projects/{p['id']}",headers=headers,json={**base,'production_settings':{'profile':'short_ad','morphing':'allowed'}}).json()
        assert changed['production_settings']['profile']=='short_ad'
        assert changed['storyboard_version']==1 and not changed['prompt']
        assert client.post(f"/api/projects/{p['id']}/storyboard/approve",headers=headers).status_code in {409,422}
        assert client.post(f"/api/projects/{p['id']}/reviews",headers=headers).status_code==409
        # Older API clients cannot erase production choices by omitting the new field.
        kept=client.put(f"/api/projects/{p['id']}",headers=headers,json={'title':'Renamed','brief':'Forest'}).json()
        assert kept['production_settings']['profile']=='short_ad'
        client.post('/api/auth/logout',headers=headers)
        assert client.get('/api/production-library').status_code==401


def test_production_linter_failures_are_repaired_before_approval(monkeypatch):
    import json
    from app import workflow
    from app.production import library
    calls=[]
    def fake(phase,prompt,media,**kwargs):
        calls.append((phase,prompt))
        if phase=='prompt_draft': return ModelResult(json.dumps({'prompt':'Unstructured draft'}))
        if phase=='supervisor':
            assert 'five exact production blocks' in prompt
            return ModelResult(json.dumps({'prompt':production_prompt(),'unresolved_critical':False}))
        return ModelResult('{"findings":[],"resolutions":[],"verdict":"clear"}')
    monkeypatch.setattr(workflow,'call_model',fake)
    with SessionLocal() as db:
        project,review=quality_project(db)
        project.production_settings={'profile':'film_scene','audio':'sfx_dialogue'}
        project.storyboard=profile_storyboard(project.storyboard)
        review.budget_usd=20;db.commit()
        workflow.run_review(db,project,[],review)
        assert review.status=='ready_for_approval',review.error
        assert review.findings['production_snapshot']['creator_choices']['profile']=='film_scene'
        assert all('production-v1-' in prompt for _,prompt in calls)
        signature=workflow.review_checkpoint_signature(db,project,[])
        project.production_settings={'profile':'short_ad','audio':'score'}
        assert signature != workflow.review_checkpoint_signature(db,project,[])


def test_production_empty_clean_audit_cannot_approve_bad_format(monkeypatch):
    import json
    from app import workflow
    from app.production import library
    def fake(phase,*args,**kwargs):
        if phase=='prompt_draft': return ModelResult('{"prompt":"Unstructured"}')
        if phase=='supervisor': return ModelResult('{"prompt":"Unstructured","unresolved_critical":false}')
        return ModelResult('{"findings":[],"resolutions":[],"verdict":"clear"}')
    monkeypatch.setattr(workflow,'call_model',fake)
    with SessionLocal() as db:
        project,review=quality_project(db);project.production_settings={'profile':'short_ad'}
        project.storyboard=profile_storyboard(project.storyboard);review.budget_usd=20;db.commit()
        workflow.run_review(db,project,[],review)
        assert review.status=='needs_changes' and not review.linter['passed']


def test_production_additive_migration_preserves_existing_projects(tmp_path,monkeypatch):
    from sqlalchemy import create_engine, text
    from app import db as database
    temporary=create_engine('sqlite:///'+str(tmp_path/'old.db'))
    monkeypatch.setattr(database,'engine',temporary)
    database.init_db()
    with temporary.begin() as connection:
        connection.execute(text('ALTER TABLE projects DROP COLUMN production_settings'))
        connection.execute(text("INSERT INTO projects (id,owner_id,title,brief,must_haves,task_type,duration,resolution,ratio,storyboard_version,prompt,status,created_at,updated_at) VALUES (1,1,'Existing','Keep me','','text_to_video',8,'720p','16:9',0,'','draft',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    database.init_db();database.init_db()
    with temporary.connect() as connection:
        row=connection.execute(text('SELECT brief,production_settings FROM projects WHERE id=1')).one()
        assert row==('Keep me',None)
    temporary.dispose()


def profile_storyboard(board):
    from app.production import library
    return {**board, '_production_version': library()['version'], 'shots': [
        {**shot, 'style_id': 'STYLE_10', 'camera_id': 'CAM_01', 'production_method': 'direct',
         'production_reason': 'Simple stationary framing suits this action.',
         'acceptance_criteria': ['The fox stays in frame.']} for shot in board['shots']]}


def test_production_storyboard_catalog_and_version_gates():
    from app import workflow
    from app.production import production_context
    p = Project(production_settings={'profile':'short_ad'}, storyboard=profile_storyboard(
        {'shots':[{'id':'s1','time_window':'0-8','visible_action':'A fox walks.'}]}))
    assert not workflow.validate_storyboard(p, [])
    assert production_context(p,'camera_visuals')['movement_examples'][0]['id']=='CAM_01'
    p.storyboard['shots'][0]['camera_id']='CAM_UNKNOWN'
    assert any('valid camera_id' in e for e in workflow.validate_storyboard(p, []))
    p.storyboard['_production_version']='older-library'
    assert any('new storyboard' in e for e in workflow.validate_storyboard(p, []))


def test_production_director_uses_profile_and_selected_library(monkeypatch):
    import json
    from app import workflow
    def fake(phase,prompt,media,**kwargs):
        assert 'production-v1-' in prompt
        if phase=='brief_intake': return ModelResult('{"intent":"A fox walks"}')
        assert phase=='storyboard' and 'available_choices' in prompt and 'short_ad' in prompt
        return ModelResult(json.dumps(profile_storyboard({'duration_seconds':8,'shots':[
            {'id':'s1','time_window':'0-8','visible_action':'A fox walks.'}]})))
    monkeypatch.setattr(workflow,'call_model',fake)
    with SessionLocal() as db:
        p,_=quality_project(db)
        p.production_settings={'profile':'short_ad','camera_id':'CAM_01'}
        db.commit()
        result=workflow.generate_storyboard(db,p,[])
        assert result['production_plan']['profile']=='short_ad'
        assert not workflow.validate_storyboard(p,[])
        assert not p.storyboard_approved_at
