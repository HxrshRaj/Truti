"""Accounts: scrypt password hashes, opaque bearer sessions (only a SHA-256 of the token is stored).

``ADMIN_TOKEN`` keeps working as a bootstrap/superuser credential (CI, scripts, first setup).
"""
import datetime as dt
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import HTTPException, Request
from sqlalchemy import select

from .db import Membership, User, UserSession, utcnow

SESSION_DAYS = 14
_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
        return hmac.compare_digest(got.hex(), h)
    except Exception:
        return False


def _tok_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(UserSession(token_hash=_tok_hash(token), user_id=user_id,
                       expires_at=utcnow() + dt.timedelta(days=SESSION_DAYS)))
    db.commit()
    return token


class LoginThrottle:
    """Max ``limit`` failed logins per key per ``window`` seconds (in-process; per replica)."""

    def __init__(self, limit=8, window=900):
        self.limit, self.window, self.hits = limit, window, defaultdict(deque)

    def check(self, key):
        q, now = self.hits[key], time.monotonic()
        while q and q[0] < now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise HTTPException(429, "too many failed logins, try again later")

    def fail(self, key):
        self.hits[key].append(time.monotonic())


@dataclass
class Principal:
    user: User | None
    is_admin: bool  # ADMIN_TOKEN holder or user.is_admin

    @property
    def label(self):
        return self.user.email if self.user else "admin-token"


def authenticate(request: Request, db, admin_token: str) -> Principal:
    tok = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not tok:
        raise HTTPException(401, "missing credentials")
    if hmac.compare_digest(tok, admin_token):
        return Principal(None, True)
    row = db.get(UserSession, _tok_hash(tok))
    exp = row.expires_at if row else None
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=dt.timezone.utc)
    if row is None or exp < utcnow():
        raise HTTPException(401, "invalid or expired session")
    user = db.get(User, row.user_id)
    return Principal(user, bool(user.is_admin))


def project_ids_for(db, who: Principal):
    if who.is_admin:
        return None  # all
    return set(db.scalars(select(Membership.project_id).where(Membership.user_id == who.user.id)))


def role_in(db, who: Principal, project_id: int):
    if who.is_admin:
        return "owner"
    m = db.scalar(select(Membership).where(Membership.user_id == who.user.id, Membership.project_id == project_id))
    return m.role if m else None
