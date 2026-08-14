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


def test_app_does_not_render_server_exception_details():
    src = APP_JSX.read_text(encoding="utf-8")
    assert "body.detail" not in src


_DASHBOARD_STAT_LABEL_PROBE = """
  const fs = require('fs');
  global.window = {};
  // The served shell supplies React globally from public/index.html. A
  // recording createElement is enough to read the rendered element tree
  // without a DOM, and keeps this contract on the real component.
  global.React = {
    createElement: (type, props, ...children) => ({
      type,
      props: props || {},
      children: children.flat(Infinity),
    }),
    Fragment: 'Fragment',
    useState: () => [],
    useEffect: () => {},
    useMemo: () => {},
    useCallback: () => {},
    useRef: () => ({}),
  };
  const src = fs.readFileSync('src/app.jsx', 'utf8');
  const transpiler = new Bun.Transpiler({
    loader: 'jsx',
    target: 'es2020',
    tsconfig: JSON.stringify({compilerOptions: {
      jsx: 'react',
      jsxFactory: 'React.createElement',
      jsxFragmentFactory: 'React.Fragment',
    }}),
  });
  eval(transpiler.transformSync(src));
  const view = window.ghpulseAppContract.dashboardToViewModel({
    range: '7d',
    bucket_s: 3600,
    start: '2026-08-01T00:00:00Z',
    end: '2026-08-08T00:00:00Z',
    issues: [],
    pull_requests: [],
    summary: {
      repositories: 1,
      issues: {opened: 2},
      pull_requests: {opened: 1},
      last_ingest: '2026-08-01T00:00:00Z',
      sync_status: 'failure',
    },
    repositories: [],
  });
  const labels = [];
  const walk = node => {
    if (!node || typeof node !== 'object') return;
    // Expand nested function components so this reads the whole rendered
    // dashboard, not just DashboardView's immediate element.
    if (typeof node.type === 'function') {
      walk(node.type({...node.props, children: node.children}));
      return;
    }
    if (node.props && node.props.className === 'stat-label') {
      labels.push(node.children.join(''));
    }
    (node.children || []).forEach(walk);
  };
  walk(DashboardView({view}));
  console.log(JSON.stringify(labels));
"""


def _rendered_dashboard_stat_labels() -> list[str]:
    """Render the real DashboardView through the no-build JSX pipeline.

    This reads what the browser is actually handed rather than what the
    source file happens to mention, so a tile cannot survive as dead markup.
    """
    proc = subprocess.run(
        ["bun", "-e", _DASHBOARD_STAT_LABEL_PROBE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_dashboard_renders_activity_stats_without_operator_ingest_tiles():
    """Ingest freshness is operator diagnostics and belongs to /health only."""
    labels = _rendered_dashboard_stat_labels()

    for expected in (
        "external repositories",
        "issues opened",
        "issues completed",
        "issues not planned",
        "issues currently open",
        "pull requests opened",
        "pull requests merged",
        "pull requests closed unmerged",
        "pull requests currently open",
    ):
        assert expected in labels
    for forbidden in ("last ingest", "ingest status"):
        assert forbidden not in labels


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
        "summary": {
            "repositories": 1,
            "issues": {"opened": 2},
            "pull_requests": {},
            "last_ingest": "2026-08-08T00:00:00Z",
        },
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


def _run_app_contract_script(body: str) -> dict:
    """Run a Node probe against the real pure app contract block."""
    script = f"""
      const fs = require('fs');
      global.window = {{}};
      const src = fs.readFileSync('src/app.jsx', 'utf8');
      const start = src.indexOf('// --- pure app contracts ---');
      const end = src.indexOf('// --- React app ---', start);
      if (start < 0 || end <= start) throw new Error('pure app contract block missing');
      eval(src.slice(start, end));
      (async () => {{
        {body}
      }})().catch(error => {{ console.error(error.stack || error); process.exit(1); }});
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
    return json.loads(proc.stdout)


def _run_app_and_chart_contract_script(body: str) -> dict:
    """Run a Node probe against the app and production chart pure blocks."""
    script = f"""
      const fs = require('fs');
      global.window = {{}};
      const app = fs.readFileSync('src/app.jsx', 'utf8');
      const appStart = app.indexOf('// --- pure app contracts ---');
      const appEnd = app.indexOf('// --- React app ---', appStart);
      if (appStart < 0 || appEnd <= appStart) throw new Error('app contract block missing');
      eval(app.slice(appStart, appEnd));
      const chart = fs.readFileSync('src/dashboard-charts.jsx', 'utf8');
      const formatterStart = chart.indexOf('function humanFmt');
      const formatterEnd = chart.indexOf('function fmtDate', formatterStart);
      const chartStart = chart.indexOf('function boundedTimeIntervals');
      const chartEnd = chart.indexOf('function Tooltip', chartStart);
      if (formatterStart < 0 || formatterEnd <= formatterStart
          || chartStart < 0 || chartEnd <= chartStart) throw new Error('chart pure block missing');
      eval(chart.slice(formatterStart, formatterEnd) + '\\n' + chart.slice(chartStart, chartEnd));
      (async () => {{
        {body}
      }})().catch(error => {{ console.error(error.stack || error); process.exit(1); }});
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
    return json.loads(proc.stdout)


def test_dashboard_request_state_keeps_selection_provenance_and_drops_old_data():
    if shutil.which("node") is None:
        pytest.skip("node not available for the executable app contract")
    result = _run_app_contract_script(
        """
        const requests = [];
        const states = [];
        const coordinator = window.ghpulseAppContract.createDashboardRequestCoordinator({
          fetchJson: (selection, signal) => new Promise((resolve, reject) =>
            requests.push({selection, signal, resolve, reject})),
          onStateChange: state => states.push({
            phase: state.phase,
            selection: state.selection,
            dataSelection: state.data && state.data.selection,
            error: state.error,
          }),
        });
        const payload = (range, repository, start, end) => ({
          range, repository, bucket_s: 3600, start, end,
          issues: [{start, end, opened: 1, completed: 0, not_planned: 0}],
          pull_requests: [], summary: {}, repositories: [],
        });
        coordinator.load({range: '7d', repository: 'R_1'});
        requests[0].resolve(payload('7d', 'R_1', '2026-08-01T00:00:00Z', '2026-08-08T00:00:00Z'));
        await Promise.resolve(); await Promise.resolve();
        coordinator.load({range: '30d', repository: 'R_2'});
        const cleared = states[states.length - 1];
        requests[1].reject(new Error('new selection failed'));
        await Promise.resolve(); await Promise.resolve();
        const failed = states[states.length - 1];
        console.log(JSON.stringify({cleared, failed, requestCount: requests.length}));
        """,
    )
    assert result["requestCount"] == 2
    assert result["cleared"]["phase"] == "loading"
    assert result["cleared"]["selection"] == {"range": "30d", "repository": "R_2"}
    assert result["cleared"]["dataSelection"] is None
    assert result["failed"]["phase"] == "error"
    assert result["failed"]["selection"] == {"range": "30d", "repository": "R_2"}
    assert result["failed"]["dataSelection"] is None


def test_dashboard_request_state_suppresses_out_of_order_results_and_accepts_dense_payload():
    if shutil.which("node") is None:
        pytest.skip("node not available for the executable app contract")
    result = _run_app_contract_script(
        """
        const requests = [];
        const states = [];
        const coordinator = window.ghpulseAppContract.createDashboardRequestCoordinator({
          fetchJson: (selection, signal) => new Promise((resolve, reject) =>
            requests.push({selection, signal, resolve, reject})),
          onStateChange: state => states.push(state),
        });
        const dense = {
          range: '7d', repository: 'R_2', bucket_s: 3600,
          start: '2026-08-01T00:00:00Z', end: '2026-08-08T00:00:00Z',
          issues: [
            {start: '2026-08-01T00:00:00Z', end: '2026-08-02T00:00:00Z',
              opened: 2, completed: 1, not_planned: 0},
            {start: '2026-08-02T00:00:00Z', end: '2026-08-08T00:00:00Z',
              opened: 0, completed: 0, not_planned: 1},
          ], pull_requests: [], summary: {}, repositories: [],
        };
        coordinator.load({range: '7d', repository: 'R_1'});
        coordinator.load({range: '7d', repository: 'R_2'});
        requests[0].resolve({range: '7d', repository: 'R_1', start: dense.start, end: dense.end,
          bucket_s: 3600, issues: [], pull_requests: [], summary: {}, repositories: []});
        requests[1].resolve(dense);
        await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
        const finalState = states[states.length - 1];
        const view = window.ghpulseAppContract.dashboardToViewModel(finalState.data.body);
        console.log(JSON.stringify({
          phase: finalState.phase, selection: finalState.data.selection,
          first: view.issues.events[0], last: view.issues.events[1],
          requestCount: requests.length,
        }));
        """,
    )
    assert result["requestCount"] == 2
    assert result["phase"] == "ready"
    assert result["selection"] == {"range": "7d", "repository": "R_2"}
    assert result["first"]["start"] == 1785542400000
    assert result["last"]["end"] == 1786147200000
    assert result["last"]["not_planned"] == 1


def test_dashboard_payload_buckets_feed_production_chart_geometry_exactly():
    if shutil.which("node") is None:
        pytest.skip("node not available for the executable app contract")
    result = _run_app_and_chart_contract_script(
        """
        const body = {
          range: '7d', repository: 'R_2', bucket_s: 3600,
          start: '2026-08-01T00:00:00Z', end: '2026-08-01T03:00:00Z',
          issues: [
            {start: '2026-08-01T00:00:00Z', end: '2026-08-01T01:00:00Z',
              opened: 2, completed: 1, not_planned: 0},
            {start: '2026-08-01T01:00:00Z', end: '2026-08-01T02:00:00Z',
              opened: 0, completed: 0, not_planned: 1},
            {start: '2026-08-01T02:00:00Z', end: '2026-08-01T03:00:00Z',
              opened: 1, completed: 0, not_planned: 0},
          ],
          pull_requests: [], summary: {}, repositories: [],
        };
        const model = window.ghpulseAppContract.dashboardToViewModel(body);
        const built = buildStackedTimeSeriesData(
          model.issues.events, model.issues.series, model.range, model.binMs);
        console.log(JSON.stringify({
          binMs: model.binMs,
          bounds: built.bins.map(bin => [bin.start, bin.end]),
          values: built.bins.map(bin => bin.values),
          totals: built.totals,
          cumulativeEnd: Object.fromEntries(Object.entries(built.cumulative).map(
            ([key, points]) => [key, points.at(-1).v])),
        }));
        """,
    )
    assert result["binMs"] == 3_600_000
    assert result["bounds"] == [
        [1785542400000, 1785546000000],
        [1785546000000, 1785549600000],
        [1785549600000, 1785553200000],
    ]
    assert result["values"] == [
        {"opened": 2, "completed": 1, "not_planned": 0},
        {"opened": 0, "completed": 0, "not_planned": 1},
        {"opened": 1, "completed": 0, "not_planned": 0},
    ]
    assert result["totals"] == {"opened": 3, "completed": 1, "not_planned": 1}
    assert result["cumulativeEnd"] == {"opened": 3, "completed": 1, "not_planned": 1}


def test_repository_options_preserve_disappeared_or_deep_linked_selection():
    if shutil.which("node") is None:
        pytest.skip("node not available for the executable app contract")
    result = _run_app_contract_script(
        """
        const history = {
          R_1: {node_id: 'R_1', name_with_owner: 'external/one', url: 'https://example.test/one'},
        };
        const disappeared = window.ghpulseAppContract.repositoryOptionsForSelection(
          [{node_id: 'R_2', name_with_owner: 'external/two'}], 'R_1', history);
        const deepLinked = window.ghpulseAppContract.repositoryOptionsForSelection(
          [], 'R_DEEP', {});
        console.log(JSON.stringify({disappeared, deepLinked}));
        """,
    )
    assert result["disappeared"][-1] == {
        "node_id": "R_1",
        "name_with_owner": "external/one · unavailable in selected range",
        "url": "https://example.test/one",
        "unavailable": True,
    }
    assert result["deepLinked"] == [{
        "node_id": "R_DEEP",
        "name_with_owner": "R_DEEP · unavailable in selected range",
        "unavailable": True,
    }]


def test_sse_refetches_current_selection_reconnects_and_cleans_up():
    if shutil.which("node") is None:
        pytest.skip("node not available for the executable app contract")
    result = _run_app_contract_script(
        """
        class MockSource {
          constructor() { this.listeners = {}; this.closed = false; }
          addEventListener(name, handler) { (this.listeners[name] ||= new Set()).add(handler); }
          removeEventListener(name, handler) { this.listeners[name]?.delete(handler); }
          emit(name) { for (const handler of this.listeners[name] || []) handler(); }
          close() { this.closed = true; }
        }
        const requests = [];
        const streamStates = [];
        let source;
        const coordinator = window.ghpulseAppContract.createDashboardRequestCoordinator({
          fetchJson: (selection, signal, fresh) => new Promise((resolve, reject) =>
            requests.push({selection, signal, fresh, resolve, reject})),
          onStateChange: () => {},
          onStreamStateChange: state => streamStates.push(state),
          eventSourceFactory: () => (source = new MockSource()),
        });
        coordinator.connect();
        coordinator.load({range: '30d', repository: 'R_1'});
        requests[0].resolve({range: '30d', repository: 'R_1', bucket_s: 3600,
          start: '2026-08-01T00:00:00Z', end: '2026-08-02T00:00:00Z',
          issues: [], pull_requests: [], summary: {}, repositories: []});
        await Promise.resolve(); await Promise.resolve();
        source.emit('open'); source.emit('error'); source.emit('ingest_done');
        const refetched = requests.length;
        requests[1].resolve({range: '30d', repository: 'R_1', bucket_s: 3600,
          start: '2026-08-01T00:00:00Z', end: '2026-08-02T00:00:00Z',
          issues: [], pull_requests: [], summary: {}, repositories: []});
        await Promise.resolve(); await Promise.resolve();
        coordinator.dispose();
        source.emit('ingest_done');
        console.log(JSON.stringify({
          refetched, afterDispose: requests.length, closed: source.closed,
          streamStates, fresh: requests[1].fresh,
        }));
        """,
    )
    assert result["refetched"] == 2
    assert result["afterDispose"] == 2
    assert result["closed"] is True
    assert result["streamStates"] == ["connected", "reconnecting", "connected"]
    assert result["fresh"] is True


def test_same_selection_sse_failure_keeps_prior_data_and_marks_it_stale():
    if shutil.which("node") is None:
        pytest.skip("node not available for the executable app contract")
    result = _run_app_contract_script(
        """
        class MockSource {
          constructor() { this.listeners = {}; this.closed = false; }
          addEventListener(name, handler) { (this.listeners[name] ||= new Set()).add(handler); }
          removeEventListener(name, handler) { this.listeners[name]?.delete(handler); }
          emit(name) { for (const handler of this.listeners[name] || []) handler(); }
          close() { this.closed = true; }
        }
        const requests = [];
        const states = [];
        let source;
        const coordinator = window.ghpulseAppContract.createDashboardRequestCoordinator({
          fetchJson: (selection, signal) => new Promise((resolve, reject) =>
            requests.push({selection, signal, resolve, reject})),
          onStateChange: state => states.push({
            phase: state.phase,
            selection: state.selection,
            data: state.data,
          }),
          eventSourceFactory: () => (source = new MockSource()),
        });
        const selection = {range: '30d', repository: 'R_1'};
        const body = {marker: 'prior-valid-body', range: '30d', repository: 'R_1'};
        coordinator.connect();
        coordinator.load(selection);
        requests[0].resolve(body);
        await Promise.resolve(); await Promise.resolve();
        source.emit('ingest_done');
        requests[1].reject(new Error('refresh unavailable'));
        await Promise.resolve(); await Promise.resolve();
        const finalState = states[states.length - 1];
        console.log(JSON.stringify({
          phase: finalState.phase,
          selection: finalState.selection,
          dataSelection: finalState.data && finalState.data.selection,
          marker: finalState.data && finalState.data.body.marker,
          requestCount: requests.length,
        }));
        """,
    )
    assert result == {
        "phase": "stale",
        "selection": {"range": "30d", "repository": "R_1"},
        "dataSelection": {"range": "30d", "repository": "R_1"},
        "marker": "prior-valid-body",
        "requestCount": 2,
    }
