"""Login, guest-session, and logout routes for the ghpulse dashboard.

The login form is intentionally self-contained so it remains available before
the dashboard shell loads. Failed password attempts are rate-limited to five
per IP in a five-minute window.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Form, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from backend import auth, db
from backend import session as session_mod

router = APIRouter()

_LOGIN_FAILURES: dict[str, list[float]] = {}
_LOGIN_MAX_FAILURES = 5
_LOGIN_WINDOW_SECONDS = 300
_DUMMY_PASSWORD_CONFIG = {
    auth.WEB_PASSWORD_HASH_KEY: "0" * 64,
    auth.WEB_PASSWORD_SALT_KEY: "00" * 16,
}


def _check_login_rate_limit(ip: str) -> bool:
    now = time.time()
    attempts = [
        t for t in _LOGIN_FAILURES.get(ip, [])
        if now - t < _LOGIN_WINDOW_SECONDS
    ]
    _LOGIN_FAILURES[ip] = attempts
    return len(attempts) >= _LOGIN_MAX_FAILURES


def _record_login_failure(ip: str) -> None:
    now = time.time()
    attempts = [
        t for t in _LOGIN_FAILURES.get(ip, [])
        if now - t < _LOGIN_WINDOW_SECONDS
    ]
    attempts.append(now)
    _LOGIN_FAILURES[ip] = attempts


def reset_login_rate_limits() -> None:
    """Clear the process-global failure dict. Tests need this between
    cases that POST from the same TestClient host; production never
    calls it."""
    _LOGIN_FAILURES.clear()


def user_exists(user_id: int) -> bool:
    """Cheap existence probe in the auth DB's users table."""
    with db.auth_conn() as c:
        row = c.execute(
            "SELECT 1 FROM users WHERE user_id = %s LIMIT 1",
            (user_id,),
        ).fetchone()
    return row is not None


_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8" />
<title>Sign in · GHPULSE</title>
<style>
  body {{ background:#0b0d10; color:#dde; font-family: 'Inter',sans-serif;
         display:flex; align-items:center; justify-content:center;
         min-height:100vh; margin:0; }}
  form {{ background:#14181d; padding:24px 28px; border:1px solid #25303c;
         border-radius:8px; min-width:320px; }}
  h1 {{ margin:0 0 16px 0; font-size:18px; letter-spacing:.04em; color:#9bd; }}
  label {{ display:block; margin:10px 0 4px 0; font-size:12px; color:#8aa; }}
  input {{ width:100%; box-sizing:border-box; padding:8px 10px;
          background:#0e1216; color:#dde; border:1px solid #25303c;
          border-radius:4px; font: 14px 'JetBrains Mono', monospace; }}
  button {{ margin-top:18px; width:100%; padding:10px 14px;
           background:#1f6f9c; color:#fff; border:0; border-radius:4px;
           font-weight:600; cursor:pointer; }}
  .guest-btn {{ background:#1a1c2e; border:1px solid #25303c; }}
  .guest-btn:hover {{ background:#222640; }}
  .or {{ text-align:center; color:#556; font-size:11px; margin:16px 0 4px; letter-spacing:.2em; }}
  .err {{ color:#e76; font-size:12px; min-height:16px; margin-top:8px; }}
</style>
</head><body>
<form method="post" action="/login">
  <h1>GHPULSE · sign in</h1>
  <label>Username</label>
  <input name="user_id" required inputmode="numeric" pattern="[0-9]+"
         autocomplete="username">
  <label>Password</label>
  <input name="password" type="password" required autocomplete="current-password">
  <button type="submit">Sign in</button>
  <div class="err">{err}</div>
  <div class="or">or</div>
  <button type="submit" class="guest-btn"
          formaction="/login/guest" formmethod="post" formnovalidate>
    Continue as guest
  </button>
</form>
</body></html>
"""


@router.get("/login")
async def login_page() -> HTMLResponse:
    """Render the password and guest sign-in form."""
    return HTMLResponse(_LOGIN_HTML.format(err=""))


@router.post("/login")
async def login_post(
    request: Request,
    user_id: str = Form(""),
    password: str = Form(""),
) -> Response:
    """Authenticate one numeric user ID and issue a signed session cookie."""
    ip = request.client.host if request.client else "unknown"
    if _check_login_rate_limit(ip):
        return Response(
            "Too many login attempts. Try again later.",
            status_code=429, media_type="text/plain",
        )
    try:
        uid = int(user_id.strip())
    except ValueError:
        uid = 0
    config = session_mod.load_user_config(uid) if uid > 0 else None
    password_config = config if config and auth.has_web_password(config) else _DUMMY_PASSWORD_CONFIG
    valid_password = auth.verify_web_password(password_config, password)
    if uid <= 0 or not config or not auth.has_web_password(config) or not valid_password:
        _record_login_failure(ip)
        return Response(
            "Invalid credentials", status_code=401, media_type="text/plain"
        )
    secret = session_mod.ensure_user_session_secret(uid)
    if not secret:
        _record_login_failure(ip)
        return Response(
            "Invalid credentials", status_code=401, media_type="text/plain"
        )
    token = session_mod.make_session_token(uid, secret)
    response = RedirectResponse("/", status_code=303)
    session_mod.set_session_cookie(response, token)
    return response


@router.get("/logout")
async def logout() -> Response:
    """Clear the browser session and return to the login form."""
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(session_mod.SESSION_COOKIE_NAME, path="/")
    return response


@router.post("/login/guest")
async def login_guest() -> Response:
    """Mint an unauthenticated guest session for public aggregate views.

    The session middleware keeps this identity read-only while allowing the
    range and repository filters used by the dashboard.
    Cookie invalidates on every server restart since the guest secret
    is regenerated."""
    token = session_mod.make_guest_session_token()
    response = RedirectResponse("/", status_code=303)
    session_mod.set_session_cookie(response, token)
    return response
