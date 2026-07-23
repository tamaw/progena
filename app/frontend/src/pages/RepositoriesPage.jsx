import { useMemo, useState } from 'react';
import {
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListVirtualized,
} from '../components/layout/PaneList';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import DetailHeader, {
  CloneActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  OpenActionIcon,
  PullActionIcon,
} from '../components/layout/DetailHeader';

const SOURCE_FILTERS = ['All sources', 'GitHub', 'Local', 'Warnings'];

const INITIAL_REPOSITORIES = [
  {
    id: 'repo-progenitor',
    name: 'progenitor',
    source: 'GitHub',
    url: 'github.com/tamaw/progenitor',
    branch: 'main',
    status: 'active',
    health: 'Synced',
    summary: 'agent workspace · python/ts',
    changed: 24,
    prs: 5,
    checks: '7/7',
    updated: '4m',
    owner: 'Progenitor',
    accent: 'green',
  },
  {
    id: 'repo-chrome',
    name: 'agent-ui-kit',
    source: 'GitHub',
    url: 'github.com/tamaw/progenitor/tree/chrome',
    branch: 'feature/chrome',
    status: 'review',
    health: 'Review',
    summary: 'frontend shell · react/vite',
    changed: 18,
    prs: 3,
    checks: '6/7',
    updated: '19m',
    owner: 'UI Agent',
    accent: 'blue',
  },
  {
    id: 'repo-runtime',
    name: 'runtime-gateway',
    source: 'Local',
    url: './app/backend',
    branch: 'main',
    status: 'synced',
    health: 'Ready',
    summary: 'FastAPI · IPC streaming',
    changed: 6,
    prs: 0,
    checks: '5/5',
    updated: '42m',
    owner: 'Runtime',
    accent: 'purple',
  },
  {
    id: 'repo-mcp',
    name: 'mcp-registry',
    source: 'GitHub',
    url: 'github.com/tamaw/progenitor/tree/mcp',
    branch: 'feature/tools',
    status: 'warning',
    health: 'Warning',
    summary: 'tool registry · MCP servers',
    changed: 11,
    prs: 2,
    checks: '4/5',
    updated: '1h',
    owner: 'Tools',
    accent: 'amber',
  },
  {
    id: 'repo-docs',
    name: 'ops-playbooks',
    source: 'Local',
    url: './docs',
    branch: 'main',
    status: 'idle',
    health: 'Indexed',
    summary: 'docs · prompt packs',
    changed: 3,
    prs: 0,
    checks: '2/2',
    updated: '2h',
    owner: 'Knowledge',
    accent: 'slate',
  },
];

const REPOSITORY_DETAILS = {
  'repo-progenitor': {
    handle: 'github.com/tamaw/progenitor · agent workspace',
    branchState: 'main · clean',
    badges: ['6 sub-projects'],
    commits: '1,284',
    branches: '14',
    openPullRequests: '5',
    stats: [
      { label: 'Commits (30d)', value: '418', detail: '241 by agents · 177 by humans' },
      { label: 'Open PRs', value: '5', detail: '2 ready · 3 in review' },
      { label: 'CI', value: '96%', detail: 'passing on main' },
      { label: 'Coverage', value: '74.2%', detail: '+0.3% this week' },
    ],
    subProjects: [
      ['backend', 'app/backend · fastapi'],
      ['frontend', 'app/frontend · react/vite'],
      ['agent loop', 'app/progenitor · langgraph'],
      ['runtime', 'app/progenitor/runtime · ipc'],
      ['tools', 'app/progenitor/tools · python'],
      ['docs', 'docs/ · markdown'],
    ],
    branchList: [
      ['main', 'protected · default', 'a4f2e1c', '@progenitor · 4m ago'],
      ['feature/chrome', '', '+24 / -8', '@builder · 19m ago'],
      ['feature/tools', '', '+11 / -3', '@tools · 1h ago'],
      ['fix/stream-order', '', '+8 / -2', '@reviewer · 3h ago'],
      ['chore/eval-refresh', '', '+4 / -1', '@observer · Wed'],
    ],
    commitsList: [
      ['P', '#2f6ff6', 'feat(chrome): tighten repository profile view', 'a4f2e1c', '4m'],
      ['B', '#a78bfa', 'refactor(chat): promote reasoning entries into timeline', '9b32d10', '1h'],
      ['O', '#22a06b', 'test(runtime): cover streamed heartbeat callbacks', '7a1e8c4', '3h'],
      ['J', '#ef6c2a', 'fix(migrate): guard against stale quick chat sessions', '5db0119', 'Wed'],
      ['P', '#2f6ff6', 'chore: refresh frontend build artifacts', '3c9f44d', 'Wed'],
    ],
    tree: `app/
  -> backend/ · FastAPI gateway
  -> frontend/ · chrome + pages
app/progenitor/
  -> graph/ · LangGraph routing
  -> runtime/ · loop + queues
  -> tools/ · system tools
docs/ · architecture + evaluations
tests/ · pytest suites`,
    agents: [
      ['P', '#2f6ff6', 'Progenitor', 'read · write · reason · last edit 4m ago', '418 commits'],
      ['B', '#a78bfa', 'Builder', 'read · write · review · last edit 1h ago', '214 commits'],
      ['O', '#22a06b', 'Observer', 'read · comment · last touch 3h ago', '61 commits'],
      ['J', '#ef6c2a', 'Reviewer', 'read · review · last touch Wed', '12 commits'],
    ],
  },
  'repo-chrome': {
    handle: 'github.com/tamaw/progenitor/tree/chrome · frontend shell',
    branchState: 'feature/chrome · review',
    badges: ['4 sub-projects'],
    commits: '612',
    branches: '8',
    openPullRequests: '3',
    stats: [
      { label: 'Commits (30d)', value: '128', detail: '92 by agents · 36 by humans' },
      { label: 'Open PRs', value: '3', detail: '1 ready · 2 in review' },
      { label: 'CI', value: '86%', detail: 'one frontend warning' },
      { label: 'Coverage', value: '68.4%', detail: '+1.1% this week' },
    ],
    subProjects: [
      ['shell', 'AppRuntimeShell · chrome frame'],
      ['chat', 'chat panel · streaming'],
      ['quick chat', 'top nav modal · markdown'],
      ['pages', 'sessions/tasks/repos'],
    ],
    branchList: [
      ['feature/chrome', 'active', '+18 / -6', '@progenitor · 19m ago'],
      ['feature/quick-chat', '', '+12 / -4', '@builder · 1h ago'],
      ['main', 'protected · default', 'c81ab20', '@reviewer · 2d ago'],
    ],
    commitsList: [
      ['C', '#2f6ff6', 'feat(repos): align profile page with prototype', 'cbe1011', '19m'],
      ['Q', '#22a06b', 'feat(quick-chat): stream markdown replies', '661a1de', '1h'],
      ['U', '#a78bfa', 'style(chat): tune gutters and icon semantics', '3a71c90', '2h'],
    ],
    tree: `app/frontend/src/
  -> components/ · shared chrome primitives
  -> pages/ · route-level workspaces
  -> styles/ · chrome-frame + chat panel
new/ · visual prototype reference`,
    agents: [
      ['P', '#2f6ff6', 'Progenitor', 'write · review · last edit 19m ago', '128 commits'],
      ['R', '#22a06b', 'Reviewer', 'read · comment · last touch 1h ago', '34 commits'],
    ],
  },
};

const FALLBACK_DETAIL = {
  handle: 'local source · indexed workspace',
  branchState: 'main · indexed',
  badges: ['1 sub-project'],
  commits: '94',
  branches: '3',
  openPullRequests: '0',
  stats: [
    { label: 'Commits (30d)', value: '24', detail: '16 by agents · 8 by humans' },
    { label: 'Open PRs', value: '0', detail: 'nothing waiting' },
    { label: 'CI', value: '100%', detail: 'checks green' },
    { label: 'Coverage', value: 'n/a', detail: 'not reported' },
  ],
  subProjects: [
    ['source', 'workspace root'],
    ['docs', 'attached context'],
  ],
  branchList: [
    ['main', 'default', 'indexed', '@progenitor · now'],
    ['scratch/source-map', '', '+0 / -0', '@progenitor · now'],
  ],
  commitsList: [
    ['P', '#2f6ff6', 'chore(source): index repository context', 'indexed', 'now'],
  ],
  tree: `./
  -> app/ · source context
  -> docs/ · knowledge context
README.md`,
  agents: [
    ['P', '#2f6ff6', 'Progenitor', 'read · indexed · now', '24 commits'],
  ],
};

const WORKSPACE_METRICS = [
  { label: 'Linked repos', value: '9', detail: '3 active' },
  { label: 'Open PRs', value: '14', detail: '5 reviewed' },
  { label: 'Sync health', value: '99%', detail: '2 warnings' },
  { label: 'Coverage', value: '74.2%', detail: '+0.3% this week' },
];

function normalizeStatus(status) {
  if (status === 'warning') return 'warning';
  if (status === 'review') return 'review';
  if (status === 'active' || status === 'synced') return 'synced';
  return 'idle';
}

// Map repo status to a shared DetailStatus tone (coloured dot + label).
const REPO_STATUS_TONE = {
  synced: 'ok',
  review: 'info',
  warning: 'warning',
  idle: 'muted',
};

function repositoryMatchesFilter(repository, activeFilter) {
  if (activeFilter === 'All sources') {
    return true;
  }
  if (activeFilter === 'Warnings') {
    return repository.status === 'warning' || repository.status === 'review';
  }
  return repository.source === activeFilter;
}

function groupRepositories(repositories) {
  return repositories.reduce((groups, repository) => {
    const group = repository.source === 'Local' ? 'Local paths' : 'GitHub remotes';
    if (!groups.has(group)) {
      groups.set(group, []);
    }
    groups.get(group).push(repository);
    return groups;
  }, new Map());
}

export default function RepositoriesPage() {
  const [repositories, setRepositories] = useState(INITIAL_REPOSITORIES);
  const [activeFilter, setActiveFilter] = useState(SOURCE_FILTERS[0]);
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState(INITIAL_REPOSITORIES[0].id);

  const filteredRepositories = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return repositories.filter((repository) => {
      const matchesFilter = repositoryMatchesFilter(repository, activeFilter);
      const haystack = `${repository.name} ${repository.url} ${repository.branch} ${repository.owner} ${repository.summary}`.toLowerCase();
      const matchesQuery = !normalizedQuery || haystack.includes(normalizedQuery);
      return matchesFilter && matchesQuery;
    });
  }, [activeFilter, query, repositories]);

  const groupedRepositories = useMemo(() => groupRepositories(filteredRepositories), [filteredRepositories]);
  const selectedRepository = repositories.find((repository) => repository.id === selectedId)
    || filteredRepositories[0]
    || repositories[0];

  const selectedDetail = REPOSITORY_DETAILS[selectedRepository.id] ?? FALLBACK_DETAIL;

  const connectRepository = () => {
    const nextIndex = repositories.length + 1;
    const nextRepository = {
      id: `repo-scratch-${Date.now()}`,
      name: `scratch-source-${nextIndex}`,
      source: nextIndex % 2 === 0 ? 'GitHub' : 'Local',
      url: nextIndex % 2 === 0 ? `github.com/tamaw/scratch-source-${nextIndex}` : `./scratch/source-${nextIndex}`,
      branch: nextIndex % 2 === 0 ? 'feature/source-map' : 'main',
      status: 'idle',
      health: 'Indexed',
      summary: 'fresh source · ready',
      changed: 0,
      prs: 0,
      checks: '0/0',
      updated: 'now',
      owner: 'New source',
      accent: nextIndex % 2 === 0 ? 'blue' : 'green',
    };

    setRepositories((current) => [nextRepository, ...current]);
    setSelectedId(nextRepository.id);
  };


  return (
    <PageShell className="repositories-page">
      <PaneList className="repositories-pane" label="Repositories">
        <PaneListFilters filters={SOURCE_FILTERS} activeFilter={activeFilter} onFilterChange={setActiveFilter} />
        <PaneListToolbar actionLabel="+ Source" onAction={connectRepository}>
          <PaneListSearch value={query} placeholder="Search sources" onChange={setQuery} />
          <PaneListSortButton title="Sort">Health</PaneListSortButton>
        </PaneListToolbar>

        <PaneListVirtualized
          groups={Array.from(groupedRepositories.entries()).map(([group, items]) => ({
            key: group,
            title: group,
            items,
          }))}
          getItemKey={(repository) => repository.id}
          estimatedItemHeight={92}
          renderItem={(repository) => (
            <button
              type="button"
              className={`repo-source-row ${selectedRepository.id === repository.id ? 'is-selected' : ''}`}
              onClick={() => setSelectedId(repository.id)}
            >
              <span className={`repo-source-row__mark repo-source-row__mark--${normalizeStatus(repository.status)}`} />
              <span className="repo-source-row__top">
                <span className="repo-source-row__source">{repository.source}</span>
                <span className="repo-source-row__branch">{repository.branch}</span>
              </span>
              <span className="repo-source-row__age">{repository.updated}</span>
              <span className="repo-source-row__title">{repository.name}</span>
              <span className="repo-source-row__meta">{repository.changed} changed · {repository.prs} PRs · checks {repository.checks}</span>
            </button>
          )}
        />
        {filteredRepositories.length === 0 ? (
          <div className="empty-state small">No repositories match the current source filter.</div>
        ) : null}

        <PaneListFooter>
          <span>{selectedRepository.branch}</span>
          <span>{selectedRepository.health}</span>
        </PaneListFooter>
      </PaneList>

      <DetailPane className="repositories-main repo-profile">
        <DetailHeader
          title={selectedRepository.name}
          actions={(
            <DetailHeaderActions>
              <DetailHeaderAction
                icon={<CloneActionIcon />}
                label="Clone"
                tooltip={`Clone ${selectedRepository.name}`}
                ariaLabel={`Clone ${selectedRepository.name}`}
              />
              <DetailHeaderAction
                icon={<OpenActionIcon />}
                label="Open in editor"
                tooltip={`Open ${selectedRepository.name} in editor`}
                ariaLabel={`Open ${selectedRepository.name} in editor`}
              />
              <DetailHeaderAction
                icon={<PullActionIcon />}
                label={`Pull ${selectedRepository.branch}`}
                tooltip={`Pull ${selectedRepository.branch}`}
                ariaLabel={`Pull ${selectedRepository.branch}`}
                variant="primary"
              />
            </DetailHeaderActions>
          )}
          meta={[
            <DetailStatus key="state" tone={REPO_STATUS_TONE[normalizeStatus(selectedRepository.status)]}>
              {selectedDetail.branchState}
            </DetailStatus>,
            ...selectedDetail.badges.map((badge) => <span className="muted" key={badge}>{badge}</span>),
            <span key="commits"><strong>{selectedDetail.commits}</strong> commits</span>,
            <span key="branches"><strong>{selectedDetail.branches}</strong> branches</span>,
            <span key="prs"><strong>{selectedDetail.openPullRequests}</strong> PRs open</span>,
          ]}
        />

        <div className="repo-profile__body">
          <section className="repo-stat-row" aria-label="Repository metrics">
            {selectedDetail.stats.map((metric) => (
              <article className="repo-stat-cell" key={metric.label}>
                <div className="repo-stat-cell__label">{metric.label}</div>
                <div className="repo-stat-cell__value">{metric.value}</div>
                <div className="repo-stat-cell__detail">{metric.detail}</div>
              </article>
            ))}
          </section>

          <section className="repo-section">
            <h2 className="repo-section__title">Workspace signal <span className="muted">{filteredRepositories.length} visible</span></h2>
            <div className="repo-stat-row repo-stat-row--compact">
              {WORKSPACE_METRICS.map((metric) => (
                <article className="repo-stat-cell" key={metric.label}>
                  <div className="repo-stat-cell__label">{metric.label}</div>
                  <div className="repo-stat-cell__value">{metric.value}</div>
                  <div className="repo-stat-cell__detail">{metric.detail}</div>
                </article>
              ))}
            </div>
          </section>

          <section className="repo-section">
            <h2 className="repo-section__title">Sub-projects <span className="muted">{selectedDetail.subProjects.length}</span></h2>
            <div className="repo-kv-grid">
              {selectedDetail.subProjects.map(([label, value]) => (
                <div className="repo-kv" key={label}>
                  <span className="repo-kv__label">{label}</span>
                  <span className="repo-kv__value">{value}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="repo-section">
            <h2 className="repo-section__title">
              Branches <span className="muted">{selectedDetail.branches}</span>
              <span className="repo-section__tools"><button type="button" className="repo-btn repo-btn--ghost">+ New branch</button></span>
            </h2>
            <div className="repo-list">
              {selectedDetail.branchList.map(([branchName, branchMeta, ahead, author]) => (
                <article className="repo-branch" key={branchName}>
                  <span className="repo-branch__icon">⎇</span>
                  <span className="repo-branch__name">{branchName} {branchMeta ? <span className="muted">· {branchMeta}</span> : null}</span>
                  <span className="repo-branch__ahead">{ahead}</span>
                  <span className="repo-branch__author">{author}</span>
                </article>
              ))}
            </div>
          </section>

          <section className="repo-section">
            <h2 className="repo-section__title">Recent commits <span className="muted">{selectedRepository.branch}</span></h2>
            <div className="repo-list">
              {selectedDetail.commitsList.map(([initial, color, message, sha, time]) => (
                <article className="repo-commit" key={`${sha}-${message}`}>
                  <span className="repo-commit__avatar" style={{ background: color }}>{initial}</span>
                  <span className="repo-commit__message">{message}</span>
                  <span className="repo-commit__sha">{sha}</span>
                  <span className="repo-commit__time">{time}</span>
                </article>
              ))}
            </div>
          </section>

          <section className="repo-section">
            <h2 className="repo-section__title">Tree</h2>
            <pre className="repo-file-tree">{selectedDetail.tree}</pre>
          </section>

          <section className="repo-section">
            <h2 className="repo-section__title">Connected agents <span className="muted">{selectedDetail.agents.length}</span></h2>
            <div className="repo-list">
              {selectedDetail.agents.map(([initial, color, name, detail, count]) => (
                <article className="repo-agent-row" key={name}>
                  <span className="repo-agent-row__avatar" style={{ background: color }}>{initial}</span>
                  <span className="repo-agent-row__body">
                    <span className="repo-agent-row__title">{name}</span>
                    <span className="repo-agent-row__detail">{detail}</span>
                  </span>
                  <span className="repo-agent-row__count">{count}</span>
                </article>
              ))}
            </div>
          </section>
        </div>
      </DetailPane>
    </PageShell>
  );
}
