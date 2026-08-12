import hashlib
import hmac
import json
import os
import threading
import time
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from backend import session


def test_token_roundtrip():
    secret = "super-secret-32-bytes" * 2
    tok = session.make_session_token(99, secret)
    assert session.verify_session_token(tok, secret) == 99


def test_verify_rejects_wrong_secret():
    tok = session.make_session_token(42, "secret-a" * 4)
    assert session.verify_session_token(tok, "secret-b" * 4) is None


def test_verify_rejects_expired_token():
    secret = "k" * 32
    tok = session.make_session_token(7, secret)
    far_future = int(time.time()) + session.SESSION_COOKIE_MAX_AGE + 60
    with patch.object(session.time, "time", return_value=far_future):
        assert session.verify_session_token(tok, secret) is None


def test_verify_rejects_future_token():
    secret = "k" * 32
    payload = "5.99999999999.nonce"
    sig = hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    tok = f"{payload}.{sig}"
    assert session.verify_session_token(tok, secret) is None


def test_parse_session_token_rejects_garbage():
    assert session.parse_session_token("not.a.real.token.too.many") is None
    assert session.parse_session_token("missing-dots") is None
    assert session.parse_session_token("a.b.c.d") is None


def test_get_or_create_session_secret_persists():
    config: dict = {}
    s1 = session.get_or_create_session_secret(config)
    assert config[session.WEB_SESSION_SECRET_KEY] == s1
    s2 = session.get_or_create_session_secret(config)
    assert s2 == s1


def test_atomic_secret_initializer_reads_existing_without_update(monkeypatch):
    class Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Cursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=()):
            self.queries.append((query, params))
            return self

        def fetchone(self):
            return ("existing-secret",)

    cursor = Cursor()
    cursor.transaction = lambda: Transaction()

    class Connection:
        transaction = lambda self: Transaction()

        def execute(self, query, params=()):
            return cursor.execute(query, params)

    class Context:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(session.db, "auth_conn", lambda: Context())

    assert session.ensure_user_session_secret(42) == "existing-secret"
    assert len(cursor.queries) == 1
    assert "FOR UPDATE" in cursor.queries[0][0]


def test_atomic_secret_initializer_concurrent_real_postgres_preserves_external_keys():
    dsn = os.environ.get("GHPULSE_TEST_DATABASE_URL")
    if not dsn:
        import pytest
        pytest.skip("GHPULSE_TEST_DATABASE_URL is not configured")
    import psycopg
    from backend import db
    auth_dsn = os.environ.get("DATABASE_URL_AUTH", dsn)

    user_id = 990001
    with psycopg.connect(auth_dsn, autocommit=True) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS users "
            "(user_id INTEGER PRIMARY KEY, config JSONB NOT NULL DEFAULT '{}'::jsonb)"
        )
        connection.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
        connection.execute(
            "INSERT INTO users (user_id, config) VALUES (%s, %s::jsonb)",
            (user_id, json.dumps({"owner_key": "owner-value"})),
        )

    db.reset_auth_pool()
    values: list[str | None] = []
    barrier = threading.Barrier(2)

    def initialize():
        barrier.wait()
        values.append(session.ensure_user_session_secret(user_id))

    workers = [threading.Thread(target=initialize) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
    assert all(not worker.is_alive() for worker in workers)
    assert len(values) == 2
    assert values[0] and values[0] == values[1]

    with psycopg.connect(auth_dsn) as connection:
        config = connection.execute(
            "SELECT config FROM users WHERE user_id = %s", (user_id,)
        ).fetchone()[0]
    assert config["owner_key"] == "owner-value"
    assert config[session.WEB_SESSION_SECRET_KEY] == values[0]
    db.reset_auth_pool()


def test_set_session_cookie_owns_complete_flag_contract(monkeypatch):
    """One helper owns every issuance flag, including absent Domain."""
    monkeypatch.setenv("COOKIE_SECURE", "1")
    response = Response()

    session.set_session_cookie(response, "token")

    header = response.headers["set-cookie"].lower()
    assert f"{session.SESSION_COOKIE_NAME}=token" in header
    assert "httponly" in header
    assert "secure" in header
    assert "samesite=strict" in header
    assert f"max-age={session.SESSION_COOKIE_MAX_AGE}" in header
    assert "path=/" in header
    assert "domain=" not in header


def test_check_origin_allows_safe_methods():
    scope = {
        "type": "http", "method": "GET", "headers": [],
        "path": "/api/projects",
    }
    req = Request(scope)
    assert session.check_origin(req)


def test_check_origin_rejects_cross_origin_post():
    scope = {
        "type": "http", "method": "POST",
        "headers": [
            (b"host", b"viz.example.com"),
            (b"origin", b"https://evil.example.com"),
        ],
        "path": "/admin/ingest",
    }
    req = Request(scope)
    assert not session.check_origin(req)


def test_check_origin_accepts_same_origin_post():
    scope = {
        "type": "http", "method": "POST",
        "headers": [
            (b"host", b"viz.example.com"),
            (b"origin", b"https://viz.example.com"),
        ],
        "path": "/admin/ingest",
    }
    req = Request(scope)
    assert session.check_origin(req)


def test_guest_blocked_from_export():
    app = FastAPI()
    app.middleware("http")(session.auth_middleware)

    @app.get("/api/export")
    async def _stub():
        return {"ok": True}

    client = TestClient(app)
    guest_cookie = session.make_guest_session_token()
    client.cookies.set(session.SESSION_COOKIE_NAME, guest_cookie)
    resp = client.get("/api/export?range=7d")
    assert resp.status_code == 403


def test_guest_can_view_aggregate_repository_filter():
    app = FastAPI()
    app.middleware("http")(session.auth_middleware)

    @app.get("/api/dashboard")
    async def _stub():
        return {"ok": True}

    client = TestClient(app)
    guest_cookie = session.make_guest_session_token()
    client.cookies.set(session.SESSION_COOKIE_NAME, guest_cookie)
    resp = client.get("/api/dashboard?range=30d&repository=R_1")
    assert resp.status_code == 200
