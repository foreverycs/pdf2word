"""Admin session auth (cookie-based, password from settings/env)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Optional
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse

from core.settings import get_settings

# Cookie name for signed admin session.
COOKIE_NAME = "toolkit_admin"


def admin_password() -> str:
    return get_settings().admin_password


def _secret() -> bytes:
    raw = get_settings().admin_secret
    return hashlib.sha256(f"toolkit-admin:{raw}".encode("utf-8")).digest()


def create_session_token() -> str:
    """Return ``exp.nonce.sig`` token."""
    ttl = get_settings().admin_session_ttl_sec
    exp = int(time.time()) + max(ttl, 300)
    nonce = secrets.token_hex(8)
    payload = f"{exp}.{nonce}"
    sig = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: Optional[str]) -> bool:
    if not token or token.count(".") != 2:
        return False
    exp_s, nonce, sig = token.split(".", 2)
    if not exp_s.isdigit() or not nonce or not sig:
        return False
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    payload = f"{exp_s}.{nonce}"
    expect = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expect, sig)


def check_password(password: str) -> bool:
    """Constant-time compare; never raises on length mismatch."""
    a = (password or "").encode("utf-8")
    b = (admin_password() or "").encode("utf-8")
    if len(a) != len(b):
        # Still do a dummy compare so timing is closer for wrong-length guesses.
        hmac.compare_digest(a, a)
        return False
    return hmac.compare_digest(a, b)


def is_admin(request: Request) -> bool:
    return verify_session_token(request.cookies.get(COOKIE_NAME))


def _cookie_path() -> str:
    root = get_settings().root_path
    return root or "/"


def require_admin(request: Request) -> Optional[RedirectResponse]:
    """Return a login redirect if not authenticated; otherwise None."""
    if is_admin(request):
        return None
    from tools.common import url_path

    nxt = request.url.path
    if request.url.query:
        nxt = f"{nxt}?{request.url.query}"
    login = url_path("/admin/login", request)
    return RedirectResponse(
        url=f"{login}?next={quote(nxt, safe='/?&=')}",
        status_code=303,
    )


def set_session_cookie(response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=s.admin_session_ttl_sec,
        path=_cookie_path(),
        secure=s.admin_cookie_secure,
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path=_cookie_path())
