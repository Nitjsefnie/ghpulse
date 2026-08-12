// ghpulse application shell.
//
// This is deliberately a small adaptation of Claudit's proven no-build React
// shell. React and Babel are supplied by public/index.html; the backend owns
// authentication, caching, ingest, and the aggregate response contract.

const { useState, useEffect, useMemo, useCallback } = React;

// --- pure app contracts ---

const RANGE_PRESETS = [
  { label: '24h', value: '1d' },
  { label: '7d', value: '7d' },
  { label: '30d', value: '30d' },
  { label: '90d', value: '90d' },
  { label: '1y', value: '365d' },
  { label: 'all', value: 'all' },
];
const VALID_RANGES = new Set(RANGE_PRESETS.map(item => item.value));

// One palette is shared by the chart bars, cumulative lines, legends,
// tooltips, and the corresponding summary values.
const SERIES_COLORS = {
  opened: '#00d4aa',
  completed: '#ff9c5a',
  not_planned: '#a98bff',
  merged: '#61d5ff',
  closed_unmerged: '#ff4d6d',
};
const ISSUE_SERIES = [
  { key: 'opened', label: 'Opened', color: SERIES_COLORS.opened },
  { key: 'completed', label: 'Completed', color: SERIES_COLORS.completed },
  { key: 'not_planned', label: 'Not planned', color: SERIES_COLORS.not_planned },
];
const PR_SERIES = [
  { key: 'opened', label: 'Opened', color: SERIES_COLORS.opened },
  { key: 'merged', label: 'Merged', color: SERIES_COLORS.merged },
  { key: 'closed_unmerged', label: 'Closed unmerged', color: SERIES_COLORS.closed_unmerged },
];

function parseTimestamp(value) {
  if (value == null || value === '') return NaN;
  if (typeof value === 'number') return Number.isFinite(value) ? value : NaN;
  const numeric = Number(value);
  if (Number.isFinite(numeric) && String(value).trim() !== '') return numeric;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : NaN;
}

function readFilterState(search) {
  const params = new URLSearchParams(search || '');
  const requestedRange = params.get('range') || '30d';
  return {
    range: VALID_RANGES.has(requestedRange) ? requestedRange : '30d',
    repository: params.get('repository') || '',
  };
}

function buildDashboardQuery(range, repository) {
  const params = new URLSearchParams({range: range || '30d'});
  if (repository) params.set('repository', repository);
  return `/api/dashboard?${params.toString()}`;
}

function buildRepositoriesQuery(range) {
  return `/api/repositories?range=${encodeURIComponent(range || '30d')}`;
}

function normalizeBuckets(rows) {
  return (Array.isArray(rows) ? rows : []).map(row => ({
    ...row,
    start: parseTimestamp(row.start),
    end: parseTimestamp(row.end),
  })).filter(row => Number.isFinite(row.start) && Number.isFinite(row.end) && row.end > row.start);
}

function dashboardToViewModel(body) {
  const source = body || {};
  const start = parseTimestamp(source.start);
  const end = parseTimestamp(source.end);
  const safeStart = Number.isFinite(start) ? start : Date.now() - 86_400_000;
  const safeEnd = Number.isFinite(end) && end > safeStart ? end : safeStart + 86_400_000;
  const bucketS = Number(source.bucket_s);
  const binMs = Number.isFinite(bucketS) && bucketS > 0 ? bucketS * 1000 : safeEnd - safeStart;
  return {
    range: {start: safeStart, end: safeEnd},
    binMs,
    issues: {events: normalizeBuckets(source.issues), series: ISSUE_SERIES},
    pullRequests: {
      events: normalizeBuckets(source.pull_requests),
      series: PR_SERIES,
    },
    summary: source.summary || {},
    repositories: Array.isArray(source.repositories) ? source.repositories : [],
    generatedAt: source.generated_at || null,
  };
}

function formatLastIngest(value, now = Date.now()) {
  const timestamp = parseTimestamp(value);
  if (!Number.isFinite(timestamp)) {
    return {label: 'unknown', stale: true, ageMs: null};
  }
  const ageMs = Math.max(0, now - timestamp);
  // The scheduled sync is hourly. Two hours gives one missed run room while
  // still making a stale/error condition visible instead of hiding it.
  const stale = ageMs > 2 * 60 * 60 * 1000;
  const date = new Date(timestamp);
  return {
    label: date.toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, ' UTC'),
    stale,
    ageMs,
  };
}

function apiPath(path) {
  const base = typeof window !== 'undefined' && window.BACKEND_URL
    ? String(window.BACKEND_URL).replace(/\/$/, '') : '';
  return `${base}${path}`;
}

function setBrowserFilterState(range, repository) {
  if (typeof window === 'undefined' || !window.history || !window.location) return;
  const url = new URL(window.location.href);
  url.searchParams.set('range', range);
  if (repository) url.searchParams.set('repository', repository);
  else url.searchParams.delete('repository');
  window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
}

// The test-facing object makes the non-React parts of the browser contract
// executable without a DOM or a component test framework.
window.ghpulseAppContract = {
  buildDashboardQuery,
  buildRepositoriesQuery,
  dashboardToViewModel,
  formatLastIngest,
  readFilterState,
};

// --- React app ---

function App() {
  const initialFilters = useMemo(
    () => readFilterState(typeof window === 'undefined' ? '' : window.location.search),
    [],
  );
  const [activeRange, setActiveRange] = useState(initialFilters.range);
  const [activeRepository, setActiveRepository] = useState(initialFilters.repository);
  const [identity, setIdentity] = useState({isGuest: !!window.IS_GUEST, loaded: false});
  const [repositories, setRepositories] = useState([]);
  const [dashboard, setDashboard] = useState(null);
  const [phase, setPhase] = useState('loading');
  const [error, setError] = useState('');
  const [streamState, setStreamState] = useState('connecting');
  const [refreshNonce, setRefreshNonce] = useState(0);

  const backendFetch = useCallback(async (path, options = {}) => {
    const response = await fetch(apiPath(path), {
      credentials: 'same-origin',
      ...options,
    });
    if (!response.ok) {
      let detail = '';
      try {
        const body = await response.json();
        detail = body && body.detail ? `: ${body.detail}` : '';
      } catch (_) {
        // Keep the status as the useful error when the response is not JSON.
      }
      throw new Error(`${response.status} ${response.statusText || 'request failed'}${detail}`);
    }
    return response.json();
  }, []);

  useEffect(() => {
    let alive = true;
    backendFetch('/api/me')
      .then(body => {
        if (!alive) return;
        setIdentity({isGuest: !!body.is_guest, loaded: true});
      })
      .catch(() => {
        // A failed identity probe must not prevent public aggregate data from
        // loading; the server still controls the actual session boundary.
        if (alive) setIdentity(previous => ({...previous, loaded: true}));
      });
    return () => { alive = false; };
  }, [backendFetch]);

  useEffect(() => {
    let alive = true;
    backendFetch(buildRepositoriesQuery(activeRange))
      .then(body => {
        if (!alive) return;
        setRepositories(Array.isArray(body.repositories) ? body.repositories : []);
      })
      .catch(err => {
        if (alive) setError(`repository list unavailable: ${err.message}`);
      });
    return () => { alive = false; };
  }, [activeRange, refreshNonce, backendFetch]);

  useEffect(() => {
    let alive = true;
    setPhase(previous => (dashboard ? 'refreshing' : 'loading'));
    setError('');
    const controller = new AbortController();
    backendFetch(buildDashboardQuery(activeRange, activeRepository), {
      signal: controller.signal,
    })
      .then(body => {
        if (!alive) return;
        setDashboard(body);
        setPhase('ready');
      })
      .catch(err => {
        if (!alive || err.name === 'AbortError') return;
        setPhase(dashboard ? 'stale' : 'error');
        setError(`dashboard unavailable: ${err.message}`);
      });
    return () => {
      alive = false;
      controller.abort();
    };
  }, [activeRange, activeRepository, refreshNonce, backendFetch]);

  useEffect(() => {
    const eventSource = new EventSource(apiPath('/api/events'), {withCredentials: true});
    const onOpen = () => setStreamState('connected');
    const onIngest = () => {
      setStreamState('connected');
      setRefreshNonce(value => value + 1);
    };
    const onError = () => setStreamState('reconnecting');
    eventSource.addEventListener('open', onOpen);
    eventSource.addEventListener('ingest_done', onIngest);
    eventSource.addEventListener('error', onError);
    return () => {
      eventSource.removeEventListener('open', onOpen);
      eventSource.removeEventListener('ingest_done', onIngest);
      eventSource.removeEventListener('error', onError);
      eventSource.close();
    };
  }, []);

  const onRangeChange = value => {
    setActiveRange(value);
    setBrowserFilterState(value, activeRepository);
  };
  const onRepositoryChange = value => {
    setActiveRepository(value);
    setBrowserFilterState(activeRange, value);
  };

  const view = useMemo(() => dashboardToViewModel(dashboard), [dashboard]);
  const hasDashboard = !!dashboard;
  const hasIdentity = identity.loaded;

  return (
    <div className="app-root">
      <TopBar isGuest={identity.isGuest} identityLoaded={hasIdentity} streamState={streamState} />
      <FilterBar
        activeRange={activeRange}
        activeRepository={activeRepository}
        repositories={repositories}
        onRangeChange={onRangeChange}
        onRepositoryChange={onRepositoryChange}
      />
      <main className="dashboard" aria-busy={phase === 'loading' || phase === 'refreshing'}>
        {(phase === 'loading' || phase === 'refreshing') && (
          <LoadingOverlay phase={phase} />
        )}
        {error && <div className={'dashboard-alert ' + (hasDashboard ? 'dashboard-alert-stale' : '')} role="alert">{error}</div>}
        {!hasDashboard && phase === 'error' && !error && (
          <StateCard title="Dashboard unavailable" detail="The aggregate endpoint did not return data." />
        )}
        {hasDashboard && (
          <DashboardView view={view} activeRange={activeRange} />
        )}
      </main>
    </div>
  );
}

function TopBar({isGuest, identityLoaded, streamState}) {
  const identityLabel = isGuest ? 'guest' : (identityLoaded ? 'authenticated' : 'checking');
  return (
    <header className="topbar">
      <div className="topbar-left">
        <div className="logo">
          <span className="logo-mark">{'>'}</span>
          <span className="logo-text">GHPULSE</span>
          <span className="logo-sub">GitHub activity · {identityLabel}</span>
        </div>
      </div>
      <nav className="topnav" aria-label="Dashboard navigation">
        <span className="navbtn on">Overview</span>
      </nav>
      <div className="topbar-right">
        <span className={'stream-indicator ' + streamState} title={`event stream: ${streamState}`}>
          {streamState === 'connected' ? 'live' : streamState}
        </span>
        {isGuest
          ? <a className="loadbtn logout-btn" href="/login">Login</a>
          : <a className="loadbtn logout-btn" href="/logout">Logout</a>}
      </div>
    </header>
  );
}

function FilterBar({activeRange, activeRepository, repositories, onRangeChange, onRepositoryChange}) {
  return (
    <div className="filterbar dashboard-filterbar">
      <div className="range-controls" aria-label="Time range">
        <span className="filter-label">range:</span>
        {RANGE_PRESETS.map(item => (
          <button key={item.value} className={'pp-btn ' + (activeRange === item.value ? 'on' : '')}
            onClick={() => onRangeChange(item.value)}>{item.label}</button>
        ))}
      </div>
      <label className="repository-control">
        <span className="filter-label">repository:</span>
        <select value={activeRepository} onChange={event => onRepositoryChange(event.target.value)}>
          <option value="">All external repositories</option>
          {repositories.map(repository => (
            <option key={repository.node_id} value={repository.node_id}>
              {repository.name_with_owner}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

function LoadingOverlay({phase}) {
  return (
    <div className="loading-overlay" role="status" aria-live="polite">
      <span className="loading-spinner" aria-hidden="true"></span>
      <span>{phase === 'refreshing' ? 'refreshing cached data…' : 'loading dashboard…'}</span>
    </div>
  );
}

function StateCard({title, detail}) {
  return (
    <div className="state-card">
      <div className="state-card-title">{title}</div>
      <div className="state-card-detail">{detail}</div>
    </div>
  );
}

function DashboardView({view, activeRange}) {
  const summary = view.summary || {};
  const issues = summary.issues || {};
  const pullRequests = summary.pull_requests || {};
  const ingest = formatLastIngest(summary.last_ingest);
  const hasIssueEvents = view.issues.events.some(bucket =>
    ISSUE_SERIES.some(series => Number(bucket[series.key]) > 0));
  const hasPullRequestEvents = view.pullRequests.events.some(bucket =>
    PR_SERIES.some(series => Number(bucket[series.key]) > 0));
  return (
    <>
      <SummaryStrip
        summary={summary}
        issues={issues}
        pullRequests={pullRequests}
        ingest={ingest}
        activeRange={activeRange}
      />
      <div className="dash-grid ghpulse-panel-grid">
        <PanelShell title="External Issues" empty={!hasIssueEvents} emptyText="No external issue activity in this range.">
          <window.StackedCumulativeTimeSeriesPanel
            title="External Issues"
            events={view.issues.events}
            series={view.issues.series}
            range={view.range}
            binMs={view.binMs}
          />
        </PanelShell>
        <PanelShell title="External Pull Requests" empty={!hasPullRequestEvents} emptyText="No external pull request activity in this range.">
          <window.StackedCumulativeTimeSeriesPanel
            title="External Pull Requests"
            events={view.pullRequests.events}
            series={view.pullRequests.series}
            range={view.range}
            binMs={view.binMs}
          />
        </PanelShell>
      </div>
    </>
  );
}

function PanelShell({title, empty, emptyText, children}) {
  return (
    <section className="panel-shell" aria-label={title}>
      {children}
      {empty && <div className="panel-empty" role="status">{emptyText}</div>}
    </section>
  );
}

function SummaryStrip({summary, issues, pullRequests, ingest, activeRange}) {
  const values = [
    {label: 'external repositories', value: summary.repositories || 0},
    {label: 'issues opened', value: issues.opened || 0, color: SERIES_COLORS.opened},
    {label: 'issues completed', value: issues.completed || 0, color: SERIES_COLORS.completed},
    {label: 'issues not planned', value: issues.not_planned || 0, color: SERIES_COLORS.not_planned},
    {label: 'issues currently open', value: issues.currently_open || 0},
    {label: 'pull requests opened', value: pullRequests.opened || 0, color: SERIES_COLORS.opened},
    {label: 'pull requests merged', value: pullRequests.merged || 0, color: SERIES_COLORS.merged},
    {label: 'pull requests closed unmerged', value: pullRequests.closed_unmerged || 0, color: SERIES_COLORS.closed_unmerged},
    {label: 'pull requests currently open', value: pullRequests.currently_open || 0},
    {label: 'range', value: activeRange},
    {label: 'last ingest', value: ingest.label, warn: ingest.stale},
    {label: 'ingest status', value: ingest.stale ? 'stale' : 'fresh', warn: ingest.stale},
  ];
  return (
    <div className="dash-summary ghpulse-summary">
      {values.map(item => (
        <div className={'stat ' + (item.warn ? 'stat-warn' : '')} key={item.label}>
          <div className="stat-label">{item.label}</div>
          <div className="stat-value" style={item.color ? {color: item.color} : undefined}>
            {typeof item.value === 'number' ? item.value.toLocaleString() : item.value}
          </div>
        </div>
      ))}
    </div>
  );
}

window.App = App;
