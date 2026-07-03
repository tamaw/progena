// Pure, React-free helpers for the workspace graph: node/edge construction,
// widget→node mapping, dataflow resolution, execution-input building, and
// persistence shaping. Extracted from WorkspaceContext so the provider only
// owns React state/effects and these stay independently testable.

const SERVER_NODE_TYPES = new Set(['agent', 'mcp', 'schedule', 'event']);
export const DEFAULT_EVENT_TYPE = 'kanban.card.created';
const DATAFLOW_SOURCE_TYPES = new Set(['event', 'prompt', 'code', 'user', 'schedule']);
const DATAFLOW_TARGET_TYPES = new Set(['prompt', 'code', 'user', 'schedule']);
const MAX_EVENT_LOG_ENTRIES = 25;
const DEFAULT_USER_MESSAGE_TYPE = 'say_to_user';
const EDGE_KIND_PROMPT_AGENT = 'prompt-agent';
const EDGE_KIND_DATAFLOW = 'dataflow';
const LEGACY_EDGE_KIND_EVENT_TRIGGER = 'event-trigger';

export const defaultNodeSizes = {
  prompt: { width: 320, height: 200 },
  code: { width: 400, height: 280 },
  user: { width: 360, height: 240 },
  webframe: { width: 480, height: 360 },
  label: { width: 200, height: 60 },
  schedule: { width: 300, height: 180 },
  event: { width: 320, height: 200 },
  group: { width: 600, height: 400 },
  agent: { width: 320, height: 200 },
  mcp: { width: 320, height: 200 },
};
const defaultNodeData = {
  prompt: () => ({
    prompt: '',
    role: '',
    sourcePath: '',
    primaryAgent: '',
    associatedAgents: [],
    targetAgent: 'progenitor',
    variables: {},
    linkedOutputIds: [],
    lastThreadId: null,
    summaryState: '',
    lastOutput: '',
  }),
  code: () => ({
    language: 'javascript',
    content: '',
    runtimeProfile: '',
    originPromptId: null,
    lastThreadId: null,
    outputRefs: [],
    lastOutput: '',
  }),
  user: () => ({
    messageType: DEFAULT_USER_MESSAGE_TYPE,
    message: '',
    notificationTitle: '',
    level: 'info',
    lastSentAt: null,
    summaryState: '',
    lastOutput: '',
  }),
  webframe: () => ({
    sourceType: 'html',
    html: '<html>\n<body>\n  <h1>Hello World</h1>\n</body>\n</html>',
    css: '',
    js: '',
    url: '',
    sandbox: {
      allowScripts: true,
      allowSameOrigin: false,
      allowForms: false,
      allowPopups: false,
    },
  }),
  label: () => ({
    text: 'Label',
    variant: 'note',
  }),
  schedule: () => ({
    scheduleType: 'cron',
    expression: '',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    enabled: false,
    instruction: '',
    targetArtefactId: null,
    nextRunAt: null,
    lastRunAt: null,
    lastRunStatus: 'idle',
    isUnsaved: false,
    isSaving: false,
    saveError: null,
    lastOutput: '',
  }),
  event: () => ({
    eventType: DEFAULT_EVENT_TYPE,
    enabled: true,
    triggerCount: 0,
    lastTriggeredAt: null,
    lastRunStatus: 'idle',
    lastPayload: null,
    isUnsaved: false,
    isSaving: false,
    saveError: null,
    summaryState: `Waiting for ${DEFAULT_EVENT_TYPE}`,
    logs: [],
    lastOutput: '',
  }),
  group: () => ({
    childIds: [],
    description: '',
  }),
  agent: () => ({
    agentId: '',
    agentStatus: 'idle',
    currentThreadId: null,
    availabilityStatus: null,
    summaryState: '',
    logs: [],
    inspection: null,
    isRefreshing: false,
    refreshError: null,
    selectedAction: null,
    widgetKind: 'agent',
  }),
  mcp: () => ({
    agentId: '',
    agentStatus: 'idle',
    currentThreadId: null,
    availabilityStatus: null,
    summaryState: '',
    logs: [],
    inspection: null,
    isRefreshing: false,
    refreshError: null,
    selectedAction: null,
    widgetKind: 'mcp',
  }),
};

const typeLabels = {
  prompt: 'Prompt',
  code: 'Code',
  user: 'User',
  webframe: 'Web Frame',
  label: 'Label',
  schedule: 'Schedule',
  event: 'Event',
  group: 'Group',
  agent: 'Agent',
  mcp: 'MCP',
};

let nodeIdCounter = 0;

export function generateId() {
  nodeIdCounter += 1;
  return `node_${Date.now()}_${nodeIdCounter}`;
}

export function isServerNode(node) {
  return SERVER_NODE_TYPES.has(node.type) || node.data?.serverManaged;
}

export function createArtefact(type, position, overrides = {}) {
  const size = defaultNodeSizes[type] || { width: 300, height: 200 };
  const now = new Date().toISOString();

  return {
    id: overrides.id || generateId(),
    type,
    position: { x: position.x, y: position.y },
    style: { width: size.width, height: size.height },
    data: {
      title: overrides.title || typeLabels[type] || type,
      createdAt: now,
      updatedAt: now,
      createdBy: { kind: 'user', id: 'local' },
      tags: [],
      status: 'idle',
      locked: false,
      expanded: false,
      ...defaultNodeData[type]?.(),
      ...overrides,
    },
  };
}

export function normalizeLogs(logs) {
  if (!Array.isArray(logs)) {
    return [];
  }
  return logs
    .map((entry) => ({
      time: entry.time || entry.timestamp || entry.created_at || entry.last_active || null,
      message: entry.message || entry.content || entry.summary || entry.description || String(entry),
      level: entry.level || 'info',
      source: entry.source || entry.path || 'system',
    }))
    .sort((left, right) => {
      const leftTime = Number.isFinite(Date.parse(left.time || '')) ? Date.parse(left.time) : -Infinity;
      const rightTime = Number.isFinite(Date.parse(right.time || '')) ? Date.parse(right.time) : -Infinity;
      if (leftTime !== rightTime) {
        return rightTime - leftTime;
      }
      return String(right.time || '').localeCompare(String(left.time || ''));
    });
}

export function deriveAvailabilityStatus(status, inspection = null) {
  const candidate = String(status || '').trim().toLowerCase();

  if (inspection) {
    return 'online';
  }

  if (['offline', 'disabled', 'stopped'].includes(candidate)) {
    return 'offline';
  }

  if (['failed', 'error'].includes(candidate)) {
    return 'failed';
  }

  if (['running', 'busy', 'ready', 'enabled', 'completed', 'success'].includes(candidate)) {
    return 'online';
  }

  return 'offline';
}

export function deriveAvailabilityStatusFromRefreshError(runtimeStatus, message) {
  const candidate = String(runtimeStatus || '').trim().toLowerCase();
  if (['offline', 'disabled', 'stopped'].includes(candidate)) {
    return 'offline';
  }

  const detail = String(message || '').toLowerCase();
  if (
    detail.includes('connection refused')
    || detail.includes('name or service not known')
    || detail.includes('no route to host')
    || detail.includes('timed out')
    || detail.includes('not registered')
    || detail.includes('not available for live inspection')
    || detail.includes('returned 404')
  ) {
    return 'offline';
  }

  return 'failed';
}

export function buildEventOutputText(payload, fallback = '') {
  if (!payload) {
    return fallback;
  }

  if (typeof payload === 'string') {
    return payload;
  }

  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}

export function mapWidgetToNodeData(widget, existingData = {}) {
  const timestamp = widget.updated_at || existingData.updatedAt || new Date().toISOString();
  const title = widget.title || widget.name || existingData.title || widget.id;
  const summary = widget.summary || widget.description || existingData.summaryState || '';
  const logs = normalizeLogs(widget.logs || widget.data?.logs || existingData.logs);

  if (widget.type === 'event') {
    const eventType = widget.data?.event_type || widget.event_type || existingData.eventType || DEFAULT_EVENT_TYPE;
    return {
      ...defaultNodeData.event(),
      ...existingData,
      ...widget.data,
      title,
      summaryState: widget.summary || existingData.summaryState || buildEventWaitingSummary(eventType),
      logs,
      serverManaged: true,
      status: widget.status || existingData.status || 'idle',
      eventType,
      enabled: widget.data?.enabled ?? widget.enabled ?? existingData.enabled ?? true,
      triggerCount: widget.data?.trigger_count ?? widget.trigger_count ?? existingData.triggerCount ?? 0,
      lastTriggeredAt: widget.data?.last_triggered_at || widget.last_triggered_at || existingData.lastTriggeredAt || null,
      lastRunStatus: widget.data?.last_run_status || widget.last_run_status || existingData.lastRunStatus || 'idle',
      lastPayload: widget.data?.last_payload ?? widget.last_payload ?? existingData.lastPayload ?? null,
      isUnsaved: false,
      isSaving: false,
      saveError: null,
      lastOutput: buildEventOutputText(
        widget.data?.last_payload ?? widget.last_payload ?? existingData.lastPayload ?? null,
        existingData.lastOutput || widget.summary || existingData.summaryState || ''
      ),
      updatedAt: timestamp,
    };
  }

  if (widget.type === 'schedule') {
    return {
      ...defaultNodeData.schedule(),
      ...existingData,
      ...widget.data,
      title,
      summaryState: summary,
      logs,
      serverManaged: true,
      status: widget.status || existingData.status || 'idle',
      scheduleType: widget.data?.schedule_type || widget.schedule_type || existingData.scheduleType || 'cron',
      expression: widget.data?.expression || widget.expression || existingData.expression || '',
      instruction: widget.data?.instruction || widget.data?.message || existingData.instruction || '',
      timezone: widget.data?.timezone || widget.timezone || existingData.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone,
      enabled: widget.data?.enabled ?? widget.enabled ?? existingData.enabled ?? false,
      nextRunAt: widget.data?.next_run_at || widget.next_run_at || existingData.nextRunAt || null,
      lastRunAt: widget.data?.last_run_at || widget.last_run_at || existingData.lastRunAt || null,
      lastRunStatus: widget.data?.last_run_status || widget.last_run_status || existingData.lastRunStatus || 'idle',
      isUnsaved: false,
      isSaving: false,
      saveError: null,
      lastOutput: existingData.lastOutput || widget.summary || existingData.summaryState || '',
      updatedAt: timestamp,
    };
  }

  return {
    ...(widget.type === 'mcp' ? defaultNodeData.mcp() : defaultNodeData.agent()),
    ...existingData,
    ...widget.data,
    title,
    summaryState: summary,
    logs,
    serverManaged: true,
    widgetKind: widget.type === 'mcp' ? 'mcp' : 'agent',
    agentId: widget.name || existingData.agentId || '',
    agentStatus: widget.status || existingData.agentStatus || 'idle',
    currentThreadId:
      widget.data?.current_thread_id
      || widget.current_thread_id
      || existingData.currentThreadId
      || null,
    availabilityStatus:
      widget.data?.availability_status
      || widget.availability_status
      || existingData.availabilityStatus
      || deriveAvailabilityStatus(widget.status, widget.data?.inspection || existingData.inspection),
    transport: widget.transport || widget.data?.transport || existingData.transport || '',
    updatedAt: timestamp,
  };
}

export function getActionText(payload) {
  if (!payload) {
    return '';
  }
  if (typeof payload === 'string') {
    return payload;
  }
  return (
    payload.response ||
    payload.content ||
    payload.output?.[0]?.content?.[0]?.text ||
    payload.data?.content ||
    payload.result ||
    payload.message ||
    ''
  );
}

export function buildEventWaitingSummary(eventType = DEFAULT_EVENT_TYPE) {
  return `Waiting for ${eventType}`;
}

export function dedupeNodeValues(values) {
  return [...new Set(
    values
      .map((value) => (typeof value === 'string' ? value.trim() : ''))
      .filter(Boolean)
  )];
}

export function normalizeAgentName(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

export function isAgentNodeType(type) {
  return type === 'agent' || type === 'mcp';
}

export function isDataflowSourceType(type) {
  return DATAFLOW_SOURCE_TYPES.has(type);
}

export function isDataflowTargetType(type) {
  return DATAFLOW_TARGET_TYPES.has(type);
}

export function getEdgeKind(edge) {
  return edge?.data?.kind || '';
}

export function isPromptAgentEdge(edge) {
  return getEdgeKind(edge) === EDGE_KIND_PROMPT_AGENT;
}

export function isDataflowEdge(edge) {
  const edgeKind = getEdgeKind(edge);
  return edgeKind === EDGE_KIND_DATAFLOW || edgeKind === LEGACY_EDGE_KIND_EVENT_TRIGGER;
}

export function createDataflowEdge(sourceId, targetId) {
  return {
    id: createEdgeId(sourceId, targetId),
    source: sourceId,
    target: targetId,
    type: 'smoothstep',
    animated: true,
    data: { kind: EDGE_KIND_DATAFLOW },
  };
}

export function createPromptAgentEdge(sourceId, targetId) {
  return {
    id: createEdgeId(sourceId, targetId),
    source: sourceId,
    target: targetId,
    type: 'smoothstep',
    animated: true,
    data: { kind: EDGE_KIND_PROMPT_AGENT },
  };
}

export function createEdgeId(sourceId, targetId) {
  return `edge_${sourceId}_${targetId}`;
}

export function formatWidgetLabel(node) {
  return node?.data?.title || typeLabels[node?.type] || node?.type || 'widget';
}

export function appendEventLog(logs, message, time = new Date().toISOString(), level = 'info') {
  return [
    { time, message, level, source: 'workspace' },
    ...(Array.isArray(logs) ? logs : []),
  ].slice(0, MAX_EVENT_LOG_ENTRIES);
}

export function buildManualEventPayload(node) {
  const triggeredAt = new Date().toISOString();
  return {
    event_type: node.data?.eventType || DEFAULT_EVENT_TYPE,
    trigger_source: 'manual',
    widget_id: node.id,
    widget_title: node.data?.title || 'Event',
    triggered_at: triggeredAt,
  };
}

export function getViewportSignature(viewport = {}) {
  return `${viewport.x ?? 0}:${viewport.y ?? 0}:${viewport.zoom ?? 1}`;
}

export function areStringArraysEqual(currentValues, nextValues) {
  return currentValues.length === nextValues.length
    && currentValues.every((value, index) => value === nextValues[index]);
}

export function extractAgentOptions(nodes) {
  const values = new Set(['progenitor']);
  nodes
    .filter((node) => node.type === 'agent' || node.type === 'mcp')
    .forEach((node) => {
      const value = node.data?.agentId || node.data?.title;
      if (value) {
        values.add(value);
      }
    });

  return [...values];
}

export function createPersistedGraph(nodes, edges) {
  return {
    nodes: nodes.map((node) => {
      const {
        selected: _selected,
        dragging: _dragging,
        resizing: _resizing,
        positionAbsolute: _positionAbsolute,
        measured: _measured,
        ...persistedNode
      } = node;
      return persistedNode;
    }),
    edges: edges.map((edge) => {
      const { selected: _selected, ...persistedEdge } = edge;
      return persistedEdge;
    }),
  };
}

export function areWorkspaceNodesEquivalent(currentNode, nextNode) {
  return currentNode.id === nextNode.id
    && currentNode.type === nextNode.type
    && currentNode.zIndex === nextNode.zIndex
    && currentNode.selected === nextNode.selected
    && currentNode.position?.x === nextNode.position?.x
    && currentNode.position?.y === nextNode.position?.y
    && JSON.stringify(currentNode.style || {}) === JSON.stringify(nextNode.style || {})
    && JSON.stringify(currentNode.data || {}) === JSON.stringify(nextNode.data || {});
}

export function haveSameNodeReferences(currentNodes, nextNodes) {
  return currentNodes.length === nextNodes.length
    && currentNodes.every((node, index) => node === nextNodes[index]);
}

export function getAgentNameFromNode(node) {
  return node?.data?.agentId || node?.data?.title || '';
}

export function getOutgoingDataflowTargets(sourceNodeId, nodes, edges) {
  const seen = new Set();

  return edges.reduce((targets, edge) => {
    if (edge.source !== sourceNodeId || seen.has(edge.target) || !isDataflowEdge(edge)) {
      return targets;
    }

    const targetNode = nodes.find((node) => node.id === edge.target);
    if (!targetNode || !isDataflowTargetType(targetNode.type)) {
      return targets;
    }

    seen.add(targetNode.id);
    targets.push(targetNode);
    return targets;
  }, []);
}

export function getPromptTargetAgent(promptData = {}, connectedTargetAgent = '') {
  return dedupeNodeValues([
    connectedTargetAgent,
    promptData.targetAgent,
    promptData.primaryAgent,
    ...(promptData.associatedAgents || []),
    'progenitor',
  ])[0] || 'progenitor';
}

export function getConnectedAgentTarget(promptNodeId, nodes, edges) {
  const edge = edges.find((entry) => entry.source === promptNodeId && isPromptAgentEdge(entry));
  if (!edge) {
    return null;
  }

  const targetNode = nodes.find((node) => node.id === edge.target && isAgentNodeType(node.type));
  if (!targetNode) {
    return null;
  }

  return {
    nodeId: targetNode.id,
    agentName: getAgentNameFromNode(targetNode),
  };
}

export function getNodeOutputText(node) {
  if (!node) {
    return '';
  }

  const explicitOutput = typeof node.data?.lastOutput === 'string' ? node.data.lastOutput : '';
  if (explicitOutput) {
    return explicitOutput;
  }

  if (node.type === 'event') {
    return buildEventOutputText(node.data?.lastPayload, node.data?.summaryState || '');
  }

  if (node.type === 'code') {
    return Array.isArray(node.data?.outputRefs) ? node.data.outputRefs.join('\n') : '';
  }

  return node.data?.summaryState || '';
}

export function getIncomingDataflowSource(targetNodeId, nodes, edges) {
  const edge = edges.find((entry) => entry.target === targetNodeId && isDataflowEdge(entry));
  if (!edge) {
    return null;
  }

  const sourceNode = nodes.find((node) => node.id === edge.source);
  if (!sourceNode || !isDataflowSourceType(sourceNode.type)) {
    return null;
  }

  return {
    edge,
    node: sourceNode,
  };
}

export function buildExecutionInput(sourceNode, outputText) {
  const normalizedOutput = String(outputText || '').trim();
  if (!normalizedOutput) {
    return '';
  }

  if (!sourceNode) {
    return normalizedOutput;
  }

  return [
    `Connected output from ${formatWidgetLabel(sourceNode)}:`,
    normalizedOutput,
  ].join('\n');
}

export function resolveNodeExecutionInput(nodeId, nodes, edges, options = {}) {
  if (typeof options.upstreamOutput === 'string') {
    const sourceNode = options.upstreamSourceNodeId
      ? nodes.find((node) => node.id === options.upstreamSourceNodeId) || null
      : null;
    return buildExecutionInput(sourceNode, options.upstreamOutput);
  }

  const incoming = getIncomingDataflowSource(nodeId, nodes, edges);
  if (!incoming) {
    return '';
  }

  return buildExecutionInput(incoming.node, getNodeOutputText(incoming.node));
}

export function augmentMessageWithInput(message, inputText) {
  const baseMessage = String(message || '').trim();
  const normalizedInput = String(inputText || '').trim();

  if (!normalizedInput) {
    return baseMessage;
  }

  return [
    baseMessage,
    '',
    'Connected input:',
    normalizedInput,
  ].filter(Boolean).join('\n');
}

export function buildPromptInvocation(promptData = {}, connectedTargetAgent = '') {
  const prompt = (promptData.prompt || '').trim();
  if (!prompt) {
    return '';
  }

  const targetAgent = getPromptTargetAgent(promptData, connectedTargetAgent);
  if (targetAgent === 'progenitor') {
    return prompt;
  }

  return [
    `Call agent ${targetAgent} for this task.`,
    'You are orchestrating from progenitor, so delegate the work and return the response here.',
    '',
    'Task:',
    prompt,
  ].join('\n');
}

export function generateWorkspaceThreadId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `thread_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`;
  }

  return `thread_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

export function buildExecutionContext(rootWidgetId, invocationSource, existingContext = null) {
  if (existingContext?.threadId) {
    return existingContext;
  }

  return {
    threadId: generateWorkspaceThreadId(),
    invocationType: 'automation',
    invocationSource,
    rootWidgetId,
  };
}

export function buildUserMessageInstruction(userData = {}, inputText = '') {
  const messageType = userData.messageType || DEFAULT_USER_MESSAGE_TYPE;
  const message = augmentMessageWithInput(userData.message, inputText);

  if (!message) {
    return { error: 'Enter a message before sending it.' };
  }

  if (messageType === 'notify_user') {
    const notificationTitle = String(userData.notificationTitle || '').trim();
    const level = String(userData.level || 'info').trim() || 'info';
    if (!notificationTitle) {
      return { error: 'Enter a notification title before sending it.' };
    }

    return {
      delivery: 'notify_user',
      level,
      previewTitle: notificationTitle,
      previewMessage: message,
      prompt: [
        'Use the `notify_user` tool exactly once with this exact payload.',
        'Do not call any other tools.',
        'After the tool call, reply with exactly: sent',
        '```json',
        JSON.stringify({
          request: message,
          tool_args: {
            title: notificationTitle,
            body: message,
            level,
          },
        }, null, 2),
        '```',
      ].join('\n'),
    };
  }

  if (messageType === 'ask_user') {
    return {
      delivery: 'ask_user',
      level: 'info',
      previewTitle: 'ask_user',
      previewMessage: message,
      prompt: [
        'Use the `ask_user` tool exactly once with this exact payload.',
        'Do not call any other tools.',
        'After the tool call, reply with exactly: sent',
        '```json',
        JSON.stringify({ request: message }, null, 2),
        '```',
      ].join('\n'),
    };
  }

  return {
    delivery: 'say_to_user',
    level: 'info',
    previewTitle: 'say_to_user',
    previewMessage: message,
    prompt: [
      'Use the `say_to_user` tool exactly once with this exact payload.',
      'Do not call any other tools.',
      'After the tool call, reply with exactly: sent',
      '```json',
      JSON.stringify({ request: message }, null, 2),
      '```',
    ].join('\n'),
  };
}

export function buildServerNodes(widgets, existingNodes) {
  const existingMap = new Map(existingNodes.map((node) => [node.id, node]));

  return widgets.map((widget, index) => {
    const existingNode = existingMap.get(widget.id);
    const nextNode = buildServerNode(widget, existingNode, index);

    if (existingNode && areWorkspaceNodesEquivalent(existingNode, nextNode)) {
      return existingNode;
    }

    return nextNode;
  });
}

export function buildServerNode(widget, existing, index = 0) {
  const type = widget.type;
  const size = defaultNodeSizes[type] || { width: 320, height: 200 };
  const column = index % 3;
  const row = Math.floor(index / 3);
  const position = existing?.position || { x: 80 + column * 360, y: 80 + row * 240 };

  return {
    id: widget.id,
    type,
    position,
    zIndex: existing?.zIndex,
    selected: existing?.selected,
    style: {
      width: existing?.style?.width || size.width,
      height: existing?.style?.height || size.height,
    },
    data: {
      ...mapWidgetToNodeData(widget, existing?.data),
    },
  };
}

export function mergeNodes(savedNodes, widgets) {
  const serverNodes = buildServerNodes(widgets, savedNodes);
  const serverIds = new Set(serverNodes.map((node) => node.id));
  const localNodes = savedNodes.filter((node) => !isServerNode(node) || !serverIds.has(node.id));
  const mergedNodes = [...localNodes, ...serverNodes];
  return haveSameNodeReferences(savedNodes, mergedNodes) ? savedNodes : mergedNodes;
}

// Server-managed widget cards (agents, MCP servers) are rebuilt from the live
// widget feed on every sync, so they cannot be deleted outright — removing one
// would unregister nothing and the card would reappear on the next refresh.
// Instead we remember dismissed card ids locally and filter them back out.
const DISMISSED_WIDGETS_KEY = 'progenitor:dismissedWidgetIds';

export function loadDismissedWidgetIds() {
  try {
    const raw = window.localStorage.getItem(DISMISSED_WIDGETS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed : []);
  } catch {
    return new Set();
  }
}

export function persistDismissedWidgetIds(idSet) {
  try {
    window.localStorage.setItem(DISMISSED_WIDGETS_KEY, JSON.stringify([...idSet]));
  } catch {
    // Storage is best-effort; ignore quota/availability failures.
  }
}
