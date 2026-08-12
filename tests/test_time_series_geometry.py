"""Behavioral geometry tests for the ghpulse stacked timeline panel.

The chart is served as JSX through Babel in the browser, so the pure helper
and geometry functions are extracted from the source and exercised by Node.
This keeps the tests on the real production functions without introducing a
frontend test framework.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHARTS_JSX = ROOT / "src" / "dashboard-charts.jsx"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


SERIES = [
    {"key": "opened", "label": "Opened", "color": "#00d4aa"},
    {"key": "completed", "label": "Completed", "color": "#ff9c5a"},
    {"key": "not_planned", "label": "Not planned", "color": "#a98bff"},
]


def _node(script: str) -> dict:
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


def _probe(events: list[dict], range_: dict, bin_ms: int) -> dict:
    script = f"""
      const fs = require('fs');
      const src = fs.readFileSync('src/dashboard-charts.jsx', 'utf8');
      const start = src.indexOf('function boundedTimeIntervals');
      const end = src.indexOf('function Tooltip', start);
      if (start < 0 || end < 0 || end <= start) throw new Error('geometry block missing');
      eval(src.slice(start, end) + '\\nwindow.__chart = {{' +
        'buildStackedTimeSeriesData, timeX, timeBarRect, timeBinIndexAtX,' +
        'boundedTimeIntervals' +
        '}};');
      const series = {json.dumps(SERIES)};
      const events = {json.dumps(events)};
      const range = {json.dumps(range_)};
      const built = window.__chart.buildStackedTimeSeriesData(events, series, range, {bin_ms});
      const rects = built.bins.map(bin => window.__chart.timeBarRect(bin, range, 60, 400));
      const cumulativeX = window.__chart.cumulativeXForTest
        ? window.__chart.cumulativeXForTest(built, range, 60, 400)
        : Object.fromEntries(Object.entries(built.cumulative).map(([key, points]) =>
            [key, points.map(point => window.__chart.timeX(point.ts, range, 60, 400))]));
      const indices = [59, 60, 299, 300, 459, 460, 461]
        .map(x => window.__chart.timeBinIndexAtX(built.bins, range, 60, 400, x));
      console.log(JSON.stringify({{built, rects, cumulativeX, indices}}));
    """
    # The probe is deliberately self-contained.  The production source only
    # exposes helpers through the browser global, not through CommonJS.
    script = "global.window = {};\n" + script
    return _node(script)


def test_intervals_are_half_open_and_final_bin_is_capped():
    result = _probe(
        [
            {"ts": 10, "key": "opened"},
            {"ts": 100, "key": "completed"},
            {"ts": 120, "key": "opened"},
            {"ts": 250, "key": "opened"},
            {"ts": 390, "key": "not_planned"},
            {"ts": 400, "key": "opened"},
        ],
        {"start": 0, "end": 400},
        120,
    )
    assert [(b["start"], b["end"]) for b in result["built"]["bins"]] == [
        (0, 120),
        (120, 240),
        (240, 360),
        (360, 400),
    ]
    assert result["built"]["bins"][0]["values"] == {
        "opened": 1,
        "completed": 1,
        "not_planned": 0,
    }
    assert result["built"]["bins"][-1]["values"] == {
        "opened": 0,
        "completed": 0,
        "not_planned": 1,
    }
    assert result["built"]["totals"] == {
        "opened": 3,
        "completed": 1,
        "not_planned": 1,
    }


def test_stack_totals_and_each_cumulative_series_have_one_zero_anchor():
    result = _probe(
        [
            {"ts": 10, "key": "opened", "value": 2},
            {"ts": 110, "key": "completed"},
            {"ts": 130, "key": "opened"},
            {"ts": 250, "key": "not_planned"},
        ],
        {"start": 0, "end": 400},
        120,
    )
    bins = result["built"]["bins"]
    assert [b["total"] for b in bins] == [3, 1, 1, 0]
    assert result["built"]["cumulative"]["opened"] == [
        {"ts": 0, "v": 0, "binIdx": -1},
        {"ts": 120, "v": 2, "binIdx": 0},
        {"ts": 240, "v": 3, "binIdx": 1},
        {"ts": 360, "v": 3, "binIdx": 2},
        {"ts": 400, "v": 3, "binIdx": 3},
    ]
    for key in ("opened", "completed", "not_planned"):
        assert result["built"]["cumulative"][key][0] == {
            "ts": 0,
            "v": 0,
            "binIdx": -1,
        }
        assert result["built"]["cumulative"][key][-1]["ts"] == 400


def test_bar_and_line_geometry_stays_inside_plot_edges():
    result = _probe(
        [{"ts": 1, "key": "opened"}, {"ts": 399, "key": "completed"}],
        {"start": 0, "end": 400},
        120,
    )
    assert result["rects"] == [
        {"x": 60, "width": 108},
        {"x": 180, "width": 108},
        {"x": 300, "width": 108},
        {"x": 420, "width": 36},
    ]
    assert all(rect["x"] >= 60 for rect in result["rects"])
    assert all(rect["x"] + rect["width"] <= 460 for rect in result["rects"])
    assert result["cumulativeX"]["opened"] == [60, 180, 300, 420, 460]
    assert result["cumulativeX"]["completed"] == [60, 180, 300, 420, 460]
    assert result["indices"] == [-1, 0, 1, 2, 3, 3, -1]


def test_empty_and_zero_series_are_deterministic():
    result = _probe([], {"start": 0, "end": 400}, 120)
    assert all(b["total"] == 0 for b in result["built"]["bins"])
    assert result["built"]["totals"] == {
        "opened": 0,
        "completed": 0,
        "not_planned": 0,
    }
    assert all(
        points == [
            {"ts": 0, "v": 0, "binIdx": -1},
            {"ts": 120, "v": 0, "binIdx": 0},
            {"ts": 240, "v": 0, "binIdx": 1},
            {"ts": 360, "v": 0, "binIdx": 2},
            {"ts": 400, "v": 0, "binIdx": 3},
        ]
        for points in result["built"]["cumulative"].values()
    )


def test_unsorted_events_and_bucket_rows_do_not_change_the_result():
    result = _probe(
        [
            {"ts": 250, "opened": 2, "completed": 0, "not_planned": 0},
            {"ts": 20, "opened": 1, "completed": 1, "not_planned": 0},
        ],
        {"start": 0, "end": 400},
        120,
    )
    assert result["built"]["totals"] == {
        "opened": 3,
        "completed": 1,
        "not_planned": 0,
    }


def test_small_width_plot_has_bounded_geometry():
    script = r"""
      const fs = require('fs');
      const src = fs.readFileSync('src/dashboard-charts.jsx', 'utf8');
      const start = src.indexOf('function boundedTimeIntervals');
      const end = src.indexOf('function Tooltip', start);
      eval(src.slice(start, end) + '\nwindow.__chart = {timeX, timeBarRect};');
      const range = {start: 0, end: 400};
      const bin = {start: 360, end: 400};
      const rect = window.__chart.timeBarRect(bin, range, 60, 1);
      console.log(JSON.stringify({rect, end: window.__chart.timeX(400, range, 60, 1)}));
    """
    result = _node("global.window = {};\n" + script)
    assert result["rect"]["x"] >= 60
    assert result["rect"]["x"] + result["rect"]["width"] <= 61
    assert result["end"] == 61
