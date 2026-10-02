"""
Editor auth: one shared password (docs/SPEC.md §2), signed session cookie,
rate-limited login. No user accounts in the MVP.
"""

from __future__ import annotations

import hmac
import time
from collections import defaultdict

from fastapi import Cookie, Depends, HTTPException, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings, get_settings

COOKIE_NAME = "wt_editor"
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60

#: Shipped in .env.example. Anyone could mint their own editor cookie with it.
INSECURE_DEFAULTS = {"change-me", "", "changeme"}


def assert_configured(settings: Settings) -> None:
    """
    Refuse to issue or accept a session signed with the published default
    secret. Failing loudly beats quietly leaving the editor wide open.
    """
    if settings.session_secret.strip() in INSECURE_DEFAULTS:
        raise HTTPException(
            status_code=503,
            detail=(
                "SESSION_SECRET is still the example value, so editor sessions "
                "cannot be trusted. Generate one with: python -c \"import "
                "secrets; print(secrets.token_urlsafe(32))\""
            ),
        )
    if settings.editor_password.strip() in INSECURE_DEFAULTS:
        raise HTTPException(
            status_code=503,
            detail="EDITOR_PASSWORD is still the example value. Set a real one in .env.",
        )

# Login attempts per client key, as (timestamp, ...) within the window.
_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_MAX_ATTEMPTS = 8
_WINDOW_SECONDS = 300


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="wt-editor-session")


def rate_limit_login(client_key: str) -> None:
    now = time.monotonic()
    attempts = [t for t in _ATTEMPTS[client_key] if now - t < _WINDOW_SECONDS]
    if len(attempts) >= _MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in a few minutes.",
        )
    attempts.append(now)
    _ATTEMPTS[client_key] = attempts


def reset_rate_limit() -> None:
    """Test hook."""
    _ATTEMPTS.clear()


def verify_password(settings: Settings, password: str) -> bool:
    assert_configured(settings)
    # Constant-time compare so the password cannot be guessed by timing.
    return hmac.compare_digest(password.encode("utf-8"), settings.editor_password.encode("utf-8"))


def issue_session(response: Response, settings: Settings) -> None:
    assert_configured(settings)
    token = _serializer(settings).dumps({"role": "editor"})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        # The MVP is served over http in dev; M10 deployment sets this true.
        secure=False,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def require_editor(
    wt_editor: str | None = Cookie(default=None, alias=COOKIE_NAME),
    settings: Settings = Depends(get_settings),
) -> None:
    assert_configured(settings)
    if not wt_editor:
        raise HTTPException(status_code=401, detail="Editor login required")
    try:
        _serializer(settings).loads(wt_editor, max_age=SESSION_MAX_AGE_SECONDS)
    except SignatureExpired as exc:
        raise HTTPException(status_code=401, detail="Session expired") from exc
    except BadSignature as exc:
        raise HTTPException(status_code=401, detail="Invalid session") from exc
