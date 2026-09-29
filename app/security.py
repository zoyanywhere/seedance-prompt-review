import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import LoginSession, SessionLocal, User, utcnow

COOKIE_NAME = "zoya_session"
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "24"))
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "true").lower() == "true"
PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
_attempts: dict[str, deque[float]] = defaultdict(deque)


def token() -> str:
    return secrets.token_urlsafe(32)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 256:
        raise ValueError("Password must be between 12 and 256 characters")
    return PASSWORD_HASHER.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(stored, password)
    except (VerifyMismatchError, ValueError):
        return False


def throttle(key: str, limit: int = 8, window_seconds: int = 900) -> None:
    now = time.monotonic()
    bucket = _attempts[key]
    while bucket and bucket[0] < now - window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(429, "Too many attempts. Try again later.")
    bucket.append(now)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_session(request: Request, db: Session = Depends(get_db)) -> tuple[User, LoginSession]:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    session = db.scalar(select(LoginSession).where(LoginSession.token_hash == digest(raw)))
    if not session or session.revoked_at or session.expires_at.replace(tzinfo=None) <= utcnow().replace(tzinfo=None):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    user = db.get(User, session.user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account unavailable")
    return user, session


def current_user(identity: tuple[User, LoginSession] = Depends(current_session)) -> User:
    return identity[0]


def require_csrf(request: Request, identity: tuple[User, LoginSession] = Depends(current_session)) -> User:
    candidate = request.headers.get("X-CSRF-Token", "")
    if not candidate or not hmac.compare_digest(digest(candidate), identity[1].csrf_hash):
        raise HTTPException(403, "Invalid CSRF token")
    return identity[0]


def require_admin(user: User = Depends(require_csrf)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Admin access required")
    return user


def new_login(db: Session, user: User) -> tuple[str, str]:
    raw = token()
    csrf = token()
    db.add(LoginSession(
        token_hash=digest(raw),
        csrf_hash=digest(csrf),
        user_id=user.id,
        expires_at=utcnow() + timedelta(hours=SESSION_HOURS),
    ))
    db.flush()
    return raw, csrf
