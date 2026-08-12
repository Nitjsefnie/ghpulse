"""Source contracts for the ghpulse chart's browser-facing surface."""
from __future__ import annotations

from pathlib import Path


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
    panel = src[src.index("function StackedCumulativeTimeSeriesPanel") :]
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
    panel = src[src.index("function StackedCumulativeTimeSeriesPanel") :]
    assert "safeSeries.map" in panel
    assert "bin.values[item.key]" in panel
    assert "cumulative[item.key]" in panel
    assert "maxCum" in panel
    assert "cumulative[item.key].map" in panel or "points={cumulative[item.key]" in panel


def test_tooltip_is_explicit_about_counts_and_selected_range_percentages():
    src = CHARTS_JSX.read_text(encoding="utf-8")
    panel = src[src.index("function StackedCumulativeTimeSeriesPanel") :]
    assert "period" in panel
    assert "cumulative" in panel
    assert "interval events" in panel
    assert "selected-range total" in panel
    assert "unique" not in panel.lower()


def test_panel_does_not_reintroduce_single_series_time_series_api():
    src = CHARTS_JSX.read_text(encoding="utf-8")
    assert "function TimeSeriesPanel" not in src
    assert "valueKey" not in src
