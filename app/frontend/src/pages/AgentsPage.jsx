import { useEffect, useMemo, useState } from 'react';
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
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

const AGENT_FILTERS = ['All', 'Running', 'Paused', 'Drafts'];
const AGENT_DETAIL_TABS = ['Details', 'Graph'];

const graphFitViewOptions = { padding: 0.1 };
const graphLayoutScale = 0.62;
const graphDefaultEdgeOptions = {
  type: 'smoothstep',
  animated: true,
};

const AGENT_TEMPLATES = [
  {
    id: 'builder',
    group: 'Core',
    name: 'Builder',
    handle: '@builder',
    initials: 'B',
    color: '#2f6ff6',
    status: 'running',
    model: 'gpt-5-mini',
    role: 'Autonomous implementation agent',
    version: 'v0.1.0',
    updated: 'now',
    summary: 'Completes coding tasks end-to-end across connected repositories.',
    tools: [
      { name: 'filesystem', type: 'MCP', calls: '418', enabled: true },
      { name: 'git', type: 'MCP', calls: '96', enabled: true },
      { name: 'shell', type: 'native', calls: '214', enabled: true },
      { name: 'todos', type: 'MCP', calls: '87', enabled: true },
      { name: 'knowledge', type: 'MCP', calls: '141', enabled: true },
    ],
    repositories: [
      { name: 'progenitor', branch: 'main', access: 'write' },
      { name: 'progenitor-mcp', branch: 'feature/tools', access: 'write' },
      { name: 'agent-runtime', branch: 'release/0.8', access: 'read' },
    ],
    knowledge: [
      { name: 'Chrome migration notes', freshness: 'updated today', records: '42' },
      { name: 'Frontend design patterns', freshness: '2d old', records: '118' },
      { name: 'LangGraph runtime spec', freshness: '1w old', records: '64' },
    ],
    memories: [
      { title: 'Use shared pane primitives', scope: 'repository', age: 'today' },
      { title: 'Prefer mock data for workspace pages', scope: 'workspace', age: '2d' },
      { title: 'Validate with lint and build', scope: 'repository', age: '3d' },
    ],
    sessions: [
      { title: 'Tools page alignment', outcome: 'complete', age: '12m' },
      { title: 'Repository profile refinement', outcome: 'complete', age: '1h' },
      { title: 'Quick chat streaming', outcome: 'complete', age: '2h' },
    ],
    tasks: [
      { title: 'Implement agents page shell', state: 'active', age: 'now' },
      { title: 'Connect MCP server controls', state: 'queued', age: 'next' },
      { title: 'Refresh knowledge graph snapshots', state: 'queued', age: 'later' },
    ],
    metrics: {
      invocations: '1,284',
      sessions: '38',
      tasks: '164',
      memories: '27',
    },
    prompt: [
      'You are Builder, an autonomous engineering agent operating inside the Progenitor workspace.',
      '',
      'Complete assigned tasks end-to-end: read context, plan briefly, edit carefully, validate, and hand off concise results.',
      '',
      'Use connected tools, repositories, and knowledge bases before guessing. Prefer existing project patterns.',
    ].join('\n'),
  },
  {
    id: 'planner',
    group: 'Core',
    name: 'Planner',
    handle: '@planner',
    initials: 'P',
    color: '#6f86c4',
    status: 'running',
    model: 'gpt-5',
    role: 'Decomposes goals into executable work',
    version: 'v0.1.0',
    updated: '4m',
    summary: 'Turns broad requests into scoped tasks, dependencies, and acceptance checks.',
    tools: [
      { name: 'todos', type: 'MCP', calls: '512', enabled: true },
      { name: 'github', type: 'MCP', calls: '88', enabled: true },
      { name: 'knowledge', type: 'MCP', calls: '303', enabled: true },
    ],
    repositories: [
      { name: 'progenitor', branch: 'main', access: 'read' },
      { name: 'planning-playground', branch: 'main', access: 'write' },
    ],
    knowledge: [
      { name: 'Architecture decisions', freshness: 'today', records: '91' },
      { name: 'Project roadmap', freshness: '4h old', records: '37' },
    ],
    memories: [
      { title: 'User prefers direct execution from plans', scope: 'user', age: '1w' },
      { title: 'Keep page-specific work after chrome', scope: 'workspace', age: 'today' },
    ],
    sessions: [
      { title: 'Chrome migration rollout', outcome: 'complete', age: '2d' },
      { title: 'Tools page planning', outcome: 'complete', age: '36m' },
    ],
    tasks: [
      { title: 'Split page work into pane/detail units', state: 'complete', age: 'today' },
      { title: 'Draft quality checklist', state: 'queued', age: 'later' },
    ],
    metrics: {
      invocations: '842',
      sessions: '24',
      tasks: '318',
      memories: '19',
    },
    prompt: 'You are Planner. Convert high-level goals into practical work queues, dependencies, and crisp acceptance criteria.',
  },
  {
    id: 'observer',
    group: 'Core',
    name: 'Observer',
    handle: '@observer',
    initials: 'O',
    color: '#22a06b',
    status: 'running',
    model: 'observe-agent',
    role: 'Monitors runtime health and workspace activity',
    version: 'v0.2.0',
    updated: '8m',
    summary: 'Watches logs, sessions, and tasks for signals that need agent attention.',
    tools: [
      { name: 'logs', type: 'native', calls: '1,020', enabled: true },
      { name: 'sqlite', type: 'MCP', calls: '288', enabled: true },
      { name: 'metrics', type: 'native', calls: '604', enabled: true },
    ],
    repositories: [
      { name: 'progenitor', branch: 'main', access: 'read' },
    ],
    knowledge: [
      { name: 'Runtime incidents', freshness: 'live', records: '12' },
      { name: 'Heartbeat evaluations', freshness: 'today', records: '28' },
    ],
    memories: [
      { title: 'Live evaluator command', scope: 'repository', age: '3d' },
      { title: 'Heartbeat callbacks require async coverage', scope: 'repository', age: '3d' },
    ],
    sessions: [
      { title: 'Heartbeat live evaluation', outcome: 'complete', age: '1d' },
      { title: 'Status bar connection cleanup', outcome: 'complete', age: '3h' },
    ],
    tasks: [
      { title: 'Watch long-running tools', state: 'active', age: 'now' },
      { title: 'Summarize stalled sessions', state: 'queued', age: '30m' },
    ],
    metrics: {
      invocations: '2,418',
      sessions: '57',
      tasks: '89',
      memories: '34',
    },
    prompt: 'You are Observer. Watch the workspace for anomalies, drift, and useful signals. Report clearly and avoid noise.',
  },
  {
    id: 'reviewer',
    group: 'Planning and review',
    name: 'Reviewer',
    handle: '@reviewer',
    initials: 'R',
    color: '#c98e5a',
    status: 'running',
    model: 'claude-haiku-4.5',
    role: 'Reviews changes for correctness',
    version: 'v0.1.0',
    updated: '18m',
    summary: 'Checks patches, tests, and behavior before a task is considered complete.',
    tools: [
      { name: 'git diff', type: 'native', calls: '164', enabled: true },
      { name: 'tests', type: 'native', calls: '78', enabled: true },
      { name: 'github', type: 'MCP', calls: '42', enabled: true },
    ],
    repositories: [
      { name: 'progenitor', branch: 'main', access: 'read' },
      { name: 'agent-runtime', branch: 'main', access: 'read' },
    ],
    knowledge: [
      { name: 'Code review rubric', freshness: 'today', records: '14' },
      { name: 'Testing conventions', freshness: '1d old', records: '22' },
    ],
    memories: [
      { title: 'No broad silent fallbacks', scope: 'repository', age: 'today' },
      { title: 'Prefer precise edits over rewrites', scope: 'workspace', age: 'today' },
    ],
    sessions: [
      { title: 'Chat page markdown review', outcome: 'complete', age: '4h' },
      { title: 'Tools pane consistency check', outcome: 'complete', age: '25m' },
    ],
    tasks: [
      { title: 'Review agents page structure', state: 'queued', age: 'next' },
      { title: 'Check responsive shell layout', state: 'queued', age: 'later' },
    ],
    metrics: {
      invocations: '694',
      sessions: '31',
      tasks: '112',
      memories: '21',
    },
    prompt: 'You are Reviewer. Find correctness issues that matter. Ignore style-only noise unless it affects usability.',
  },
  {
    id: 'researcher',
    group: 'Planning and review',
    name: 'Researcher',
    handle: '@researcher',
    initials: 'Re',
    color: '#a78bfa',
    status: 'paused',
    model: 'gemini-2.5-pro',
    role: 'Grounds unknowns in external or repo context',
    version: 'v0.1.0',
    updated: '1h',
    summary: 'Collects citations, examples, and compatibility notes for unfamiliar work.',
    tools: [
      { name: 'web search', type: 'MCP', calls: '244', enabled: true },
      { name: 'github search', type: 'MCP', calls: '138', enabled: true },
      { name: 'knowledge', type: 'MCP', calls: '119', enabled: true },
    ],
    repositories: [
      { name: 'progenitor', branch: 'main', access: 'read' },
    ],
    knowledge: [
      { name: 'MCP protocol notes', freshness: '1w old', records: '56' },
      { name: 'Design reference captures', freshness: 'today', records: '76' },
    ],
    memories: [
      { title: 'Use citations for research claims', scope: 'workspace', age: '4d' },
      { title: 'Prototype visuals live in new/', scope: 'repository', age: 'today' },
    ],
    sessions: [
      { title: 'MCP server page reference', outcome: 'complete', age: '52m' },
      { title: 'Agent Workspace prototype pass', outcome: 'complete', age: 'today' },
    ],
    tasks: [
      { title: 'Compare agent profile layouts', state: 'paused', age: '1h' },
      { title: 'Collect model picker references', state: 'queued', age: 'later' },
    ],
    metrics: {
      invocations: '388',
      sessions: '15',
      tasks: '64',
      memories: '18',
    },
    prompt: 'You are Researcher. Find grounded context, cite sources, and summarize only the parts that change the plan.',
  },
  {
    id: 'indexer',
    group: 'Background',
    name: 'Indexer',
    handle: '@indexer',
    initials: 'I',
    color: '#58a6ff',
    status: 'running',
    model: 'local-embed',
    role: 'Keeps knowledge and repository indexes warm',
    version: 'v0.3.0',
    updated: '2m',
    summary: 'Indexes files, memories, and session artifacts for fast retrieval.',
    tools: [
      { name: 'filesystem', type: 'MCP', calls: '1,448', enabled: true },
      { name: 'embeddings', type: 'native', calls: '902', enabled: true },
    ],
    repositories: [
      { name: 'progenitor', branch: 'main', access: 'read' },
      { name: 'progenitor-docs', branch: 'main', access: 'read' },
    ],
    knowledge: [
      { name: 'Repository symbols', freshness: 'live', records: '12.4k' },
      { name: 'Session artifacts', freshness: 'live', records: '249' },
    ],
    memories: [
      { title: 'Session screenshots are not committed', scope: 'workspace', age: 'today' },
      { title: 'Frontend screenshots saved under files/', scope: 'workspace', age: 'today' },
    ],
    sessions: [
      { title: 'Knowledge page mock data refresh', outcome: 'complete', age: '5h' },
      { title: 'Repository tree indexing', outcome: 'complete', age: '1d' },
    ],
    tasks: [
      { title: 'Embed new frontend files', state: 'active', age: 'now' },
      { title: 'Compact old session traces', state: 'queued', age: 'later' },
    ],
    metrics: {
      invocations: '4,902',
      sessions: '12',
      tasks: '41',
      memories: '86',
    },
    prompt: 'You are Indexer. Keep workspace knowledge current, lightweight, and easy for other agents to query.',
  },
  {
    id: 'watchdog',
    group: 'Background',
    name: 'Watchdog',
    handle: '@watchdog',
    initials: 'W',
    color: '#ef6c2a',
    status: 'paused',
    model: 'gpt-5-mini',
    role: 'Guards long-running jobs and stalled queues',
    version: 'v0.1.0',
    updated: '36m',
    summary: 'Tracks timeouts, retries, and background work that needs intervention.',
    tools: [
      { name: 'processes', type: 'native', calls: '184', enabled: true },
      { name: 'logs', type: 'native', calls: '268', enabled: true },
      { name: 'alerts', type: 'MCP', calls: '31', enabled: false },
    ],
    repositories: [
      { name: 'progenitor', branch: 'main', access: 'read' },
    ],
    knowledge: [
      { name: 'Operational runbooks', freshness: '3d old', records: '18' },
      { name: 'Known flaky checks', freshness: 'today', records: '7' },
    ],
    memories: [
      { title: 'Do not kill processes by name', scope: 'workspace', age: 'today' },
      { title: 'Use detached mode for persistent servers', scope: 'workspace', age: 'today' },
    ],
    sessions: [
      { title: 'Dev server smoke check', outcome: 'complete', age: '3h' },
      { title: 'Playwright route verification', outcome: 'complete', age: '1h' },
    ],
    tasks: [
      { title: 'Watch frontend preview server', state: 'paused', age: '36m' },
      { title: 'Alert on failed build loop', state: 'queued', age: 'later' },
    ],
    metrics: {
      invocations: '528',
      sessions: '9',
      tasks: '73',
      memories: '11',
    },
    prompt: 'You are Watchdog. Detect stalls and risky process states. Escalate with precise evidence and safe actions.',
  },
];

const AGENT_GRAPH_BLUEPRINTS = {
  builder: {
    status: 'Designed',
    summary: 'DeepAgent implementation subagent with a LangGraph-style build loop and validation gate.',
    nodes: [
      { id: 'intake', label: 'Task intake', kind: 'input', detail: 'Normalize user goal, repo context, and constraints.', x: 0, y: 70 },
      { id: 'observe', label: 'Observe', kind: 'state', detail: 'Read files, plans, todos, memories, and tool output.', x: 210, y: 25 },
      { id: 'plan', label: 'Plan', kind: 'reason', detail: 'Choose edits, risks, and validation strategy.', x: 410, y: 92 },
      { id: 'build', label: 'Build', kind: 'action', detail: 'Patch code and update mock data or UI surfaces.', x: 630, y: 28 },
      { id: 'validate', label: 'Validate', kind: 'check', detail: 'Run lint, build, route smoke, and focused tests.', x: 840, y: 92 },
      { id: 'handoff', label: 'Handoff', kind: 'output', detail: 'Summarize what changed and remaining risks.', x: 1040, y: 52 },
    ],
    edges: [
      ['intake', 'observe'],
      ['observe', 'plan'],
      ['plan', 'build'],
      ['build', 'validate'],
      ['validate', 'handoff'],
      ['validate', 'plan', 'fix loop'],
    ],
  },
  planner: {
    status: 'Drafting',
    summary: 'Planning subagent that turns high-level intent into prioritized work and acceptance checks.',
    nodes: [
      { id: 'goal', label: 'Goal', kind: 'input', detail: 'Capture desired outcome and constraints.', x: 20, y: 74 },
      { id: 'breakdown', label: 'Breakdown', kind: 'reason', detail: 'Split work into coherent slices.', x: 255, y: 30 },
      { id: 'dependencies', label: 'Dependencies', kind: 'state', detail: 'Order tasks by dependency and readiness.', x: 505, y: 106 },
      { id: 'criteria', label: 'Acceptance', kind: 'check', detail: 'Define what proves each slice is done.', x: 760, y: 34 },
      { id: 'queue', label: 'Todo queue', kind: 'output', detail: 'Write executable planner items.', x: 1000, y: 82 },
    ],
    edges: [
      ['goal', 'breakdown'],
      ['breakdown', 'dependencies'],
      ['dependencies', 'criteria'],
      ['criteria', 'queue'],
    ],
  },
  observer: {
    status: 'Live',
    summary: 'Runtime monitor subagent with a lightweight graph for signal collection and escalation.',
    nodes: [
      { id: 'heartbeat', label: 'Heartbeat', kind: 'input', detail: 'Listen to status events and runtime cycles.', x: 40, y: 45 },
      { id: 'logs', label: 'Logs', kind: 'state', detail: 'Read recent backend and agent events.', x: 255, y: 115 },
      { id: 'score', label: 'Signal score', kind: 'reason', detail: 'Filter noise and detect drift.', x: 505, y: 55 },
      { id: 'escalate', label: 'Escalate', kind: 'action', detail: 'Open task or alert only when useful.', x: 760, y: 120 },
      { id: 'memory', label: 'Memory update', kind: 'output', detail: 'Preserve durable operational facts.', x: 1010, y: 52 },
    ],
    edges: [
      ['heartbeat', 'logs'],
      ['logs', 'score'],
      ['score', 'escalate'],
      ['score', 'memory'],
      ['escalate', 'heartbeat', 'watch'],
    ],
  },
  researcher: {
    status: 'Optional',
    summary: 'Research subagent graph focused on collecting grounded context before implementation.',
    nodes: [
      { id: 'question', label: 'Question', kind: 'input', detail: 'Clarify the unknown being researched.', x: 40, y: 88 },
      { id: 'sources', label: 'Sources', kind: 'action', detail: 'Search repo, docs, web, and GitHub.', x: 285, y: 34 },
      { id: 'evidence', label: 'Evidence', kind: 'state', detail: 'Keep cited facts and discard weak signals.', x: 540, y: 110 },
      { id: 'synthesis', label: 'Synthesis', kind: 'reason', detail: 'Turn findings into a decision-changing summary.', x: 800, y: 45 },
      { id: 'handoff', label: 'Handoff', kind: 'output', detail: 'Return concise findings with citations.', x: 1040, y: 92 },
    ],
    edges: [
      ['question', 'sources'],
      ['sources', 'evidence'],
      ['evidence', 'synthesis'],
      ['synthesis', 'handoff'],
    ],
  },
};

const DEFAULT_AGENT_GRAPH_NODES = [
  { id: 'input', label: 'Input', kind: 'input', detail: 'Receive delegated work or scheduled trigger.', x: 50, y: 80 },
  { id: 'context', label: 'Context', kind: 'state', detail: 'Load tools, repositories, memories, and skills.', x: 305, y: 34 },
  { id: 'think', label: 'Think', kind: 'reason', detail: 'Choose the next graph step.', x: 560, y: 112 },
  { id: 'act', label: 'Act', kind: 'action', detail: 'Call tools or update task state.', x: 815, y: 42 },
  { id: 'return', label: 'Return', kind: 'output', detail: 'Send result back to the parent DeepAgent graph.', x: 1055, y: 92 },
];

const DEFAULT_AGENT_GRAPH_EDGES = [
  ['input', 'context'],
  ['context', 'think'],
  ['think', 'act'],
  ['act', 'return'],
  ['act', 'think', 'retry'],
];

function normalizeStatus(status) {
  if (status === 'draft') {
    return 'Drafts';
  }
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function createAgentGraph(agent) {
  return {
    status: 'Draft',
    summary: `${agent.name} is modeled as a DeepAgent subagent with one editable LangGraph-style node graph.`,
    nodes: DEFAULT_AGENT_GRAPH_NODES,
    edges: DEFAULT_AGENT_GRAPH_EDGES,
  };
}

function getAgentGraph(agent) {
  return AGENT_GRAPH_BLUEPRINTS[agent.id] || agent.graph || createAgentGraph(agent);
}

function createDraftAgent(index) {
  const agent = {
    ...AGENT_TEMPLATES[0],
    id: `draft-agent-${index}`,
    group: 'Drafts',
    name: `New Agent ${index}`,
    handle: `@new_agent_${index}`,
    initials: 'N',
    color: '#8b949e',
    status: 'draft',
    model: 'choose-model',
    role: 'Lightweight draft agent',
    version: 'v0.0.1',
    updated: 'new',
    summary: 'A new mocked agent ready for tools, repositories, and knowledge.',
    tools: [
      { name: 'knowledge', type: 'MCP', calls: '0', enabled: true },
      { name: 'todos', type: 'MCP', calls: '0', enabled: true },
    ],
    repositories: [
      { name: 'progenitor', branch: 'main', access: 'read' },
    ],
    knowledge: [
      { name: 'Workspace starter pack', freshness: 'new', records: '8' },
    ],
    memories: [
      { title: 'Draft agent created from workspace UI', scope: 'workspace', age: 'now' },
    ],
    sessions: [],
    tasks: [
      { title: 'Configure role, model, and connected context', state: 'draft', age: 'now' },
    ],
    metrics: {
      invocations: '0',
      sessions: '0',
      tasks: '1',
      memories: '1',
    },
    prompt: 'Describe this agent role, the tools it can use, and when it should hand off to another agent.',
  };
  return {
    ...agent,
    graph: createAgentGraph(agent),
  };
}

function matchesAgent(agent, filter, search) {
  const normalizedFilter = normalizeStatus(agent.status);
  const matchesFilter = filter === 'All' || normalizedFilter === filter;
  const haystack = [
    agent.name,
    agent.handle,
    agent.model,
    agent.role,
    agent.summary,
    ...agent.tools.map((tool) => tool.name),
    ...agent.repositories.map((repo) => repo.name),
    ...agent.knowledge.map((item) => item.name),
  ].join(' ').toLowerCase();
  return matchesFilter && (!search || haystack.includes(search.toLowerCase()));
}

function groupAgents(agents) {
  return agents.reduce((groups, agent) => {
    if (!groups[agent.group]) {
      groups[agent.group] = [];
    }
    groups[agent.group].push(agent);
    return groups;
  }, {});
}

function StatusPill({ status }) {
  return (
    <span className={`agents-status-pill agents-status-pill--${status}`}>
      <span className="adot" />
      {status}
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
        <span className="sess-row__agent agents-agent-row__name" style={{ color: agent.color }}>{agent.name}</span>
        <span className="sess-row__project">{agent.model}</span>
      </span>
      <span className="sess-row__age">{agent.updated}</span>
      <span className="sess-row__title">{agent.role}</span>
      <span className="sess-row__msgs">
        <span><span className="num">{agent.tools.length}</span> tools</span>
        <span><span className="num">{agent.repositories.length}</span> repos</span>
        <span><span className="num">{agent.knowledge.length}</span> knowledge</span>
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

function AgentGraphNode({ data }) {
  return (
    <div className={`agents-graph-node agents-graph-node--${data.kind}`} style={{ '--node-color': data.color }}>
      <Handle type="target" position={Position.Left} />
      <div className="agents-graph-node__kind">{data.kind}</div>
      <strong>{data.label}</strong>
      <p>{data.detail}</p>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const agentGraphNodeTypes = {
  agentGraph: AgentGraphNode,
};

function toFlowNode(node, agent) {
  return {
    id: node.id,
    type: 'agentGraph',
    position: { x: Math.round(node.x * graphLayoutScale), y: node.y },
    data: {
      ...node,
      color: agent.color,
    },
  };
}

function toFlowEdge(edge, index, agent) {
  const [source, target, label] = edge;
  return {
    id: `${source}-${target}-${index}`,
    source,
    target,
    label,
    type: 'smoothstep',
    animated: true,
    style: { stroke: agent.color },
    markerEnd: { type: MarkerType.ArrowClosed, color: agent.color },
  };
}

function AgentGraphCanvas({ agent, graph, onShowProfile }) {
  const initialNodes = useMemo(
    () => graph.nodes.map((node) => toFlowNode(node, agent)),
    [agent, graph],
  );
  const initialEdges = useMemo(
    () => graph.edges.map((edge, index) => toFlowEdge(edge, index, agent)),
    [agent, graph],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
  }, [initialEdges, initialNodes, setEdges, setNodes]);

  function addDraftNode() {
    setNodes((current) => {
      const nextIndex = current.filter((node) => node.id.startsWith('draft-node')).length + 1;
      return [
        ...current,
        {
          id: `draft-node-${Date.now()}`,
          type: 'agentGraph',
          position: { x: 320 + nextIndex * 42, y: 280 },
          data: {
            kind: 'draft',
            label: `Draft node ${nextIndex}`,
            detail: 'Sketch a future LangGraph step for the builder agent to generate.',
            color: agent.color,
          },
        },
      ];
    });
  }

  return (
    <section className="agents-graph-panel" style={{ '--agent-color': agent.color }}>
      <div className="agents-graph-shell">
        <div className="agents-graph-canvas">
          <div className="agents-graph-floating">
            <span>{agent.name}</span>
            <span>{nodes.length} nodes</span>
            <button type="button" onClick={onShowProfile}>Details</button>
            <button type="button" onClick={addDraftNode}>+ Node</button>
          </div>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={agentGraphNodeTypes}
            defaultEdgeOptions={graphDefaultEdgeOptions}
            fitView
            fitViewOptions={graphFitViewOptions}
            minZoom={0.6}
            maxZoom={1.8}
            snapToGrid
            snapGrid={[16, 16]}
            nodesConnectable={false}
            deleteKeyCode={null}
          >
            <Background gap={18} size={1} color="var(--color-border)" variant="dots" />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      </div>
    </section>
  );
}

function AgentGraphTab({ agent, graph, onShowProfile }) {
  return (
    <ReactFlowProvider>
      <AgentGraphCanvas key={agent.id} agent={agent} graph={graph} onShowProfile={onShowProfile} />
    </ReactFlowProvider>
  );
}

function AgentsPage() {
  const [customAgents, setCustomAgents] = useState([]);
  const [activeFilter, setActiveFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [selectedAgentId, setSelectedAgentId] = useState(AGENT_TEMPLATES[0].id);
  const [activeDetailTab, setActiveDetailTab] = useState('Details');

  const agents = useMemo(() => [...AGENT_TEMPLATES, ...customAgents], [customAgents]);
  const selectedAgent = agents.find((agent) => agent.id === selectedAgentId) || agents[0];
  const selectedGraph = useMemo(() => getAgentGraph(selectedAgent), [selectedAgent]);
  const visibleAgents = useMemo(
    () => agents.filter((agent) => matchesAgent(agent, activeFilter, search)),
    [activeFilter, agents, search],
  );
  const groupedAgents = useMemo(() => groupAgents(visibleAgents), [visibleAgents]);
  const runningCount = agents.filter((agent) => agent.status === 'running').length;
  const taskCount = agents.reduce((total, agent) => total + agent.tasks.length, 0);
  const memoryCount = agents.reduce((total, agent) => total + agent.memories.length, 0);
  const order = ['Core', 'Planning and review', 'Background', 'Drafts'];

  function handleNewAgent() {
    const nextAgent = createDraftAgent(customAgents.length + 1);
    setCustomAgents((current) => [...current, nextAgent]);
    setSelectedAgentId(nextAgent.id);
    setActiveFilter('All');
    setActiveDetailTab('Details');
  }

  return (
    <div className="agents-page">
      <PaneList label="Agents" className="agents-pane">
        <PaneListHeader title="Agents" count={agents.length} actionLabel="+ New agent" onAction={handleNewAgent} />
        <PaneListFilters filters={AGENT_FILTERS} activeFilter={activeFilter} onFilterChange={setActiveFilter} />
        <PaneListToolbar>
          <PaneListSearch value={search} placeholder="Search agents..." onChange={setSearch} />
          <PaneListSortButton>Active</PaneListSortButton>
        </PaneListToolbar>
        <PaneListScroll>
          {visibleAgents.length ? (
            order.map((group) => (
              groupedAgents[group]?.length ? (
                <PaneListGroup key={group} title={group} count={groupedAgents[group].length}>
                  {groupedAgents[group].map((agent) => (
                    <AgentRow
                      key={agent.id}
                      agent={agent}
                      selected={selectedAgent.id === agent.id}
                      onSelect={setSelectedAgentId}
                    />
                  ))}
                </PaneListGroup>
              ) : null
            ))
          ) : (
            <div className="agents-empty-state">No agents match this view.</div>
          )}
        </PaneListScroll>
        <PaneListFooter>
          <span><span className="num">{runningCount}</span> running</span>
          <span><span className="num">{taskCount}</span> tasks</span>
          <span><span className="num">{memoryCount}</span> memories</span>
        </PaneListFooter>
      </PaneList>

      <main className={`agents-detail ${activeDetailTab === 'Graph' ? 'is-graph-mode' : ''}`} aria-label={`${selectedAgent.name} profile`}>
          <DetailHeader
            title={selectedAgent.name}
            subtitle={`${selectedAgent.handle} · ${selectedAgent.version}`}
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
              <span key="model" className="sess-detail__model"><span className="adot" style={{ background: selectedAgent.color }} />{selectedAgent.model}</span>,
              <span key="role" className="muted">{selectedAgent.role}</span>,
              <span key="links" className="muted">{selectedAgent.tools.length} tools · {selectedAgent.repositories.length} repos · {selectedAgent.knowledge.length} knowledge</span>,
            ]}
            tabs={AGENT_DETAIL_TABS}
            activeTab={activeDetailTab}
            onTabChange={setActiveDetailTab}
            tabLabel="Agent detail tabs"
          />

        {activeDetailTab === 'Details' ? (
        <div className="agents-detail__body">
          <div className="agents-metrics">
            <MetricCard label="Invocations" value={selectedAgent.metrics.invocations} detail="last 30 days" />
            <MetricCard label="Sessions" value={selectedAgent.metrics.sessions} detail="linked conversations" />
            <MetricCard label="Tasks" value={selectedAgent.metrics.tasks} detail="open and completed" />
            <MetricCard label="Memories" value={selectedAgent.metrics.memories} detail="available context" />
          </div>

          <section className="agents-section agents-section--prompt">
            <header className="agents-section__head">
              <h2>System prompt <span>{selectedAgent.prompt.length} chars</span></h2>
              <div>
                <button type="button" className="repo-btn repo-btn--ghost">Edit</button>
                <button type="button" className="repo-btn repo-btn--ghost">Diff</button>
              </div>
            </header>
            <pre className="agents-prompt"><code>{selectedAgent.prompt}</code></pre>
          </section>

          <div className="agents-detail-grid">
            <MiniListSection title="Connected tools" count={`${selectedAgent.tools.length} attached`} action="+ Add tool">
              <div className="agents-connection-list">
                {selectedAgent.tools.map((tool) => (
                  <ConnectionRow
                    key={tool.name}
                    icon={tool.name.slice(0, 2)}
                    title={tool.name}
                    detail={`${tool.type} - ${tool.calls} calls`}
                    meta={tool.enabled ? 'on' : 'off'}
                    tone={tool.enabled ? 'ok' : 'muted'}
                  />
                ))}
              </div>
            </MiniListSection>

            <MiniListSection title="Repositories" count={`${selectedAgent.repositories.length} connected`} action="+ Attach repo">
              <div className="agents-connection-list">
                {selectedAgent.repositories.map((repo) => (
                  <ConnectionRow
                    key={`${repo.name}-${repo.branch}`}
                    icon="repo"
                    title={repo.name}
                    detail={`branch ${repo.branch}`}
                    meta={repo.access}
                    tone={repo.access === 'write' ? 'ok' : 'info'}
                  />
                ))}
              </div>
            </MiniListSection>

            <MiniListSection title="Knowledge" count={`${selectedAgent.knowledge.length} sources`} action="+ Link source">
              <div className="agents-connection-list">
                {selectedAgent.knowledge.map((item) => (
                  <ConnectionRow
                    key={item.name}
                    icon="kb"
                    title={item.name}
                    detail={`${item.records} records`}
                    meta={item.freshness}
                    tone="info"
                  />
                ))}
              </div>
            </MiniListSection>

          </div>

          <div className="agents-activity-grid">
            <MiniListSection title="Memories" count={`${selectedAgent.memories.length} pinned`}>
              <div className="agents-mini-list">
                {selectedAgent.memories.map((memory) => (
                  <div key={memory.title} className="agents-mini-row">
                    <span className="agents-mini-row__icon">mem</span>
                    <div>
                      <strong>{memory.title}</strong>
                      <p>{memory.scope} - {memory.age}</p>
                    </div>
                  </div>
                ))}
              </div>
            </MiniListSection>

            <MiniListSection title="Sessions" count={`${selectedAgent.sessions.length} recent`}>
              <div className="agents-mini-list">
                {selectedAgent.sessions.length ? selectedAgent.sessions.map((session) => (
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

            <MiniListSection title="Tasks" count={`${selectedAgent.tasks.length} tracked`}>
              <div className="agents-mini-list">
                {selectedAgent.tasks.map((task) => (
                  <div key={task.title} className="agents-mini-row">
                    <span className="agents-mini-row__icon">tsk</span>
                    <div>
                      <strong>{task.title}</strong>
                      <p>{task.state} - {task.age}</p>
                    </div>
                    <span className={`agents-chip agents-chip--${task.state === 'active' ? 'ok' : 'info'}`}>{task.state}</span>
                  </div>
                ))}
              </div>
            </MiniListSection>
          </div>
        </div>
        ) : (
          <div className="agents-detail__body agents-detail__body--graph">
            <AgentGraphTab agent={selectedAgent} graph={selectedGraph} onShowProfile={() => setActiveDetailTab('Details')} />
          </div>
        )}
      </main>
    </div>
  );
}

export default AgentsPage;
