"""Real-browser smoke for the Uvicorn-served no-build dashboard shell."""
from __future__ import annotations

import os
import socket
import threading
import time

import pytest


def _free_port() -> int:
    """Reserve a local TCP port long enough to configure Uvicorn."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.browser
def test_uvicorn_served_page_mounts_exactly_two_panels_without_browser_errors(
    monkeypatch,
):
    """Exercise CDN/SRI/Babel/CSP execution instead of only parsing source."""
    if os.environ.get("GHPULSE_BROWSER_SMOKE") != "1":
        pytest.skip("set GHPULSE_BROWSER_SMOKE=1 to run the Chromium smoke")
    playwright = pytest.importorskip("playwright.sync_api")

    from uvicorn import Config, Server
    from backend import api as api_module
    from backend import api_dashboard as dashboard_module
    from backend import app as app_module

    # Keep this smoke focused on the served browser/runtime boundary. The
    # complete PostgreSQL-backed and fixture smoke gates exercise persistence;
    # these deterministic endpoint seams supply the same public payload here.
    monkeypatch.setattr(app_module.db, "open_pools", lambda **kwargs: (None, None))
    monkeypatch.setattr(app_module.db, "schema_check", lambda: None)
    monkeypatch.setattr(app_module.db, "close_pools", lambda: None)
    monkeypatch.setattr(
        app_module.ingest,
        "run_ingest",
        lambda trigger: {"trigger": trigger, "skipped": False},
    )
    monkeypatch.setattr(
        api_module,
        "repositories",
        lambda **kwargs: {
            "range": kwargs.get("rng", "30d"),
            "repositories": [
                {
                    "node_id": "R_browser",
                    "name_with_owner": "external/browser-smoke",
                    "url": "https://github.com/external/browser-smoke",
                }
            ],
            "generated_at": "2026-08-12T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        dashboard_module,
        "dashboard",
        lambda **kwargs: {
            "range": kwargs.get("rng", "30d"),
            "bucket_s": 3600,
            "start": "2026-08-11T00:00:00+00:00",
            "end": "2026-08-12T00:00:00+00:00",
            "issues": [
                {
                    "start": "2026-08-11T00:00:00+00:00",
                    "end": "2026-08-12T00:00:00+00:00",
                    "opened": 1,
                    "completed": 0,
                    "not_planned": 0,
                }
            ],
            "pull_requests": [
                {
                    "start": "2026-08-11T00:00:00+00:00",
                    "end": "2026-08-12T00:00:00+00:00",
                    "opened": 1,
                    "merged": 1,
                    "closed_unmerged": 0,
                }
            ],
            "summary": {
                "repositories": 1,
                "issues": {"opened": 1, "completed": 0, "not_planned": 0},
                "pull_requests": {
                    "opened": 1,
                    "merged": 1,
                    "closed_unmerged": 0,
                },
                "last_ingest": "2026-08-12T00:00:00+00:00",
            },
            "repositories": [],
            "generated_at": "2026-08-12T00:00:00+00:00",
        },
    )

    port = _free_port()
    server = Server(
        Config(
            app_module.app,
            host="127.0.0.1",
            port=port,
            lifespan="on",
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, name="ghpulse-browser-server")
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started

        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            console_errors: list[str] = []
            page_errors: list[str] = []
            failed_requests: list[str] = []
            policy_violations: list[str] = []
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on(
                "requestfailed",
                lambda request: failed_requests.append(
                    f"{request.url}: {request.failure}"
                ),
            )
            page.add_init_script(
                "document.addEventListener('securitypolicyviolation', "
                "event => window.__ghpulsePolicyViolations = "
                "(window.__ghpulsePolicyViolations || []).concat(event.violatedDirective));"
            )

            page.goto(f"{base_url}/login")
            page.locator("button.guest-btn").click()
            page.wait_for_url(f"{base_url}/")
            page.locator('section[aria-label="External Issues"]').wait_for()
            page.locator('section[aria-label="External Pull Requests"]').wait_for()
            page.wait_for_timeout(750)
            policy_violations.extend(
                page.evaluate("window.__ghpulsePolicyViolations || []")
            )

            assert page.locator("section.panel-shell").count() == 2
            assert page.locator("svg").count() >= 2
            assert console_errors == []
            assert page_errors == []
            assert failed_requests == []
            assert policy_violations == []
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
