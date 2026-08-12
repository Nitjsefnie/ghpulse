"""Reproducible production-boundary integration proof for the final release.

This test deliberately does not replace any application component with a
mock.  The source adapter's checked-in fixture mode feeds the same validated
snapshot boundary used by production, while PostgreSQL, ingest, middleware,
API routes, Uvicorn, Chromium, React, Babel, and the chart helper remain real.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import threading
import time

import httpx
import psycopg
import pytest

from backend import auth, db

# This is intentionally a large, boundary-level test. The production
# application components remain real; the disables only keep the test's
# orchestration helpers from obscuring the quality signal.
# pylint: disable=import-outside-toplevel,not-context-manager,too-many-locals
# pylint: disable=too-many-statements,line-too-long


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
INITIAL = FIXTURES / "snapshot_initial.json"
CHANGED = FIXTURES / "snapshot_changed.json"


def _require_dsn() -> str:
    dsn = os.environ.get("GHPULSE_TEST_DATABASE_URL")
    if not dsn:
        pytest.fail("GHPULSE_TEST_DATABASE_URL is required for final integration")
    return dsn


def _reset_database(dsn: str) -> None:
    schema = (ROOT / "backend" / "schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(db.sql_text(schema))
        connection.execute(
            "TRUNCATE issues, pull_requests, repositories, ingest_runs CASCADE"
        )
        connection.execute("DELETE FROM sync_state")
        connection.execute("INSERT INTO sync_state (id) VALUES (1)")


def _create_auth_user(dsn: str) -> None:
    config: dict = {}
    auth.set_web_password(config, "correct horse battery staple")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS users "
            "(user_id INTEGER PRIMARY KEY, config JSONB NOT NULL DEFAULT '{}'::jsonb)"
        )
        connection.execute("DELETE FROM users")
        connection.execute(
            "INSERT INTO users (user_id, config) VALUES (%s, %s::jsonb)",
            (42, json.dumps(config)),
        )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_initial_ingest(base_url: str) -> None:
    deadline = time.monotonic() + 30
    with httpx.Client(base_url=base_url, timeout=3) as client:
        while time.monotonic() < deadline:
            response = client.get("/health")
            if response.status_code == 200 and response.json().get("last_success"):
                return
            time.sleep(0.1)
    pytest.fail("startup complete-snapshot ingest did not finish within 30 seconds")


def _event_totals(body: dict, panel: str, keys: tuple[str, ...]) -> dict[str, int]:
    return {
        key: sum(int(bucket.get(key, 0)) for bucket in body[panel])
        for key in keys
    }


def _assert_initial_totals(body: dict) -> None:
    assert _event_totals(body, "issues", ("opened", "completed", "not_planned")) == {
        "opened": 2,
        "completed": 1,
        "not_planned": 0,
    }
    assert _event_totals(
        body, "pull_requests", ("opened", "merged", "closed_unmerged")
    ) == {"opened": 2, "merged": 0, "closed_unmerged": 1}


def _assert_changed_totals(body: dict) -> None:
    assert _event_totals(body, "issues", ("opened", "completed", "not_planned")) == {
        "opened": 1,
        "completed": 0,
        "not_planned": 1,
    }
    assert _event_totals(
        body, "pull_requests", ("opened", "merged", "closed_unmerged")
    ) == {"opened": 1, "merged": 1, "closed_unmerged": 0}


def _assert_changed_dashboard_dom(page) -> None:
    expected = {
        "issues opened": "1",
        "issues completed": "0",
        "issues not planned": "1",
        "pull requests opened": "1",
        "pull requests merged": "1",
        "pull requests closed unmerged": "0",
    }
    for label, value in expected.items():
        stat = page.locator(".stat").filter(has_text=label)
        assert stat.locator(".stat-value").inner_text() == value
    assert page.locator('svg[data-panel="External Issues"]').count() == 1
    assert page.locator('svg[data-panel="External Pull Requests"]').count() == 1


def _attach_browser_assertions(page):
    errors: dict[str, list[str]] = {
        "console": [],
        "page": [],
        "request": [],
        "csp": [],
    }
    page.on(
        "console",
        lambda message: errors["console"].append(message.text)
        if message.type == "error"
        else None,
    )
    page.on("pageerror", lambda error: errors["page"].append(str(error)))
    page.on(
        "requestfailed",
        lambda request: errors["request"].append(
            f"{request.url}: {request.failure}"
        ),
    )
    page.add_init_script(
        "document.addEventListener('securitypolicyviolation', "
        "event => window.__ghpulsePolicyViolations = "
        "(window.__ghpulsePolicyViolations || []).concat(event.violatedDirective));"
    )
    return errors


def _make_external_font_loading_deterministic(context) -> None:
    """Keep the production shell independent of a mutable font CDN response.

    The page still serves its production stylesheet and CSP.  Only the
    non-functional Google Fonts stylesheet is fulfilled with an empty,
    successful response so a changed CDN font URL cannot create a browser
    request error unrelated to the dashboard boundary under test.
    """
    context.route(
        "https://fonts.googleapis.com/**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/css",
            body="",
        ),
    )


def _wait_dashboard(page) -> None:
    page.locator('section[aria-label="External Issues"]').wait_for()
    page.locator('section[aria-label="External Pull Requests"]').wait_for()
    assert page.locator("section.panel-shell").count() == 2


def _dashboard(page, query: str) -> dict:
    return page.evaluate(
        "query => fetch('/api/dashboard?range=' + query)"
        ".then(response => { if (!response.ok) throw new Error(response.status); "
        "return response.json(); })",
        query,
    )


def _dashboard_fresh(page, query: str) -> dict:
    """Explicitly bypass cache for independent range/filter probes."""
    return page.evaluate(
        "query => fetch('/api/dashboard?range=' + query + '&fresh=1')"
        ".then(response => { if (!response.ok) throw new Error(response.status); "
        "return response.json(); })",
        query,
    )


def _repositories(page, query: str) -> dict:
    return page.evaluate(
        "query => fetch('/api/repositories?range=' + query)"
        ".then(response => { if (!response.ok) throw new Error(response.status); "
        "return response.json(); })",
        query,
    )


def _assert_browser_clean(page, errors: dict[str, list[str]]) -> None:
    page.wait_for_timeout(750)
    errors["csp"].extend(page.evaluate("window.__ghpulsePolicyViolations || []"))
    assert errors == {"console": [], "page": [], "request": [], "csp": []}


def _assert_chart_range_boundaries(page) -> dict:
    return page.evaluate(
        """async ranges => {
          const series = {
            issues: [{key: 'opened'}, {key: 'completed'}, {key: 'not_planned'}],
            pull_requests: [{key: 'opened'}, {key: 'merged'}, {key: 'closed_unmerged'}],
          };
          const checked = {};
          for (const [label, query] of Object.entries(ranges)) {
            const response = await fetch('/api/dashboard?range=' + query + '&fresh=1');
            if (!response.ok) throw new Error(label + ': ' + response.status);
            const body = await response.json();
            const range = {start: Date.parse(body.start), end: Date.parse(body.end)};
            for (const [panel, rows] of [
              ['issues', body.issues], ['pull_requests', body.pull_requests]
            ]) {
              const events = rows.map(row => ({...row,
                start: Date.parse(row.start), end: Date.parse(row.end)}));
              const built = window.buildStackedTimeSeriesData(
                events, series[panel], range, body.bucket_s * 1000);
              for (const item of series[panel]) {
                const points = built.cumulative[item.key];
                const first = points[0];
                const last = points[points.length - 1];
                if (first.ts !== range.start || first.v !== 0 || first.binIdx !== -1
                    || last.ts !== range.end) {
                  throw new Error(label + '/' + panel + '/' + item.key + ': bad boundaries');
                }
              }
            }
            checked[label] = true;
          }
          return checked;
        }""",
        {"24h": "1d", "7d": "7d", "30d": "30d", "90d": "90d", "1y": "365d", "all": "all"},
    )


def test_final_production_integration(monkeypatch):
    """Run the complete fixture transition through the real app boundary."""
    dsn = _require_dsn()
    auth_dsn = os.environ.get("DATABASE_URL_AUTH", dsn)
    monkeypatch.setenv("DATABASE_URL_VIZ", dsn)
    monkeypatch.setenv("DATABASE_URL_AUTH", auth_dsn)
    monkeypatch.setenv("GH_USER", "octocat")
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("ADMIN_TOKEN", "task8-integration-admin")
    monkeypatch.setenv("GHPULSE_TEST_SOURCE_SNAPSHOT", str(INITIAL))

    _reset_database(dsn)
    if auth_dsn != dsn:
        with psycopg.connect(auth_dsn, autocommit=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS users "
                "(user_id INTEGER PRIMARY KEY, config JSONB NOT NULL DEFAULT '{}'::jsonb)"
            )
            connection.execute("DELETE FROM users")
    _create_auth_user(auth_dsn)

    from playwright.sync_api import sync_playwright
    from uvicorn import Config, Server
    from backend import app as app_module

    # Any earlier unit test may have created a pool with another DSN. Closing
    # it here is real lifecycle cleanup; the Uvicorn lifespan opens fresh pools.
    db.close_pools()
    port = _free_port()
    server = Server(
        Config(
            app_module.app,
            host="127.0.0.1",
            port=port,
            ws="none",
            lifespan="on",
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, name="ghpulse-final-integration")
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        _wait_for_initial_ingest(base_url)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            guest_context = browser.new_context()
            _make_external_font_loading_deterministic(guest_context)
            guest = guest_context.new_page()
            guest_errors = _attach_browser_assertions(guest)
            guest.goto(f"{base_url}/login")
            guest.locator("button.guest-btn").click()
            guest.wait_for_url(f"{base_url}/")
            _wait_dashboard(guest)
            assert guest.evaluate("fetch('/api/me').then(response => response.json())") == {
                "user_id": 0,
                "is_guest": True,
            }
            initial = _dashboard(guest, "all")
            _assert_initial_totals(initial)
            assert [repo["node_id"] for repo in _repositories(guest, "all")["repositories"]] == [
                "R_1",
                "R_2",
            ]
            assert (
                _dashboard_fresh(guest, "all&repository=R_1")["summary"]["repositories"]
                == 1
            )
            assert "fixture-token" not in guest.content()

            monkeypatch.setenv("GHPULSE_TEST_SOURCE_SNAPSHOT", str(CHANGED))
            with httpx.Client(base_url=base_url, timeout=30) as admin:
                response = admin.post(
                    "/admin/ingest",
                    headers={
                        "Origin": base_url,
                        "X-Admin-Token": "task8-integration-admin",
                    },
                )
                assert response.status_code == 200, response.text
                assert response.json()["data_changed"] is True

            guest.locator('.stat').filter(has_text='issues completed').get_by_text(
                '0', exact=True
            ).wait_for()
            guest.locator('.stat').filter(has_text='issues not planned').get_by_text(
                '1', exact=True
            ).wait_for()
            guest.locator('.stat').filter(has_text='pull requests merged').get_by_text(
                '1', exact=True
            ).wait_for()
            assert guest.locator('select option').count() == 2
            assert guest.locator('select option').nth(1).inner_text() == 'external/one'
            # This normal endpoint read is intentionally not used as proof:
            # the cache contract may serve its stale body while refresh work
            # is draining.  The mounted app's fresh event path is the release
            # boundary under test, and these DOM assertions prove both panels
            # and the repository selector consumed the changed snapshot.
            _assert_changed_dashboard_dom(guest)
            assert _assert_chart_range_boundaries(guest) == {
                "24h": True,
                "7d": True,
                "30d": True,
                "90d": True,
                "1y": True,
                "all": True,
            }
            _assert_browser_clean(guest, guest_errors)

            auth_context = browser.new_context()
            _make_external_font_loading_deterministic(auth_context)
            authenticated = auth_context.new_page()
            auth_errors = _attach_browser_assertions(authenticated)
            authenticated.goto(f"{base_url}/login")
            authenticated.locator('input[name="user_id"]').fill("42")
            authenticated.locator('input[name="password"]').fill(
                "correct horse battery staple"
            )
            authenticated.locator("form button").first.click()
            authenticated.wait_for_url(f"{base_url}/")
            _wait_dashboard(authenticated)
            assert authenticated.evaluate(
                "fetch('/api/me').then(response => response.json())"
            ) == {"user_id": 42, "is_guest": False}
            auth_body = _dashboard(authenticated, "all")
            _assert_changed_totals(auth_body)
            assert (
                _dashboard_fresh(authenticated, "30d&repository=R_1")["summary"]["repositories"]
                == 1
            )
            assert [
                repo["node_id"] for repo in _repositories(authenticated, "all")["repositories"]
            ] == ["R_1"]
            assert _assert_chart_range_boundaries(authenticated) == {
                "24h": True,
                "7d": True,
                "30d": True,
                "90d": True,
                "1y": True,
                "all": True,
            }
            assert "fixture-token" not in authenticated.content()
            _assert_browser_clean(authenticated, auth_errors)
            auth_context.close()
            guest_context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        assert not thread.is_alive()
