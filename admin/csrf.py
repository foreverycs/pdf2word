"""Double-submit CSRF tokens for admin HTML forms."""

from __future__ import annotations

import hmac
import secrets
from typing import Optional

from fastapi import Request
from starlette.responses import Response

from core.settings import get_settings

COOKIE_NAME = "toolkit_csrf"
FIELD_NAME = "csrf_token"
_TOKEN_BYTES = 32


def _cookie_path() -> str:
    root = get_settings().root_path
    return root or "/"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def get_or_create_csrf_token(request: Request) -> str:
    """Reuse a valid existing cookie token so multi-tab forms keep working."""
    existing = (request.cookies.get(COOKIE_NAME) or "").strip()
    if len(existing) >= 16:
        return existing
    return new_csrf_token()


def set_csrf_cookie(response: Response, token: str) -> None:
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


def verify_csrf(request: Request, form_token: Optional[str]) -> bool:
    """Return True when cookie and form field match (constant-time)."""
    cookie = (request.cookies.get(COOKIE_NAME) or "").strip()
    field = (form_token or "").strip()
    if len(cookie) < 16 or len(field) < 16:
        return False
    return hmac.compare_digest(cookie, field)
