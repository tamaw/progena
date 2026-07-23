import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { getTimeBucket } from '../components/chat/conversationModel';
import { useConfig } from '../context/ConfigContext';
import { selectRealtime } from '../store/realtimeSlice';
import {
  fetchAgentProfiles,
  requestAgentDetail,
  selectActiveAgentProfile,
  selectAgentProfiles,
  selectAgentProfilesState,
  updateAgentGraph,
} from '../store/uiDataSlice';
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
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import DetailHeader, {
  ConfigureActionIcon,
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DuplicateActionIcon,
  PlayActionIcon,
  SaveActionIcon,
} from '../components/layout/DetailHeader';
import { AgentGraphTab } from '../components/agents/AgentGraph';
import { A2ADetailBody, ACPDetailBody, SubagentDetailBody } from '../components/agents/AgentDetailBodies';
import { formatAge } from '../components/agents/agentFormat';


const AGENT_FILTERS = ['All', 'Running', 'Failed', 'Stopped', 'Disabled'];
// the read-only detail bodies above (subagent / a2a / acp) and its own draft
// field set. Blurbs explain the distinction in the type-picker modal.
const AGENT_TYPES = [
  {
    kind: 'subagent',
    label: 'Subagent',
    blurb: 'In-process DeepAgent persona with a system prompt, tools, repositories and knowledge.',
  },
  {
    kind: 'a2a',
    label: 'A2A agent',
    blurb: 'Remote agent reached over the Agent-to-Agent protocol via its published agent card.',
  },
  {
    kind: 'acp',
    label: 'ACP agent',
    blurb: 'Local agent driven over the Agent Client Protocol (stdio/TCP), e.g. a CLI coding agent.',
  },
];

const AGENT_TYPE_LABELS = Object.fromEntries(AGENT_TYPES.map((type) => [type.kind, type.label]));

// Seed a blank draft for the picked kind. The editable form binds to these
// fields; everything is mocked client-side until a real register_*_agent call
// exists, so creating an agent just clears the draft for now.
function makeDraftAgent(kind) {
  const base = { kind, name: '', role: '', model: '', summary: '' };
  if (kind === 'a2a') {
    return { ...base, url: '', transport: 'jsonrpc', skills: '' };
  }
  if (kind === 'acp') {
    return { ...base, command: '', transport: 'stdio', permissionMode: 'prompt', timeoutSeconds: '120' };
  }
  return { ...base, prompt: '', tools: '', repositories: '' };
}

// Sort modes offered by the toolbar sort menu. Each re-orders the visible list
// (and, since group order is derived from list order, the groups too).
const SORT_MODES = ['Type', 'Name', 'Recent'];
// Lower rank sorts first under "Type" so kinds cluster in a stable order.
const TYPE_RANK = { subagent: 0, a2a: 1, acp: 2 };
// Recency buckets for the "Recent" sort, mirroring the chat sidebar's headers.
const RECENCY_BUCKETS = [
  ['today', 'Today'],
  ['yesterday', 'Yesterday'],
  ['last-week', 'Last Week'],
  ['earlier', 'Earlier'],
];
// The agent graph now lives on the Sequences page; the Agents page shows only
// Details, so the detail tab row is hidden in both views.

// Default graph shape for agents without a stored graph (passed to the canvas).
const FALLBACK_GRAPH = {
  status: 'Draft',
  summary: '',
  nodes: [],
  edges: [],
};

// Sequences isn't wired to real data yet — show one pretend entry instead of
// the live agent list so the page reads as intentional, not empty/broken.
const DUMMY_SEQUENCES = [{
  id: 'sequence-demo',
  kind: 'subagent',
  name: 'Demo Sequence',
  role: 'Placeholder — sequences aren’t wired up yet.',
  summary: 'Placeholder — sequences aren’t wired up yet.',
  status: 'draft',
  updated: new Date().toISOString(),
  model: '',
  tools: [],
  repositories: [],
  knowledge: [],
  skills: [],
}];

function normalizeStatus(status) {
  if (!status || status === 'draft') {
    return 'Drafts';
  }
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function matchesAgent(agent, filter, search) {
  const matchesFilter = filter === 'All' || normalizeStatus(agent.enabled === false ? 'disabled' : agent.status) === filter;
  if (!matchesFilter) {
    return false;
  }
  if (!search) {
    return true;
  }
  const haystack = [
    agent.name,
    agent.handle,
    agent.model,
    agent.role,
    agent.summary,
    ...(agent.skills || []).map((skill) => skill.name),
    ...(agent.tools || []).map((tool) => tool.name),
    ...(agent.repositories || []).map((repo) => repo.name),
    ...(agent.knowledge || []).map((item) => item.name),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return haystack.includes(search.toLowerCase());
}

function updatedTime(agent) {
  const ms = new Date(agent.updated || 0).getTime();
  return Number.isNaN(ms) ? 0 : ms;
}

// Returns a new array; the comparators are total orders with a stable name
// tiebreak so the list never jitters between equal entries.
function sortAgents(agents, mode) {
  const byName = (a, b) => (a.name || '').localeCompare(b.name || '');
  const comparators = {
    Name: byName,
    Recent: (a, b) => updatedTime(b) - updatedTime(a) || byName(a, b),
    Type: (a, b) => {
      const rank = (TYPE_RANK[a.kind] ?? 9) - (TYPE_RANK[b.kind] ?? 9);
      return rank || byName(a, b);
    },
  };
  return [...agents].sort(comparators[mode] || comparators.Type);
}

function groupAgents(agents) {
  return agents.reduce((groups, agent) => {
    const key = agent.group || 'Agents';
    if (!groups[key]) {
      groups[key] = [];
    }
    groups[key].push(agent);
    return groups;
  }, {});
}

function StatusPill({ status }) {
  const label = status || 'draft';
  return (
    <span className={`agents-status-pill agents-status-pill--${label}`}>
      <span className="adot" />
      {label}
    </span>
  );
}

function AgentRow({ agent, selected, onSelect }) {
  const status = agent.enabled === false ? 'disabled' : (agent.status || 'draft');
  return (
    <button
      type="button"
      className={`sess-row agents-agent-row agents-agent-row--${status} ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(agent.id)}
    >
      <span className="sess-row__top">
        <span className={`sess-row__dot agents-agent-row__status agents-agent-row__status--${status}`} />
        <span className="sess-row__agent agents-agent-row__name">{agent.name}</span>
        <span className="sess-row__project">{agent.model}</span>
      </span>
      <span className="sess-row__age">{formatAge(agent.updated)}</span>
      <span className="sess-row__title">{agent.role}</span>
      <span className="sess-row__msgs">
        <span><span className="num">{(agent.tools || []).length}</span> tools</span>
        <span><span className="num">{(agent.repositories || []).length}</span> repos</span>
        <span><span className="num">{(agent.knowledge || []).length}</span> knowledge</span>
      </span>
    </button>
  );
}

// Sequences card list: a two-line card matching the Chat/Workflows pattern —
// a leading status dot (empty unless running/failed), name + last-activity
// on the header line, and the agent's role as a single preview line below.
function SequenceRow({ agent, selected, onSelect }) {
  const status = agent.status || 'draft';
  const dotTone = status === 'running' ? 'info' : (status === 'failed' || status === 'error') ? 'error' : '';
  return (
    <button
      type="button"
      className={`pane-card-row ${selected ? 'active' : ''}`}
      onClick={() => onSelect(agent.id)}
    >
      <span
        className={`pane-card-row__dot${dotTone ? ` pane-card-row__dot--${dotTone}` : ''}`}
        aria-hidden="true"
      />
      <div className="pane-card-row__body">
        <div className="pane-card-row__top">
          <span className="pane-card-row__title">{agent.name}</span>
          <span className="pane-card-row__age">{formatAge(agent.updated)}</span>
        </div>
        <div className="pane-card-row__preview">{agent.role || agent.summary || 'No description yet.'}</div>
      </div>
    </button>
  );
}

// Detail pane for A2A agents: everything shown is sourced from the live agent
// Compact type-picker shown by "+ New": choose what kind of agent to scaffold.
// Picking a type opens the editable draft in the detail pane.
function NewAgentModal({ onPick, onClose }) {
  return (
    <div className="agents-modal-backdrop" onClick={onClose}>
      <section
        className="agents-modal"
        role="dialog"
        aria-modal="true"
        aria-label="New agent"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="agents-modal__head">
          <h3>New agent</h3>
          <button type="button" className="agents-modal__close" onClick={onClose} aria-label="Close">×</button>
        </header>
        <p className="agents-modal__lead">Pick the kind of agent to create.</p>
        <div className="agents-modal__types">
          {AGENT_TYPES.map((type) => (
            <button
              key={type.kind}
              type="button"
              className={`agents-type-card agents-type-card--${type.kind}`}
              onClick={() => onPick(type.kind)}
            >
              <strong>{type.label}</strong>
              <span>{type.blurb}</span>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

function DraftField({ label, hint, children }) {
  return (
    <label className="agents-draft-field">
      <span className="agents-draft-field__label">{label}</span>
      {children}
      {hint ? <span className="agents-draft-field__hint">{hint}</span> : null}
    </label>
  );
}

// Editable body for a not-yet-created agent. Mirrors the read-only detail bodies
// per kind, but every fact is an input. Values are mocked client-side; nothing
// persists until the (future) register_*_agent call is wired up.
function DraftAgentDetailBody({ draft, onChange }) {
  const set = (field) => (event) => onChange(field, event.target.value);
  return (
    <div className="detail-pane__body agents-draft">
      <div className="agents-draft__note">
        Draft {AGENT_TYPE_LABELS[draft.kind]} — fill in the details, then Create. Nothing is saved until you create the agent.
      </div>

      <section className="agents-section">
        <header className="agents-section__head"><h2>Identity</h2></header>
        <div className="agents-draft-grid">
          <DraftField label="Role"><input value={draft.role} onChange={set('role')} placeholder="e.g. Code reviewer" /></DraftField>
          <DraftField label="Model"><input value={draft.model} onChange={set('model')} placeholder="e.g. gpt-5" /></DraftField>
        </div>
        <DraftField label="Description">
          <textarea rows="3" value={draft.summary} onChange={set('summary')} placeholder="What this agent is for…" />
        </DraftField>
      </section>

      {draft.kind === 'subagent' ? (
        <>
          <section className="agents-section agents-section--prompt">
            <header className="agents-section__head"><h2>System prompt</h2></header>
            <DraftField label="Prompt" hint="The persona and instructions the subagent runs with.">
              <textarea rows="8" value={draft.prompt} onChange={set('prompt')} placeholder="You are…" />
            </DraftField>
          </section>
          <section className="agents-section">
            <header className="agents-section__head"><h2>Connections</h2></header>
            <div className="agents-draft-grid">
              <DraftField label="Tools" hint="Comma-separated tool names.">
                <input value={draft.tools} onChange={set('tools')} placeholder="shell, mcp, call_agent" />
              </DraftField>
              <DraftField label="Repositories" hint="Comma-separated repo names.">
                <input value={draft.repositories} onChange={set('repositories')} placeholder="progenitor, docs" />
              </DraftField>
            </div>
          </section>
        </>
      ) : draft.kind === 'a2a' ? (
        <section className="agents-section">
          <header className="agents-section__head"><h2>Connection</h2></header>
          <DraftField label="Endpoint" hint="Base URL of the remote A2A server.">
            <input value={draft.url} onChange={set('url')} placeholder="https://host:8107" />
          </DraftField>
          <div className="agents-draft-grid">
            <DraftField label="Transport">
              <select value={draft.transport} onChange={set('transport')}>
                <option value="jsonrpc">JSON-RPC</option>
                <option value="grpc">gRPC</option>
                <option value="http">HTTP+JSON</option>
              </select>
            </DraftField>
            <DraftField label="Skills" hint="Comma-separated skill ids.">
              <input value={draft.skills} onChange={set('skills')} placeholder="build_agent, build_mcp" />
            </DraftField>
          </div>
        </section>
      ) : (
        <section className="agents-section">
          <header className="agents-section__head"><h2>Connection</h2></header>
          <DraftField label="Command" hint="Process Progenitor spawns to speak ACP.">
            <input value={draft.command} onChange={set('command')} placeholder="copilot --acp" />
          </DraftField>
          <div className="agents-draft-grid">
            <DraftField label="Transport">
              <select value={draft.transport} onChange={set('transport')}>
                <option value="stdio">stdio</option>
                <option value="tcp">TCP</option>
              </select>
            </DraftField>
            <DraftField label="Permissions">
              <select value={draft.permissionMode} onChange={set('permissionMode')}>
                <option value="prompt">Prompt</option>
                <option value="auto">Auto-approve</option>
                <option value="readonly">Read-only</option>
              </select>
            </DraftField>
            <DraftField label="Timeout" hint="Seconds before a run is abandoned.">
              <input value={draft.timeoutSeconds} onChange={set('timeoutSeconds')} inputMode="numeric" placeholder="120" />
            </DraftField>
          </div>
        </section>
      )}
    </div>
  );
}

function AgentsPage({ variant = 'agents' }) {
  const isSequences = variant === 'sequences';
  const basePath = isSequences ? '/sequences' : '/agents';
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const realtime = useSelector(selectRealtime);
  const fetchedAgents = useSelector(selectAgentProfiles);
  // Sequences shows one pretend entry for now instead of the real agent list.
  const agents = isSequences ? DUMMY_SEQUENCES : fetchedAgents;
  const agentsState = useSelector(selectAgentProfilesState);
  const activeAgent = useSelector(selectActiveAgentProfile);

  const { agentId: routeAgentId } = useParams();
  const navigate = useNavigate();

  const [activeFilter, setActiveFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [sortMode, setSortMode] = useState('Type');
  const [graphEditorOpen, setGraphEditorOpen] = useState(false);
  // "+ New" opens a small type-picker; choosing a type scaffolds an editable
  // draft in the detail pane (mocked client-side until registration is wired up).
  const [newAgentOpen, setNewAgentOpen] = useState(false);
  const [draftAgent, setDraftAgent] = useState(null);

  const handleNewAgent = useCallback(() => {
    setNewAgentOpen(true);
  }, []);

  const handlePickType = useCallback((kind) => {
    setDraftAgent(makeDraftAgent(kind));
    setNewAgentOpen(false);
  }, []);

  const updateDraft = useCallback((field, value) => {
    setDraftAgent((current) => (current ? { ...current, [field]: value } : current));
  }, []);

  const discardDraft = useCallback(() => {
    setDraftAgent(null);
  }, []);

  // Mocked create: real agents register through Progenitor's register_*_agent
  // tools, so for now creating just clears the draft.
  const createDraft = useCallback(() => {
    setDraftAgent(null);
  }, []);

  // Selection is derived from the URL (/agents/<id> or /sequences/<id>) so the
  // view is refreshable and shareable; user actions navigate rather than setState.
  const selectedAgentId = routeAgentId ?? null;

  const agentsUrl = getApiUrl('agentProfiles');
  const requestedListRef = useRef(null);

  // Load the agent list once the realtime stream is connected; the snapshot
  // arrives on the stream (the GET just acknowledges and triggers it). Skipped
  // on Sequences, which shows a pretend entry instead of the real list.
  useEffect(() => {
    if (isSequences || !realtime.connected) {
      requestedListRef.current = null;
      return;
    }
    if (requestedListRef.current === agentsUrl) {
      return;
    }
    requestedListRef.current = agentsUrl;
    dispatch(fetchAgentProfiles({ agentsUrl }));
  }, [agentsUrl, dispatch, isSequences, realtime.connected]);

  // Selection defaults to the first agent without writing state in an effect.
  const effectiveAgentId = selectedAgentId ?? agents[0]?.id ?? null;

  const selectAgent = useCallback((id) => {
    setDraftAgent(null);
    navigate(isSequences ? `${basePath}/${encodeURIComponent(id)}` : `${basePath}/${encodeURIComponent(id)}/details`);
  }, [navigate, basePath, isSequences]);
  // From a sequence graph, jump to that agent's profile on the Agents page.
  const showProfile = useCallback(() => {
    if (!effectiveAgentId) return;
    navigate(`/agents/${encodeURIComponent(effectiveAgentId)}/details`);
  }, [navigate, effectiveAgentId]);

  // Request the full profile (incl. graph) whenever the selection changes.
  // Skipped on Sequences — the pretend entry has no real detail to fetch.
  useEffect(() => {
    if (isSequences || !effectiveAgentId) {
      return;
    }
    dispatch(requestAgentDetail({
      agentsUrl,
      agentId: effectiveAgentId,
      requestId: `${effectiveAgentId}-${Date.now()}`,
    }));
  }, [agentsUrl, dispatch, effectiveAgentId, isSequences]);

  const listAgent = useMemo(
    () => agents.find((agent) => agent.id === effectiveAgentId) || null,
    [agents, effectiveAgentId],
  );
  const selectedAgent = activeAgent && activeAgent.id === effectiveAgentId ? activeAgent : listAgent;
  const selectedGraph = selectedAgent?.graph || FALLBACK_GRAPH;
  const isA2A = selectedAgent?.kind === 'a2a';
  const isACP = selectedAgent?.kind === 'acp';
  // Sequences view always shows the graph; Agents view always shows Details.
  // The tab row is hidden in both (single view per page).
  const detailTabs = [];
  const effectiveDetailTab = isSequences ? 'Graph' : 'Details';

  // Reflect the active agent in the URL so the view is refreshable and shareable
  // (e.g. /agents/subagent-code-reviewer/details or /sequences/subagent-...).
  useEffect(() => {
    if (!effectiveAgentId) return;
    const target = isSequences
      ? `${basePath}/${encodeURIComponent(effectiveAgentId)}`
      : `${basePath}/${encodeURIComponent(effectiveAgentId)}/details`;
    if (`${window.location.pathname}` !== target) {
      navigate(target, { replace: true });
    }
  }, [effectiveAgentId, isSequences, basePath, navigate]);

  const visibleAgents = useMemo(
    () => sortAgents(agents.filter((agent) => matchesAgent(agent, activeFilter, search)), sortMode),
    [activeFilter, agents, search, sortMode],
  );
  // List sections depend on the sort: "Name" is a flat list (no headers),
  // "Recent" uses the chat sidebar's recency buckets, and "Type" keeps the
  // agent's own group headers.
  const sections = useMemo(() => {
    // Sequences drops category grouping for now — a flat list, appearance-only.
    if (isSequences || sortMode === 'Name') {
      return [{ key: 'all', title: null, agents: visibleAgents }];
    }
    if (sortMode === 'Recent') {
      const buckets = new Map(RECENCY_BUCKETS.map(([key]) => [key, []]));
      visibleAgents.forEach((agent) => {
        const bucket = getTimeBucket(agent.updated);
        (buckets.get(bucket) || buckets.get('earlier')).push(agent);
      });
      return RECENCY_BUCKETS
        .map(([key, label]) => ({ key, title: label, agents: buckets.get(key) }))
        .filter((section) => section.agents.length);
    }
    const grouped = groupAgents(visibleAgents);
    return [...new Set(visibleAgents.map((agent) => agent.group || 'Agents'))]
      .map((group) => ({ key: group, title: group, agents: grouped[group] || [] }))
      .filter((section) => section.agents.length);
  }, [isSequences, sortMode, visibleAgents]);
  const runningCount = agents.filter((agent) => agent.status === 'running').length;
  const taskCount = agents.reduce((total, agent) => total + (agent.tasks?.length || 0), 0);
  const memoryCount = agents.reduce((total, agent) => total + (agent.memories?.length || 0), 0);

  async function handleSaveGraph(parsed) {
    return dispatch(updateAgentGraph({
      agentsUrl,
      agentId: effectiveAgentId,
      nodes: parsed.nodes,
      edges: parsed.edges,
      status: parsed.status,
      summary: parsed.summary,
    })).unwrap();
  }


  return (
    <PageShell>
      <PaneList label="Agents" className="agents-pane">
        <PaneListFilters
          filters={AGENT_FILTERS}
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
          actionLabel="+ New"
          onAction={handleNewAgent}
        />
        <PaneListToolbar>
          <PaneListSearch value={search} placeholder="Search agents..." onChange={setSearch} />
          <PaneListSortButton
            title="Sort agents"
            options={SORT_MODES}
            value={sortMode}
            onSelect={setSortMode}
          />
        </PaneListToolbar>
        <PaneListScroll>
          {visibleAgents.length ? (
            sections.map((section) => {
              const rows = section.agents.map((agent) => (
                isSequences ? (
                  <SequenceRow
                    key={agent.id}
                    agent={agent}
                    selected={selectedAgent?.id === agent.id}
                    onSelect={selectAgent}
                  />
                ) : (
                  <AgentRow
                    key={agent.id}
                    agent={agent}
                    selected={selectedAgent?.id === agent.id}
                    onSelect={selectAgent}
                  />
                )
              ));
              return section.title ? (
                <PaneListGroup key={section.key} title={section.title} count={section.agents.length}>
                  {rows}
                </PaneListGroup>
              ) : (
                <div key={section.key}>{rows}</div>
              );
            })
          ) : (
            <div className="agents-empty-state">
              {agentsState.loading || !agentsState.loaded ? 'Loading agents…' : 'No agents match this view.'}
            </div>
          )}
        </PaneListScroll>
        <PaneListFooter>
          <span><span className="num">{runningCount}</span> running</span>
          <span><span className="num">{taskCount}</span> tasks</span>
          <span><span className="num">{memoryCount}</span> memories</span>
        </PaneListFooter>
      </PaneList>

      {draftAgent ? (
        <DetailPane className="detail-pane--dense agents-detail agents-detail--draft" label="New agent">
          <DetailHeader
            title={(
              <input
                className="agents-draft-title"
                value={draftAgent.name}
                onChange={(event) => updateDraft('name', event.target.value)}
                placeholder={`New ${AGENT_TYPE_LABELS[draftAgent.kind]}`}
                aria-label="Agent name"
              />
            )}
            actions={(
              <DetailHeaderActions>
                <DetailHeaderAction icon={<DeleteActionIcon />} label="Discard" tooltip="Discard draft" ariaLabel="Discard draft" onClick={discardDraft} />
                <DetailHeaderAction icon={<SaveActionIcon />} label="Create" tooltip="Create agent" ariaLabel="Create agent" variant="primary" onClick={createDraft} />
              </DetailHeaderActions>
            )}
            meta={[
              <span key="type" className="agents-status-pill agents-status-pill--draft"><span className="adot" />{AGENT_TYPE_LABELS[draftAgent.kind]}</span>,
              <span key="hint" className="muted">Unsaved draft</span>,
            ]}
          />
          <DraftAgentDetailBody draft={draftAgent} onChange={updateDraft} />
        </DetailPane>
      ) : selectedAgent ? (
        <DetailPane className={`detail-pane--dense agents-detail ${effectiveDetailTab === 'Graph' ? 'is-graph-mode' : ''}`} label={`${selectedAgent.name} profile`}>
          {/* Sequences (Graph) hides the detail header so the canvas fills the
              pane, matching the Workflows page. The Agents Details view keeps it. */}
          {effectiveDetailTab !== 'Graph' ? (
            <DetailHeader
              title={selectedAgent.name}
              actions={(
                <DetailHeaderActions>
                  <DetailHeaderAction icon={<ConfigureActionIcon />} label="Configure" tooltip="Configure agent" ariaLabel="Configure agent" />
                  <DetailHeaderAction icon={<DuplicateActionIcon />} label="Duplicate" tooltip="Duplicate agent" ariaLabel="Duplicate agent" />
                  <DetailHeaderAction
                    icon={<PlayActionIcon />}
                    label="Run"
                    tooltip="Run agent"
                    ariaLabel="Run agent"
                    variant="primary"
                  />
                </DetailHeaderActions>
              )}
              meta={[
                <StatusPill key="status" status={selectedAgent.enabled === false ? 'disabled' : selectedAgent.status} />,
                <span key="model" className="sess-detail__model">{selectedAgent.model}</span>,
                <span key="role" className="muted">{selectedAgent.role}</span>,
                selectedAgent.url ? (
                  <a key="url" className="muted agents-agent-url" href={selectedAgent.url} target="_blank" rel="noreferrer">
                    {selectedAgent.transport ? `${selectedAgent.transport} · ` : ''}{selectedAgent.url}
                  </a>
                ) : null,
                <span key="links" className="muted">{(selectedAgent.skills || []).length ? `${selectedAgent.skills.length} skills · ` : ''}{(selectedAgent.tools || []).length} tools · {(selectedAgent.repositories || []).length} repos · {(selectedAgent.knowledge || []).length} knowledge</span>,
              ]}
              tabs={detailTabs}
              activeTab={effectiveDetailTab}
              tabLabel="Agent detail tabs"
            />
          ) : null}

          {effectiveDetailTab === 'Details' ? (
            isA2A
              ? <A2ADetailBody agent={selectedAgent} />
              : isACP
                ? <ACPDetailBody agent={selectedAgent} />
                : <SubagentDetailBody agent={selectedAgent} />
          ) : (
            <div className="detail-pane__body detail-pane__body--graph">
              <div className={`agents-graph-layout ${graphEditorOpen ? 'is-editing' : ''}`}>
                <AgentGraphTab
                  key={selectedAgent.id}
                  agent={selectedAgent}
                  graph={selectedGraph}
                  onPersist={handleSaveGraph}
                  onShowProfile={showProfile}
                  jsonOpen={graphEditorOpen}
                  onOpenJson={() => setGraphEditorOpen(true)}
                  onCloseJson={() => setGraphEditorOpen(false)}
                />
              </div>
            </div>
          )}
        </DetailPane>
      ) : (
        <DetailPane className="detail-pane--dense agents-detail" label="Agent profile">
          <div className="agents-empty-state agents-empty-state--detail">
            {agentsState.loading || !agentsState.loaded ? 'Loading agents…' : 'Select an agent to view its profile.'}
          </div>
        </DetailPane>
      )}

      {newAgentOpen ? <NewAgentModal onPick={handlePickType} onClose={() => setNewAgentOpen(false)} /> : null}
    </PageShell>
  );
}

export default AgentsPage;
