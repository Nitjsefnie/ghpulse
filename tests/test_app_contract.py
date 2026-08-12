"""Executable contracts for the ghpulse browser application shell."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_JSX = ROOT / "src" / "app.jsx"
INDEX_HTML = ROOT / "public" / "index.html"


def test_app_source_is_the_two_panel_surface():
    src = APP_JSX.read_text(encoding="utf-8")

    assert src.count("<window.StackedCumulativeTimeSeriesPanel") == 2
    assert 'title="External Issues"' in src
    assert 'title="External Pull Requests"' in src
    for endpoint in ("/api/me", "/api/repositories", "/api/dashboard", "/api/events"):
        assert endpoint in src
    for label in (
        "range",
        "repository",
        "external repositories",
        "issues opened",
        "issues completed",
        "issues not planned",
        "pull requests opened",
        "pull requests merged",
        "pull requests closed unmerged",
        "last ingest",
    ):
        assert label in src.lower()


def test_app_does_not_restore_claudit_transcript_or_upload_surfaces():
    src = APP_JSX.read_text(encoding="utf-8").lower()

    forbidden = (
        "transcript",
        "inspector",
        "sessionview",
        "upload",
        "input type=\"file\"",
        "raw item",
        "model filter",
    )
    for marker in forbidden:
        assert marker not in src


def test_index_loads_react_babel_chart_before_app_and_mounts_app():
    src = INDEX_HTML.read_text(encoding="utf-8")
    assert src.index("react.production.min.js") < src.index("babel.min.js")
    assert src.index("/src/dashboard-charts.jsx") < src.index("/src/app.jsx")
    assert "window.App" in src
    assert "window.BACKEND_URL" in src
    assert "window.IS_GUEST" in src


def test_app_transpiles_with_the_no_build_browser_compiler():
    if shutil.which("bun") is None:
        pytest.skip("bun not available for the executable JSX contract")
    script = f"""
      const fs = require('fs');
      const app = fs.readFileSync({str(APP_JSX)!r}, 'utf8');
      const chart = fs.readFileSync({str(ROOT / 'src' / 'dashboard-charts.jsx')!r}, 'utf8');
      const transpiler = new Bun.Transpiler({{loader: 'jsx', target: 'es2020'}});
      const appOut = transpiler.transformSync(app);
      const chartOut = transpiler.transformSync(chart);
      console.log(JSON.stringify({{app: appOut.length, chart: chartOut.length,
        hasApp: appOut.includes('function App'),
        hasPanel: appOut.includes('StackedCumulativeTimeSeriesPanel')}}));
    """
    proc = subprocess.run(
        ["bun", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["app"] > 0
    assert result["chart"] > 0
    assert result["hasApp"]
    assert result["hasPanel"]


def test_pure_app_contract_normalizes_api_payload_and_query_state():
    if shutil.which("node") is None:
        pytest.skip("node not available for the executable app contract")
    body = {
        "range": "7d",
        "bucket_s": 3600,
        "start": "2026-08-01T00:00:00Z",
        "end": "2026-08-08T00:00:00Z",
        "issues": [
            {"start": "2026-08-01T00:00:00Z", "end": "2026-08-01T01:00:00Z",
             "opened": 2, "completed": 1, "not_planned": 0},
        ],
        "pull_requests": [],
        "summary": {"repositories": 1, "issues": {"opened": 2},
                     "pull_requests": {}, "last_ingest": "2026-08-08T00:00:00Z"},
        "repositories": [{"node_id": "R_1", "name_with_owner": "external/repo"}],
    }
    script = f"""
      const fs = require('fs');
      global.window = {{}};
      const src = fs.readFileSync('src/app.jsx', 'utf8');
      const start = src.indexOf('// --- pure app contracts ---');
      const end = src.indexOf('// --- React app ---', start);
      if (start < 0 || end <= start) throw new Error('pure app contract block missing');
      eval(src.slice(start, end));
      const body = {json.dumps(body)};
      const model = window.ghpulseAppContract.dashboardToViewModel(body);
      const stale = window.ghpulseAppContract.formatLastIngest(
        body.summary.last_ingest, Date.parse('2026-08-08T03:00:00Z'));
      console.log(JSON.stringify({{
        query: window.ghpulseAppContract.buildDashboardQuery('7d', 'R_1'),
        range: model.range,
        binMs: model.binMs,
        issueCount: model.issues.events.length,
        prCount: model.pullRequests.events.length,
        issueKeys: model.issues.series.map(s => s.key),
        prKeys: model.pullRequests.series.map(s => s.key),
        stale,
      }}));
    """
    proc = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["query"] == "/api/dashboard?range=7d&repository=R_1"
    assert result["range"] == {"start": 1785542400000, "end": 1786147200000}
    assert result["binMs"] == 3_600_000
    assert result["issueCount"] == 1
    assert result["prCount"] == 0
    assert result["issueKeys"] == ["opened", "completed", "not_planned"]
    assert result["prKeys"] == ["opened", "merged", "closed_unmerged"]
    assert result["stale"]["stale"] is True
