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

function normalizeDashboardSelection(selection) {
  return {
    range: selection && selection.range ? selection.range : '30d',
    repository: selection && selection.repository ? selection.repository : '',
  };
}

function sameDashboardSelection(left, right) {
  return !!left && !!right
    && left.range === right.range
    && left.repository === right.repository;
}

function mergeRepositoryHistory(history, repositories) {
  const next = {...(history || {})};
  (Array.isArray(repositories) ? repositories : []).forEach(repository => {
    if (!repository || !repository.node_id) return;
    next[repository.node_id] = {...next[repository.node_id], ...repository};
  });
  return next;
}

function repositoryOptionsForSelection(repositories, activeRepository, history) {
  const options = [];
  const seen = new Set();
  (Array.isArray(repositories) ? repositories : []).forEach(repository => {
    if (!repository || !repository.node_id || seen.has(repository.node_id)) return;
    seen.add(repository.node_id);
    options.push(repository);
  });
  if (activeRepository && !seen.has(activeRepository)) {
    const remembered = (history || {})[activeRepository] || {node_id: activeRepository};
    const name = remembered.name_with_owner || activeRepository;
    options.push({
      ...remembered,
      node_id: activeRepository,
      name_with_owner: `${name} · unavailable in selected range`,
      unavailable: true,
    });
  }
  return options;
}

function createDashboardRequestCoordinator({
  fetchJson,
  onStateChange,
  onStreamStateChange,
  onIngest: onIngestCallback,
  eventSourceFactory,
}) {
  let disposed = false;
  let requestId = 0;
  let activeSelection = null;
  let activeData = null;
  let controller = null;
  let eventSource = null;
  const notify = onStateChange || (() => {});
  const streamNotify = onStreamStateChange || (() => {});

  function abortCurrent() {
    if (!controller || typeof controller.abort !== 'function') return;
    controller.abort();
    controller = null;
  }

  function load(selection, {retain = false} = {}) {
    if (disposed) return Promise.resolve(null);
    const normalized = normalizeDashboardSelection(selection);
    const retained = retain && activeData
      && sameDashboardSelection(activeData.selection, normalized)
      ? activeData : null;
    abortCurrent();
    activeSelection = normalized;
    activeData = retained;
    const currentRequest = ++requestId;
    notify({
      phase: retained ? 'refreshing' : 'loading',
      selection: {...normalized},
      data: retained,
      error: '',
      requestId: currentRequest,
    });
    const requestController = typeof AbortController === 'function'
      ? new AbortController() : {signal: undefined, abort() {}};
    controller = requestController;
    let request;
    try {
      // Start the transport synchronously. This makes selection transitions
      // deterministic: the request is registered before the caller can
      // receive or resolve a mocked fetch promise, while normal `fetch`
      // still returns its promise immediately.
      request = fetchJson(normalized, requestController.signal);
    } catch (error) {
      request = Promise.reject(error);
    }
    return Promise.resolve(request)
      .then(body => {
        if (disposed || currentRequest !== requestId
            || !sameDashboardSelection(activeSelection, normalized)) return null;
        activeData = {body, selection: {...normalized}};
        if (controller === requestController) controller = null;
        notify({
          phase: 'ready',
          selection: {...normalized},
          data: activeData,
          error: '',
          requestId: currentRequest,
        });
        return activeData;
      })
      .catch(error => {
        if (disposed || currentRequest !== requestId
            || !sameDashboardSelection(activeSelection, normalized)
            || (error && error.name === 'AbortError')) return null;
        if (controller === requestController) controller = null;
        const fallback = retain && activeData
          && sameDashboardSelection(activeData.selection, normalized)
          ? activeData : null;
        activeData = fallback;
        notify({
          phase: fallback ? 'stale' : 'error',
          selection: {...normalized},
          data: fallback,
          error: error && error.message ? error.message : String(error),
          requestId: currentRequest,
        });
        return null;
      });
  }

  function connect() {
    if (disposed || eventSource || typeof eventSourceFactory !== 'function') return eventSource;
    eventSource = eventSourceFactory();
    if (!eventSource || typeof eventSource.addEventListener !== 'function') return eventSource;
    const onOpen = () => streamNotify('connected');
    const onError = () => streamNotify('reconnecting');
    const onIngest = () => {
      streamNotify('connected');
      if (typeof onIngestCallback === 'function') onIngestCallback(activeSelection);
      if (activeSelection) load(activeSelection, {retain: true});
    };
    eventSource.__ghpulseHandlers = {onOpen, onError, onIngest};
    eventSource.addEventListener('open', onOpen);
    eventSource.addEventListener('error', onError);
    eventSource.addEventListener('ingest_done', onIngest);
    return eventSource;
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    requestId += 1;
    abortCurrent();
    if (!eventSource) return;
    const handlers = eventSource.__ghpulseHandlers || {};
    if (typeof eventSource.removeEventListener === 'function') {
      if (handlers.onOpen) eventSource.removeEventListener('open', handlers.onOpen);
      if (handlers.onError) eventSource.removeEventListener('error', handlers.onError);
      if (handlers.onIngest) eventSource.removeEventListener('ingest_done', handlers.onIngest);
    }
    if (typeof eventSource.close === 'function') eventSource.close();
    eventSource = null;
  }

  return {load, connect, dispose};
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
  createDashboardRequestCoordinator,
  dashboardToViewModel,
  formatLastIngest,
  mergeRepositoryHistory,
  normalizeDashboardSelection,
  repositoryOptionsForSelection,
  readFilterState,
  sameDashboardSelection,
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
  const [repositoryHistory, setRepositoryHistory] = useState({});
  const [repositoryError, setRepositoryError] = useState('');
  const [dashboardState, setDashboardState] = useState({
    phase: 'loading',
    selection: initialFilters,
    data: null,
    error: '',
  });
  const [streamState, setStreamState] = useState('connecting');
  const [repositoryRefreshNonce, setRepositoryRefreshNonce] = useState(0);

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
    setRepositoryError('');
    backendFetch(buildRepositoriesQuery(activeRange))
      .then(body => {
        if (!alive) return;
        const nextRepositories = Array.isArray(body.repositories) ? body.repositories : [];
        setRepositories(nextRepositories);
        setRepositoryHistory(previous => mergeRepositoryHistory(previous, nextRepositories));
      })
      .catch(err => {
        if (alive) setRepositoryError(`repository list unavailable: ${err.message}`);
      });
    return () => { alive = false; };
  }, [activeRange, repositoryRefreshNonce, backendFetch]);

  const requestCoordinator = useMemo(() => createDashboardRequestCoordinator({
    fetchJson: (selection, signal) => backendFetch(
      buildDashboardQuery(selection.range, selection.repository), {signal}),
    onStateChange: state => {
      setDashboardState(state);
      const body = state.data && state.data.body;
      if (body && Array.isArray(body.repositories)) {
        setRepositoryHistory(previous => mergeRepositoryHistory(previous, body.repositories));
      }
    },
    onStreamStateChange: setStreamState,
    onIngest: () => setRepositoryRefreshNonce(value => value + 1),
    eventSourceFactory: () => new EventSource(apiPath('/api/events'), {withCredentials: true}),
  }), [backendFetch]);

  useEffect(() => {
    requestCoordinator.load(
      {range: activeRange, repository: activeRepository},
      {retain: true},
    );
  }, [activeRange, activeRepository, requestCoordinator]);

  useEffect(() => {
    requestCoordinator.connect();
    return () => requestCoordinator.dispose();
  }, [requestCoordinator]);

  const onRangeChange = value => {
    const selection = {range: value, repository: activeRepository};
    setDashboardState({phase: 'loading', selection, data: null, error: ''});
    // The repository endpoint is range-scoped. Clear its old visible list
    // immediately; the history map still supplies an explicit active-repo
    // fallback while the new range is loading.
    setRepositories([]);
    setActiveRange(value);
    setBrowserFilterState(value, activeRepository);
  };
  const onRepositoryChange = value => {
    const selection = {range: activeRange, repository: value};
    setDashboardState({phase: 'loading', selection, data: null, error: ''});
    setActiveRepository(value);
    setBrowserFilterState(activeRange, value);
  };

  const view = useMemo(
    () => {
      const selection = {range: activeRange, repository: activeRepository};
      const body = dashboardState.data && sameDashboardSelection(
        dashboardState.data.selection, selection) ? dashboardState.data.body : null;
      return dashboardToViewModel(body);
    },
    [dashboardState.data, activeRange, activeRepository],
  );
  const hasDashboard = !!dashboardState.data && sameDashboardSelection(
    dashboardState.data.selection,
    {range: activeRange, repository: activeRepository},
  );
  const hasIdentity = identity.loaded;
  const repositoryOptions = useMemo(
    () => repositoryOptionsForSelection(repositories, activeRepository, repositoryHistory),
    [repositories, activeRepository, repositoryHistory],
  );
  const error = dashboardState.error || repositoryError;
  const phase = dashboardState.phase;

  return (
    <div className="app-root">
      <TopBar isGuest={identity.isGuest} identityLoaded={hasIdentity} streamState={streamState} />
      <FilterBar
        activeRange={activeRange}
        activeRepository={activeRepository}
        repositories={repositoryOptions}
        onRangeChange={onRangeChange}
        onRepositoryChange={onRepositoryChange}
      />
      <main className="dashboard" aria-busy={phase === 'loading' || phase === 'refreshing'}>
        {(phase === 'loading' || phase === 'refreshing') && (
          <LoadingOverlay phase={phase} />
        )}
        {error && <div className={'dashboard-alert ' + (hasDashboard ? 'dashboard-alert-stale' : '')} role="alert">{error}</div>}
        {hasDashboard && (
          <DashboardView view={view} />
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
        <span className="navbtn on" aria-current="page">Overview</span>
      </nav>
      <div className="topbar-right">
        <span className={'stream-indicator ' + streamState} aria-live="polite" title={`event stream: ${streamState}`}>
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
            aria-pressed={activeRange === item.value}
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

function DashboardView({view}) {
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

function SummaryStrip({summary, issues, pullRequests, ingest}) {
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
