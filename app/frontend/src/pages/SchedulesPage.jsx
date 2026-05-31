import { useMemo, useState } from 'react';
import {
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListGroup,
  PaneListHeader,
  PaneListScroll,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
} from '../components/layout/PaneList';
import DetailHeader, {
  ConfigureActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DuplicateActionIcon,
  PlayActionIcon,
} from '../components/layout/DetailHeader';

const SCHEDULE_FILTERS = ['All', 'Active', 'Paused', 'Failed'];

const SCHEDULE_TEMPLATES = [
  {
    id: 'morning-brief',
    group: 'Daily',
    name: 'Morning workspace brief',
    status: 'active',
    cadence: 'Every day at 08:30',
    nextRun: 'tomorrow 08:30',
    owner: 'Planner',
    timezone: 'Australia/Brisbane',
    summary: 'Summarize new sessions, active tasks, repository drift, and overnight alerts.',
    tools: ['sessions', 'todos', 'github', 'knowledge'],
    repositories: ['progenitor', 'agent-runtime'],
    knowledge: ['Project roadmap', 'Runtime incidents'],
    tasks: [
      { title: 'Collect changed sessions', state: 'ready', age: 'next run' },
      { title: 'Rank open tasks by priority', state: 'ready', age: 'next run' },
      { title: 'Draft brief into chat', state: 'ready', age: 'next run' },
    ],
    recentRuns: [
      { title: 'Morning workspace brief', outcome: 'success', detail: '6 sections - 11 sources', age: 'today 08:30' },
      { title: 'Morning workspace brief', outcome: 'success', detail: '5 sections - 9 sources', age: 'yesterday' },
      { title: 'Morning workspace brief', outcome: 'partial', detail: 'GitHub rate limited', age: '2d ago' },
    ],
    metrics: {
      runs: '31',
      success: '96.7%',
      tasks: '3',
      latency: '18s',
    },
  },
  {
    id: 'repo-sweep',
    group: 'Daily',
    name: 'Repository sweep',
    status: 'active',
    cadence: 'Every 6 hours',
    nextRun: 'in 42m',
    owner: 'Observer',
    timezone: 'Local workspace',
    summary: 'Scan connected repositories for changed branches, stale worktrees, and pending reviews.',
    tools: ['filesystem', 'git', 'github'],
    repositories: ['progenitor', 'progenitor-mcp', 'planning-playground'],
    knowledge: ['Repository symbols', 'Code review rubric'],
    tasks: [
      { title: 'List active branches', state: 'ready', age: '42m' },
      { title: 'Check stale workspace locks', state: 'ready', age: '42m' },
      { title: 'Notify Reviewer for risky diffs', state: 'waiting', age: '42m' },
    ],
    recentRuns: [
      { title: 'Repository sweep', outcome: 'success', detail: '3 repos - 9 branches', age: '5h ago' },
      { title: 'Repository sweep', outcome: 'success', detail: '3 repos - 8 branches', age: '11h ago' },
      { title: 'Repository sweep', outcome: 'success', detail: '2 repos - 5 branches', age: '17h ago' },
    ],
    metrics: {
      runs: '118',
      success: '99.1%',
      tasks: '3',
      latency: '9s',
    },
  },
  {
    id: 'knowledge-refresh',
    group: 'Background',
    name: 'Knowledge refresh',
    status: 'active',
    cadence: 'Every 30 minutes',
    nextRun: 'in 12m',
    owner: 'Indexer',
    timezone: 'Local workspace',
    summary: 'Refresh indexed files, memory snapshots, and session artifacts for agent retrieval.',
    tools: ['filesystem', 'embeddings', 'knowledge'],
    repositories: ['progenitor', 'progenitor-docs'],
    knowledge: ['Repository symbols', 'Session artifacts', 'Design reference captures'],
    tasks: [
      { title: 'Hash changed files', state: 'active', age: 'now' },
      { title: 'Embed updated documents', state: 'queued', age: '12m' },
      { title: 'Compact old session snippets', state: 'queued', age: '12m' },
    ],
    recentRuns: [
      { title: 'Knowledge refresh', outcome: 'success', detail: '249 artifacts indexed', age: '18m ago' },
      { title: 'Knowledge refresh', outcome: 'success', detail: '241 artifacts indexed', age: '48m ago' },
      { title: 'Knowledge refresh', outcome: 'success', detail: '238 artifacts indexed', age: '1h ago' },
    ],
    metrics: {
      runs: '422',
      success: '98.8%',
      tasks: '3',
      latency: '41s',
    },
  },
  {
    id: 'health-watch',
    group: 'Background',
    name: 'Health watch',
    status: 'paused',
    cadence: 'Every 5 minutes',
    nextRun: 'paused',
    owner: 'Watchdog',
    timezone: 'Local workspace',
    summary: 'Watch long-running dev servers, stale background agents, and failed frontend checks.',
    tools: ['processes', 'logs', 'alerts'],
    repositories: ['progenitor'],
    knowledge: ['Operational runbooks', 'Known flaky checks'],
    tasks: [
      { title: 'Check frontend preview server', state: 'paused', age: 'paused' },
      { title: 'Inspect long-running shells', state: 'paused', age: 'paused' },
      { title: 'Prepare alert note', state: 'paused', age: 'paused' },
    ],
    recentRuns: [
      { title: 'Health watch', outcome: 'success', detail: 'all clear', age: '36m ago' },
      { title: 'Health watch', outcome: 'success', detail: 'one stale shell noted', age: '41m ago' },
      { title: 'Health watch', outcome: 'success', detail: 'all clear', age: '46m ago' },
    ],
    metrics: {
      runs: '204',
      success: '97.5%',
      tasks: '3',
      latency: '3s',
    },
  },
  {
    id: 'release-notes',
    group: 'Weekly',
    name: 'Release note draft',
    status: 'active',
    cadence: 'Fridays at 15:00',
    nextRun: 'Fri 15:00',
    owner: 'Builder',
    timezone: 'Australia/Brisbane',
    summary: 'Draft release notes from merged tasks, repository changes, and session summaries.',
    tools: ['github', 'git', 'knowledge', 'chat'],
    repositories: ['progenitor', 'progenitor-mcp'],
    knowledge: ['Architecture decisions', 'Project roadmap', 'Code review rubric'],
    tasks: [
      { title: 'Collect merged PRs', state: 'ready', age: 'Fri' },
      { title: 'Group changes by feature area', state: 'ready', age: 'Fri' },
      { title: 'Generate human review draft', state: 'ready', age: 'Fri' },
    ],
    recentRuns: [
      { title: 'Release note draft', outcome: 'success', detail: '12 changes grouped', age: 'last Fri' },
      { title: 'Release note draft', outcome: 'success', detail: '8 changes grouped', age: '2w ago' },
      { title: 'Release note draft', outcome: 'partial', detail: 'missing labels', age: '3w ago' },
    ],
    metrics: {
      runs: '9',
      success: '88.9%',
      tasks: '3',
      latency: '1m 22s',
    },
  },
  {
    id: 'incident-digest',
    group: 'Weekly',
    name: 'Incident digest',
    status: 'failed',
    cadence: 'Mondays at 09:00',
    nextRun: 'retry pending',
    owner: 'Observer',
    timezone: 'Australia/Brisbane',
    summary: 'Compile production and local runtime incidents into a short learning digest.',
    tools: ['logs', 'sentry', 'knowledge'],
    repositories: ['progenitor'],
    knowledge: ['Runtime incidents', 'Operational runbooks'],
    tasks: [
      { title: 'Fetch incident list', state: 'failed', age: 'today' },
      { title: 'Classify incident themes', state: 'blocked', age: 'today' },
      { title: 'Publish digest', state: 'blocked', age: 'today' },
    ],
    recentRuns: [
      { title: 'Incident digest', outcome: 'failed', detail: 'Sentry token expired', age: 'today 09:00' },
      { title: 'Incident digest', outcome: 'success', detail: '4 incidents summarized', age: 'last Mon' },
      { title: 'Incident digest', outcome: 'success', detail: '2 incidents summarized', age: '2w ago' },
    ],
    metrics: {
      runs: '14',
      success: '85.7%',
      tasks: '3',
      latency: '-',
    },
  },
];

function createDraftSchedule(index) {
  return {
    id: `draft-schedule-${index}`,
    group: 'Drafts',
    name: `New schedule ${index}`,
    status: 'paused',
    cadence: 'Choose trigger',
    nextRun: 'not scheduled',
    owner: 'Planner',
    timezone: 'Local workspace',
    summary: 'A mocked schedule ready to connect to agents, tools, repositories, and knowledge.',
    tools: ['todos', 'knowledge'],
    repositories: ['progenitor'],
    knowledge: ['Workspace starter pack'],
    tasks: [
      { title: 'Choose cadence and trigger', state: 'draft', age: 'now' },
      { title: 'Attach the target agent', state: 'draft', age: 'now' },
    ],
    recentRuns: [],
    metrics: {
      runs: '0',
      success: '-',
      tasks: '2',
      latency: '-',
    },
  };
}

function normalizeStatus(status) {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function matchesSchedule(schedule, filter, search) {
  const matchesFilter = filter === 'All' || normalizeStatus(schedule.status) === filter;
  const haystack = [
    schedule.name,
    schedule.owner,
    schedule.cadence,
    schedule.summary,
    ...schedule.tools,
    ...schedule.repositories,
    ...schedule.knowledge,
  ].join(' ').toLowerCase();
  return matchesFilter && (!search || haystack.includes(search.toLowerCase()));
}

function groupSchedules(schedules) {
  return schedules.reduce((groups, schedule) => {
    if (!groups[schedule.group]) {
      groups[schedule.group] = [];
    }
    groups[schedule.group].push(schedule);
    return groups;
  }, {});
}

function ScheduleRow({ schedule, selected, onSelect }) {
  return (
    <button
      type="button"
      className={`sess-row schedule-row schedule-row--${schedule.status} ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(schedule.id)}
    >
      <span className="sess-row__top">
        <span className={`sess-row__dot schedule-row__status schedule-row__status--${schedule.status}`} />
        <span className="sess-row__agent schedule-row__name">{schedule.name}</span>
        <span className="sess-row__project">{schedule.owner}</span>
      </span>
      <span className="sess-row__age">{schedule.nextRun}</span>
      <span className="sess-row__title">{schedule.cadence}</span>
    </button>
  );
}

function MetricCard({ label, value, detail }) {
  return (
    <div className="agents-metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function Section({ title, count, children, action }) {
  return (
    <section className="agents-section">
      <header className="agents-section__head">
        <h2>{title} <span>{count}</span></h2>
        {action ? <button type="button" className="repo-btn repo-btn--ghost">{action}</button> : null}
      </header>
      {children}
    </section>
  );
}

function ResourceList({ items, icon, meta }) {
  return (
    <div className="agents-connection-list">
      {items.map((item) => (
        <div key={item} className="agents-connection-row">
          <span className="agents-connection-row__icon">{icon}</span>
          <div className="agents-connection-row__body">
            <strong>{item}</strong>
            <p>{meta}</p>
          </div>
          <span className="agents-chip agents-chip--info">linked</span>
        </div>
      ))}
    </div>
  );
}

function SchedulesPage() {
  const [customSchedules, setCustomSchedules] = useState([]);
  const [activeFilter, setActiveFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [selectedScheduleId, setSelectedScheduleId] = useState(SCHEDULE_TEMPLATES[0].id);

  const schedules = useMemo(() => [...SCHEDULE_TEMPLATES, ...customSchedules], [customSchedules]);
  const selectedSchedule = schedules.find((schedule) => schedule.id === selectedScheduleId) || schedules[0];
  const visibleSchedules = useMemo(
    () => schedules.filter((schedule) => matchesSchedule(schedule, activeFilter, search)),
    [activeFilter, schedules, search],
  );
  const groupedSchedules = useMemo(() => groupSchedules(visibleSchedules), [visibleSchedules]);
  const activeCount = schedules.filter((schedule) => schedule.status === 'active').length;
  const taskCount = schedules.reduce((total, schedule) => total + schedule.tasks.length, 0);
  const order = ['Daily', 'Background', 'Weekly', 'Drafts'];

  function handleNewSchedule() {
    const nextSchedule = createDraftSchedule(customSchedules.length + 1);
    setCustomSchedules((current) => [...current, nextSchedule]);
    setSelectedScheduleId(nextSchedule.id);
    setActiveFilter('All');
  }

  return (
    <div className="schedules-page">
      <PaneList label="Schedules" className="schedules-pane">
        <PaneListHeader title="Schedules" count={schedules.length} actionLabel="+ New schedule" onAction={handleNewSchedule} />
        <PaneListFilters filters={SCHEDULE_FILTERS} activeFilter={activeFilter} onFilterChange={setActiveFilter} />
        <PaneListToolbar>
          <PaneListSearch value={search} placeholder="Search schedules..." onChange={setSearch} />
          <PaneListSortButton>Next</PaneListSortButton>
        </PaneListToolbar>
        <PaneListScroll>
          {visibleSchedules.length ? (
            order.map((group) => (
              groupedSchedules[group]?.length ? (
                <PaneListGroup key={group} title={group} count={groupedSchedules[group].length}>
                  {groupedSchedules[group].map((schedule) => (
                    <ScheduleRow
                      key={schedule.id}
                      schedule={schedule}
                      selected={selectedSchedule.id === schedule.id}
                      onSelect={setSelectedScheduleId}
                    />
                  ))}
                </PaneListGroup>
              ) : null
            ))
          ) : (
            <div className="agents-empty-state">No schedules match this view.</div>
          )}
        </PaneListScroll>
        <PaneListFooter>
          <span><span className="num">{activeCount}</span> active</span>
          <span><span className="num">{taskCount}</span> tasks</span>
          <span><span className="num">{schedules.length}</span> schedules</span>
        </PaneListFooter>
      </PaneList>

      <main className="agents-detail schedules-detail" aria-label={`${selectedSchedule.name} schedule`}>
        <DetailHeader
          title={selectedSchedule.name}
          subtitle={`${selectedSchedule.owner} - ${selectedSchedule.timezone}`}
          avatar={<span className={`schedules-detail__avatar schedules-detail__avatar--${selectedSchedule.status}`}>sc</span>}
          actions={(
            <DetailHeaderActions>
              <DetailHeaderAction icon={<ConfigureActionIcon />} label="Configure" tooltip="Configure schedule" ariaLabel="Configure schedule" />
              <DetailHeaderAction icon={<DuplicateActionIcon />} label="Duplicate" tooltip="Duplicate schedule" ariaLabel="Duplicate schedule" />
              <DetailHeaderAction
                icon={<PlayActionIcon />}
                label="Run now"
                tooltip="Run schedule now"
                ariaLabel="Run schedule now"
                variant="primary"
              />
            </DetailHeaderActions>
          )}
          meta={[
            <span key="status" className={`agents-status-pill agents-status-pill--${selectedSchedule.status}`}>
              <span className="adot" />
              {selectedSchedule.status}
            </span>,
            <span key="cadence" className="agents-badge">{selectedSchedule.cadence}</span>,
            <span key="next">next: <strong>{selectedSchedule.nextRun}</strong></span>,
            <span key="links"><strong>{selectedSchedule.tools.length}</strong> tools · <strong>{selectedSchedule.repositories.length}</strong> repos</span>,
          ]}
        />

        <div className="agents-detail__body">
          <div className="agents-metrics">
            <MetricCard label="Runs" value={selectedSchedule.metrics.runs} detail="last 30 days" />
            <MetricCard label="Success" value={selectedSchedule.metrics.success} detail="completed runs" />
            <MetricCard label="Tasks" value={selectedSchedule.metrics.tasks} detail="per schedule run" />
            <MetricCard label="Latency" value={selectedSchedule.metrics.latency} detail="average runtime" />
          </div>

          <section className="agents-section schedules-summary">
            <header className="agents-section__head">
              <h2>Schedule intent <span>{selectedSchedule.status}</span></h2>
              <div>
                <button type="button" className="repo-btn repo-btn--ghost">Edit</button>
                <button type="button" className="repo-btn repo-btn--ghost">Pause</button>
              </div>
            </header>
            <p>{selectedSchedule.summary}</p>
          </section>

          <div className="agents-detail-grid">
            <Section title="Assigned agent" count={selectedSchedule.owner} action="Change">
              <div className="agents-connection-list">
                <div className="agents-connection-row">
                  <span className="agents-connection-row__icon">{selectedSchedule.owner.slice(0, 2)}</span>
                  <div className="agents-connection-row__body">
                    <strong>{selectedSchedule.owner}</strong>
                    <p>receives each scheduled run as a tracked task</p>
                  </div>
                  <span className="agents-chip agents-chip--ok">owner</span>
                </div>
              </div>
            </Section>

            <Section title="Tools" count={`${selectedSchedule.tools.length} attached`} action="+ Add tool">
              <ResourceList items={selectedSchedule.tools} icon="tl" meta="available during run" />
            </Section>

            <Section title="Repositories" count={`${selectedSchedule.repositories.length} connected`} action="+ Attach repo">
              <ResourceList items={selectedSchedule.repositories} icon="rp" meta="workspace context" />
            </Section>

            <Section title="Knowledge" count={`${selectedSchedule.knowledge.length} sources`} action="+ Link source">
              <ResourceList items={selectedSchedule.knowledge} icon="kb" meta="retrieval context" />
            </Section>
          </div>

          <div className="agents-activity-grid">
            <Section title="Run tasks" count={`${selectedSchedule.tasks.length} steps`}>
              <div className="agents-mini-list">
                {selectedSchedule.tasks.map((task) => (
                  <div key={task.title} className="agents-mini-row">
                    <span className="agents-mini-row__icon">tsk</span>
                    <div>
                      <strong>{task.title}</strong>
                      <p>{task.state} - {task.age}</p>
                    </div>
                    <span className={`agents-chip agents-chip--${task.state === 'active' || task.state === 'ready' ? 'ok' : 'info'}`}>{task.state}</span>
                  </div>
                ))}
              </div>
            </Section>

            <Section title="Recent runs" count={`${selectedSchedule.recentRuns.length} shown`}>
              <div className="agents-mini-list">
                {selectedSchedule.recentRuns.length ? selectedSchedule.recentRuns.map((run) => (
                  <div key={`${run.title}-${run.age}`} className="agents-mini-row">
                    <span className="agents-mini-row__icon">run</span>
                    <div>
                      <strong>{run.title}</strong>
                      <p>{run.detail} - {run.age}</p>
                    </div>
                    <span className={`agents-chip agents-chip--${run.outcome === 'success' ? 'ok' : 'info'}`}>{run.outcome}</span>
                  </div>
                )) : <div className="agents-empty-state">No runs yet.</div>}
              </div>
            </Section>
          </div>
        </div>
      </main>
    </div>
  );
}

export default SchedulesPage;
