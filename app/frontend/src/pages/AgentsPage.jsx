import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import {
  addEdge,
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  getSmoothStepPath,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
// Reuse the Workspace canvas chrome (dock, minimap, controls, node card) so both
// canvases share one source of truth. Agent-domain deltas live in chrome-frame.css.
import '../styles/workspace.css';
import '../styles/nodes.css';
import hljs from 'highlight.js/lib/common';
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

const AGENT_FILTERS = ['All', 'Running', 'Failed', 'Stopped'];

// The three kinds of agent the "+ New" flow can scaffold. Each maps to one of
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

// Chip tone per A2A task status (matches Progenitor's A2ATaskStatus enum).
const TASK_TONE = {
  completed: 'ok',
  working: 'info',
  submitted: 'info',
  'input-required': 'info',
  'auth-required': 'info',
  failed: 'error',
  rejected: 'error',
  canceled: 'muted',
  unknown: 'muted',
};

const graphFitViewOptions = { padding: 0.18 };
const graphDefaultEdgeOptions = {
  type: 'editable',
  animated: true,
};

// Workflow step kinds offered in the canvas dock (drag onto the canvas or click
// to add). Mirrors the backend VALID_NODE_KINDS executable schema.
const GRAPH_NODE_KINDS = [
  { kind: 'start', label: 'Start', icon: '▶' },
  { kind: 'ask', label: 'Ask', icon: '?' },
  { kind: 'tool', label: 'Tool', icon: '⚙' },
  { kind: 'decision', label: 'Decision', icon: '⑃' },
  { kind: 'end', label: 'End', icon: '■' },
];

// Accent per kind (kept in sync with the .agents-graph-node--* CSS and used by
// the minimap so steps read at a glance).
const GRAPH_KIND_COLORS = {
  start: '#2f9e6f',
  ask: '#2f6ff6',
  tool: '#b06ef6',
  decision: '#e0a23a',
  end: '#6b7280',
};

const GRAPH_KIND_ICONS = Object.fromEntries(GRAPH_NODE_KINDS.map((item) => [item.kind, item.icon]));

function minimapNodeColor(node) {
  return GRAPH_KIND_COLORS[node?.data?.kind] || '#8b949e';
}

const FALLBACK_GRAPH = {
  status: 'Draft',
  summary: '',
  nodes: [],
  edges: [],
};

function normalizeStatus(status) {
  if (!status || status === 'draft') {
    return 'Drafts';
  }
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function formatAge(iso) {
  if (!iso) {
    return '';
  }
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) {
    return '';
  }
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return 'now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.round(hours / 24);
  return `${days}d`;
}

function matchesAgent(agent, filter, search) {
  const matchesFilter = filter === 'All' || normalizeStatus(agent.status) === filter;
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
  return (
    <button
      type="button"
      className={`sess-row agents-agent-row agents-agent-row--${agent.status} ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(agent.id)}
    >
      <span className="sess-row__top">
        <span className={`sess-row__dot agents-agent-row__status agents-agent-row__status--${agent.status}`} />
        <span className="sess-row__agent agents-agent-row__name" style={{ '--agent-color': agent.color }}>{agent.name}</span>
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

function MetricCard({ label, value, detail }) {
  return (
    <div className="agents-metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function MiniListSection({ title, count, children, action }) {
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

function ConnectionRow({ icon, title, detail, meta, tone }) {
  return (
    <div className="agents-connection-row">
      <span className="agents-connection-row__icon">{icon}</span>
      <div className="agents-connection-row__body">
        <strong>{title}</strong>
        <p>{detail}</p>
      </div>
      <span className={`agents-chip ${tone ? `agents-chip--${tone}` : ''}`}>{meta}</span>
    </div>
  );
}

function CapabilityChip({ label, on }) {
  return (
    <span className={`agents-chip ${on ? 'agents-chip--ok' : 'agents-chip--muted'}`}>
      {label}: {on ? 'yes' : 'no'}
    </span>
  );
}

function CardFact({ label, children }) {
  return (
    <div className="agents-card-fact">
      <span className="agents-card-fact__label">{label}</span>
      <span className="agents-card-fact__value">{children}</span>
    </div>
  );
}

// Detail pane for A2A agents: everything shown is sourced from the live agent
// card (capabilities, transport, protocol, interaction modes, skills), not the
// subagent definition fields, which do not apply to a remote A2A server.
function A2ADetailBody({ agent }) {
  const card = agent.card || {};
  const capabilities = card.capabilities || {};
  const inputModes = card.inputModes || [];
  const outputModes = card.outputModes || [];
  const provider = card.provider || {};
  const skills = agent.skills || [];
  const tasks = agent.tasks || [];
  const checkpoints = agent.metrics?.checkpoints ?? new Set(tasks.map((t) => t.context_id).filter(Boolean)).size;

  return (
    <div className="detail-pane__body">
      <div className="agents-metrics">
        <MetricCard label="Skills" value={skills.length} detail="advertised" />
        <MetricCard label="Tasks" value={agent.metrics?.tasks ?? tasks.length} detail="A2A runs" />
        <MetricCard label="Checkpoints" value={checkpoints} detail="conversation threads" />
        <MetricCard label="Protocol" value={card.protocolVersion || '—'} detail="A2A version" />
      </div>

      <section className="agents-section">
        <header className="agents-section__head"><h2>Agent card</h2></header>
        <div className="agents-card-facts">
          {agent.summary ? <CardFact label="Description">{agent.summary}</CardFact> : null}
          {agent.url ? (
            <CardFact label="Endpoint">
              <a className="agents-agent-url" href={agent.url} target="_blank" rel="noreferrer">{agent.url}</a>
            </CardFact>
          ) : null}
          {provider.organization ? (
            <CardFact label="Provider">
              {provider.url ? (
                <a className="agents-agent-url" href={provider.url} target="_blank" rel="noreferrer">{provider.organization}</a>
              ) : provider.organization}
            </CardFact>
          ) : null}
          {card.documentationUrl ? (
            <CardFact label="Docs">
              <a className="agents-agent-url" href={card.documentationUrl} target="_blank" rel="noreferrer">{card.documentationUrl}</a>
            </CardFact>
          ) : null}
        </div>
      </section>

      <section className="agents-section">
        <header className="agents-section__head"><h2>Capabilities</h2></header>
        <div className="agents-chip-row">
          <CapabilityChip label="Streaming" on={!!capabilities.streaming} />
          <CapabilityChip label="Push notifications" on={!!capabilities.pushNotifications} />
          <CapabilityChip label="State history" on={!!capabilities.stateTransitionHistory} />
        </div>
      </section>

      {inputModes.length || outputModes.length ? (
        <section className="agents-section">
          <header className="agents-section__head"><h2>Interaction modes</h2></header>
          <div className="agents-card-facts">
            {inputModes.length ? (
              <CardFact label="Input">
                <span className="agents-chip-row">
                  {inputModes.map((mode) => <span key={mode} className="agents-chip agents-chip--info">{mode}</span>)}
                </span>
              </CardFact>
            ) : null}
            {outputModes.length ? (
              <CardFact label="Output">
                <span className="agents-chip-row">
                  {outputModes.map((mode) => <span key={mode} className="agents-chip agents-chip--info">{mode}</span>)}
                </span>
              </CardFact>
            ) : null}
          </div>
        </section>
      ) : null}

      <MiniListSection title="Skills" count={`${skills.length} advertised`}>
        <div className="agents-skill-list">
          {skills.length ? skills.map((skill) => (
            <article key={skill.id || skill.name} className="agents-skill-card">
              <header className="agents-skill-card__head">
                <strong>{skill.name || skill.id}</strong>
                <span className="agents-chip-row">
                  {(skill.tags || []).map((tag) => <span key={tag} className="agents-chip agents-chip--info">{tag}</span>)}
                </span>
              </header>
              {skill.description ? <p className="agents-skill-card__desc">{skill.description}</p> : null}
              {(skill.examples || []).length ? (
                <ul className="agents-skill-card__examples">
                  {skill.examples.map((example) => <li key={example}><code>{example}</code></li>)}
                </ul>
              ) : null}
            </article>
          )) : <div className="agents-empty-state">No skills advertised.</div>}
        </div>
      </MiniListSection>

      <MiniListSection title="Recent tasks" count={`${tasks.length} tracked`}>
        <div className="agents-skill-list">
          {tasks.length ? tasks.map((task) => (
            <article key={task.id} className="agents-skill-card">
              <header className="agents-skill-card__head">
                <strong>{task.title || task.id || '(task)'}</strong>
                <span className="agents-chip-row">
                  <span className={`agents-chip agents-chip--${TASK_TONE[task.status] || 'muted'}`}>{task.status}</span>
                  {task.updated ? <span className="agents-skill-card__time">{formatAge(task.updated)}</span> : null}
                </span>
              </header>
              {task.summary ? <p className="agents-skill-card__desc">{task.summary}</p> : null}
              <p className="agents-task-meta">
                {task.source || 'a2a'}{task.context_id ? ` · thread ${task.context_id}` : ''}
              </p>
            </article>
          )) : <div className="agents-empty-state">No A2A tasks yet.</div>}
        </div>
      </MiniListSection>
    </div>
  );
}

// Detail pane for ACP agents (Agent Client Protocol over stdio, e.g. the GitHub
// Copilot CLI the Builder Agent drives). Card-driven like the A2A pane: the
// connection facts, capabilities, and exposed tools come from the agent's ACP
// card, since there is no in-process subagent definition behind it.
function ACPDetailBody({ agent }) {
  const card = agent.card || {};
  const capabilities = card.capabilities || {};
  const tools = agent.tools || [];
  const runs = agent.tasks || [];

  return (
    <div className="detail-pane__body">
      <div className="agents-metrics">
        <MetricCard label="Transport" value={card.transport || 'stdio'} detail={card.transport === 'tcp' ? 'ACP over TCP' : 'ACP subprocess'} />
        <MetricCard label="Model" value={card.model || agent.model || '—'} detail="Copilot model" />
        <MetricCard label="Tools" value={tools.length} detail="exposed over ACP" />
        <MetricCard label="Protocol" value="ACP" detail="Agent Client Protocol" />
      </div>

      <section className="agents-section">
        <header className="agents-section__head">
          <h2>Connection</h2>
          {card.preview ? <span className="agents-chip agents-chip--info">Preview</span> : null}
        </header>
        <div className="agents-card-facts">
          {agent.summary ? <CardFact label="Description">{agent.summary}</CardFact> : null}
          {card.command ? (
            <CardFact label="Command"><code>{card.command}</code></CardFact>
          ) : null}
          {card.protocol ? <CardFact label="Protocol">{card.protocol}</CardFact> : null}
          {card.permissionMode ? <CardFact label="Permissions">{card.permissionMode}</CardFact> : null}
          {card.timeoutSeconds ? <CardFact label="Timeout">{card.timeoutSeconds}s</CardFact> : null}
        </div>
      </section>

      <section className="agents-section">
        <header className="agents-section__head"><h2>Capabilities</h2></header>
        <div className="agents-chip-row">
          <CapabilityChip label="Streaming" on={!!capabilities.streaming} />
          <CapabilityChip label="Terminal" on={!!capabilities.terminal} />
          <CapabilityChip label="File access" on={!!capabilities.fileAccess} />
          <CapabilityChip label="Auto-approve" on={!!capabilities.autoApprove} />
        </div>
      </section>

      <MiniListSection title="Exposed tools" count={`${tools.length} over ACP`}>
        <div className="agents-connection-list">
          {tools.length ? tools.map((tool) => (
            <ConnectionRow
              key={tool.name}
              icon={(tool.name || '').slice(0, 2)}
              title={tool.name}
              detail={`${tool.type} - ${tool.calls} calls`}
              meta={tool.enabled ? 'on' : 'off'}
              tone={tool.enabled ? 'ok' : 'muted'}
            />
          )) : <div className="agents-empty-state">No tools exposed.</div>}
        </div>
      </MiniListSection>

      <MiniListSection title="Recent runs" count={`${runs.length} tracked`}>
        <div className="agents-skill-list">
          {runs.length ? runs.map((run) => (
            <article key={run.id} className="agents-skill-card">
              <header className="agents-skill-card__head">
                <strong>{run.title || run.id || '(run)'}</strong>
                <span className="agents-chip-row">
                  <span className={`agents-chip agents-chip--${TASK_TONE[run.status] || 'muted'}`}>{run.status}</span>
                  {run.updated ? <span className="agents-skill-card__time">{formatAge(run.updated)}</span> : null}
                </span>
              </header>
              {run.summary ? <p className="agents-skill-card__desc">{run.summary}</p> : null}
            </article>
          )) : <div className="agents-empty-state">No ACP runs yet.</div>}
        </div>
      </MiniListSection>
    </div>
  );
}

// Detail pane for DeepAgent subagents: definition-centric (system prompt, tools,
// repositories, knowledge, and activity), reflecting their in-process nature.
function SubagentDetailBody({ agent }) {
  return (
    <div className="detail-pane__body">
      <div className="agents-metrics">
        <MetricCard label="Invocations" value={agent.metrics?.invocations ?? '0'} detail="last 30 days" />
        <MetricCard label="Sessions" value={agent.metrics?.sessions ?? '0'} detail="linked conversations" />
        <MetricCard label="Tasks" value={agent.metrics?.tasks ?? '0'} detail="open and completed" />
        <MetricCard label="Memories" value={agent.metrics?.memories ?? '0'} detail="available context" />
      </div>

      <section className="agents-section agents-section--prompt">
        <header className="agents-section__head">
          <h2>System prompt <span>{(agent.prompt || '').length} chars</span></h2>
          <div>
            <button type="button" className="repo-btn repo-btn--ghost">Edit</button>
            <button type="button" className="repo-btn repo-btn--ghost">Diff</button>
          </div>
        </header>
        <pre className="agents-prompt"><code>{agent.prompt || 'No system prompt set.'}</code></pre>
      </section>

      <div className="agents-detail-grid">
        <MiniListSection title="Connected tools" count={`${(agent.tools || []).length} attached`} action="+ Add tool">
          <div className="agents-connection-list">
            {(agent.tools || []).length ? agent.tools.map((tool) => (
              <ConnectionRow
                key={tool.name}
                icon={(tool.name || '').slice(0, 2)}
                title={tool.name}
                detail={`${tool.type} - ${tool.calls} calls`}
                meta={tool.enabled ? 'on' : 'off'}
                tone={tool.enabled ? 'ok' : 'muted'}
              />
            )) : <div className="agents-empty-state">No tools connected.</div>}
          </div>
        </MiniListSection>

        <MiniListSection title="Repositories" count={`${(agent.repositories || []).length} connected`} action="+ Attach repo">
          <div className="agents-connection-list">
            {(agent.repositories || []).length ? agent.repositories.map((repo) => (
              <ConnectionRow
                key={`${repo.name}-${repo.branch}`}
                icon="repo"
                title={repo.name}
                detail={`branch ${repo.branch}`}
                meta={repo.access}
                tone={repo.access === 'write' ? 'ok' : 'info'}
              />
            )) : <div className="agents-empty-state">No repositories connected.</div>}
          </div>
        </MiniListSection>

        <MiniListSection title="Knowledge" count={`${(agent.knowledge || []).length} sources`} action="+ Link source">
          <div className="agents-connection-list">
            {(agent.knowledge || []).length ? agent.knowledge.map((item) => (
              <ConnectionRow
                key={item.name}
                icon="kb"
                title={item.name}
                detail={`${item.records} records`}
                meta={item.freshness}
                tone="info"
              />
            )) : <div className="agents-empty-state">No knowledge linked.</div>}
          </div>
        </MiniListSection>
      </div>

      <div className="agents-activity-grid">
        <MiniListSection title="Memories" count={`${(agent.memories || []).length} pinned`}>
          <div className="agents-mini-list">
            {(agent.memories || []).length ? agent.memories.map((memory) => (
              <div key={memory.title} className="agents-mini-row">
                <span className="agents-mini-row__icon">mem</span>
                <div>
                  <strong>{memory.title}</strong>
                  <p>{memory.scope} - {memory.age}</p>
                </div>
              </div>
            )) : <div className="agents-empty-state">No memories yet.</div>}
          </div>
        </MiniListSection>

        <MiniListSection title="Sessions" count={`${(agent.sessions || []).length} recent`}>
          <div className="agents-mini-list">
            {(agent.sessions || []).length ? agent.sessions.map((session) => (
              <div key={session.title} className="agents-mini-row">
                <span className="agents-mini-row__icon">ses</span>
                <div>
                  <strong>{session.title}</strong>
                  <p>{session.outcome} - {session.age}</p>
                </div>
              </div>
            )) : <div className="agents-empty-state">No sessions yet.</div>}
          </div>
        </MiniListSection>

        <MiniListSection title="Tasks" count={`${(agent.tasks || []).length} tracked`}>
          <div className="agents-mini-list">
            {(agent.tasks || []).length ? agent.tasks.map((task) => (
              <div key={task.title} className="agents-mini-row">
                <span className="agents-mini-row__icon">tsk</span>
                <div>
                  <strong>{task.title}</strong>
                  <p>{task.state} - {task.age}</p>
                </div>
                <span className={`agents-chip agents-chip--${task.state === 'active' ? 'ok' : 'info'}`}>{task.state}</span>
              </div>
            )) : <div className="agents-empty-state">No tasks tracked.</div>}
          </div>
        </MiniListSection>
      </div>
    </div>
  );
}

// Edit callbacks shared with the custom node/edge renderers (avoids threading
// mutable refs through node data, which React forbids reading during render).
const GraphEditContext = createContext({ updateNodeData: () => {}, updateEdgeLabel: () => {} });

// Inline-editable workflow step, styled like the Workspace canvas nodes: a title
// bar (type-icon chip + editable label + kind badge) over a body whose content is
// editable per kind. Edits are autosaved by the canvas.
function EditableGraphNode({ id, data, selected }) {
  const { updateNodeData } = useContext(GraphEditContext);
  const hasTarget = data.kind !== 'start';
  const hasSource = data.kind !== 'end';
  const hasBody = data.kind === 'ask' || data.kind === 'decision' || data.kind === 'tool';
  return (
    <div
      className={`workspace-node agents-graph-node agents-graph-node--${data.kind} ${selected ? 'selected' : ''}`}
      style={{ '--node-color': GRAPH_KIND_COLORS[data.kind] || data.color }}
    >
      {hasTarget ? <Handle type="target" position={Position.Left} className="workspace-handle" /> : null}
      <div className="node-title-bar drag-handle">
        <span className="node-type-icon">{GRAPH_KIND_ICONS[data.kind] || '•'}</span>
        <input
          className="nodrag node-title agents-node-title"
          value={data.label || ''}
          placeholder="Label"
          onChange={(event) => updateNodeData(id, { label: event.target.value })}
        />
        <span className="node-status-badge agents-node-kind-badge">{data.kind}</span>
      </div>
      {hasBody ? (
        <div className="node-body node-body-compact">
          {data.kind === 'tool' ? (
            <input
              className="nodrag agents-graph-node__toolinput"
              value={data.tool || ''}
              placeholder="tool name"
              onChange={(event) => updateNodeData(id, { tool: event.target.value })}
            />
          ) : (
            <textarea
              className="nodrag nowheel agents-graph-node__prompt"
              value={data.prompt || ''}
              placeholder={data.kind === 'decision' ? 'Decision question…' : 'Prompt…'}
              onChange={(event) => updateNodeData(id, { prompt: event.target.value })}
            />
          )}
        </div>
      ) : null}
      {hasSource ? <Handle type="source" position={Position.Right} className="workspace-handle" /> : null}
    </div>
  );
}

// Edge with a double-click-to-edit label. Decision branches and retry loops use
// the label to name the route.
function EditableGraphEdge({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, style, label,
}) {
  const { updateEdgeLabel } = useContext(GraphEditContext);
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
  });
  const [editing, setEditing] = useState(false);
  const value = label ?? '';
  return (
    <>
      <BaseEdge id={id} path={edgePath} markerEnd={markerEnd} style={style} />
      <EdgeLabelRenderer>
        <div
          className="agents-graph-edge-label nodrag nopan"
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          onDoubleClick={() => setEditing(true)}
        >
          {editing ? (
            <input
              autoFocus
              className="nodrag"
              value={value}
              onChange={(event) => updateEdgeLabel(id, event.target.value)}
              onBlur={() => setEditing(false)}
              onKeyDown={(event) => { if (event.key === 'Enter') setEditing(false); }}
            />
          ) : (
            value ? <span>{value}</span> : <span className="agents-graph-edge-label__add">+ label</span>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

const agentGraphNodeTypes = { agentGraph: EditableGraphNode };
const agentGraphEdgeTypes = { editable: EditableGraphEdge };

function seedNodes(graph, agent) {
  return (graph.nodes || []).map((node, index) => ({
    id: node.id,
    type: 'agentGraph',
    position: { x: node.x ?? index * 240, y: node.y ?? 80 },
    data: {
      kind: node.kind,
      label: node.label ?? node.id,
      prompt: node.prompt,
      tool: node.tool,
      detail: node.detail,
      color: agent.color,
    },
  }));
}

function seedEdges(graph, agent) {
  return (graph.edges || []).map((edge, index) => {
    const [source, target, label] = edge;
    return {
      id: `${source}-${target}-${index}`,
      source,
      target,
      label,
      type: 'editable',
      animated: true,
      style: { stroke: agent.color },
      markerEnd: { type: MarkerType.ArrowClosed, color: agent.color },
    };
  });
}

// Serialize the live React Flow state back into the backend graph schema,
// stripping the injected callbacks and keeping positions for round-tripping.
function serializeGraph(nodes, edges, meta) {
  return {
    status: meta.status || 'Draft',
    summary: meta.summary || '',
    nodes: nodes.map((node) => {
      const data = node.data || {};
      const out = {
        id: node.id,
        kind: data.kind,
        label: data.label,
        x: Math.round(node.position?.x ?? 0),
        y: Math.round(node.position?.y ?? 0),
      };
      if (data.prompt) out.prompt = data.prompt;
      if (data.tool) out.tool = data.tool;
      if (data.detail) out.detail = data.detail;
      return out;
    }),
    edges: edges.map((edge) => (edge.label ? [edge.source, edge.target, String(edge.label)] : [edge.source, edge.target])),
  };
}

function uniqueNodeId(kind, nodes) {
  const ids = new Set(nodes.map((node) => node.id));
  if (!ids.has(kind)) return kind;
  let i = 1;
  while (ids.has(`${kind}-${i}`)) i += 1;
  return `${kind}-${i}`;
}

// The Sessions/Workspace canvas: an interactive React Flow editor with a bottom
// dock of step kinds, a minimap (right), zoom controls (bottom-left), and inline
// editable nodes/edges. Edits autosave (debounced) to the backend.
function GraphCanvas({ agent, graph, onPersist, onShowProfile, jsonOpen, onOpenJson, onCloseJson }) {
  const reactFlow = useReactFlow();
  const wrapperRef = useRef(null);
  const persistRef = useRef(onPersist);

  const [nodes, setNodes, onNodesChange] = useNodesState(seedNodes(graph, agent));
  const [edges, setEdges, onEdgesChange] = useEdgesState(seedEdges(graph, agent));
  const [meta, setMeta] = useState({ status: graph.status || 'Draft', summary: graph.summary || '' });
  const [saveState, setSaveState] = useState('idle');
  const [saveError, setSaveError] = useState(null);

  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);

  const updateNodeData = useCallback((id, patch) => {
    setNodes((current) => current.map((node) => (node.id === id ? { ...node, data: { ...node.data, ...patch } } : node)));
  }, [setNodes]);

  const updateEdgeLabel = useCallback((id, label) => {
    setEdges((current) => current.map((edge) => (edge.id === id ? { ...edge, label } : edge)));
  }, [setEdges]);

  const editContext = useMemo(() => ({ updateNodeData, updateEdgeLabel }), [updateNodeData, updateEdgeLabel]);

  // Keep refs in sync outside of render so callbacks/effects see the latest
  // values (React forbids writing refs during render).
  useEffect(() => { persistRef.current = onPersist; }, [onPersist]);
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);

  const addNode = useCallback((kind, position) => {
    const id = uniqueNodeId(kind, nodesRef.current);
    const point = position || (() => {
      const rect = wrapperRef.current?.getBoundingClientRect();
      if (!rect) return { x: 0, y: 0 };
      return reactFlow.screenToFlowPosition({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
    })();
    const data = {
      kind,
      label: kind.charAt(0).toUpperCase() + kind.slice(1),
      color: agent.color,
    };
    if (kind === 'ask' || kind === 'decision') data.prompt = '';
    if (kind === 'tool') data.tool = '';
    setNodes((current) => [...current, { id, type: 'agentGraph', position: point, data }]);
  }, [agent.color, reactFlow, setNodes]);

  const onConnect = useCallback((params) => {
    const sourceNode = nodesRef.current.find((node) => node.id === params.source);
    const label = sourceNode?.data?.kind === 'decision' ? 'branch' : undefined;
    setEdges((current) => addEdge({
      ...params,
      type: 'editable',
      animated: true,
      label,
      style: { stroke: agent.color },
      markerEnd: { type: MarkerType.ArrowClosed, color: agent.color },
    }, current));
  }, [agent.color, setEdges]);

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback((event) => {
    event.preventDefault();
    const kind = event.dataTransfer.getData('application/agentgraph');
    if (!kind) return;
    const position = reactFlow.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    addNode(kind, position);
  }, [addNode, reactFlow]);

  // Delete selected nodes/edges on Delete/Backspace, but never while editing a
  // node's text fields (so typing in a prompt doesn't remove the node).
  useEffect(() => {
    function handleKey(event) {
      const tag = event.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (event.key !== 'Delete' && event.key !== 'Backspace') return;
      const selectedNodeIds = nodesRef.current.filter((node) => node.selected).map((node) => node.id);
      const selectedEdgeIds = edgesRef.current.filter((edge) => edge.selected).map((edge) => edge.id);
      if (!selectedNodeIds.length && !selectedEdgeIds.length) return;
      if (selectedNodeIds.length) {
        const removed = new Set(selectedNodeIds);
        setNodes((current) => current.filter((node) => !removed.has(node.id)));
        setEdges((current) => current.filter((edge) => !removed.has(edge.source) && !removed.has(edge.target) && !selectedEdgeIds.includes(edge.id)));
      } else {
        const removed = new Set(selectedEdgeIds);
        setEdges((current) => current.filter((edge) => !removed.has(edge.id)));
      }
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [setNodes, setEdges]);

  // Debounced autosave: serialize the working graph and PATCH it. The first run
  // records the baseline so simply opening the tab does not trigger a save.
  const lastSavedRef = useRef(null);
  useEffect(() => {
    const payload = serializeGraph(nodes, edges, meta);
    const signature = JSON.stringify(payload);
    if (lastSavedRef.current === null) {
      lastSavedRef.current = signature;
      return undefined;
    }
    if (signature === lastSavedRef.current) return undefined;
    const timer = setTimeout(async () => {
      setSaveState('saving');
      try {
        await persistRef.current(payload);
        lastSavedRef.current = signature;
        setSaveError(null);
        setSaveState('saved');
      } catch (error) {
        setSaveError(error.message || 'Save failed');
        setSaveState('error');
      }
    }, 700);
    return () => clearTimeout(timer);
  }, [nodes, edges, meta]);

  const workingGraph = useMemo(() => serializeGraph(nodes, edges, meta), [nodes, edges, meta]);

  const handleJsonApply = useCallback(async (parsed) => {
    const nextMeta = { status: parsed.status || 'Draft', summary: parsed.summary || '' };
    setMeta(nextMeta);
    const nextNodes = seedNodes(parsed, agent);
    const nextEdges = seedEdges(parsed, agent);
    setNodes(nextNodes);
    setEdges(nextEdges);
    const payload = { status: nextMeta.status, summary: nextMeta.summary, nodes: parsed.nodes, edges: parsed.edges };
    await persistRef.current(payload);
    lastSavedRef.current = JSON.stringify(serializeGraph(nextNodes, nextEdges, nextMeta));
    setSaveError(null);
    setSaveState('saved');
  }, [agent, setNodes, setEdges]);

  return (
    <>
      <section className="agents-graph-panel" style={{ '--agent-color': agent.color }}>
        <div className="agents-graph-shell">
          <div
            className="workspace-canvas agents-graph-canvas"
            ref={wrapperRef}
            onDrop={onDrop}
            onDragOver={onDragOver}
          >
            <div className="agents-graph-floating">
              <span>{agent.name}</span>
              <span>{nodes.length} nodes</span>
              {saveState !== 'idle' ? (
                <span className={`agents-graph-save agents-graph-save--${saveState}`}>
                  {saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved' : 'Save failed'}
                </span>
              ) : null}
              <button type="button" onClick={onShowProfile}>Details</button>
            </div>
            <GraphEditContext.Provider value={editContext}>
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                nodeTypes={agentGraphNodeTypes}
                edgeTypes={agentGraphEdgeTypes}
                defaultEdgeOptions={graphDefaultEdgeOptions}
                fitView
                fitViewOptions={graphFitViewOptions}
                minZoom={0.3}
                maxZoom={2}
                snapToGrid
                snapGrid={[16, 16]}
                nodesConnectable
                nodesDraggable
                elementsSelectable
                deleteKeyCode={null}
                multiSelectionKeyCode="Shift"
              >
                <Background gap={16} size={1} color="var(--color-border)" variant="dots" />
                <Controls showInteractive={false} />
                <MiniMap
                  style={{ height: 100, width: 150 }}
                  nodeColor={minimapNodeColor}
                  maskColor="rgba(0, 0, 0, 0.6)"
                  pannable
                  zoomable
                />
              </ReactFlow>
            </GraphEditContext.Provider>
            <GraphDock onAdd={addNode} />
          </div>
          {saveError ? <div className="agents-graph-save-error">{saveError}</div> : null}
        </div>
      </section>
      {jsonOpen ? (
        <AgentGraphEditor
          key={agent.id}
          graph={workingGraph}
          onSave={handleJsonApply}
          onClose={onCloseJson}
        />
      ) : (
        <GraphSideDock onSelect={(id) => { if (id === 'json') onOpenJson(); }} />
      )}
    </>
  );
}

// Chevrons for the right tools dock / collapse seam (point toward the action:
// left = expand the panel leftward, right = collapse it away).
function ChevronLeftSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m9.8 4.2-3.6 3.8 3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

function ChevronRightSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m6.2 4.2 3.6 3.8-3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

// Collapsed right-edge tools dock for the sequences canvas. Mirrors the left
// pane's collapse rail but on the right; each item expands its own panel. Add
// future tools by extending GRAPH_SIDE_ITEMS and handling their id in onSelect.
const GRAPH_SIDE_ITEMS = [
  { id: 'json', label: 'JSON', title: 'Edit as JSON' },
];

function GraphSideDock({ onSelect }) {
  return (
    <aside className="agents-graph-side" aria-label="Graph tools">
      {GRAPH_SIDE_ITEMS.map((item) => (
        <button
          key={item.id}
          type="button"
          className="agents-graph-side__item"
          title={item.title}
          aria-label={item.title}
          onClick={() => onSelect(item.id)}
        >
          <ChevronLeftSmall />
          <span className="agents-graph-side__label">{item.label}</span>
        </button>
      ))}
    </aside>
  );
}

// Bottom-center dock of step kinds. Drag an item onto the canvas, or click to add
// it at the canvas centre. Mirrors the Workspace dock.
function GraphDock({ onAdd }) {
  const onDragStart = (event, kind) => {
    event.dataTransfer.setData('application/agentgraph', kind);
    event.dataTransfer.effectAllowed = 'move';
  };
  return (
    <div className="workspace-dock agents-graph-dock">
      <div className="workspace-dock-shell">
        {GRAPH_NODE_KINDS.map((item) => (
          <button
            key={item.kind}
            type="button"
            className={`workspace-dock-item agents-graph-dock__item--${item.kind}`}
            draggable
            onDragStart={(event) => onDragStart(event, item.kind)}
            onClick={() => onAdd(item.kind)}
          >
            <span className={`workspace-dock-icon ${item.kind}`}>{item.icon}</span>
            <span className="workspace-dock-tooltip">{item.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function AgentGraphTab({ agent, graph, onPersist, onShowProfile, jsonOpen, onOpenJson, onCloseJson }) {
  return (
    <ReactFlowProvider>
      <GraphCanvas
        agent={agent}
        graph={graph}
        onPersist={onPersist}
        onShowProfile={onShowProfile}
        jsonOpen={jsonOpen}
        onOpenJson={onOpenJson}
        onCloseJson={onCloseJson}
      />
    </ReactFlowProvider>
  );
}

function graphToJson(graph) {
  return JSON.stringify(
    {
      status: graph.status || 'Draft',
      summary: graph.summary || '',
      nodes: graph.nodes || [],
      edges: graph.edges || [],
    },
    null,
    2,
  );
}

// A lightweight JSON editor: an editable textarea overlaid on a highlight.js
// rendered <pre> so the user gets JSON syntax highlighting while typing. The two
// layers share identical typography so the caret/text line up; the textarea text
// is transparent and the highlighted layer shows through.
function JsonCodeEditor({ value, onChange }) {
  const highlightRef = useRef(null);
  const highlighted = useMemo(() => {
    try {
      return hljs.highlight(value, { language: 'json' }).value;
    } catch {
      return value;
    }
  }, [value]);

  function syncScroll(event) {
    const target = highlightRef.current;
    if (target) {
      target.scrollTop = event.target.scrollTop;
      target.scrollLeft = event.target.scrollLeft;
    }
  }

  return (
    <div className="agents-graph-editor__code">
      <pre ref={highlightRef} className="agents-graph-editor__highlight" aria-hidden="true">
        <code
          className="hljs language-json"
          dangerouslySetInnerHTML={{ __html: `${highlighted}\n` }}
        />
      </pre>
      <textarea
        className="agents-graph-editor__textarea"
        spellCheck={false}
        value={value}
        onChange={onChange}
        onScroll={syncScroll}
      />
    </div>
  );
}

// Edit the agent graph by describing it as JSON conforming to the published
// schema. The PATCH endpoint validates and persists; the broadcast updates the
// canvas. Keyed by agent id so switching agents resets the draft.
function AgentGraphEditor({ graph, onSave, onClose }) {
  const [draft, setDraft] = useState(() => graphToJson(graph));
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function handleApply() {
    let parsed;
    try {
      parsed = JSON.parse(draft);
    } catch (parseError) {
      setError(`Invalid JSON: ${parseError.message}`);
      return;
    }
    if (!Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) {
      setError('Graph must include "nodes" and "edges" arrays.');
      return;
    }
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await onSave(parsed);
      setSaved(true);
    } catch (saveError) {
      setError(saveError.message || 'Failed to save graph');
    } finally {
      setSaving(false);
    }
  }

  return (
    <aside className="agents-graph-editor">
      <button
        type="button"
        className="agents-graph-editor__collapse"
        onClick={onClose}
        title="Collapse"
        aria-label="Collapse JSON editor"
      >
        <ChevronRightSmall />
      </button>
      <header className="agents-graph-editor__head">
        <h2>Edit graph as JSON</h2>
      </header>
      <JsonCodeEditor
        value={draft}
        onChange={(event) => { setDraft(event.target.value); setSaved(false); }}
      />
      {error ? <p className="agents-graph-editor__error">{error}</p> : null}
      {saved ? <p className="agents-graph-editor__saved">Graph saved.</p> : null}
      <div className="agents-graph-editor__actions">
        <button type="button" className="repo-btn repo-btn--ghost" onClick={() => { setDraft(graphToJson(graph)); setError(null); setSaved(false); }}>Reset</button>
        <button type="button" className="repo-btn" onClick={handleApply} disabled={saving}>
          {saving ? 'Saving…' : 'Apply changes'}
        </button>
      </div>
    </aside>
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
  const agents = useSelector(selectAgentProfiles);
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
  // arrives on the stream (the GET just acknowledges and triggers it).
  useEffect(() => {
    if (!realtime.connected) {
      requestedListRef.current = null;
      return;
    }
    if (requestedListRef.current === agentsUrl) {
      return;
    }
    requestedListRef.current = agentsUrl;
    dispatch(fetchAgentProfiles({ agentsUrl }));
  }, [agentsUrl, dispatch, realtime.connected]);

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
  useEffect(() => {
    if (!effectiveAgentId) {
      return;
    }
    dispatch(requestAgentDetail({
      agentsUrl,
      agentId: effectiveAgentId,
      requestId: `${effectiveAgentId}-${Date.now()}`,
    }));
  }, [agentsUrl, dispatch, effectiveAgentId]);

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
    if (sortMode === 'Name') {
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
  }, [sortMode, visibleAgents]);
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
                <AgentRow
                  key={agent.id}
                  agent={agent}
                  selected={selectedAgent?.id === agent.id}
                  onSelect={selectAgent}
                />
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
                <StatusPill key="status" status={selectedAgent.status} />,
                <span key="model" className="sess-detail__model" style={{ '--agent-color': selectedAgent.color }}>{selectedAgent.model}</span>,
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
