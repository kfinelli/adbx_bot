"""
webui/auth.py — defense-in-depth authentication for the DM control panel.

Step 1: a signed session-cookie layer gated by a single middleware over every
route, with password login as the front door. The panel is intended to sit behind
Tailscale (private network); this layer is a second line of defense so a compromised
device or proxy misconfiguration doesn't hand over full DM control.

Design notes:
- The cookie is a stdlib HMAC-SHA256-signed token (no third-party dependency). The
  signer/verifier are pure functions so they can be unit-tested without the ASGI app.
- The password is stored as a scrypt hash (`salt$hash`), never plaintext. Generate the
  value with `python -m webui.auth` and put it in `.env` as DM_PANEL_PASSWORD_HASH.

Env vars:
- DM_PANEL_PASSWORD_HASH  — `salt$hash` (required for login to work).
- DM_PANEL_SECRET_KEY     — HMAC signing key. If unset, an ephemeral key is generated
                            at import and sessions won't survive a restart.
- DM_PANEL_SESSION_DAYS   — cookie lifetime in days (default 30).
- DM_PANEL_COOKIE_SECURE  — "false" to drop the Secure flag for plain-http local testing
                            (default: Secure on).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from webui.templates import login_page

log = logging.getLogger(__name__)

COOKIE_NAME = "dm_session"

# Paths reachable without a valid session. "/auth" is reserved for the step-2
# Discord magic-link exchange.
PUBLIC_PATHS = {"/login", "/logout", "/auth"}

# scrypt cost parameters (~16 MiB working set — comfortably under the default maxmem).
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def _init_secret_key() -> bytes:
    env_key = os.environ.get("DM_PANEL_SECRET_KEY", "")
    if env_key:
        return env_key.encode()
    log.warning(
        "DM_PANEL_SECRET_KEY not set; generated an ephemeral signing key. "
        "Sessions will not survive a restart. Set DM_PANEL_SECRET_KEY in .env."
    )
    return secrets.token_bytes(32)


_SECRET_KEY = _init_secret_key()


# ---------------------------------------------------------------------------
# Config accessors (read live so .env changes / tests take effect)
# ---------------------------------------------------------------------------

def _password_hash() -> str:
    return os.environ.get("DM_PANEL_PASSWORD_HASH", "")


def _session_days() -> int:
    try:
        return int(os.environ.get("DM_PANEL_SESSION_DAYS", "30"))
    except ValueError:
        return 30


def _cookie_secure() -> bool:
    return os.environ.get("DM_PANEL_COOKIE_SECURE", "true").strip().lower() != "false"


# ---------------------------------------------------------------------------
# Password hashing (pure)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Return a `salt$hash` string suitable for DM_PANEL_PASSWORD_HASH."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of `password` against a stored `salt$hash`."""
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    if not expected:
        return False
    dk = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT["n"], r=_SCRYPT["r"], p=_SCRYPT["p"], dklen=len(expected)
    )
    return hmac.compare_digest(dk, expected)


# ---------------------------------------------------------------------------
# Session cookie signing (pure)
# ---------------------------------------------------------------------------

def sign_session(expiry_ts: int, key: bytes | None = None) -> str:
    """Return a signed token encoding a unix expiry timestamp."""
    key = _SECRET_KEY if key is None else key
    payload_b64 = base64.urlsafe_b64encode(str(int(expiry_ts)).encode())
    sig = hmac.new(key, payload_b64, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig)
    return f"{payload_b64.decode()}.{sig_b64.decode()}"


def verify_session(token: str, key: bytes | None = None) -> bool:
    """Return True if `token` is well-signed and not expired."""
    key = _SECRET_KEY if key is None else key
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        got_sig = base64.urlsafe_b64decode(sig_b64)
    except (ValueError, AttributeError, TypeError, binascii.Error):
        return False
    expected_sig = hmac.new(key, payload_b64.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected_sig, got_sig):
        return False
    try:
        expiry = int(base64.urlsafe_b64decode(payload_b64))
    except (ValueError, binascii.Error):
        return False
    return expiry > int(time.time())


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME, "")
    return bool(token) and verify_session(token)


def set_session_cookie(response: Response) -> None:
    max_age = _session_days() * 86400
    token = sign_session(int(time.time()) + max_age)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )


# ---------------------------------------------------------------------------
# Magic-link tokens (step 2): single-use, short-TTL, in-memory
# ---------------------------------------------------------------------------

# token -> unix expiry timestamp. In-memory is fine: tokens live ~5 min, so a
# restart just invalidates any outstanding links (request a fresh one).
_tokens: dict[str, float] = {}


def _prune_tokens() -> None:
    now = time.time()
    for tok in [t for t, exp in _tokens.items() if exp <= now]:
        _tokens.pop(tok, None)


def issue_token(ttl_seconds: int = 300) -> str:
    """Mint a single-use login token valid for `ttl_seconds`."""
    _prune_tokens()
    token = secrets.token_urlsafe(32)
    _tokens[token] = time.time() + ttl_seconds
    return token


def consume_token(token: str) -> bool:
    """Return True (and invalidate the token) iff it's known and unexpired."""
    _prune_tokens()
    if not token:
        return False
    expiry = _tokens.pop(token, None)
    return expiry is not None and expiry > time.time()


# ---------------------------------------------------------------------------
# Middleware + routes
# ---------------------------------------------------------------------------

async def auth_middleware(request: Request, call_next):
    """Gate every route except PUBLIC_PATHS behind a valid session cookie."""
    if request.url.path in PUBLIC_PATHS or is_authenticated(request):
        return await call_next(request)
    # HTMX swaps fragments by default; HX-Redirect forces a full-page bounce instead.
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(login_page())


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: Annotated[str, Form()] = ""):
    stored = _password_hash()
    if not stored or not verify_password(password, stored):
        return HTMLResponse(login_page(error="Incorrect password."), status_code=401)
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response)
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/auth", response_class=HTMLResponse)
async def auth_exchange(t: str = ""):
    """Exchange a single-use magic-link token (from /dm_panel) for a session cookie."""
    if consume_token(t):
        response = RedirectResponse("/", status_code=303)
        set_session_cookie(response)
        return response
    return HTMLResponse(
        login_page(error="That sign-in link is invalid or expired."), status_code=401
    )


# ---------------------------------------------------------------------------
# CLI: `python -m webui.auth` to generate a password hash for .env
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import getpass
    import sys

    pw = getpass.getpass("New DM panel password: ")
    if not pw:
        print("Empty password — aborting.", file=sys.stderr)
        sys.exit(1)
    if pw != getpass.getpass("Confirm password: "):
        print("Passwords do not match — aborting.", file=sys.stderr)
        sys.exit(1)
    print("\nAdd this line to your .env:\n")
    print(f"DM_PANEL_PASSWORD_HASH={hash_password(pw)}")
