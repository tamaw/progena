import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { getTimeBucket } from '../components/chat/conversationModel';
import Select from '../components/common/Select';
import { DraftField } from '../components/common/DetailCards';
import PopupModal from '../components/common/PopupModal';
import { useConfig } from '../context/ConfigContext';
import { selectRealtimeConnected, selectRealtimeStreamReady } from '../store/realtimeSlice';
import {
  fetchAgentProfiles,
  requestAgentDetail,
  selectActiveAgentProfile,
  selectActiveAgentProfileState,
  selectAgentProfiles,
  selectAgentProfilesState,
  updateAgentGraph,
  deleteAgentProfile,
  updateObjectAgentProfile,
} from '../store/uiDataSlice';
import {
  PaneEmptyState,
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
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  EditActionIcon,
  SaveActionIcon,
} from '../components/layout/DetailHeader';
import { AgentGraphTab } from '../components/agents/AgentGraph';
import { A2ADetailBody, ACPDetailBody, ObjectAgentDetailBody } from '../components/agents/AgentDetailBodies';
import { formatAge } from '../components/agents/agentFormat';

const AGENT_FILTERS = ['All', 'Running', 'Failed', 'Stopped', 'Disabled'];
// Object agents are portable SYSTEM.md manifests. A2A and ACP are protocol
// integrations, not internal agents, and are intentionally absent here.
const AGENT_TYPES = [
  {
    kind: 'object',
    label: 'Object agent',
    blurb: 'Manifest-backed internal agent with a Markdown system prompt and selected tools.',
  },
];

const AGENT_TYPE_LABELS = Object.fromEntries(AGENT_TYPES.map((type) => [type.kind, type.label]));

function makeDraftAgent(kind) {
  return {
    kind,
    name: '',
    model: '',
    summary: '',
    version: '1.0.0',
    prompt: '',
    tools: '',
    skills: '',
  };
}

const SORT_MODES = ['Type', 'Name', 'Recent'];
const TYPE_RANK = { object: 0, routine: 2 };
// Recency buckets for the "Recent" sort, mirroring the chat sidebar's headers.
const RECENCY_BUCKETS = [
  ['today', 'Today'],
  ['yesterday', 'Yesterday'],
  ['this-week', 'This Week'],
  ['last-week', 'Last Week'],
  ['later', 'Later'],
];
// The agent graph now lives on the Routines page; the Agents page shows only
// Details, so the detail tab row is hidden in both views.

// Default graph shape for agents without a stored graph (passed to the canvas).
const FALLBACK_GRAPH = {
  status: 'Draft',
  summary: '',
  entry: null,
  nodes: [],
  edges: [],
};


function normalizeStatus(status) {
  if (!status || status === 'draft') {
    return 'Drafts';
  }
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function matchesAgent(agent, filter, search) {
  // Object agents have no runtime status/enabled concept (see AgentRow);
  // they only ever match the unfiltered "All" view.
  const matchesFilter = filter === 'All'
    || (agent.kind !== 'object' && normalizeStatus(agent.enabled === false ? 'disabled' : agent.status) === filter);
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
  // Object agents have no runtime status/enabled concept (see DetailHeader);
  // showing a status dot for them would fabricate a "draft"/"disabled" state
  // that doesn't exist in the manifest.
  const status = agent.kind === 'object' ? null : (agent.enabled === false ? 'disabled' : (agent.status || 'draft'));
  return (
    <button
      type="button"
      className={`sess-row agents-agent-row ${status ? `agents-agent-row--${status}` : ''} ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(agent.id)}
    >
      <span className="sess-row__top">
        {status ? <span className={`sess-row__dot agents-agent-row__status agents-agent-row__status--${status}`} /> : null}
        <span className="sess-row__agent agents-agent-row__name">{agent.name}</span>
        <span className="sess-row__project">{agent.model}</span>
      </span>
      <span className="sess-row__age">{formatAge(agent.updated)}</span>
      <span className="sess-row__title">{agent.role}</span>
      <span className="sess-row__msgs">
        <span><span className="num">{(agent.tools || []).length}</span> tools</span>
        <span><span className="num">{(agent.skills || []).length}</span> skills</span>
      </span>
    </button>
  );
}

// Routines card list: a two-line card matching the Chat/Workflows pattern —
// a leading status dot (empty unless running/failed), name + last-activity
// on the header line, and the agent's role as a single preview line below.
function RoutineRow({ agent, selected, onSelect }) {
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

// Editable body for a new manifest-backed object agent — the only creatable
// kind (A2A/ACP are protocol integrations wired via config, not created here).
// Fields serialize to YAML front matter plus the Markdown system prompt in
// SYSTEM.md.
function DraftAgentDetailBody({ draft, onChange }) {
  const set = (field) => (event) => onChange(field, event.target.value);
  return (
    <div className="detail-pane__body agents-draft">
      <div className="agents-draft__note">
        Draft {AGENT_TYPE_LABELS[draft.kind]} — fill in the details, then Create. Nothing is saved until you create the agent.
      </div>

      <section className="agents-section">
        <header className="agents-section__head"><h2>Identity</h2></header>
        <DraftField label="Model"><input value={draft.model} onChange={set('model')} placeholder="e.g. gpt-5" /></DraftField>
        <DraftField label="Description">
          <textarea rows="3" value={draft.summary} onChange={set('summary')} placeholder="What this agent is for…" />
        </DraftField>
      </section>

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
          <DraftField label="Skills" hint="Comma-separated /skills/ source paths.">
            <input value={draft.skills} onChange={set('skills')} placeholder="/skills/review/" />
          </DraftField>
        </div>
      </section>
    </div>
  );
}

const AGENTS_PAGE = {
  kind: 'agents',
  basePath: '/agents',
  listLabel: 'Agents',
  searchPlaceholder: 'Search agents...',
  sortTitle: 'Sort agents',
  createLabel: '+ New',
};

function AgentsPage() {
  return <AgentWorkspace page={AGENTS_PAGE} />;
}

export function AgentWorkspace({ page }) {
  const isRoutines = page.kind === 'routines';
  const { basePath, listLabel, searchPlaceholder, sortTitle, createLabel } = page;
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const realtimeConnected = useSelector(selectRealtimeConnected);
  const fetchedAgents = useSelector(selectAgentProfiles);
  const { agentId: routeAgentId } = useParams();
  const agents = useMemo(() => {
    if (!isRoutines) {
      return fetchedAgents.filter((agent) => agent.kind !== 'routine');
    }
    return fetchedAgents.filter((agent) => agent.kind === 'routine');
  }, [fetchedAgents, isRoutines]);
  const agentsState = useSelector(selectAgentProfilesState);
  const activeAgent = useSelector(selectActiveAgentProfile);
  const activeAgentState = useSelector(selectActiveAgentProfileState);
  const streamReady = useSelector(selectRealtimeStreamReady);

  const navigate = useNavigate();

  const [activeFilter, setActiveFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [sortMode, setSortMode] = useState('Type');
  const [graphEditorOpen, setGraphEditorOpen] = useState(false);
  const [routineRunsOpen, setRoutineRunsOpen] = useState(false);
  const [routineRevisionsOpen, setRoutineRevisionsOpen] = useState(false);
  const [deleteConfirmationOpen, setDeleteConfirmationOpen] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  // Object agents only -- `null` means read-only, a string is the in-progress
  // manifest draft. Lifted out of ObjectAgentDetailBody so the DetailHeader's
  // top-level Edit action (detail-header__actions) and the inline "Edit"
  // button inside the Manifest section drive the same state.
  const [objectAgentDraft, setObjectAgentDraft] = useState(null);
  const [objectAgentSaving, setObjectAgentSaving] = useState(false);
  const [objectAgentError, setObjectAgentError] = useState('');
  // "+ New" creates a portable SYSTEM.md manifest through the profile API.
  const [newAgentOpen, setNewAgentOpen] = useState(false);
  const [draftAgent, setDraftAgent] = useState(null);
  const [draftError, setDraftError] = useState('');
  const agentsUrl = getApiUrl('agentProfiles');

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

  const createDraft = useCallback(async () => {
    if (!draftAgent) return;
    const list = (value) => value.split(',').map((item) => item.trim()).filter(Boolean);
    try {
      setDraftError('');
      const response = await fetch(`${agentsUrl}/object`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: draftAgent.name,
          description: draftAgent.summary,
          version: draftAgent.version,
          model: draftAgent.model || null,
          system_prompt: draftAgent.prompt,
          tools: list(draftAgent.tools),
          skills: list(draftAgent.skills),
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || 'Unable to create object agent.');
      }
      setDraftAgent(null);
      dispatch(fetchAgentProfiles({ agentsUrl }));
    } catch (error) {
      setDraftError(error instanceof Error ? error.message : 'Unable to create object agent.');
    }
  }, [agentsUrl, dispatch, draftAgent]);

  // Selection is derived from the URL (/agents/<id> or /routines/<id>) so the
  // view is refreshable and shareable; user actions navigate rather than setState.
  const selectedAgentId = routeAgentId ?? null;

  const requestedListRef = useRef(null);

  // Load profiles once the realtime stream is connected. The GET acknowledges
  // the request and the snapshot arrives on the stream.
  useEffect(() => {
    if (!realtimeConnected) {
      requestedListRef.current = null;
      return;
    }
    if (requestedListRef.current === agentsUrl) {
      return;
    }
    requestedListRef.current = agentsUrl;
    dispatch(fetchAgentProfiles({ agentsUrl }));
  }, [agentsUrl, dispatch, realtimeConnected]);

  // Selection defaults to the first agent without writing state in an effect.
  const effectiveAgentId = selectedAgentId ?? agents[0]?.id ?? null;

  const selectAgent = useCallback((id) => {
    setDraftAgent(null);
    navigate(isRoutines ? `${basePath}/${encodeURIComponent(id)}` : `${basePath}/${encodeURIComponent(id)}/details`);
  }, [navigate, basePath, isRoutines]);
  // From a routine graph, jump to that agent's profile on the Agents page.
  const showProfile = useCallback(() => {
    if (!effectiveAgentId) return;
    navigate(`/agents/${encodeURIComponent(effectiveAgentId)}/details`);
  }, [navigate, effectiveAgentId]);

  // Routines and author-created ("source: user") object agents only --
  // native object agents are repo files, marketplace ones are plugin-managed
  // (Plugins > Uninstall), and A2A/ACP are read through external registries.
  // The eligibility check lives at each call site (button visibility); this
  // handler just needs an id. Mirrors Workflows' delete flow (PopupModal
  // confirm -> DELETE -> navigate back to the list).
  const handleDeleteAgent = useCallback(() => {
    if (effectiveAgentId) {
      setDeleteError('');
      setDeleteConfirmationOpen(true);
    }
  }, [effectiveAgentId]);

  const confirmDeleteAgent = useCallback(async () => {
    if (!effectiveAgentId) {
      setDeleteConfirmationOpen(false);
      return;
    }
    setDeleteBusy(true);
    try {
      await dispatch(deleteAgentProfile({ agentsUrl, agentId: effectiveAgentId })).unwrap();
      setDeleteConfirmationOpen(false);
      navigate(basePath, { replace: true });
    } catch (error) {
      setDeleteError(error.message || 'Failed to delete.');
    } finally {
      setDeleteBusy(false);
    }
  }, [agentsUrl, basePath, dispatch, effectiveAgentId, navigate]);

  // The detail only ever comes back as a one-shot `agent.selected` broadcast,
  // and the backend replays nothing to a subscriber it did not have at publish
  // time (replay needs a Last-Event-ID, which a first load has none of). Ask
  // before the stream is ready and the answer is published into the void: the
  // graph pane then waits on `graphDetailPending` forever, since nothing
  // retries. Wait for the subscriber to exist, then ask.
  useEffect(() => {
    if (!effectiveAgentId || !streamReady) {
      return;
    }
    // A reconnect flips `streamReady` again; re-asking for a detail already in
    // hand would restart the request for no new information.
    if (activeAgent?.id === effectiveAgentId) {
      return;
    }
    dispatch(requestAgentDetail({ agentsUrl, agentId: effectiveAgentId }));
  }, [activeAgent?.id, agentsUrl, dispatch, effectiveAgentId, streamReady]);

  // Object agents only. Switching agents mid-edit must never leave a draft
  // bound to the wrong one underneath the newly-selected agent's page.
  useEffect(() => {
    setObjectAgentDraft(null);
    setObjectAgentError('');
  }, [effectiveAgentId]);

  const listAgent = useMemo(
    () => agents.find((agent) => agent.id === effectiveAgentId) || null,
    [agents, effectiveAgentId],
  );
  const selectedAgent = activeAgent && activeAgent.id === effectiveAgentId ? activeAgent : listAgent;
  const selectedGraph = selectedAgent?.graph || FALLBACK_GRAPH;
  // The roster snapshot carries no `graph` -- only the per-agent detail does.
  // GraphCanvas seeds its working nodes/edges once on mount, so mounting it
  // against the roster entry would pin an empty chain that the later detail
  // can never replace. Hold the canvas back until the detail for THIS id has
  // landed. A failed detail request falls through to the roster entry rather
  // than holding the gate shut forever.
  const graphDetailPending = !activeAgentState.error
    && (!activeAgent || activeAgent.id !== effectiveAgentId);
  const isA2A = selectedAgent?.kind === 'a2a';
  const isACP = selectedAgent?.kind === 'acp';
  // Routines view always shows the graph; Agents view always shows Details.
  // The tab row is hidden in both (single view per page).
  const detailTabs = [];
  const effectiveDetailTab = isRoutines ? 'Graph' : 'Details';

  const [runResult, setRunResult] = useState(null);
  // Reflect the active agent in the URL so the view is refreshable and shareable
  // (e.g. /agents/subagent-code-reviewer/details or /routines/subagent-...).
  useEffect(() => {
    if (!effectiveAgentId) return;
    const target = isRoutines
      ? `${basePath}/${encodeURIComponent(effectiveAgentId)}`
      : `${basePath}/${encodeURIComponent(effectiveAgentId)}/details`;
    if (`${window.location.pathname}` !== target) {
      navigate(target, { replace: true });
    }
  }, [effectiveAgentId, isRoutines, basePath, navigate]);

  const visibleAgents = useMemo(
    () => sortAgents(agents.filter((agent) => matchesAgent(agent, activeFilter, search)), sortMode),
    [activeFilter, agents, search, sortMode],
  );
  // Routine graphs are a flat list; other views can group by agent type.
  const sections = useMemo(() => {
    if (isRoutines || sortMode === 'Name') {
      return [{ key: 'all', title: null, agents: visibleAgents }];
    }
    if (sortMode === 'Recent') {
      const buckets = new Map(RECENCY_BUCKETS.map(([key]) => [key, []]));
      visibleAgents.forEach((agent) => {
        const bucket = getTimeBucket(agent.updated);
        (buckets.get(bucket) || buckets.get('later')).push(agent);
      });
      return RECENCY_BUCKETS
        .map(([key, label]) => ({ key, title: label, agents: buckets.get(key) }))
        .filter((section) => section.agents.length);
    }
    const grouped = groupAgents(visibleAgents);
    return [...new Set(visibleAgents.map((agent) => agent.group || 'Agents'))]
      .map((group) => ({ key: group, title: group, agents: grouped[group] || [] }))
      .filter((section) => section.agents.length);
  }, [isRoutines, sortMode, visibleAgents]);
  const runningCount = agents.filter((agent) => agent.status === 'running').length;
  const taskCount = agents.reduce((total, agent) => total + (agent.tasks?.length || 0), 0);

  async function handleSaveGraph(parsed) {
    return dispatch(updateAgentGraph({
      agentsUrl,
      agentId: effectiveAgentId,
      entry: parsed.entry,
      nodes: parsed.nodes,
      edges: parsed.edges,
      status: parsed.status,
      summary: parsed.summary,
    })).unwrap();
  }

  function slugifyAgentName(value) {
    return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'agent';
  }

  function extractManifestName(content) {
    const frontMatter = content.match(/^---\s*\n([\s\S]*?)\n---/);
    if (!frontMatter) return null;
    const nameLine = frontMatter[1].split('\n').find((line) => /^name:\s*/.test(line));
    if (!nameLine) return null;
    return nameLine.replace(/^name:\s*/, '').trim().replace(/^['"]|['"]$/g, '') || null;
  }

  async function handleSaveObjectAgent(manifest) {
    if (!effectiveAgentId) return;
    await dispatch(updateObjectAgentProfile({
      agentsUrl,
      agentId: effectiveAgentId,
      manifest,
    })).unwrap();
    const name = extractManifestName(manifest.content);
    if (name) {
      const newId = `object-${slugifyAgentName(name)}`;
      if (newId !== effectiveAgentId) {
        selectAgent(newId);
      }
    }
  }

  function startEditObjectAgent() {
    setObjectAgentDraft(selectedAgent?.card?.raw_content ?? '');
    setObjectAgentError('');
  }

  function cancelEditObjectAgent() {
    setObjectAgentDraft(null);
    setObjectAgentError('');
  }

  async function saveObjectAgentDraft() {
    if (objectAgentDraft === null) return;
    setObjectAgentSaving(true);
    setObjectAgentError('');
    try {
      await handleSaveObjectAgent({ content: objectAgentDraft });
      setObjectAgentDraft(null);
    } catch (error) {
      setObjectAgentError(error.message || 'Failed to save agent.');
    } finally {
      setObjectAgentSaving(false);
    }
  }

  const handleRunRoutine = useCallback((workingGraph = selectedGraph) => {
    const nodes = workingGraph.nodes || [];
    const hasSimulatedFailure = nodes.some((node) => String(node.config?.expression || '').includes('raise '));
    const result = {
      status: hasSimulatedFailure ? 'error' : 'completed',
      events: nodes.length,
      revision: workingGraph.settings?.checkpoint?.namespace || effectiveAgentId,
    };
    setRunResult(result);
    window.dispatchEvent(new CustomEvent('progenitor:run-routine', {
      detail: {
        prompt: `Run the "${effectiveAgentId}" routine graph and report its simulated ${result.status} result.`,
      },
    }));
  }, [effectiveAgentId, selectedGraph]);


  return (
    <PageShell>
      <PaneList label={listLabel} className="agents-pane">
        <PaneListFilters
          filters={AGENT_FILTERS}
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
          actionLabel={createLabel}
          onAction={handleNewAgent}
        />
        <PaneListToolbar>
          <PaneListSearch value={search} placeholder={searchPlaceholder} onChange={setSearch} />
          <PaneListSortButton
            title={sortTitle}
            options={SORT_MODES}
            value={sortMode}
            onSelect={setSortMode}
          />
        </PaneListToolbar>
        {visibleAgents.length ? (
          <PaneListVirtualized
            groups={sections}
            getGroupItems={(section) => section.agents}
            renderItem={(agent) => (
              isRoutines ? (
                <RoutineRow
                  agent={agent}
                  selected={selectedAgent?.id === agent.id}
                  onSelect={selectAgent}
                />
              ) : (
                <AgentRow
                  agent={agent}
                  selected={selectedAgent?.id === agent.id}
                  onSelect={selectAgent}
                />
              )
            )}
          />
        ) : (
          <PaneEmptyState>{agentsState.loading || !agentsState.loaded ? `Loading ${listLabel.toLowerCase()}…` : `No ${listLabel.toLowerCase()} match this view.`}</PaneEmptyState>
        )}
        <PaneListFooter>
          <span><span className="num">{runningCount}</span> running</span>
          <span><span className="num">{taskCount}</span> tasks</span>
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
          {draftError ? <p className="agents-draft__note agents-draft__note--error">{draftError}</p> : null}
          <DraftAgentDetailBody draft={draftAgent} onChange={updateDraft} />
        </DetailPane>
      ) : selectedAgent ? (
        <DetailPane className={`detail-pane--dense agents-detail ${effectiveDetailTab === 'Graph' ? 'is-graph-mode' : ''}`} label={`${selectedAgent.name} profile`}>
          {/* Routines (Graph) hides the detail header so the canvas fills the
              pane, matching the Workflows page. The Agents Details view keeps it. */}
          {effectiveDetailTab !== 'Graph' ? (
            <DetailHeader
              title={selectedAgent.name}
              actions={selectedAgent.kind === 'object' ? (
                <DetailHeaderActions menuLabel="Agent actions">
                  <DetailHeaderAction
                    icon={<EditActionIcon />}
                    label="Edit"
                    tooltip="Edit manifest"
                    ariaLabel="Edit manifest"
                    onClick={startEditObjectAgent}
                  />
                  {selectedAgent.card?.source === 'user' ? (
                    <DetailHeaderAction
                      icon={<DeleteActionIcon />}
                      label="Delete"
                      tooltip="Delete agent"
                      ariaLabel="Delete agent"
                      variant="danger"
                      onClick={handleDeleteAgent}
                    />
                  ) : null}
                </DetailHeaderActions>
              ) : null}
              meta={[
                // Object agents have no schema-backed status/enabled concept
                // (no runtime process, no manifest field) -- showing a pill
                // here would fabricate a value the backend doesn't have.
                selectedAgent.kind !== 'object'
                  ? <StatusPill key="status" status={selectedAgent.enabled === false ? 'disabled' : selectedAgent.status} /> : null,
                // Object agents show Model in the Manifest section and
                // Tools/Skills counts in their own MiniListSection headers
                // below -- repeating them here would be a literal duplicate
                // of data already on the page. A2A/ACP have no such section,
                // so the header remains their only place to show these.
                selectedAgent.kind !== 'object' && selectedAgent.model
                  ? <span key="model" className="sess-detail__model">{selectedAgent.model}</span> : null,
                selectedAgent.url ? (
                  <a key="url" className="muted agents-agent-url" href={selectedAgent.url} target="_blank" rel="noreferrer">
                    {selectedAgent.transport ? `${selectedAgent.transport} · ` : ''}{selectedAgent.url}
                  </a>
                ) : null,
                selectedAgent.kind !== 'object' ? (
                  <span key="links" className="muted">{(selectedAgent.skills || []).length ? `${selectedAgent.skills.length} skills · ` : ''}{(selectedAgent.tools || []).length} tools</span>
                ) : null,
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
                : (
                  <ObjectAgentDetailBody
                    agent={selectedAgent}
                    agentsUrl={agentsUrl}
                    editing={objectAgentDraft !== null}
                    draft={objectAgentDraft}
                    onDraftChange={setObjectAgentDraft}
                    onStartEdit={startEditObjectAgent}
                    onCancelEdit={cancelEditObjectAgent}
                    onSaveEdit={saveObjectAgentDraft}
                    saving={objectAgentSaving}
                    error={objectAgentError}
                  />
                )
          ) : (
            <div className="detail-pane__body detail-pane__body--graph">
              {graphDetailPending ? (
                <PaneEmptyState detail>Loading graph…</PaneEmptyState>
              ) : (
                <div className={`agents-graph-layout ${graphEditorOpen || routineRunsOpen || routineRevisionsOpen ? 'is-editing' : ''}`}>
                  <AgentGraphTab
                    key={selectedAgent.id}
                    agent={selectedAgent}
                    graph={selectedGraph}
                    confirmedGraph={selectedGraph}
                    onPersist={handleSaveGraph}
                    onRun={handleRunRoutine}
                    runResult={runResult}
                    onShowProfile={isRoutines ? null : showProfile}
                    onDelete={isRoutines ? handleDeleteAgent : null}
                    jsonOpen={graphEditorOpen}
                    onOpenJson={() => { setGraphEditorOpen(true); setRoutineRunsOpen(false); setRoutineRevisionsOpen(false); }}
                    onCloseJson={() => setGraphEditorOpen(false)}
                    runsOpen={routineRunsOpen}
                    onOpenRuns={() => { setRoutineRunsOpen(true); setGraphEditorOpen(false); setRoutineRevisionsOpen(false); }}
                    onCloseRuns={() => setRoutineRunsOpen(false)}
                    revisionsOpen={routineRevisionsOpen}
                    onOpenRevisions={() => { setRoutineRevisionsOpen(true); setGraphEditorOpen(false); setRoutineRunsOpen(false); }}
                    onCloseRevisions={() => setRoutineRevisionsOpen(false)}
                  />
                </div>
              )}
            </div>
          )}
        </DetailPane>
      ) : (
        <DetailPane className="detail-pane--dense agents-detail" label={`${listLabel.slice(0, -1)} profile`}>
          <PaneEmptyState detail>{agentsState.loading || !agentsState.loaded ? `Loading ${listLabel.toLowerCase()}…` : `Select a${isRoutines ? '' : 'n'} ${listLabel.toLowerCase().slice(0, -1)} to view its profile.`}</PaneEmptyState>
        </DetailPane>
      )}

      {newAgentOpen ? <NewAgentModal onPick={handlePickType} onClose={() => setNewAgentOpen(false)} /> : null}
      {deleteConfirmationOpen && selectedAgent ? (
        <PopupModal
          eyebrow={selectedAgent.kind === 'routine' ? 'Delete routine' : 'Delete agent'}
          title={`Delete “${selectedAgent.name}”?`}
          message={deleteError || `This ${selectedAgent.kind === 'routine' ? 'routine and its graph' : 'agent and its manifest'} will be permanently deleted.`}
          confirmLabel={selectedAgent.kind === 'routine' ? 'Delete routine' : 'Delete agent'}
          busyLabel="Deleting…"
          onClose={() => setDeleteConfirmationOpen(false)}
          onConfirm={confirmDeleteAgent}
          busy={deleteBusy}
        />
      ) : null}
    </PageShell>
  );
}

export default AgentsPage;
