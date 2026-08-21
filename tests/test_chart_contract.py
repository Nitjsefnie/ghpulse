"""Source contracts for the ghpulse chart's browser-facing surface."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHARTS_JSX = ROOT / "src" / "dashboard-charts.jsx"


def test_chart_exports_the_public_component_and_pure_helper():
    src = CHARTS_JSX.read_text(encoding="utf-8")
    assert "function StackedCumulativeTimeSeriesPanel" in src
    assert "function buildStackedTimeSeriesData" in src
    assert "window.StackedCumulativeTimeSeriesPanel" in src
    assert "window.buildStackedTimeSeriesData" in src


def test_chart_keeps_claudit_responsive_geometry_hooks():
    src = CHARTS_JSX.read_text(encoding="utf-8")
    panel = src[src.index("function StackedCumulativeTimeSeriesPanel"):]
    assert "new ResizeObserver" in panel
    assert "useLayoutEffect" in panel
    assert "getComputedTextLength" in panel
    assert "timeTicksUTC(range.start, range.end)" in panel
    assert "timeBarRect(bin, range, padL, plotW)" in panel
    assert "timeBinIndexAtX(bins, range, padL, plotW, mx)" in panel
    assert 'data-plot-boundary=""' in panel
    assert 'data-time-bar=""' in panel
    assert 'data-cumulative-line=""' in panel
    assert "clipPath" in panel


def test_chart_renders_stacked_bars_and_one_cumulative_line_per_series():
    src = CHARTS_JSX.read_text(encoding="utf-8")
    panel = src[src.index("function StackedCumulativeTimeSeriesPanel"):]
    assert "safeSeries.map" in panel
    assert "buildStackedBarSegments(" in panel
    assert "cumulative[item.key]" in panel
    assert "maxCum" in panel
    assert "cumulative[item.key].map" in panel or "points={cumulative[item.key]" in panel


def test_tooltip_is_explicit_about_counts_and_selected_range_percentages():
    src = CHARTS_JSX.read_text(encoding="utf-8")
    panel = src[src.index("function StackedCumulativeTimeSeriesPanel"):]
    assert "buildTooltipLines(" in panel
    assert "unique" not in panel.lower()


def test_panel_does_not_reintroduce_single_series_time_series_api():
    src = CHARTS_JSX.read_text(encoding="utf-8")
    assert "function TimeSeriesPanel" not in src
    assert "valueKey" not in src


def test_full_jsx_parses_through_the_bun_no_build_transpiler():
    if shutil.which("bun") is None:
        pytest.skip("bun not available for the executable JSX contract")
    script = f"""
      const fs = require('fs');
      const src = fs.readFileSync({str(CHARTS_JSX)!r}, 'utf8');
      const transpiler = new Bun.Transpiler({{loader: 'jsx', target: 'es2020'}});
      const output = transpiler.transformSync(src);
      console.log(JSON.stringify({{length: output.length,
        hasComponent: output.includes('StackedCumulativeTimeSeriesPanel')}}));
    """
    proc = subprocess.run(
        ["bun", "-e", script], cwd=ROOT, capture_output=True,
        text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["length"] > 0
    assert result["hasComponent"]


def test_pure_contract_helpers_are_exposed_for_executable_validation():
    src = CHARTS_JSX.read_text(encoding="utf-8")
    assert "function buildStackedBarSegments" in src
    assert "function buildTooltipLines" in src
    assert "function layoutLegend" in src
    assert "window.buildStackedBarSegments" in src
    assert "window.buildTooltipLines" in src
    assert "window.layoutLegend" in src


def test_tooltip_helper_uses_the_production_value_formatter():
    src = CHARTS_JSX.read_text(encoding="utf-8")
    helper = src[
        src.index("function buildTooltipLines"):src.index("function wrapLegendLabel")
    ]
    assert "humanFmt(" in helper
    assert "tooltipFmt" not in src
