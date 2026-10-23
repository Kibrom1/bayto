"""Optional shared-secret authentication (BAYTO_API_TOKEN).

Unset or empty: nothing changes, every route is open (the single-user localhost default). Set: every route except
GET /healthz, GET /app-config and POST /auth/login needs `Authorization: Bearer <token>` or the HttpOnly cookie
`bayto_session`. The cookie never holds the token itself, only an HMAC of a fixed label keyed by the token, so a
copied cookie does not reveal the secret and changing the token logs everyone out. All comparisons use
`hmac.compare_digest`. Failed logins are throttled per client address with an exponential lock-out.

This is a gate for exposing the port beyond localhost, not a user system: one shared secret, no identities.
TLS in front is still required (the cookie is marked Secure when the request arrived over https, directly or via
X-Forwarded-Proto).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

COOKIE_NAME = "bayto_session"
COOKIE_MAX_AGE = 7 * 24 * 3600
EXEMPT = {("GET", "/healthz"), ("GET", "/app-config"), ("POST", "/auth/login")}

router = APIRouter()


def api_token() -> str | None:
    return os.environ.get("BAYTO_API_TOKEN") or None


def _session_value(token: str) -> str:
    return hmac.new(token.encode(), b"bayto-session-v1", hashlib.sha256).hexdigest()


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def is_authenticated(request: Request) -> bool:
    """True when auth is off, or the request carries the bearer token or a valid session cookie."""
    token = api_token()
    if token is None:
        return True
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() == "bearer" and presented and _eq(presented.strip(), token):
        return True
    cookie = request.cookies.get(COOKIE_NAME)
    return bool(cookie) and _eq(cookie, _session_value(token))


def _unauthorized() -> JSONResponse:
    return JSONResponse({"detail": {"error": {"code": "unauthorized", "detail": "authentication required"}}},
                        status_code=401, headers={"WWW-Authenticate": "Bearer"})


async def auth_middleware(request: Request, call_next):
    if api_token() is None or (request.method, request.url.path) in EXEMPT or is_authenticated(request):
        return await call_next(request)
    return _unauthorized()


# ---- login throttle: per client address, 5 free failures, then 1s, 2s, 4s ... up to 60s lock-outs
_FREE_FAILURES = 5
_failures: dict[str, tuple[int, float]] = {}  # addr -> (count, locked_until monotonic)
_MAX_TRACKED = 1024


def _addr(request: Request) -> str:
    return request.client.host if request.client else "?"


def _retry_after(addr: str) -> int:
    _, until = _failures.get(addr, (0, 0.0))
    return max(0, int(until - time.monotonic() + 0.999))


def _record_failure(addr: str) -> None:
    if len(_failures) >= _MAX_TRACKED and addr not in _failures:
        _failures.clear()
    count = _failures.get(addr, (0, 0.0))[0] + 1
    delay = 0 if count <= _FREE_FAILURES else min(60, 2 ** (count - _FREE_FAILURES - 1))
    _failures[addr] = (count, time.monotonic() + delay)


class LoginIn(BaseModel):
    token: str = Field(max_length=1024)


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response):
    token = api_token()
    if token is None:
        return JSONResponse({"detail": {"error": {"code": "auth_not_enabled", "detail": "authentication is not enabled"}}}, status_code=404)
    addr = _addr(request)
    wait = _retry_after(addr)
    if wait:
        return JSONResponse({"detail": {"error": {"code": "too_many_attempts", "detail": f"too many attempts; try again in {wait}s"}}},
                            status_code=429, headers={"Retry-After": str(wait)})
    if not _eq(body.token, token):
        _record_failure(addr)
        return JSONResponse({"detail": {"error": {"code": "invalid_token", "detail": "that token is not right"}}}, status_code=401)
    _failures.pop(addr, None)
    response.set_cookie(COOKIE_NAME, _session_value(token), max_age=COOKIE_MAX_AGE, httponly=True, samesite="strict",
                        secure=_secure(request), path="/")
    return {"authenticated": True}


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict", secure=_secure(request))
    return {"authenticated": False}
