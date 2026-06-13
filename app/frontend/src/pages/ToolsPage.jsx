import { useMemo, useState } from 'react';
import {
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListGroup,
  PaneListScroll,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
} from '../components/layout/PaneList';
import DetailHeader, {
  ConfigureActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  LogsActionIcon,
  ReconnectActionIcon,
} from '../components/layout/DetailHeader';
import { useChromeRuntime } from '../context/ChromeRuntimeContext';

const SERVER_FILTERS = ['All', 'Connected', 'Disabled', 'Errors'];

const MCP_SERVERS = [
  {
    id: 'github',
    name: 'github',
    initials: 'gh',
    packageName: '@modelcontextprotocol/github',
    version: 'v1.4.0',
    status: 'connected',
    transport: 'http · streamable',
    latency: '24ms',
    endpoint: 'api.githubcopilot.com/mcp/',
    auth: 'OAuth 2.1',
    protocol: '2025-06-18',
    approval: 'Auto (allowlist)',
    timeout: '30,000 ms',
    connection: 'Persistent · keep-alive 30s',
    handshake: '2m ago',
    tools: 26,
    enabledTools: 18,
    resources: 4,
    calls: '4,128',
    callsDetail: 'last 30 days',
    avgLatency: '24ms',
    latencyDetail: 'p95 · 180ms',
    errorRate: '0.4%',
    errorDetail: '17 / 4,128 calls',
    capabilities: ['tools', 'resources', 'prompts', 'logging', 'roots', 'sampling'],
    activeCapabilities: ['tools', 'resources', 'prompts'],
    serverTools: [
      { name: 'list_issues', description: 'List & filter issues in a repository', calls: '1,204', enabled: true },
      { name: 'get_pull_request', description: 'Read a PR with diff and review state', calls: '880', enabled: true },
      { name: 'search_code', description: 'Search code across connected repositories', calls: '642', enabled: true },
      { name: 'create_issue', description: 'Open a tracked GitHub issue from context', calls: '421', enabled: true },
      { name: 'request_review', description: 'Ask a reviewer to inspect a branch or PR', calls: '318', enabled: false },
    ],
    scope: 'remote',
  },
  {
    id: 'filesystem',
    name: 'filesystem',
    initials: 'fs',
    packageName: '@modelcontextprotocol/filesystem',
    version: 'v0.9.2',
    status: 'connected',
    transport: 'stdio',
    latency: '3ms',
    endpoint: './app',
    auth: 'local process',
    protocol: '2025-03-26',
    approval: 'Workspace paths',
    timeout: '10,000 ms',
    connection: 'Child process',
    handshake: 'now',
    tools: 8,
    enabledTools: 8,
    resources: 0,
    calls: '2,046',
    callsDetail: 'last 30 days',
    avgLatency: '3ms',
    latencyDetail: 'p95 · 12ms',
    errorRate: '0.1%',
    errorDetail: '2 / 2,046 calls',
    capabilities: ['tools', 'resources', 'logging'],
    activeCapabilities: ['tools'],
    serverTools: [
      { name: 'read_file', description: 'Read files inside approved workspace roots', calls: '912', enabled: true },
      { name: 'list_directory', description: 'Inspect directory entries and metadata', calls: '684', enabled: true },
      { name: 'search_files', description: 'Find files by glob pattern', calls: '320', enabled: true },
    ],
    scope: 'local',
  },
  {
    id: 'postgres',
    name: 'postgres',
    initials: 'pg',
    packageName: '@modelcontextprotocol/postgres',
    version: 'v0.8.1',
    status: 'connected',
    transport: 'stdio',
    latency: '12ms',
    endpoint: 'localhost:5432/progenitor',
    auth: 'managed secret',
    protocol: '2025-03-26',
    approval: 'Read-only queries',
    timeout: '20,000 ms',
    connection: 'Pool · 4 workers',
    handshake: '8m ago',
    tools: 11,
    enabledTools: 9,
    resources: 9,
    calls: '1,188',
    callsDetail: 'last 30 days',
    avgLatency: '12ms',
    latencyDetail: 'p95 · 64ms',
    errorRate: '0.2%',
    errorDetail: '3 / 1,188 calls',
    capabilities: ['tools', 'resources', 'logging'],
    activeCapabilities: ['tools', 'resources'],
    serverTools: [
      { name: 'query', description: 'Execute approved read-only SQL statements', calls: '804', enabled: true },
      { name: 'list_tables', description: 'Inspect schemas, tables, and columns', calls: '212', enabled: true },
      { name: 'describe_table', description: 'Return table metadata and indexes', calls: '172', enabled: true },
    ],
    scope: 'local',
  },
  {
    id: 'linear',
    name: 'linear',
    initials: 'ln',
    packageName: '@modelcontextprotocol/linear',
    version: 'v1.1.0',
    status: 'connected',
    transport: 'sse',
    latency: '88ms',
    endpoint: 'mcp.linear.app/sse',
    auth: 'OAuth',
    protocol: '2025-03-26',
    approval: 'Team allowlist',
    timeout: '30,000 ms',
    connection: 'Event stream',
    handshake: '12m ago',
    tools: 14,
    enabledTools: 12,
    resources: 0,
    calls: '732',
    callsDetail: 'last 30 days',
    avgLatency: '88ms',
    latencyDetail: 'p95 · 240ms',
    errorRate: '0.7%',
    errorDetail: '5 / 732 calls',
    capabilities: ['tools', 'prompts', 'logging'],
    activeCapabilities: ['tools', 'prompts'],
    serverTools: [
      { name: 'list_issues', description: 'List current Linear issues by team', calls: '312', enabled: true },
      { name: 'create_issue', description: 'Create an issue from chat context', calls: '188', enabled: true },
      { name: 'update_status', description: 'Move work items between workflow states', calls: '94', enabled: true },
    ],
    scope: 'remote',
  },
  {
    id: 'sentry',
    name: 'sentry',
    initials: 'se',
    packageName: '@modelcontextprotocol/sentry',
    version: 'v0.5.4',
    status: 'connected',
    transport: 'http · streamable',
    latency: '42ms',
    endpoint: 'mcp.sentry.io',
    auth: 'API token',
    protocol: '2025-03-26',
    approval: 'Read incidents',
    timeout: '30,000 ms',
    connection: 'Persistent',
    handshake: '5m ago',
    tools: 9,
    enabledTools: 7,
    resources: 0,
    calls: '604',
    callsDetail: 'last 30 days',
    avgLatency: '42ms',
    latencyDetail: 'p95 · 190ms',
    errorRate: '0.8%',
    errorDetail: '5 / 604 calls',
    capabilities: ['tools', 'logging'],
    activeCapabilities: ['tools', 'logging'],
    serverTools: [
      { name: 'list_events', description: 'List recent application errors', calls: '260', enabled: true },
      { name: 'get_trace', description: 'Fetch stack trace and release metadata', calls: '211', enabled: true },
      { name: 'assign_issue', description: 'Assign an incident to a responder', calls: '28', enabled: false },
    ],
    scope: 'remote',
  },
  {
    id: 'brave-search',
    name: 'brave-search',
    initials: 'bs',
    packageName: '@modelcontextprotocol/brave-search',
    version: 'v0.6.0',
    status: 'connected',
    transport: 'stdio',
    latency: '31ms',
    endpoint: 'brave-search local bridge',
    auth: 'API key',
    protocol: '2025-03-26',
    approval: 'Search only',
    timeout: '20,000 ms',
    connection: 'Child process',
    handshake: '18m ago',
    tools: 2,
    enabledTools: 2,
    resources: 0,
    calls: '356',
    callsDetail: 'last 30 days',
    avgLatency: '31ms',
    latencyDetail: 'p95 · 126ms',
    errorRate: '0.0%',
    errorDetail: '0 / 356 calls',
    capabilities: ['tools'],
    activeCapabilities: ['tools'],
    serverTools: [
      { name: 'web_search', description: 'Search the web with grounded snippets', calls: '292', enabled: true },
      { name: 'local_search', description: 'Find nearby business or place results', calls: '64', enabled: true },
    ],
    scope: 'remote',
  },
  {
    id: 'puppeteer',
    name: 'puppeteer',
    initials: 'pp',
    packageName: '@modelcontextprotocol/puppeteer',
    version: 'v0.7.3',
    status: 'disabled',
    transport: 'stdio',
    latency: '-',
    endpoint: 'local chromium',
    auth: 'local process',
    protocol: '2025-03-26',
    approval: 'Manual',
    timeout: '45,000 ms',
    connection: 'Stopped',
    handshake: 'disabled',
    tools: 7,
    enabledTools: 0,
    resources: 0,
    calls: '0',
    callsDetail: 'last 30 days',
    avgLatency: '-',
    latencyDetail: 'disabled',
    errorRate: '-',
    errorDetail: 'server disabled',
    capabilities: ['tools', 'resources'],
    activeCapabilities: [],
    serverTools: [
      { name: 'navigate', description: 'Open a page in a browser context', calls: '0', enabled: false },
      { name: 'screenshot', description: 'Capture viewport or full-page screenshots', calls: '0', enabled: false },
    ],
    scope: 'local',
  },
  {
    id: 'slack',
    name: 'slack',
    initials: 'sl',
    packageName: '@modelcontextprotocol/slack',
    version: 'v0.4.9',
    status: 'disabled',
    transport: 'http',
    latency: '-',
    endpoint: 'mcp.slack.com',
    auth: 'OAuth',
    protocol: '2025-03-26',
    approval: 'Manual',
    timeout: '30,000 ms',
    connection: 'Stopped',
    handshake: 'disabled',
    tools: 10,
    enabledTools: 0,
    resources: 0,
    calls: '0',
    callsDetail: 'last 30 days',
    avgLatency: '-',
    latencyDetail: 'disabled',
    errorRate: '-',
    errorDetail: 'server disabled',
    capabilities: ['tools', 'prompts'],
    activeCapabilities: [],
    serverTools: [
      { name: 'send_message', description: 'Post an approved message to a channel', calls: '0', enabled: false },
      { name: 'search_messages', description: 'Search connected Slack workspaces', calls: '0', enabled: false },
    ],
    scope: 'remote',
  },
  {
    id: 'browser-use',
    name: 'browser-use',
    initials: 'bu',
    packageName: '@modelcontextprotocol/browser-use',
    version: 'v0.2.0',
    status: 'error',
    transport: 'sse',
    latency: '-',
    endpoint: 'browser-use.local/sse',
    auth: 'local session',
    protocol: '2025-03-26',
    approval: 'Manual',
    timeout: '45,000 ms',
    connection: 'Retry paused',
    handshake: 'failed 23m ago',
    tools: 12,
    enabledTools: 0,
    resources: 0,
    calls: '48',
    callsDetail: 'last 30 days',
    avgLatency: '-',
    latencyDetail: 'not connected',
    errorRate: '18.7%',
    errorDetail: '9 / 48 calls',
    capabilities: ['tools', 'resources', 'sampling'],
    activeCapabilities: [],
    serverTools: [
      { name: 'open_page', description: 'Open browser automation target', calls: '21', enabled: false },
      { name: 'click', description: 'Click an element by selector', calls: '14', enabled: false },
      { name: 'extract_text', description: 'Extract visible page content', calls: '13', enabled: false },
    ],
    scope: 'local',
  },
];

function ServerIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M5.2 3.2h5.6M8 1.8v3M4.6 6.3 2.8 4.5M11.4 6.3l1.8-1.8M4.6 9.7l-1.8 1.8M11.4 9.7l1.8 1.8M5.2 12.8h5.6M8 11.2v3" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.25" />
      <rect x="5" y="5" width="6" height="6" rx="1.4" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

function ToolIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="M5.2 5.4 3 8l2.2 2.6M10.8 5.4 13 8l-2.2 2.6M7.2 11.5l1.6-7" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.25" />
    </svg>
  );
}

function statusLabel(status) {
  if (status === 'connected') return 'Connected';
  if (status === 'disabled') return 'Disabled';
  return 'Error';
}

function serverMatchesFilter(server, filter) {
  if (filter === 'All') return true;
  if (filter === 'Errors') return server.status === 'error';
  return server.status === filter.toLowerCase();
}

function groupServers(servers) {
  return servers.reduce((groups, server) => {
    const group = server.status === 'connected' ? 'Connected' : server.status === 'disabled' ? 'Disabled' : 'Needs attention';
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(server);
    return groups;
  }, new Map());
}

export default function ToolsPage() {
  const { emitActivity } = useChromeRuntime();
  const [activeFilter, setActiveFilter] = useState('All');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState('github');
  const [servers, setServers] = useState(MCP_SERVERS);

  const filteredServers = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return servers.filter((server) => {
      const matchesFilter = serverMatchesFilter(server, activeFilter);
      const haystack = `${server.name} ${server.packageName} ${server.transport} ${server.endpoint} ${server.scope}`.toLowerCase();
      const matchesQuery = !normalizedQuery || haystack.includes(normalizedQuery);
      return matchesFilter && matchesQuery;
    });
  }, [activeFilter, query, servers]);
  const groupedServers = useMemo(() => groupServers(filteredServers), [filteredServers]);
  const selectedServer = servers.find((server) => server.id === selectedId) ?? filteredServers[0] ?? servers[0];

  const totals = useMemo(() => ({
    connected: servers.filter((server) => server.status === 'connected').length,
    disabled: servers.filter((server) => server.status === 'disabled').length,
    errors: servers.filter((server) => server.status === 'error').length,
  }), [servers]);

  const emitServerAction = (title) => {
    emitActivity?.({
      kind: 'tools',
      title,
      detail: `${selectedServer.name} · ${selectedServer.transport}`,
    });
  };

  const toggleServerTool = (toolName) => {
    setServers((current) => current.map((server) => {
      if (server.id !== selectedServer.id) return server;
      return {
        ...server,
        serverTools: server.serverTools.map((tool) => (
          tool.name === toolName ? { ...tool, enabled: !tool.enabled } : tool
        )),
      };
    }));
  };

  const addServer = () => {
    const nextIndex = servers.length + 1;
    const nextServer = {
      ...MCP_SERVERS[1],
      id: `custom-${Date.now()}`,
      name: `custom-server-${nextIndex}`,
      initials: 'cs',
      packageName: '@workspace/custom-mcp',
      version: 'v0.1.0',
      status: 'disabled',
      latency: '-',
      tools: 0,
      enabledTools: 0,
      resources: 0,
      calls: '0',
      callsDetail: 'new server',
      serverTools: [],
    };
    setServers((current) => [nextServer, ...current]);
    setSelectedId(nextServer.id);
    emitActivity?.({
      kind: 'tools',
      title: `Added ${nextServer.name}`,
      detail: 'MCP server scaffold ready to configure',
    });
  };

  return (
    <div className="tools-page">
      <PaneList className="tools-pane" label="MCP servers">
        <PaneListFilters filters={SERVER_FILTERS} activeFilter={activeFilter} onFilterChange={setActiveFilter} />
        <PaneListToolbar actionLabel="+ Add server" onAction={addServer}>
          <PaneListSearch value={query} placeholder="Search servers" onChange={setQuery} />
          <PaneListSortButton title="Sort" onClick={() => emitServerAction('Sorted MCP servers')}>Usage</PaneListSortButton>
        </PaneListToolbar>

        <PaneListScroll>
          {Array.from(groupedServers.entries()).map(([group, groupServersList]) => (
            <PaneListGroup key={group} title={group} count={groupServersList.length}>
              {groupServersList.map((server) => (
                <button
                  type="button"
                  key={server.id}
                  className={`sess-row tools-server-row ${selectedServer.id === server.id ? 'is-selected' : ''} tools-server-row--${server.status}`}
                  aria-label={`${server.name}, ${statusLabel(server.status)}, ${server.transport}`}
                  onClick={() => setSelectedId(server.id)}
                >
                  <span className="sess-row__top">
                    <span className={`sess-row__dot tools-status-dot--${server.status}`} />
                    <span className="sess-row__project">{server.transport}</span>
                  </span>
                  <span className="sess-row__age">{server.latency}</span>
                  <span className="pane-row-title tools-server-row__title">{server.name}</span>
                  <span className="pane-row-caption sess-row__msgs">
                    <span className="num">{server.tools}</span> tools · {server.resources} resources · <span className="tools-server-row__scope">{server.scope}</span>
                  </span>
                </button>
              ))}
            </PaneListGroup>
          ))}
          {filteredServers.length === 0 ? (
            <div className="empty-state small">No MCP servers match the current filter.</div>
          ) : null}
        </PaneListScroll>

        <PaneListFooter>
          <span>{totals.connected} connected</span>
          <span>{totals.disabled} disabled</span>
          <span>{totals.errors} errors</span>
        </PaneListFooter>
      </PaneList>

      <main className="tools-detail">
        <DetailHeader
          title={selectedServer.name}
          subtitle={`${selectedServer.packageName} · ${selectedServer.version}`}
          actions={(
            <DetailHeaderActions>
              <DetailHeaderAction
                icon={<LogsActionIcon />}
                label="Logs"
                tooltip={`Open logs for ${selectedServer.name}`}
                ariaLabel={`Open logs for ${selectedServer.name}`}
                onClick={() => emitServerAction(`Opened logs for ${selectedServer.name}`)}
              />
              <DetailHeaderAction
                icon={<ReconnectActionIcon />}
                label="Reconnect"
                tooltip={`Reconnect ${selectedServer.name}`}
                ariaLabel={`Reconnect ${selectedServer.name}`}
                onClick={() => emitServerAction(`Reconnected ${selectedServer.name}`)}
              />
              <DetailHeaderAction
                icon={<ConfigureActionIcon />}
                label="Configure"
                tooltip={`Configure ${selectedServer.name}`}
                ariaLabel={`Configure ${selectedServer.name}`}
                variant="primary"
                onClick={() => emitServerAction(`Configured ${selectedServer.name}`)}
              />
            </DetailHeaderActions>
          )}
          meta={[
            <span key="status" className={`tools-status-pill tools-status-pill--${selectedServer.status}`}>
              <span className={`tools-status-dot tools-status-dot--${selectedServer.status}`} />
              {statusLabel(selectedServer.status)}
            </span>,
            <span key="counts">{selectedServer.tools} tools · {selectedServer.resources} resources</span>,
            <span key="endpoint">endpoint: <code>{selectedServer.endpoint}</code></span>,
            <span key="auth">auth: {selectedServer.auth}</span>,
          ]}
        />

        <div className="tools-detail__body">
          <section className="tools-metrics" aria-label="MCP server metrics">
            <article className="tools-metric-card">
              <span>Tools</span>
              <strong>{selectedServer.tools}</strong>
              <small>{selectedServer.enabledTools} enabled · {Math.max(selectedServer.tools - selectedServer.enabledTools, 0)} off</small>
            </article>
            <article className="tools-metric-card">
              <span>Calls</span>
              <strong>{selectedServer.calls}</strong>
              <small>{selectedServer.callsDetail}</small>
            </article>
            <article className="tools-metric-card">
              <span>Avg latency</span>
              <strong>{selectedServer.avgLatency}</strong>
              <small>{selectedServer.latencyDetail}</small>
            </article>
            <article className="tools-metric-card">
              <span>Error rate</span>
              <strong>{selectedServer.errorRate}</strong>
              <small>{selectedServer.errorDetail}</small>
            </article>
          </section>

          <section className="tools-section">
            <h2>Connection</h2>
            <dl className="tools-connection-grid">
              <div><dt>Transport</dt><dd>{selectedServer.transport.replace(' · ', '-')}</dd></div>
              <div><dt>Protocol version</dt><dd>{selectedServer.protocol}</dd></div>
              <div><dt>Endpoint</dt><dd>{selectedServer.endpoint}</dd></div>
              <div><dt>Auth</dt><dd>{selectedServer.auth.toLowerCase()}</dd></div>
              <div><dt>Approval policy</dt><dd>{selectedServer.approval}</dd></div>
              <div><dt>Timeout</dt><dd>{selectedServer.timeout}</dd></div>
              <div><dt>Connection</dt><dd>{selectedServer.connection}</dd></div>
              <div><dt>Last handshake</dt><dd>{selectedServer.handshake}</dd></div>
            </dl>
          </section>

          <section className="tools-section">
            <h2>Capabilities</h2>
            <div className="tools-capabilities">
              {selectedServer.capabilities.map((capability) => (
                <span
                  key={capability}
                  className={`tools-capability ${selectedServer.activeCapabilities.includes(capability) ? 'is-active' : ''}`}
                >
                  {capability}
                </span>
              ))}
            </div>
          </section>

          <section className="tools-section">
            <div className="tools-section__head">
              <h2>Tools <span>{selectedServer.enabledTools} of {selectedServer.tools} enabled</span></h2>
              <div>
                <button type="button" className="tools-link-button" onClick={() => emitServerAction(`Enabled all tools for ${selectedServer.name}`)}>Enable all</button>
                <button type="button" className="tools-link-button" onClick={() => emitServerAction(`Added tool to ${selectedServer.name}`)}>+ Add</button>
              </div>
            </div>
            <div className="tools-tool-list">
              {selectedServer.serverTools.length > 0 ? selectedServer.serverTools.map((tool) => (
                <article className="tools-tool-row" key={tool.name}>
                  <span className="tools-tool-row__icon"><ToolIcon /></span>
                  <div className="tools-tool-row__body">
                    <strong>{tool.name}</strong>
                    <p>{tool.description}</p>
                  </div>
                  <span className="tools-tool-row__calls">{tool.calls} calls</span>
                  <button
                    type="button"
                    className={`tools-toggle ${tool.enabled ? 'is-on' : ''}`}
                    aria-label={`${tool.enabled ? 'Disable' : 'Enable'} ${tool.name}`}
                    onClick={() => toggleServerTool(tool.name)}
                  >
                    <span />
                  </button>
                </article>
              )) : (
                <div className="tools-empty-state">No tools have been registered for this server yet.</div>
              )}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
