// ghpulse dashboard chart components.
//
// This file intentionally follows Claudit's no-build-step browser contract:
// React and Babel are supplied by public/index.html and the public components
// are published on window for the later application shell.

const cssVar = (name, fallback) => {
  if (typeof window === 'undefined' || typeof document === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name).trim();
  return value || fallback;
};

const TH = {
  bgAxes: cssVar('--bg-card', '#12141d'),
  border: cssVar('--border', '#1f2230'),
  text: cssVar('--fg', '#e7e9f2'),
  textDim: cssVar('--muted', '#6b7193'),
  grid: 'rgba(255,255,255,0.05)',
};

function humanFmt(value) {
  const number = Number(value) || 0;
  const abs = Math.abs(number);
  if (abs >= 1e9) return (number / 1e9).toFixed(2).replace(/\.?0+$/, '') + 'B';
  if (abs >= 1e6) return (number / 1e6).toFixed(2).replace(/\.?0+$/, '') + 'M';
  if (abs >= 1e3) return (number / 1e3).toFixed(1).replace(/\.?0+$/, '') + 'K';
  return String(Math.round(number));
}

function fmtDate(ts, opts = {}) {
  const date = new Date(ts);
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  if (opts.month) return months[date.getUTCMonth()] + ' ' + date.getUTCFullYear();
  if (opts.day) return months[date.getUTCMonth()] + ' ' + date.getUTCDate();
  if (opts.full) {
    return `${months[date.getUTCMonth()]} ${String(date.getUTCDate()).padStart(2, '0')} ` +
      `${String(date.getUTCHours()).padStart(2, '0')}:${String(date.getUTCMinutes()).padStart(2, '0')}`;
  }
  return date.toISOString();
}

const HOUR_MS = 3600_000;

// Adaptive UTC x-axis ticks copied from Claudit's proven time-series chart.
function timeTicksUTC(start, end) {
  const DAY = 24 * HOUR_MS;
  const span = Math.max(1, end - start);
  const ticks = [];
  if (span >= 80 * DAY) {
    const all = [];
    const date = new Date(start);
    let month = date.getUTCMonth();
    let year = date.getUTCFullYear();
    for (let iteration = 0; iteration < 60; iteration++) {
      const ts = Date.UTC(year, month, 1);
      if (ts > end) break;
      if (ts > start) all.push(ts);
      month += 1;
      if (month > 11) { month = 0; year += 1; }
    }
    const step = Math.max(1, Math.ceil(all.length / 12));
    for (let index = 0; index < all.length; index += step) {
      ticks.push({ts: all[index], label: fmtDate(all[index], {month: true})});
    }
  } else if (span >= 3 * DAY) {
    const stepDays = span > 50 * DAY ? 7 : span > 25 * DAY ? 4 : span > 12 * DAY ? 2 : 1;
    const date = new Date(start);
    let ts = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
    while (ts <= end) {
      if (ts > start) ticks.push({ts, label: fmtDate(ts, {day: true})});
      ts += stepDays * DAY;
    }
  } else {
    const stepHours = span > 36 * HOUR_MS ? 6 : span > 18 * HOUR_MS ? 3 : span > 8 * HOUR_MS ? 2 : 1;
    let ts = Math.ceil(start / (stepHours * HOUR_MS)) * (stepHours * HOUR_MS);
    while (ts <= end) {
      const date = new Date(ts);
      ticks.push({ts, label: `${String(date.getUTCHours()).padStart(2, '0')}:00`});
      ts += stepHours * HOUR_MS;
    }
  }
  return ticks;
}

function binMsLabel(ms) {
  if (ms < HOUR_MS) return `${ms / 60_000}m`;
  if (ms < 24 * HOUR_MS) return `${ms / HOUR_MS}h`;
  return `${ms / (24 * HOUR_MS)}d`;
}

function boundedTimeIntervals(range, binMs) {
  if (!range || !Number.isFinite(range.start) || !Number.isFinite(range.end)
      || range.end <= range.start || !Number.isFinite(binMs) || binMs <= 0) {
    throw new TypeError('Invalid time-series range or bin width');
  }
  const bins = [];
  for (let start = range.start; start < range.end;) {
    const end = Math.min(start + binMs, range.end);
    bins.push({start, end});
    if (end >= range.end) break;
    start = end;
  }
  return bins;
}

function eventTimestamp(event) {
  if (!event) return NaN;
  const raw = event.ts != null ? event.ts
    : event.start != null ? event.start
      : event.bucket_start != null ? event.bucket_start : event.time;
  if (raw == null) return NaN;
  if (typeof raw === 'number') return raw;
  if (raw instanceof Date) return raw.getTime();
  const numeric = Number(raw);
  if (Number.isFinite(numeric)) return numeric;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : NaN;
}

function eventAmount(value, fallback = 1) {
  if (value == null || value === '') return fallback;
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, number) : 0;
}

function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key);
}

function denseIntervalInput(events) {
  return Array.isArray(events) && events.some(event => event && (
    hasOwn(event, 'start') || hasOwn(event, 'end')
  ));
}

function validateSeries(series) {
  const seen = new Set();
  (Array.isArray(series) ? series : []).forEach((item, index) => {
    if (!item || typeof item.key !== 'string' || !item.key || seen.has(item.key)) {
      throw new TypeError(`Invalid chart series at index ${index}`);
    }
    seen.add(item.key);
  });
}

function validateTimeSeriesInputs(range, binMs) {
  if (!range || !Number.isFinite(range.start) || !Number.isFinite(range.end)
      || range.end <= range.start || !Number.isFinite(binMs) || binMs <= 0) {
    throw new TypeError('Invalid time-series range or bin width');
  }
}

function normalizedDenseIntervals(events, series, range) {
  const keys = series.map(item => item.key);
  const intervals = [];
  let previousStart = null;
  let previousEnd = null;

  events.forEach((event, index) => {
    if (!event || !hasOwn(event, 'start') || !hasOwn(event, 'end')) {
      throw new TypeError(`Malformed dense interval at index ${index}`);
    }
    const start = eventTimestamp({start: event.start});
    const end = eventTimestamp({start: event.end});
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      throw new TypeError(`Malformed dense interval at index ${index}`);
    }
    if (start < range.start || end > range.end) {
      throw new TypeError(`Dense interval at index ${index} is outside the selected range`);
    }
    if (previousStart !== null && start < previousStart) {
      throw new TypeError('Dense intervals must be ordered by start timestamp');
    }
    if (previousEnd !== null && start < previousEnd) {
      throw new TypeError(`Overlapping dense interval at index ${index}`);
    }

    const values = Object.fromEntries(keys.map(key => [key, eventAmount(event[key], 0)]));
    const total = keys.reduce((sum, key) => sum + values[key], 0);
    intervals.push({start, end, values, total});
    previousStart = start;
    previousEnd = end;
  });
  return intervals;
}

// Normalize both useful browser inputs without making the chart depend on
// the API layer.  A discrete event is {ts, key, value?}; a dense API bucket
// is {start, end, opened, completed, not_planned} and is handled separately
// below so the API's clipped boundaries survive intact.  Point entries are
// never the caller's objects and are sorted deterministically.
function normalizedSeriesEvents(events, series, range) {
  const keys = new Set(series.map(item => item.key));
  const normalized = [];
  (Array.isArray(events) ? events : []).forEach((event, index) => {
    const ts = eventTimestamp(event);
    if (!Number.isFinite(ts) || ts < range.start || ts >= range.end) return;
    const values = {};
    const keyed = event.key != null ? event.key
      : event.series != null ? event.series
        : event.kind != null ? event.kind : null;
    if (keyed != null) {
      if (keys.has(keyed)) {
        values[keyed] = eventAmount(
          event.value != null ? event.value : event.count,
          1,
        );
      }
    } else {
      series.forEach(item => {
        if (Object.prototype.hasOwnProperty.call(event, item.key)) {
          values[item.key] = eventAmount(event[item.key], 0);
        }
      });
    }
    if (Object.keys(values).length) normalized.push({ts, index, values});
  });
  normalized.sort((left, right) => left.ts - right.ts || left.index - right.index);
  return normalized;
}

function buildStackedTimeSeriesData(events, series, range, binMs) {
  const safeSeries = Array.isArray(series) ? series : [];
  validateTimeSeriesInputs(range, binMs);
  validateSeries(safeSeries);
  const dense = denseIntervalInput(events);
  const bins = dense
    ? normalizedDenseIntervals(events, safeSeries, range)
    : boundedTimeIntervals(range, binMs).map(interval => ({
      ...interval,
      values: Object.fromEntries(safeSeries.map(item => [item.key, 0])),
      total: 0,
    }));
  const totals = Object.fromEntries(safeSeries.map(item => [item.key, 0]));
  if (dense) {
    bins.forEach(bin => {
      safeSeries.forEach(item => {
        totals[item.key] += bin.values[item.key];
      });
    });
  }
  const normalized = dense ? [] : normalizedSeriesEvents(events, safeSeries, range);
  let eventIndex = 0;
  if (!dense) {
    bins.forEach((bin) => {
      while (eventIndex < normalized.length && normalized[eventIndex].ts < bin.end) {
        const values = normalized[eventIndex].values;
        Object.keys(values).forEach(key => {
          const value = values[key];
          bin.values[key] += value;
          bin.total += value;
          totals[key] += value;
        });
        eventIndex += 1;
      }
    });
  }

  const cumulative = {};
  safeSeries.forEach(item => {
    let value = 0;
    const points = [{ts: range.start, v: 0, binIdx: -1}];
    bins.forEach((bin, binIdx) => {
      value += bin.values[item.key];
      points.push({ts: bin.end, v: value, binIdx});
    });
    cumulative[item.key] = points;
  });
  return {bins, cumulative, totals};
}

function buildStackedBarSegments(
  bin, series, range, padL, plotW, maxValue, plotH = 1, padT = 0, barRect = null,
) {
  const safeSeries = Array.isArray(series) ? series : [];
  const rect = barRect || timeBarRect(bin, range, padL, plotW);
  const safeMax = Math.max(1, Number(maxValue) || 0);
  const height = Math.max(0, Number(plotH) || 0);
  let baseline = 0;
  return safeSeries.map(item => {
    const value = eventAmount(bin && bin.values ? bin.values[item.key] : 0, 0);
    const bottom = padT + height - (baseline / safeMax) * height;
    baseline += value;
    const top = padT + height - (baseline / safeMax) * height;
    return {
      key: item.key,
      color: item.color,
      value,
      x: rect.x,
      width: rect.width,
      y: top,
      height: Math.max(0, bottom - top),
    };
  });
}

function tooltipFmt(value) {
  const number = Number(value) || 0;
  const abs = Math.abs(number);
  if (abs >= 1e9) return (number / 1e9).toFixed(2).replace(/\.?0+$/, '') + 'B';
  if (abs >= 1e6) return (number / 1e6).toFixed(2).replace(/\.?0+$/, '') + 'M';
  if (abs >= 1e3) return (number / 1e3).toFixed(1).replace(/\.?0+$/, '') + 'K';
  return String(Math.round(number));
}

function buildTooltipLines(bin, cumulative, series, binIdx, totals) {
  const safeSeries = Array.isArray(series) ? series : [];
  const safeCumulative = cumulative || {};
  const safeTotals = totals || {};
  const index = Number.isInteger(binIdx) ? binIdx : 0;
  const lines = [];
  safeSeries.forEach(item => {
    const period = eventAmount(bin && bin.values ? bin.values[item.key] : 0, 0);
    const points = Array.isArray(safeCumulative[item.key]) ? safeCumulative[item.key] : [];
    const point = points[index + 1];
    lines.push([`${item.label} period`, tooltipFmt(period), item.color]);
    lines.push([`${item.label} cumulative`, tooltipFmt(point ? point.v : 0), item.color]);
  });
  lines.push(['interval events', String(bin && Number.isFinite(bin.total) ? bin.total : 0)]);
  safeSeries.forEach(item => {
    const period = eventAmount(bin && bin.values ? bin.values[item.key] : 0, 0);
    const selectedTotal = eventAmount(safeTotals[item.key], 0);
    lines.push([`${item.label} % selected-range total`, selectedTotal > 0
      ? `${((period / selectedTotal) * 100).toFixed(2)}%` : '0%', item.color]);
  });
  return lines;
}

function wrapLegendLabel(label, maxChars) {
  const words = String(label == null ? '' : label).trim().split(/\s+/).filter(Boolean);
  if (!words.length) return [''];
  const chunks = [];
  words.forEach(word => {
    if (word.length <= maxChars) {
      chunks.push(word);
      return;
    }
    for (let offset = 0; offset < word.length; offset += maxChars) {
      chunks.push(word.slice(offset, offset + maxChars));
    }
  });
  const lines = [];
  chunks.forEach(chunk => {
    const current = lines[lines.length - 1];
    if (current && current.length + 1 + chunk.length <= maxChars) {
      lines[lines.length - 1] = `${current} ${chunk}`;
    } else {
      lines.push(chunk);
    }
  });
  return lines;
}

function layoutLegend(series, width, charPx = 6.4) {
  const available = Math.max(1, Number(width) || 1);
  const measuredCharPx = Math.max(1, Number(charPx) || 6.4);
  const markerAndGap = 24;
  const gap = 6;
  const rowHeight = 16;
  const maxChars = Math.max(1, Math.floor((available - markerAndGap) / measuredCharPx));
  const items = [];
  let cursorX = 0;
  let cursorY = 0;
  let currentRowHeight = rowHeight;
  (Array.isArray(series) ? series : []).forEach(item => {
    const labelLines = wrapLegendLabel(item && item.label, maxChars);
    const longest = Math.max(...labelLines.map(line => line.length), 1);
    const itemWidth = Math.min(available, Math.max(30, markerAndGap + longest * measuredCharPx));
    const itemHeight = Math.max(rowHeight, labelLines.length * rowHeight);
    if (cursorX > 0 && cursorX + itemWidth > available) {
      cursorX = 0;
      cursorY += currentRowHeight;
      currentRowHeight = rowHeight;
    }
    items.push({
      key: item && item.key,
      color: item && item.color,
      labelLines,
      maxChars,
      x: cursorX,
      y: cursorY,
      width: itemWidth,
      height: itemHeight,
    });
    cursorX += itemWidth + gap;
    currentRowHeight = Math.max(currentRowHeight, itemHeight);
  });
  return {
    width: available,
    height: items.length ? cursorY + currentRowHeight : 0,
    items,
  };
}

function timeX(ts, range, padL, plotW) {
  const width = Math.max(0, Number(plotW) || 0);
  const span = range.end - range.start;
  if (!Number.isFinite(span) || span <= 0) return padL;
  const raw = padL + ((ts - range.start) / span) * width;
  return Math.min(padL + width, Math.max(padL, raw));
}

function timeBarRect(bin, range, padL, plotW) {
  const x = timeX(bin.start, range, padL, plotW);
  const endX = timeX(bin.end, range, padL, plotW);
  const available = Math.max(0, Number(plotW) || 0);
  return {x, width: Math.min(available, Math.max(0, (endX - x) * 0.9))};
}

function timeBinIndexAtX(bins, range, padL, plotW, x) {
  const width = Number(plotW) || 0;
  if (!bins.length || width <= 0 || x < padL || x > padL + width) return -1;
  const ts = range.start + ((x - padL) / width) * (range.end - range.start);
  return bins.findIndex((bin, index) => ts >= bin.start && (
    ts < bin.end || (index === bins.length - 1 && ts === bin.end))
  );
}

function Tooltip({tip}) {
  const ref = React.useRef(null);
  const [position, setPosition] = React.useState({left: 0, top: 0, ready: false});
  React.useLayoutEffect(() => {
    if (!tip || !ref.current) return;
    const element = ref.current;
    const width = element.offsetWidth;
    const height = element.offsetHeight;
    const parentRect = element.offsetParent
      ? element.offsetParent.getBoundingClientRect()
      : {left: 0, top: 0};
    const margin = 8;
    let left = tip.x + 12;
    let top = tip.y + 12;
    if (parentRect.left + left + width > window.innerWidth - margin) left = tip.x - width - 12;
    if (parentRect.top + top + height > window.innerHeight - margin) top = tip.y - height - 12;
    left = Math.max(-parentRect.left + margin, left);
    top = Math.max(-parentRect.top + margin, top);
    setPosition({left, top, ready: true});
  }, [tip]);
  if (!tip) return null;
  return (
    <div ref={ref} className="chart-tooltip" style={{
      position: 'absolute', left: position.left, top: position.top,
      visibility: position.ready ? 'visible' : 'hidden',
      borderColor: tip.accent || undefined, pointerEvents: 'none',
      zIndex: 5, maxWidth: 300,
    }}>
      {tip.title && <div className="chart-tooltip-title" style={{color: tip.accent || undefined}}>
        {tip.title}
      </div>}
      {(tip.lines || []).map((line, index) => (
        <div key={index} className="chart-tooltip-row">
          <span className="chart-tooltip-key">{line[0]}</span>
          <span className="chart-tooltip-val" style={{color: line[2] || undefined}}>{line[1]}</span>
        </div>
      ))}
    </div>
  );
}

// --- Stacked interval bars plus independent cumulative lines ---
function StackedCumulativeTimeSeriesPanel({title, events, series, range, binMs}) {
  const ref = React.useRef(null);
  const [size, setSize] = React.useState({w: 600, h: 280});
  const [tip, setTip] = React.useState(null);
  const [yLabelPx, setYLabelPx] = React.useState(0);
  const safeSeries = Array.isArray(series) ? series : [];
  const {bins, cumulative, totals} = buildStackedTimeSeriesData(
    events, safeSeries, range, binMs,
  );

  React.useLayoutEffect(() => {
    if (!ref.current) return;
    let measured = 0;
    ref.current.querySelectorAll('text[data-yl-label]').forEach((text) => {
      const length = text.getComputedTextLength ? text.getComputedTextLength() : 0;
      if (length > measured) measured = length;
    });
    if (measured > 0 && Math.abs(measured - yLabelPx) > 0.5) setYLabelPx(measured);
  });

  React.useEffect(() => {
    if (!ref.current || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(entries => {
      const rect = entries[0].contentRect;
      setSize({w: rect.width, h: Math.max(280, rect.height)});
    });
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);

  const {w, h} = size;
  const padR = 70;
  const padT = 28;
  const basePadB = 48;
  const measuredPadL = Math.min(
    Math.max(60, w * 0.25),
    Math.max(50, Math.ceil(yLabelPx) + 32),
  );
  // On a narrow card preserve a positive plot and never let its right edge
  // escape the SVG, even while the measured label gutter is settling.
  const padL = Math.min(measuredPadL, Math.max(0, w - padR - 1));
  const plotW = Math.max(1, w - padL - padR);
  const legendLayout = layoutLegend(safeSeries, plotW, 6.4);
  const padB = Math.max(basePadB, legendLayout.height + 30);
  const plotH = Math.max(10, h - padT - padB);
  const maxBin = Math.max(1, ...bins.map(bin => bin.total));
  const maxCum = Math.max(1, ...safeSeries.map(item => totals[item.key] || 0));
  const xScale = ts => timeX(ts, range, padL, plotW);
  const yBar = value => padT + plotH - (Math.max(0, value) / maxBin) * plotH;
  const yCum = value => padT + plotH - (Math.max(0, value) / maxCum) * plotH;
  const ticks = timeTicksUTC(range.start, range.end);
  const clipId = `ghpulse-plot-${String(title || 'chart').replace(/[^a-zA-Z0-9_-]/g, '-')}`;

  function niceTicks(maxValue, count = 4) {
    const step0 = Math.max(1, maxValue) / count;
    const exponent = Math.pow(10, Math.floor(Math.log10(step0)));
    const normalized = step0 / exponent;
    const niceStep = (normalized < 1.5 ? 1 : normalized < 3 ? 2 : normalized < 7 ? 5 : 10) * exponent;
    const values = [];
    for (let value = 0; value <= maxValue + niceStep * 0.001; value += niceStep) {
      values.push(value);
    }
    return values;
  }
  const yTicksL = niceTicks(maxBin);
  const yTicksR = niceTicks(maxCum);

  function onMouseMove(event) {
    const rect = ref.current.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    if (my < padT || my > padT + plotH) { setTip(null); return; }
    const idx = timeBinIndexAtX(bins, range, padL, plotW, mx);
    if (idx < 0) { setTip(null); return; }
    const bin = bins[idx];
    const lines = buildTooltipLines(bin, cumulative, safeSeries, idx, totals);
    setTip({
      x: mx, y: my, idx,
      title: `${fmtDate(bin.start, {full: true})} – ${fmtDate(bin.end, {full: true})} UTC`,
      accent: safeSeries[0] ? safeSeries[0].color : TH.text,
      lines,
    });
  }

  return (
    <div ref={ref} style={{
      background: TH.bgAxes, border: `1px solid ${TH.border}`,
      borderRadius: 4, padding: 0, position: 'relative', minHeight: 220,
    }} onMouseMove={onMouseMove} onMouseLeave={() => setTip(null)}>
      <svg data-panel={title} width={w} height={h} style={{display: 'block'}}>
        <defs>
          <clipPath id={clipId}>
            <rect x={padL} y={padT} width={plotW} height={plotH} />
          </clipPath>
        </defs>
        {yTicksL.map((value, index) => (
          <line data-plot-boundary="" key={`grid-${index}`} x1={padL} x2={padL + plotW}
            y1={yBar(value)} y2={yBar(value)} stroke={TH.grid} strokeOpacity="0.3" strokeWidth="1" />
        ))}
        <g clipPath={`url(#${clipId})`}>
          {bins.map((bin, binIdx) => {
            const segments = buildStackedBarSegments(
              bin, safeSeries, range, padL, plotW, maxBin, plotH, padT,
              timeBarRect(bin, range, padL, plotW),
            );
            return (
              <g key={`bar-${binIdx}`}>
                {segments.map(segment => (
                  <rect data-time-bar="" key={segment.key} x={segment.x} y={segment.y}
                    width={segment.width} height={segment.height}
                    fill={segment.color} fillOpacity={tip && tip.idx === binIdx ? 0.85 : 0.3} />
                ))}
              </g>
            );
          })}
          {safeSeries.map(item => {
            const points = cumulative[item.key] || [];
            const pointString = cumulative[item.key].map(point =>
              `${xScale(point.ts)},${yCum(point.v)}`).join(' ');
            return <g key={`line-${item.key}`}>
              <polyline points={pointString} stroke="#fff" strokeOpacity="0.15"
                strokeWidth="4" fill="none" />
              <polyline data-cumulative-line="" points={pointString}
                stroke={item.color} strokeWidth="2" fill="none" />
              {points.length === 0 && null}
            </g>;
          })}
          {tip && <line x1={tip.x} x2={tip.x} y1={padT} y2={padT + plotH}
            stroke={tip.accent || TH.text} strokeOpacity="0.4" strokeWidth="1" strokeDasharray="2,3" />}
        </g>
        {yTicksL.map((value, index) => (
          <text data-yl-label="" key={`yl-${index}`} x={padL - 6} y={yBar(value) + 4}
            fontSize="9" fill={TH.textDim} textAnchor="end" fontFamily="monospace">{humanFmt(value)}</text>
        ))}
        {yTicksR.map((value, index) => (
          <text key={`yr-${index}`} x={Math.min(w - padR + 6, w - 1)} y={yCum(value) + 4}
            fontSize="9" fill={TH.textDim} textAnchor="start" fontFamily="monospace">{humanFmt(value)}</text>
        ))}
        {ticks.map((tick, index) => (
          <text key={`x-${index}`} x={xScale(tick.ts)} y={h - padB + 14}
            fontSize="9" fill={TH.textDim} textAnchor="middle" fontFamily="monospace">{tick.label}</text>
        ))}
        <text x={w / 2} y={18} fontSize="13" fontWeight="bold" fill={TH.text}
          textAnchor="middle" fontFamily="monospace">{title}</text>
        <text x={17} y={padT + plotH / 2} fontSize="9" fill={TH.textDim}
          textAnchor="middle" fontFamily="monospace"
          transform={`rotate(-90 17 ${padT + plotH / 2})`}>events / {binMsLabel(binMs)}</text>
        <text x={w - 12} y={padT + plotH / 2} fontSize="9" fill={TH.textDim}
          textAnchor="middle" fontFamily="monospace"
          transform={`rotate(-90 ${w - 12} ${padT + plotH / 2})`}>cumulative events</text>
        <g transform={`translate(${padL}, ${h - legendLayout.height - 8})`}>
          {legendLayout.items.map(item => (
            <g key={`legend-${item.key}`} transform={`translate(${item.x}, ${item.y})`}>
              <line x1={0} x2={18} y1={5} y2={5} stroke={item.color} strokeWidth="2" />
              <text x={24} y={9} fontSize="10" fill={TH.text} fontFamily="monospace">
                {item.labelLines.map((line, lineIndex) => (
                  <tspan key={lineIndex} x={24} dy={lineIndex === 0 ? 0 : 12}>{line}</tspan>
                ))}
              </text>
            </g>
          ))}
        </g>
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

window.StackedCumulativeTimeSeriesPanel = StackedCumulativeTimeSeriesPanel;
window.buildStackedTimeSeriesData = buildStackedTimeSeriesData;
window.buildStackedBarSegments = buildStackedBarSegments;
window.buildTooltipLines = buildTooltipLines;
window.layoutLegend = layoutLegend;
window.dashboardTheme = TH;
window.humanFmt = humanFmt;
window.fmtDate = fmtDate;
window.timeTicksUTC = timeTicksUTC;
