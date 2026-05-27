"""Authentication for the operator console.

Two account tiers:

* **Super admin** — fixed username ``sa``. Its password is generated fresh
  on every process start and printed to the server log; it is never
  persisted. The super admin manages normal users.
* **Normal users** — username is an email address. Created by the super
  admin; the initial password is generated server-side and returned to the
  admin exactly once. Stored (salted + PBKDF2-hashed) in a dedicated
  ``auth.sqlite3``.

Sessions are stateless HMAC-signed tokens. The signing secret is persisted
to ``~/.tradingagents/app/auth_secret`` so normal-user sessions survive a
backend restart; the super admin's password still rotates every restart.

No third-party crypto dependency — PBKDF2-HMAC-SHA256 and HMAC-SHA256 from
the standard library are sufficient and well-understood.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import string
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, Request

from .settings_store import APP_HOME


logger = logging.getLogger(__name__)

_AUTH_DB = APP_HOME / "auth.sqlite3"
_SECRET_PATH = APP_HOME / "auth_secret"

SA_USERNAME = "sa"
ROLE_ADMIN = "admin"
ROLE_USER = "user"

TOKEN_TTL_SECONDS = 12 * 3600
_PBKDF2_ROUNDS = 200_000

# Endpoints reachable without a token. Everything else under /api/ requires
# a valid bearer token (see ``require_authenticated``).
_PUBLIC_PATHS = frozenset({
    "/api/health",
    "/api/auth/login",
    "/api/dashboard/stats",
    "/api/dashboard/top",
    # Ticker → company name map. Wikipedia-derived public data; loaded by
    # DecisionCard / DecisionSummaryCard which now render on public pages.
    "/api/tickers/names",
})

# Parameterised public routes. The decision-detail endpoint
# (/api/decisions/{ticker}/{trade_date}) is reachable from the public
# dashboard's ticker cards, so the full report is also public — the
# decision list, models, and dates endpoints stay token-gated because
# they back the operator-only browsing UI.
_PUBLIC_PATH_PATTERNS = (
    re.compile(r"^/api/decisions/[^/]+/[^/]+$"),
)

# Unambiguous alphabet for generated passwords — no 0/O, 1/l/I.
_PW_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"

# Process-local super-admin credential + token-signing secret, populated by
# ``init_auth()``.
_sa_salt: Optional[bytes] = None
_sa_hash: Optional[str] = None
_server_secret: Optional[bytes] = None


# ─── Storage ────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TEXT NOT NULL
);
"""


@contextmanager
def _connect():
    _AUTH_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_AUTH_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Password hashing ───────────────────────────────────────────────────────

def _hash_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS
    )
    return digest.hex()


def _gen_password(length: int = 16) -> str:
    return "".join(secrets.choice(_PW_ALPHABET) for _ in range(length))


# ─── Token signing ──────────────────────────────────────────────────────────

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(username: str, role: str) -> str:
    """Return an HMAC-signed ``body.signature`` session token."""
    assert _server_secret is not None, "init_auth() not called"
    payload = {
        "u": username,
        "r": role,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64e(hmac.new(_server_secret, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str) -> Optional[dict[str, Any]]:
    """Return the token payload when the signature and expiry are valid."""
    if not token or "." not in token or _server_secret is None:
        return None
    body, _, sig = token.partition(".")
    expected = _b64e(
        hmac.new(_server_secret, body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None
    return payload


# ─── Bootstrap ──────────────────────────────────────────────────────────────

def _load_or_create_secret() -> bytes:
    if _SECRET_PATH.exists():
        try:
            data = _SECRET_PATH.read_bytes().strip()
            if data:
                return data
        except OSError:
            pass
    secret = secrets.token_hex(32).encode("ascii")
    _SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SECRET_PATH.write_bytes(secret)
    try:
        os.chmod(_SECRET_PATH, 0o600)
    except OSError:
        pass
    return secret


def init_auth() -> None:
    """Create the user table, load the token secret, mint the SA password."""
    global _sa_salt, _sa_hash, _server_secret

    with _connect() as conn:
        conn.executescript(_SCHEMA)

    _server_secret = _load_or_create_secret()

    sa_password = _gen_password(16)
    _sa_salt = secrets.token_bytes(16)
    _sa_hash = _hash_password(sa_password, _sa_salt)

    banner = (
        "\n"
        + "=" * 64 + "\n"
        + "  Super-admin account (regenerated every restart)\n"
        + f"    username : {SA_USERNAME}\n"
        + f"    password : {sa_password}\n"
        + "  This password is shown ONLY here. Copy it now.\n"
        + "=" * 64 + "\n"
    )
    # Use print so it lands in stdout even if logging is filtered.
    print(banner, flush=True)
    logger.info("Super-admin password generated (see stdout banner).")


# ─── User management ────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def _is_email(value: str) -> bool:
    if value.count("@") != 1:
        return False
    local, _, domain = value.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".") \
        and not domain.endswith(".")


def create_user(email: str) -> str:
    """Create a normal user; return the one-time generated password.

    Raises ``ValueError`` on a bad email or a duplicate username.
    """
    username = _normalise_email(email)
    if username == SA_USERNAME:
        raise ValueError("Reserved username")
    if not _is_email(username):
        raise ValueError("Username must be a valid email address")

    with _connect() as conn:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            raise ValueError("A user with this email already exists")

        password = _gen_password(12)
        salt = secrets.token_bytes(16)
        conn.execute(
            "INSERT INTO users (username, password_hash, salt, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, _hash_password(password, salt), salt.hex(), ROLE_USER, _now_iso()),
        )
    return password


def reset_password(username: str) -> str:
    """Admin action: regenerate a normal user's password; return it once."""
    username = _normalise_email(username)
    if username == SA_USERNAME:
        raise ValueError("The super-admin password rotates on restart")
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row:
            raise ValueError("User not found")
        password = _gen_password(12)
        salt = secrets.token_bytes(16)
        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE username = ?",
            (_hash_password(password, salt), salt.hex(), username),
        )
    return password


def set_password(username: str, new_password: str) -> None:
    """Self-service password change for a normal user."""
    username = _normalise_email(username)
    if username == SA_USERNAME:
        raise ValueError("The super-admin password rotates on restart")
    if len(new_password) < 6:
        raise ValueError("Password must be at least 6 characters")
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row:
            raise ValueError("User not found")
        salt = secrets.token_bytes(16)
        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE username = ?",
            (_hash_password(new_password, salt), salt.hex(), username),
        )


def delete_user(username: str) -> None:
    username = _normalise_email(username)
    if username == SA_USERNAME:
        raise ValueError("Cannot delete the super-admin account")
    with _connect() as conn:
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        if cur.rowcount == 0:
            raise ValueError("User not found")


def list_users() -> list[dict[str, Any]]:
    """Return all accounts, with the built-in super admin synthesised first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT username, role, created_at FROM users ORDER BY created_at DESC"
        ).fetchall()
    users = [
        {
            "username": SA_USERNAME,
            "role": ROLE_ADMIN,
            "created_at": None,
            "builtin": True,
        }
    ]
    users.extend(
        {
            "username": r["username"],
            "role": r["role"],
            "created_at": r["created_at"],
            "builtin": False,
        }
        for r in rows
    )
    return users


def verify_credentials(username: str, password: str) -> Optional[str]:
    """Return the role on a correct username/password, else ``None``."""
    username = (username or "").strip()
    if username == SA_USERNAME:
        if _sa_hash is None or _sa_salt is None:
            return None
        candidate = _hash_password(password, _sa_salt)
        return ROLE_ADMIN if hmac.compare_digest(candidate, _sa_hash) else None

    username = _normalise_email(username)
    with _connect() as conn:
        row = conn.execute(
            "SELECT password_hash, salt, role FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if not row:
        return None
    candidate = _hash_password(password, bytes.fromhex(row["salt"]))
    if hmac.compare_digest(candidate, row["password_hash"]):
        return row["role"]
    return None


# ─── FastAPI dependencies ───────────────────────────────────────────────────

def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return ""


def require_authenticated(request: Request) -> Optional[dict[str, Any]]:
    """App-level dependency: enforce a valid token on every /api/ route.

    Public paths (login, health) and non-API paths (docs) are skipped. The
    decoded payload is stashed on ``request.state.user`` for downstream
    dependencies and handlers.
    """
    path = request.url.path
    if not path.startswith("/api/") or path in _PUBLIC_PATHS:
        return None
    if any(p.match(path) for p in _PUBLIC_PATH_PATTERNS):
        return None
    payload = verify_token(_bearer_token(request))
    if not payload:
        raise HTTPException(status_code=401, detail="Not authenticated")
    request.state.user = payload
    return payload


def current_user(request: Request) -> dict[str, Any]:
    """Route dependency: return the authenticated user payload."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request) -> dict[str, Any]:
    """Route dependency: 403 unless the caller is the super admin."""
    user = current_user(request)
    if user.get("r") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user
