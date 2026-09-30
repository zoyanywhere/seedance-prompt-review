import base64
import json
import os
import re
import secrets
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Invitation, LoginSession, Media, Preview, Project, Review, SessionLocal, Usage, User, init_db, utcnow
from .providers import MEDIA_ROOT, MODEL_IDS
from .security import COOKIE_NAME, SECURE_COOKIES, SESSION_HOURS, current_user, digest, get_db, hash_password, new_login, require_admin, require_csrf, throttle, token, verify_password
from .workflow import generate_storyboard, normalize_storyboard_references, run_review, validate_settings, validate_storyboard

STATIC_ROOT = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PREVIEW_ESTIMATED_COST_USD = 0.067


@asynccontextmanager
async def lifespan(_app: FastAPI):
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    init_db()
    # Interrupted workers never appear successful after a container restart.
    with SessionLocal() as db:
        for review in db.scalars(select(Review).where(Review.status == "running")):
            review.status = "failed"
            review.phase = "interrupted"
            review.error = "Review interrupted by server restart; start a new review."
            review.completed_at = utcnow()
        for project in db.scalars(select(Project).where(Project.status == "preview_running")):
            job = {**(project.storyboard or {}).get("_preview_job", {}), "status": "interrupted",
                   "error": "Image generation stopped when the server restarted. You can start it again."}
            project.storyboard = {**(project.storyboard or {}), "_preview_job": job}
            project.status = "storyboard_draft"
        db.commit()
    yield


app = FastAPI(title="Zoyanywhere Seedance Prompt Review", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "public, max-age=300"
    return response


class LoginIn(BaseModel):
    email: str
    password: str


class RegisterIn(BaseModel):
    token: str
    name: str = Field(min_length=1, max_length=120)
    password: str


class InviteIn(BaseModel):
    email: str
    expires_hours: int = Field(default=72, ge=1, le=168)


class ProjectIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    brief: str = Field(min_length=1, max_length=12000)
    must_haves: str = Field(default="", max_length=5000)
    task_type: str = "text_to_video"
    duration: int = 8
    resolution: str = "720p"
    ratio: str = "16:9"


class StoryboardIn(BaseModel):
    shots: list[dict] = Field(min_length=1, max_length=20)
    open_questions: list[str] = []


class MediaRoleIn(BaseModel):
    role: str = Field(min_length=3, max_length=300)


class PreviewIn(BaseModel):
    shot_ids: list[str] = Field(min_length=1, max_length=20)


def clean_email(email: str) -> str:
    email = email.strip().lower()
    if len(email) > 320 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(422, "Invalid email address")
    return email


def get_project(db: Session, project_id: int, user: User) -> Project:
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(404, "Project not found")
    return project


def project_data(db: Session, project: Project) -> dict:
    media = list(db.scalars(select(Media).where(Media.project_id == project.id).order_by(Media.id)))
    previews = list(db.scalars(select(Preview).where(Preview.project_id == project.id, Preview.storyboard_version == project.storyboard_version).order_by(Preview.id)))
    review = db.scalar(select(Review).where(Review.project_id == project.id).order_by(Review.id.desc()))
    usage = list(db.scalars(select(Usage).where(Usage.project_id == project.id).order_by(Usage.id)))
    return {
        "id": project.id, "title": project.title, "brief": project.brief, "must_haves": project.must_haves,
        "task_type": project.task_type, "duration": project.duration, "resolution": project.resolution,
        "ratio": project.ratio,
        "storyboard": {k: v for k, v in (project.storyboard or {}).items() if not k.startswith("_")} if project.storyboard else None,
        "preview_progress": (project.storyboard or {}).get("_preview_job"),
        "storyboard_version": project.storyboard_version,
        "storyboard_approved": bool(project.storyboard_approved_at), "prompt": project.prompt,
        "prompt_approved": bool(project.prompt_approved_at), "status": project.status,
        "media": [{"id": m.id, "filename": m.filename, "kind": m.kind, "mime": m.mime, "role": m.role,
                   "duration_seconds": m.duration_seconds, "width": m.width, "height": m.height,
                   "url": f"/api/media/{m.id}/content"} for m in media],
        "previews": [{"id": x.id, "shot_id": x.shot_id, "model": x.model, "approved": x.approved,
                      "estimated_cost_usd": x.estimated_cost_usd, "source_media_ids": x.source_media_ids,
                      "url": f"/api/previews/{x.id}/content"} for x in previews],
        "review": review_data(review) if review else None,
        "usage": [{"review_id": x.review_id, "phase": x.phase, "round": x.round_number, "model": x.model, "input_tokens": x.input_tokens,
                   "output_tokens": x.output_tokens, "cached_tokens": x.cached_tokens,
                   "reasoning_tokens": x.reasoning_tokens, "attempts": x.attempts, "estimated_cost_usd": x.estimated_cost_usd,
                   "status": x.status} for x in usage],
        "total_estimated_cost_usd": round(sum(x.estimated_cost_usd or 0 for x in usage), 6),
    }


def review_data(review: Review) -> dict:
    return {"id": review.id, "status": review.status, "phase": review.phase,
            "findings": {key: value for key, value in (review.findings or {}).items() if not key.startswith("_")},
            "linter": review.linter, "final_prompt": review.final_prompt,
            "error": review.error, "budget_usd": review.budget_usd,
            "storyboard_version": review.storyboard_version}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/auth/me")
def me(request: Request, user: User = Depends(current_user)):
    csrf = request.cookies.get("zoya_csrf", "")
    return {"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin, "csrf": csrf}


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = clean_email(body.email)
    throttle(f"login:{request.client.host if request.client else 'unknown'}:{email}")
    user = db.scalar(select(User).where(User.email == email))
    if not user or not user.is_active or not verify_password(user.password_hash, body.password):
        raise HTTPException(401, "Invalid credentials")
    raw, csrf = new_login(db, user)
    db.commit()
    response.set_cookie(COOKIE_NAME, raw, httponly=True, secure=SECURE_COOKIES, samesite="strict", max_age=SESSION_HOURS * 3600, path="/")
    response.set_cookie("zoya_csrf", csrf, httponly=False, secure=SECURE_COOKIES, samesite="strict", max_age=SESSION_HOURS * 3600, path="/")
    return {"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin, "csrf": csrf}


@app.post("/api/auth/logout")
def logout(response: Response, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    raw = request.cookies.get(COOKIE_NAME, "")
    session = db.scalar(select(LoginSession).where(LoginSession.token_hash == digest(raw)))
    if session:
        session.revoked_at = utcnow()
        db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie("zoya_csrf", path="/")
    return {"ok": True}


@app.post("/api/auth/register")
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    throttle(f"register:{request.client.host if request.client else 'unknown'}", limit=12)
    invitation = db.scalar(select(Invitation).where(Invitation.token_hash == digest(body.token)))
    if not invitation or invitation.used_at or invitation.expires_at.replace(tzinfo=None) <= utcnow().replace(tzinfo=None):
        raise HTTPException(400, "Invitation is invalid or expired")
    if db.scalar(select(User).where(User.email == invitation.email)):
        raise HTTPException(409, "Account already exists")
    try:
        password_hash = hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    user = User(email=invitation.email, name=body.name.strip(), password_hash=password_hash)
    db.add(user)
    db.flush()
    invitation.used_at = utcnow()
    raw, csrf = new_login(db, user)
    db.commit()
    response.set_cookie(COOKIE_NAME, raw, httponly=True, secure=SECURE_COOKIES, samesite="strict", max_age=SESSION_HOURS * 3600, path="/")
    response.set_cookie("zoya_csrf", csrf, httponly=False, secure=SECURE_COOKIES, samesite="strict", max_age=SESSION_HOURS * 3600, path="/")
    return {"id": user.id, "email": user.email, "name": user.name, "is_admin": False, "csrf": csrf}


@app.post("/api/admin/invitations")
def create_invitation(body: InviteIn, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    email = clean_email(body.email)
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Account already exists")
    raw = token()
    expiry = utcnow() + timedelta(hours=body.expires_hours)
    db.add(Invitation(token_hash=digest(raw), email=email, created_by=admin.id, expires_at=expiry))
    db.commit()
    # Relative link avoids trusting an attacker-controlled Host header.
    return {"email": email, "link": f"/invite?token={raw}", "expires_at": expiry.isoformat()}


@app.get("/api/admin/invitations")
def list_invitations(admin: User = Depends(current_user), db: Session = Depends(get_db)):
    if not admin.is_admin:
        raise HTTPException(403, "Admin access required")
    invites = db.scalars(select(Invitation).order_by(Invitation.id.desc()).limit(100))
    return [{"id": x.id, "email": x.email, "expires_at": x.expires_at,
             "used": bool(x.used_at)} for x in invites]


@app.get("/api/projects")
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Project).where(Project.owner_id == user.id).order_by(Project.updated_at.desc()))
    return [{"id": p.id, "title": p.title, "status": p.status, "updated_at": p.updated_at} for p in rows]


@app.post("/api/projects")
def create_project(body: ProjectIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = Project(owner_id=user.id, **body.model_dump())
    errors = validate_settings(project, [])
    if errors:
        raise HTTPException(422, errors)
    db.add(project)
    db.commit()
    return project_data(db, project)


@app.get("/api/projects/{project_id}")
def read_project(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return project_data(db, get_project(db, project_id, user))


@app.put("/api/projects/{project_id}")
def update_project(project_id: int, body: ProjectIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = get_project(db, project_id, user)
    if project.status in {"review_running", "storyboard_running", "preview_running"}:
        raise HTTPException(409, "Review is running")
    for key, value in body.model_dump().items():
        setattr(project, key, value)
    media = list(db.scalars(select(Media).where(Media.project_id == project.id)))
    errors = validate_settings(project, media)
    if errors:
        db.rollback()
        raise HTTPException(422, errors)
    project.storyboard_approved_at = None
    project.prompt_approved_at = None
    project.status = "draft"
    db.commit()
    return project_data(db, project)


def inspect_media(blob: bytes, filename: str) -> tuple[str, str, float | None, int | None, int | None]:
    try:
        image = Image.open(BytesIO(blob))
        image.verify()
        image = Image.open(BytesIO(blob))
        if image.format in {"JPEG", "PNG", "WEBP"} and image.width * image.height <= 40_000_000:
            mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[image.format]
            return "image", mime, None, image.width, image.height
    except (UnidentifiedImageError, ValueError, OSError):
        pass
    suffix = Path(filename).suffix.lower()
    if suffix not in {".mp4", ".webm", ".mp3", ".wav", ".m4a"}:
        raise HTTPException(422, "Unsupported media format")
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
        temp.write(blob)
        path = Path(temp.name)
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                                capture_output=True, text=True, timeout=15, check=True)
        info = json.loads(result.stdout)
        streams = info.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = float(info.get("format", {}).get("duration", 0))
        if duration <= 0 or duration > 30:
            raise HTTPException(422, "Media duration must be between 0 and 30 seconds")
        if video and suffix in {".mp4", ".webm"}:
            mime = "video/mp4" if suffix == ".mp4" else "video/webm"
            return "video", mime, duration, int(video.get("width", 0)), int(video.get("height", 0))
        if audio and suffix in {".mp3", ".wav", ".m4a"}:
            mime = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}[suffix]
            return "audio", mime, duration, None, None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(422, "Media could not be validated") from exc
    finally:
        path.unlink(missing_ok=True)
    raise HTTPException(422, "Unsupported media content")


@app.post("/api/projects/{project_id}/media")
async def upload_media(project_id: int, file: UploadFile = File(...), role: str = Form(...),
                       user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = get_project(db, project_id, user)
    if project.status in {"review_running", "storyboard_running", "preview_running"}:
        raise HTTPException(409, "Review is running")
    if len(role.strip()) < 3 or len(role) > 300:
        raise HTTPException(422, "Describe the reference role")
    blob = await file.read(MAX_UPLOAD_BYTES + 1)
    if not blob or len(blob) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Media must be between 1 byte and 25 MiB")
    safe_name = Path(file.filename or "media").name[:255]
    kind, mime, duration, width, height = inspect_media(blob, safe_name)
    key = uuid.uuid4().hex
    path = MEDIA_ROOT / key
    path.write_bytes(blob)
    media = Media(project_id=project.id, kind=kind, mime=mime, filename=safe_name,
                  storage_key=key, size=len(blob), duration_seconds=duration,
                  width=width, height=height, role=role.strip())
    db.add(media)
    db.flush()
    all_media = list(db.scalars(select(Media).where(Media.project_id == project.id)))
    errors = validate_settings(project, all_media)
    if errors:
        db.rollback()
        path.unlink(missing_ok=True)
        raise HTTPException(422, errors)
    project.storyboard_approved_at = None
    project.prompt_approved_at = None
    project.status = "draft"
    db.commit()
    return project_data(db, project)


@app.put("/api/media/{media_id}/role")
def update_media_role(media_id: int, body: MediaRoleIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    media = db.get(Media, media_id)
    if not media:
        raise HTTPException(404, "Media not found")
    project = get_project(db, media.project_id, user)
    if project.status in {"review_running", "storyboard_running", "preview_running"}:
        raise HTTPException(409, "Review is running")
    media.role = body.role.strip()
    project.storyboard_approved_at = None
    project.prompt_approved_at = None
    project.status = "draft"
    db.commit()
    return {"ok": True}


@app.get("/api/media/{media_id}/content")
def media_content(media_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    media = db.get(Media, media_id)
    if not media:
        raise HTTPException(404, "Media not found")
    get_project(db, media.project_id, user)
    path = MEDIA_ROOT / media.storage_key
    if not path.exists():
        raise HTTPException(404, "Media file missing")
    return FileResponse(path, media_type=media.mime, headers={"Cache-Control": "private, no-store"})


@app.get("/api/previews/{preview_id}/content")
def preview_content(preview_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    preview = db.get(Preview, preview_id)
    if not preview:
        raise HTTPException(404, "Preview not found")
    get_project(db, preview.project_id, user)
    path = MEDIA_ROOT / preview.storage_key
    if not path.exists():
        raise HTTPException(404, "Preview file missing")
    return FileResponse(path, media_type=preview.mime, headers={"Cache-Control": "private, no-store"})


def _update_preview_progress(db: Session, project: Project, **changes) -> None:
    current = (project.storyboard or {}).get("_preview_job", {})
    project.storyboard = {**(project.storyboard or {}), "_preview_job": {**current, **changes}}
    db.commit()


def _preview_task(project_id: int, shot_ids: list[str], expected_version: int):
    from google import genai
    from PIL import Image
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        media = list(db.scalars(select(Media).where(Media.project_id == project_id)))
        try:
            client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        except Exception as exc:
            project.status = "storyboard_draft"
            _update_preview_progress(db, project, status="failed", error=f"{type(exc).__name__}: {exc}"[:500])
            return
        for index, shot_id in enumerate(shot_ids, start=1):
            if project.storyboard_version != expected_version or project.status != "preview_running":
                break
            _update_preview_progress(db, project, current_index=index, current_shot=shot_id)
            shot = next((x for x in project.storyboard.get("shots", []) if str(x.get("id")) == shot_id), None)
            if not shot:
                continue
            source_ids = [int(x) for x in shot.get("reference_media_ids", []) if str(x).isdigit()]
            images = [m for m in media if m.id in source_ids and m.kind == "image"][:4]
            prompt = ("Create one visual storyboard preview for the following video shot. This is a planning image, "
                      "not a source reference or final video frame. Keep uploaded characters, props and brand details "
                      "faithful to the provided images. Do not add text or logos. Creator brief: " + project.brief[:3000] +
                      "\nShot: " + json.dumps(shot, ensure_ascii=False)[:5000] + "\nTarget aspect ratio: " + project.ratio +
                      "\nGenerate one 1K-resolution planning image.")
            parts = [{"type": "text", "text": prompt}]
            for image in images:
                parts.append({"type": "image", "data": base64.b64encode((MEDIA_ROOT / image.storage_key).read_bytes()).decode("ascii"), "mime_type": image.mime})
            usage = Usage(project_id=project_id, phase="preview", model=MODEL_IDS["preview"],
                          estimated_cost_usd=PREVIEW_ESTIMATED_COST_USD, status="running")
            db.add(usage)
            db.commit()
            try:
                interaction = client.interactions.create(model=MODEL_IDS["preview"], input=parts)
                output = interaction.output_image
                if not output or not output.data:
                    raise ValueError("Image model returned no preview")
                blob = base64.b64decode(output.data)
                image = Image.open(BytesIO(blob))
                image.verify()
                key = uuid.uuid4().hex
                (MEDIA_ROOT / key).write_bytes(blob)
                mime = getattr(output, "mime_type", None) or "image/png"
                preview = Preview(project_id=project_id, shot_id=shot_id, storyboard_version=expected_version,
                                  storage_key=key, mime=mime, model=MODEL_IDS["preview"],
                                  source_media_ids=[m.id for m in images], approved=False,
                                  estimated_cost_usd=0.067)
                db.add(preview)
                db.flush()
                for older in db.scalars(select(Preview).where(Preview.project_id == project_id,
                                                                Preview.storyboard_version == expected_version,
                                                                Preview.shot_id == shot_id,
                                                                Preview.id != preview.id)):
                    older.approved = False
                usage.status = "completed"
                db.commit()
                job = (project.storyboard or {}).get("_preview_job", {})
                _update_preview_progress(db, project, completed=index,
                                         completed_shot_ids=[*job.get("completed_shot_ids", []), shot_id])
            except Exception as exc:
                usage.status = "failed"
                db.commit()
                project.status = "storyboard_draft"
                error = f"{type(exc).__name__}: {exc}"[:500]
                project.storyboard = {**project.storyboard, "preview_error": error}
                _update_preview_progress(db, project, status="failed", error=error)
                return
        if project.status == "preview_running":
            project.status = "storyboard_draft"
            _update_preview_progress(db, project, status="completed", current_shot=None)


@app.post("/api/projects/{project_id}/previews")
def generate_previews(project_id: int, body: PreviewIn, background: BackgroundTasks,
                      user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = get_project(db, project_id, user)
    if project.status != "storyboard_draft" or not project.storyboard:
        raise HTTPException(409, "Save a storyboard draft before generating previews")
    valid_ids = {str(shot.get("id")) for shot in project.storyboard.get("shots", [])}
    if len(set(body.shot_ids)) != len(body.shot_ids) or not set(body.shot_ids).issubset(valid_ids):
        raise HTTPException(422, "Unknown or duplicate shot ID")
    job = {"status": "running", "total": len(body.shot_ids), "completed": 0,
           "current_index": 0, "current_shot": None, "completed_shot_ids": [],
           "estimated_total_usd": round(len(body.shot_ids) * PREVIEW_ESTIMATED_COST_USD, 3)}
    project.storyboard = {**project.storyboard, "_preview_job": job}
    project.status = "preview_running"
    db.commit()
    background.add_task(_preview_task, project.id, body.shot_ids, project.storyboard_version)
    return {"status": "preview_running", "preview_progress": job}


@app.post("/api/previews/{preview_id}/approve")
def approve_preview(preview_id: int, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    preview = db.get(Preview, preview_id)
    if not preview:
        raise HTTPException(404, "Preview not found")
    project = get_project(db, preview.project_id, user)
    if project.status != "storyboard_draft" or preview.storyboard_version != project.storyboard_version:
        raise HTTPException(409, "Preview does not match the current storyboard draft")
    latest = db.scalar(select(Preview).where(Preview.project_id == project.id,
                                             Preview.storyboard_version == project.storyboard_version,
                                             Preview.shot_id == preview.shot_id).order_by(Preview.id.desc()))
    if latest.id != preview.id:
        raise HTTPException(409, "Only the latest preview for a shot can be approved")
    preview.approved = True
    db.commit()
    return {"approved": True}


@app.delete("/api/previews/{preview_id}")
def reject_preview(preview_id: int, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    preview = db.get(Preview, preview_id)
    if not preview:
        raise HTTPException(404, "Preview not found")
    project = get_project(db, preview.project_id, user)
    if project.status != "storyboard_draft" or preview.storyboard_version != project.storyboard_version:
        raise HTTPException(409, "Only previews for the current draft can be rejected")
    storage_path = MEDIA_ROOT / preview.storage_key
    db.delete(preview)
    db.commit()
    storage_path.unlink(missing_ok=True)
    return {"rejected": True}


def _storyboard_task(project_id: int):
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        media = list(db.scalars(select(Media).where(Media.project_id == project_id)))
        try:
            generate_storyboard(db, project, media)
        except Exception as exc:
            project.status = "storyboard_failed"
            project.storyboard = {"error": f"{type(exc).__name__}: {exc}"[:500]}
            db.commit()


@app.post("/api/projects/{project_id}/storyboard/generate")
def start_storyboard(project_id: int, background: BackgroundTasks, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = get_project(db, project_id, user)
    if project.status in {"review_running", "storyboard_running", "preview_running"}:
        raise HTTPException(409, "A workflow is already running")
    media = list(db.scalars(select(Media).where(Media.project_id == project.id)))
    errors = validate_settings(project, media)
    if errors:
        raise HTTPException(422, errors)
    project.status = "storyboard_running"
    db.commit()
    background.add_task(_storyboard_task, project.id)
    return {"status": "storyboard_running"}


@app.put("/api/projects/{project_id}/storyboard")
def save_storyboard(project_id: int, body: StoryboardIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = get_project(db, project_id, user)
    if project.status in {"review_running", "storyboard_running", "preview_running"}:
        raise HTTPException(409, "Workflow is running")
    ids = [str(shot.get("id", "")) for shot in body.shots]
    if any(not shot_id for shot_id in ids) or len(ids) != len(set(ids)):
        raise HTTPException(422, "Every shot needs a unique ID")
    project.storyboard = normalize_storyboard_references(body.model_dump())
    project.storyboard_version += 1
    project.storyboard_approved_at = None
    project.prompt_approved_at = None
    project.status = "storyboard_draft"
    db.commit()
    return project_data(db, project)


@app.post("/api/projects/{project_id}/storyboard/approve")
def approve_storyboard(project_id: int, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = get_project(db, project_id, user)
    if project.status != "storyboard_draft" or not project.storyboard or not project.storyboard.get("shots"):
        raise HTTPException(409, "No storyboard draft to approve")
    project.storyboard = normalize_storyboard_references(project.storyboard)
    media = list(db.scalars(select(Media).where(Media.project_id == project.id)))
    errors = validate_settings(project, media) + validate_storyboard(project, media)
    if errors:
        raise HTTPException(422, errors)
    project.storyboard_approved_at = utcnow()
    project.status = "storyboard_approved"
    db.commit()
    return project_data(db, project)


def _review_task(project_id: int, review_id: int):
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        review = db.get(Review, review_id)
        media = list(db.scalars(select(Media).where(Media.project_id == project_id)))
        run_review(db, project, media, review)


@app.post("/api/projects/{project_id}/reviews")
def start_review(project_id: int, background: BackgroundTasks,
                 user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = get_project(db, project_id, user)
    if project.status != "storyboard_approved" or not project.storyboard_approved_at:
        raise HTTPException(409, "Approve the storyboard before review")
    media = list(db.scalars(select(Media).where(Media.project_id == project.id)))
    errors = validate_settings(project, media)
    if errors:
        raise HTTPException(422, errors)
    review = Review(project_id=project.id, storyboard_version=project.storyboard_version,
                    budget_usd=max(0.10, min(100.0, float(os.getenv("DEFAULT_REVIEW_BUDGET_USD", "5")))), status="running")
    db.add(review)
    project.status = "review_running"
    db.commit()
    background.add_task(_review_task, project.id, review.id)
    return review_data(review)


@app.post("/api/projects/{project_id}/prompt/approve")
def approve_prompt(project_id: int, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = get_project(db, project_id, user)
    review = db.scalar(select(Review).where(Review.project_id == project.id).order_by(Review.id.desc()))
    if not review or review.status != "ready_for_approval" or review.storyboard_version != project.storyboard_version:
        raise HTTPException(409, "No valid review is ready for approval")
    if not review.linter or not review.linter.get("passed"):
        raise HTTPException(409, "Linter has unresolved errors")
    project.prompt_approved_at = utcnow()
    project.status = "approved"
    db.commit()
    return project_data(db, project)


@app.get("/")
@app.get("/{path:path}")
def frontend(path: str = ""):
    if path.startswith("api/"):
        raise HTTPException(404)
    candidate = STATIC_ROOT / path
    if path and candidate.is_file() and candidate.resolve().is_relative_to(STATIC_ROOT.resolve()):
        return FileResponse(candidate)
    return FileResponse(STATIC_ROOT / "index.html")
