/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  useEffect,
  useMemo,
} from 'react';
import { addEdge, applyEdgeChanges, applyNodeChanges } from '@xyflow/react';
import { useApi } from '../hooks/useApi';
import { loadWorkspaceState, saveWorkspaceState } from '../lib/indexedDb';

const WorkspaceGraphContext = createContext(null);
const WorkspaceActionsContext = createContext(null);
const WorkspaceMetaContext = createContext(null);

const SAVE_DEBOUNCE_MS = 500;
const SERVER_NODE_TYPES = new Set(['agent', 'mcp', 'schedule', 'event']);
const DEFAULT_EVENT_TYPE = 'kanban.card.created';
const DATAFLOW_SOURCE_TYPES = new Set(['event', 'prompt', 'code', 'user', 'schedule']);
const DATAFLOW_TARGET_TYPES = new Set(['prompt', 'code', 'user', 'schedule']);
const MAX_EVENT_LOG_ENTRIES = 25;
const DEFAULT_USER_MESSAGE_TYPE = 'say_to_user';
const EDGE_KIND_PROMPT_AGENT = 'prompt-agent';
const EDGE_KIND_DATAFLOW = 'dataflow';
const LEGACY_EDGE_KIND_EVENT_TRIGGER = 'event-trigger';

const defaultNodeSizes = {
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
    lastRunId: null,
    summaryState: '',
    lastOutput: '',
  }),
  code: () => ({
    language: 'javascript',
    content: '',
    runtimeProfile: '',
    originPromptId: null,
    lastRunId: null,
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
    currentRunId: null,
    summaryState: '',
    logs: [],
    widgetKind: 'agent',
  }),
  mcp: () => ({
    agentId: '',
    agentStatus: 'idle',
    currentRunId: null,
    summaryState: '',
    logs: [],
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

function generateId() {
  nodeIdCounter += 1;
  return `node_${Date.now()}_${nodeIdCounter}`;
}

function isServerNode(node) {
  return SERVER_NODE_TYPES.has(node.type) || node.data?.serverManaged;
}

function createArtefact(type, position, overrides = {}) {
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

function normalizeLogs(logs) {
  if (!Array.isArray(logs)) {
    return [];
  }
  return logs.map((entry) => ({
    time: entry.time || entry.timestamp || entry.created_at || entry.last_active || null,
    message: entry.message || entry.content || entry.summary || entry.description || String(entry),
    level: entry.level || 'info',
    source: entry.source || entry.path || 'system',
  }));
}

function buildEventOutputText(payload, fallback = '') {
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

function mapWidgetToNodeData(widget, existingData = {}) {
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
    currentRunId: widget.data?.current_run_id || widget.current_run_id || existingData.currentRunId || null,
    transport: widget.transport || widget.data?.transport || existingData.transport || '',
    updatedAt: timestamp,
  };
}

function getActionText(payload) {
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

function buildEventWaitingSummary(eventType = DEFAULT_EVENT_TYPE) {
  return `Waiting for ${eventType}`;
}

function dedupeNodeValues(values) {
  return [...new Set(
    values
      .map((value) => (typeof value === 'string' ? value.trim() : ''))
      .filter(Boolean)
  )];
}

function normalizeAgentName(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function isAgentNodeType(type) {
  return type === 'agent' || type === 'mcp';
}

function isDataflowSourceType(type) {
  return DATAFLOW_SOURCE_TYPES.has(type);
}

function isDataflowTargetType(type) {
  return DATAFLOW_TARGET_TYPES.has(type);
}

function getEdgeKind(edge) {
  return edge?.data?.kind || '';
}

function isPromptAgentEdge(edge) {
  return getEdgeKind(edge) === EDGE_KIND_PROMPT_AGENT;
}

function isDataflowEdge(edge) {
  const edgeKind = getEdgeKind(edge);
  return edgeKind === EDGE_KIND_DATAFLOW || edgeKind === LEGACY_EDGE_KIND_EVENT_TRIGGER;
}

function createDataflowEdge(sourceId, targetId) {
  return {
    id: createEdgeId(sourceId, targetId),
    source: sourceId,
    target: targetId,
    type: 'smoothstep',
    animated: true,
    data: { kind: EDGE_KIND_DATAFLOW },
  };
}

function createPromptAgentEdge(sourceId, targetId) {
  return {
    id: createEdgeId(sourceId, targetId),
    source: sourceId,
    target: targetId,
    type: 'smoothstep',
    animated: true,
    data: { kind: EDGE_KIND_PROMPT_AGENT },
  };
}

function createEdgeId(sourceId, targetId) {
  return `edge_${sourceId}_${targetId}`;
}

function formatWidgetLabel(node) {
  return node?.data?.title || typeLabels[node?.type] || node?.type || 'widget';
}

function appendEventLog(logs, message, time = new Date().toISOString(), level = 'info') {
  return [
    { time, message, level, source: 'workspace' },
    ...(Array.isArray(logs) ? logs : []),
  ].slice(0, MAX_EVENT_LOG_ENTRIES);
}

function buildManualEventPayload(node) {
  const triggeredAt = new Date().toISOString();
  return {
    event_type: node.data?.eventType || DEFAULT_EVENT_TYPE,
    trigger_source: 'manual',
    widget_id: node.id,
    widget_title: node.data?.title || 'Event',
    triggered_at: triggeredAt,
  };
}

function getViewportSignature(viewport = {}) {
  return `${viewport.x ?? 0}:${viewport.y ?? 0}:${viewport.zoom ?? 1}`;
}

function areStringArraysEqual(currentValues, nextValues) {
  return currentValues.length === nextValues.length
    && currentValues.every((value, index) => value === nextValues[index]);
}

function extractAgentOptions(nodes) {
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

function createPersistedGraph(nodes, edges) {
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

function areWorkspaceNodesEquivalent(currentNode, nextNode) {
  return currentNode.id === nextNode.id
    && currentNode.type === nextNode.type
    && currentNode.zIndex === nextNode.zIndex
    && currentNode.selected === nextNode.selected
    && currentNode.position?.x === nextNode.position?.x
    && currentNode.position?.y === nextNode.position?.y
    && JSON.stringify(currentNode.style || {}) === JSON.stringify(nextNode.style || {})
    && JSON.stringify(currentNode.data || {}) === JSON.stringify(nextNode.data || {});
}

function haveSameNodeReferences(currentNodes, nextNodes) {
  return currentNodes.length === nextNodes.length
    && currentNodes.every((node, index) => node === nextNodes[index]);
}

function getAgentNameFromNode(node) {
  return node?.data?.agentId || node?.data?.title || '';
}

function getOutgoingDataflowTargets(sourceNodeId, nodes, edges) {
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

function getPromptTargetAgent(promptData = {}, connectedTargetAgent = '') {
  return dedupeNodeValues([
    connectedTargetAgent,
    promptData.targetAgent,
    promptData.primaryAgent,
    ...(promptData.associatedAgents || []),
    'progenitor',
  ])[0] || 'progenitor';
}

function getConnectedAgentTarget(promptNodeId, nodes, edges) {
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

function getNodeOutputText(node) {
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

function getIncomingDataflowSource(targetNodeId, nodes, edges) {
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

function buildExecutionInput(sourceNode, outputText) {
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

function resolveNodeExecutionInput(nodeId, nodes, edges, options = {}) {
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

function augmentMessageWithInput(message, inputText) {
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

function buildPromptInvocation(promptData = {}, connectedTargetAgent = '') {
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

function buildUserMessageInstruction(userData = {}, inputText = '') {
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
        'Use the `notify_user` tool exactly once with these exact arguments.',
        'Do not call any other tools.',
        'After the tool call, reply with exactly: sent',
        '```json',
        JSON.stringify({
          title: notificationTitle,
          body: message,
          level,
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
        'Use the `ask_user` tool exactly once with these exact arguments.',
        'Do not call any other tools.',
        'After the tool call, reply with exactly: sent',
        '```json',
        JSON.stringify({ question: message }, null, 2),
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
      'Use the `say_to_user` tool exactly once with these exact arguments.',
      'Do not call any other tools.',
      'After the tool call, reply with exactly: sent',
      '```json',
      JSON.stringify({ message }, null, 2),
      '```',
    ].join('\n'),
  };
}

function buildServerNodes(widgets, existingNodes) {
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

function buildServerNode(widget, existing, index = 0) {
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

function mergeNodes(savedNodes, widgets) {
  const serverNodes = buildServerNodes(widgets, savedNodes);
  const serverIds = new Set(serverNodes.map((node) => node.id));
  const localNodes = savedNodes.filter((node) => !isServerNode(node) || !serverIds.has(node.id));
  const mergedNodes = [...localNodes, ...serverNodes];
  return haveSameNodeReferences(savedNodes, mergedNodes) ? savedNodes : mergedNodes;
}

export function WorkspaceProvider({ children }) {
  const {
    getWorkspace,
    getWidgets,
    getWidgetSchemas,
    updateWidget,
    createSchedule,
    createEvent,
    runAction,
    generateAction,
  } = useApi();
  const [nodes, setNodes] = useState([]);
  const [viewport, setViewportState] = useState({ x: 0, y: 0, zoom: 1 });
  const [viewportVersion, setViewportVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [widgetSchemas, setWidgetSchemas] = useState({});
  const [agentOptions, setAgentOptions] = useState(['progenitor']);
  const saveTimerRef = useRef(null);
  const patchTimersRef = useRef(new Map());
  const latestNodesRef = useRef([]);
  const latestEdgesRef = useRef([]);
  const latestViewportRef = useRef({ x: 0, y: 0, zoom: 1 });
  const lastSavedGraphSignatureRef = useRef('');
  const lastSavedViewportSignatureRef = useRef(getViewportSignature({ x: 0, y: 0, zoom: 1 }));
  const runNodeByTypeRef = useRef(null);
  const [edges, setEdges] = useState([]);

  const mergeWidgetNodes = useCallback((widgetPayload, nextViewport = null) => {
    const widgetList = widgetPayload?.items || widgetPayload?.widgets || widgetPayload || [];
    if (!Array.isArray(widgetList)) {
      return;
    }

    setNodes((currentNodes) => {
      const mergedNodes = mergeNodes(currentNodes, widgetList);
      latestNodesRef.current = mergedNodes;
      return mergedNodes;
    });

    if (nextViewport && getViewportSignature(latestViewportRef.current) !== getViewportSignature(nextViewport)) {
      latestViewportRef.current = nextViewport;
      setViewportState(nextViewport);
      setViewportVersion((currentVersion) => currentVersion + 1);
    }
  }, []);

  const refreshWorkspace = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const [savedState, workspacePayload, widgetsPayload, schemasPayload] = await Promise.all([
        loadWorkspaceState(),
        getWorkspace().catch(() => ({})),
        getWidgets(),
        getWidgetSchemas().catch(() => ({ schemas: [] })),
      ]);

      const savedNodes = savedState?.nodes || [];
      const savedEdges = savedState?.edges || [];
      const savedViewport = savedState?.viewport || workspacePayload?.viewport || { x: 0, y: 0, zoom: 1 };
      const widgetList = widgetsPayload?.items || widgetsPayload?.widgets || widgetsPayload || [];
      const schemaMap = Object.fromEntries(
        ((schemasPayload?.schemas) || []).map((schema) => [schema.type, schema])
      );
      const mergedNodes = mergeNodes(savedNodes, widgetList);
      const persistedGraph = createPersistedGraph(mergedNodes, savedEdges);

      latestNodesRef.current = mergedNodes;
      latestEdgesRef.current = savedEdges;
      latestViewportRef.current = savedViewport;
      lastSavedGraphSignatureRef.current = JSON.stringify(persistedGraph);
      lastSavedViewportSignatureRef.current = getViewportSignature(savedViewport);
      setWidgetSchemas(schemaMap);
      setNodes(mergedNodes);
      setEdges(savedEdges);
      setViewportState(savedViewport);
      setViewportVersion((currentVersion) => currentVersion + 1);
    } catch (loadError) {
      console.error('[WorkspaceContext] Failed to load workspace:', loadError);
      setError(loadError);
    } finally {
      setLoading(false);
    }
  }, [getWidgetSchemas, getWidgets, getWorkspace]);

  const getWidgetSchema = useCallback((type) => widgetSchemas[type] || null, [widgetSchemas]);
  const getWidgetTabs = useCallback((type) => widgetSchemas[type]?.tabs || [], [widgetSchemas]);
  const getWidgetDefaultTab = useCallback((type) => widgetSchemas[type]?.default_tab || null, [widgetSchemas]);

  const syncWidgets = useCallback((widgetPayload) => {
    mergeWidgetNodes(widgetPayload);
  }, [mergeWidgetNodes]);

  useEffect(() => {
    refreshWorkspace();
  }, [refreshWorkspace]);

  const persistWorkspace = useCallback(() => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = setTimeout(() => {
      const nextGraph = createPersistedGraph(latestNodesRef.current, latestEdgesRef.current);
      const nextGraphSignature = JSON.stringify(nextGraph);
      const nextViewport = latestViewportRef.current;
      const nextViewportSignature = getViewportSignature(nextViewport);

      if (
        lastSavedGraphSignatureRef.current === nextGraphSignature
        && lastSavedViewportSignatureRef.current === nextViewportSignature
      ) {
        return;
      }

      lastSavedGraphSignatureRef.current = nextGraphSignature;
      lastSavedViewportSignatureRef.current = nextViewportSignature;
      saveWorkspaceState({
        nodes: nextGraph.nodes,
        edges: nextGraph.edges,
        viewport: nextViewport,
      });
    }, SAVE_DEBOUNCE_MS);
  }, []);

  const captureViewport = useCallback((nextViewport) => {
    if (getViewportSignature(latestViewportRef.current) === getViewportSignature(nextViewport)) {
      return;
    }

    latestViewportRef.current = nextViewport;
    if (!loading) {
      persistWorkspace();
    }
  }, [loading, persistWorkspace]);

  useEffect(() => {
    latestNodesRef.current = nodes;
    const nextAgentOptions = extractAgentOptions(nodes);
    setAgentOptions((currentValues) => (
      areStringArraysEqual(currentValues, nextAgentOptions) ? currentValues : nextAgentOptions
    ));
    if (!loading) {
      persistWorkspace();
    }
  }, [nodes, loading, persistWorkspace]);

  useEffect(() => {
    latestEdgesRef.current = edges;
    if (!loading) {
      persistWorkspace();
    }
  }, [edges, loading, persistWorkspace]);

  useEffect(() => () => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
    }
    patchTimersRef.current.forEach((timerId) => clearTimeout(timerId));
  }, []);

  const queueWidgetPatch = useCallback((nodeId, updates) => {
    const existing = patchTimersRef.current.get(nodeId);
    if (existing) {
      clearTimeout(existing);
    }

    const timerId = setTimeout(async () => {
      patchTimersRef.current.delete(nodeId);
      try {
        await updateWidget(nodeId, updates);
      } catch (patchError) {
        console.warn(`[WorkspaceContext] Failed to patch widget ${nodeId}:`, patchError);
      }
    }, SAVE_DEBOUNCE_MS);

    patchTimersRef.current.set(nodeId, timerId);
  }, [updateWidget]);

  const onNodesChange = useCallback((changes) => {
    setNodes((currentNodes) => applyNodeChanges(changes, currentNodes));
  }, []);

  const onEdgesChange = useCallback((changes) => {
    setEdges((currentEdges) => applyEdgeChanges(changes, currentEdges));
  }, []);

  const addNode = useCallback((type, position, overrides = {}) => {
    const newNode = createArtefact(type, position, overrides);
    if (type === 'schedule' && !newNode.data.serverManaged) {
      newNode.style = {
        ...newNode.style,
        width: Math.max(defaultNodeSizes.schedule.width, 480),
        height: Math.max(defaultNodeSizes.schedule.height, 420),
      };
      newNode.data = {
        ...newNode.data,
        title: overrides.title || 'New Schedule',
        expanded: overrides.expanded ?? true,
        isUnsaved: true,
      };
    }
    if (type === 'event' && !newNode.data.serverManaged) {
      newNode.style = {
        ...newNode.style,
        width: Math.max(defaultNodeSizes.event.width, 480),
        height: Math.max(defaultNodeSizes.event.height, 400),
      };
      newNode.data = {
        ...newNode.data,
        title: overrides.title || 'New Event',
        expanded: overrides.expanded ?? true,
        enabled: overrides.enabled ?? true,
        isUnsaved: true,
        summaryState: overrides.summaryState || buildEventWaitingSummary(overrides.eventType || newNode.data.eventType),
      };
    }
    setNodes((currentNodes) => [...currentNodes, newNode]);
    return newNode;
  }, []);

  const addPromptFromLibrary = useCallback((prompt, position = null) => {
    const baseIndex = latestNodesRef.current.length;
    const nextPosition = position || {
      x: 120 + (baseIndex % 3) * 340,
      y: 100 + Math.floor(baseIndex / 3) * 220,
    };
    return addNode('prompt', nextPosition, {
      title: prompt.filename.replace(/\.md$/i, ''),
      prompt: prompt.content || '',
      role: prompt.filename.toLowerCase().includes('system') ? 'system' : '',
      sourcePath: prompt.path,
      primaryAgent: prompt.primary_agent || '',
      associatedAgents: prompt.associated_agents || [],
      targetAgent: prompt.primary_agent || prompt.associated_agents?.[0] || 'progenitor',
      tags: [...(prompt.associated_agents || [])],
    });
  }, [addNode]);

  const upsertNode = useCallback((nextNode) => {
    setNodes((currentNodes) => {
      const hasNode = currentNodes.some((node) => node.id === nextNode.id);
      if (!hasNode) {
        return [...currentNodes, nextNode];
      }
      return currentNodes.map((node) => (node.id === nextNode.id ? nextNode : node));
    });
  }, []);

  const updateNodeData = useCallback((nodeId, dataUpdates) => {
    setNodes((currentNodes) =>
      currentNodes.map((node) => {
        if (node.id !== nodeId) {
          return node;
        }

        let nextDataUpdates = dataUpdates;
        if (
          node.type === 'event'
          && Object.prototype.hasOwnProperty.call(dataUpdates, 'eventType')
          && dataUpdates.summaryState === undefined
        ) {
          const currentEventType = node.data?.eventType || DEFAULT_EVENT_TYPE;
          const nextEventType = dataUpdates.eventType || currentEventType;
          const currentSummary = node.data?.summaryState || '';
          if (!currentSummary || currentSummary === buildEventWaitingSummary(currentEventType)) {
            nextDataUpdates = {
              ...dataUpdates,
              summaryState: buildEventWaitingSummary(nextEventType),
            };
          }
        }

        const updatedNode = {
          ...node,
          data: {
            ...node.data,
            ...nextDataUpdates,
            updatedAt: new Date().toISOString(),
          },
        };

        if (node.type === 'schedule' && node.data?.serverManaged) {
          queueWidgetPatch(nodeId, {
            name: updatedNode.data.title,
            instruction: updatedNode.data.instruction,
            enabled: updatedNode.data.enabled,
            schedule_type: updatedNode.data.scheduleType,
            expression: updatedNode.data.expression,
            timezone: updatedNode.data.timezone,
          });
        }

        if (node.type === 'event' && node.data?.serverManaged) {
          queueWidgetPatch(nodeId, {
            name: updatedNode.data.title,
            enabled: updatedNode.data.enabled,
            event_type: updatedNode.data.eventType,
          });
        }

        return updatedNode;
      })
    );
  }, [queueWidgetPatch]);

  const connectPromptToAgent = useCallback((promptNodeId, targetAgent) => {
    const promptNode = latestNodesRef.current.find((node) => node.id === promptNodeId && node.type === 'prompt');
    if (!promptNode) {
      return;
    }

    const normalizedTarget = normalizeAgentName(targetAgent);
    const targetNode = latestNodesRef.current.find((node) => (
      isAgentNodeType(node.type)
      && normalizeAgentName(getAgentNameFromNode(node)) === normalizedTarget
    ));

    setEdges((currentEdges) => {
      const withoutPromptAgentEdges = currentEdges.filter(
        (edge) => !(edge.source === promptNodeId && isPromptAgentEdge(edge))
      );
      if (!targetNode || normalizedTarget === 'progenitor') {
        latestEdgesRef.current = withoutPromptAgentEdges;
        return withoutPromptAgentEdges;
      }

      const nextEdges = addEdge(
        createPromptAgentEdge(promptNodeId, targetNode.id),
        withoutPromptAgentEdges
      );
      latestEdgesRef.current = nextEdges;
      return nextEdges;
    });

    updateNodeData(promptNodeId, {
      targetAgent: targetNode ? getAgentNameFromNode(targetNode) : 'progenitor',
    });
  }, [updateNodeData]);

  const connectDataflowNodes = useCallback((sourceNodeId, targetNodeId) => {
    const sourceNode = latestNodesRef.current.find((node) => node.id === sourceNodeId);
    const targetNode = latestNodesRef.current.find((node) => node.id === targetNodeId);
    if (
      !sourceNode
      || !targetNode
      || sourceNodeId === targetNodeId
      || !isDataflowSourceType(sourceNode.type)
      || !isDataflowTargetType(targetNode.type)
    ) {
      return;
    }

    setEdges((currentEdges) => {
      const nextEdgesBase = currentEdges.filter(
        (edge) => !(edge.target === targetNodeId && isDataflowEdge(edge))
      );
      const nextEdges = addEdge(
        createDataflowEdge(sourceNodeId, targetNodeId),
        nextEdgesBase
      );
      latestEdgesRef.current = nextEdges;
      return nextEdges;
    });
  }, []);

  const removeEdges = useCallback((edgeIds) => {
    if (!Array.isArray(edgeIds) || edgeIds.length === 0) {
      return;
    }

    const idSet = new Set(edgeIds);
    const currentEdges = latestEdgesRef.current;
    const removedEdges = currentEdges.filter((edge) => idSet.has(edge.id));
    if (removedEdges.length === 0) {
      return;
    }

    const nextEdges = currentEdges.filter((edge) => !idSet.has(edge.id));
    latestEdgesRef.current = nextEdges;
    setEdges(nextEdges);

    const promptIdsToReset = new Set();
    removedEdges.forEach((edge) => {
      const sourceNode = latestNodesRef.current.find((node) => node.id === edge.source);
      const targetNode = latestNodesRef.current.find((node) => node.id === edge.target);
      if (sourceNode?.type === 'prompt' && isPromptAgentEdge(edge) && isAgentNodeType(targetNode?.type)) {
        const stillConnected = nextEdges.some((nextEdge) => {
          if (nextEdge.source !== edge.source || !isPromptAgentEdge(nextEdge)) {
            return false;
          }
          const nextTarget = latestNodesRef.current.find((node) => node.id === nextEdge.target);
          return isAgentNodeType(nextTarget?.type);
        });

        if (!stillConnected) {
          promptIdsToReset.add(edge.source);
        }
      }
    });

    promptIdsToReset.forEach((promptId) => {
      updateNodeData(promptId, { targetAgent: 'progenitor' });
    });
  }, [updateNodeData]);

  const onConnect = useCallback((connection) => {
    const sourceNode = latestNodesRef.current.find((node) => node.id === connection.source);
    const targetNode = latestNodesRef.current.find((node) => node.id === connection.target);
    if (!sourceNode || !targetNode || sourceNode.id === targetNode.id) {
      return;
    }

    if (sourceNode.type === 'prompt' && isAgentNodeType(targetNode.type)) {
      connectPromptToAgent(sourceNode.id, getAgentNameFromNode(targetNode));
      return;
    }

    if (isDataflowSourceType(sourceNode.type) && isDataflowTargetType(targetNode.type)) {
      connectDataflowNodes(sourceNode.id, targetNode.id);
    }
  }, [connectDataflowNodes, connectPromptToAgent]);

  const saveScheduleNode = useCallback(async (nodeId) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node || node.type !== 'schedule' || node.data?.serverManaged) {
      return;
    }

    const title = (node.data.title || '').trim();
    const instruction = (node.data.instruction || '').trim();
    const expression = (node.data.expression || '').trim();

    if (!title || !instruction || !expression) {
      updateNodeData(nodeId, {
        saveError: 'Name, task, and schedule expression are required before saving.',
      });
      return;
    }

    setNodes((currentNodes) => currentNodes.map((entry) => (
      entry.id === nodeId
        ? {
          ...entry,
          data: {
            ...entry.data,
            isSaving: true,
            saveError: null,
            updatedAt: new Date().toISOString(),
          },
        }
        : entry
    )));

    try {
      const widget = await createSchedule({
        name: title,
        instruction,
        schedule_type: node.data.scheduleType,
        expression,
        timezone: node.data.timezone,
        enabled: node.data.enabled,
      });

      setNodes((currentNodes) => {
        const existingNode = currentNodes.find((entry) => entry.id === nodeId) || node;
        const remainingNodes = currentNodes.filter((entry) => entry.id !== nodeId);
        const serverNode = buildServerNode(
          widget,
          {
            ...existingNode,
            id: widget.id,
            data: {
              ...existingNode.data,
              expanded: existingNode.data.expanded,
            },
          },
          remainingNodes.length,
        );
        return [...remainingNodes, serverNode];
      });
    } catch (saveError) {
      setNodes((currentNodes) => currentNodes.map((entry) => (
        entry.id === nodeId
          ? {
            ...entry,
            data: {
              ...entry.data,
              isSaving: false,
              saveError: saveError.message,
              updatedAt: new Date().toISOString(),
            },
          }
          : entry
      )));
    }
  }, [createSchedule, updateNodeData]);

  const saveEventNode = useCallback(async (nodeId) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node || node.type !== 'event' || node.data?.serverManaged) {
      return;
    }

    const title = (node.data.title || '').trim();
    const eventType = (node.data.eventType || '').trim();

    if (!title || !eventType) {
      updateNodeData(nodeId, {
        saveError: 'Name and event type are required before saving.',
      });
      return;
    }

    setNodes((currentNodes) => currentNodes.map((entry) => (
      entry.id === nodeId
        ? {
          ...entry,
          data: {
            ...entry.data,
            isSaving: true,
            saveError: null,
            updatedAt: new Date().toISOString(),
          },
        }
        : entry
    )));

    try {
      const widget = await createEvent({
        name: title,
        event_type: eventType,
        enabled: node.data.enabled,
      });

      setNodes((currentNodes) => {
        const existingNode = currentNodes.find((entry) => entry.id === nodeId) || node;
        const remainingNodes = currentNodes.filter((entry) => entry.id !== nodeId);
        const serverNode = buildServerNode(
          widget,
          {
            ...existingNode,
            id: widget.id,
            data: {
              ...existingNode.data,
              expanded: existingNode.data.expanded,
            },
          },
          remainingNodes.length,
        );
        return [...remainingNodes, serverNode];
      });
    } catch (saveError) {
      setNodes((currentNodes) => currentNodes.map((entry) => (
        entry.id === nodeId
          ? {
            ...entry,
            data: {
              ...entry.data,
              isSaving: false,
              saveError: saveError.message,
              updatedAt: new Date().toISOString(),
            },
          }
          : entry
      )));
    }
  }, [createEvent, updateNodeData]);

  const removeNodes = useCallback((nodeIds) => {
    const idSet = new Set(nodeIds);
    setNodes((currentNodes) =>
      currentNodes.filter((node) => !idSet.has(node.id) || node.data?.serverManaged)
    );
    setEdges((currentEdges) =>
      currentEdges.filter((edge) => !idSet.has(edge.source) && !idSet.has(edge.target))
    );
  }, []);

  const duplicateNode = useCallback((nodeId) => {
    setNodes((currentNodes) => {
      const source = currentNodes.find((node) => node.id === nodeId);
      if (!source || source.data?.serverManaged) {
        return currentNodes;
      }

      const newNode = {
        ...source,
        id: generateId(),
        position: {
          x: source.position.x + 40,
          y: source.position.y + 40,
        },
        data: {
          ...source.data,
          title: `${source.data.title} (copy)`,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        },
      };

      return [...currentNodes, newNode];
    });
  }, []);

  const bringToFront = useCallback((nodeId) => {
    setNodes((currentNodes) => {
      const maxZ = Math.max(...currentNodes.map((node) => node.zIndex || 0), 0);
      return currentNodes.map((node) =>
        node.id === nodeId ? { ...node, zIndex: maxZ + 1 } : node
      );
    });
  }, []);

  const sendToBack = useCallback((nodeId) => {
    setNodes((currentNodes) => {
      const minZ = Math.min(...currentNodes.map((node) => node.zIndex || 0), 0);
      return currentNodes.map((node) =>
        node.id === nodeId ? { ...node, zIndex: minZ - 1 } : node
      );
    });
  }, []);

  const toggleExpanded = useCallback((nodeId) => {
    setNodes((currentNodes) =>
      currentNodes.map((node) => {
        if (node.id !== nodeId) {
          return node;
        }

        const expanded = !node.data.expanded;
        const defaultSize = defaultNodeSizes[node.type] || { width: 300, height: 200 };
        const expandedSize = {
          width: Math.max(defaultSize.width, 480),
          height: Math.max(defaultSize.height, 400),
        };

        return {
          ...node,
          style: {
            ...node.style,
            width: expanded ? expandedSize.width : defaultSize.width,
            height: expanded ? expandedSize.height : defaultSize.height,
          },
          data: {
            ...node.data,
            expanded,
            updatedAt: new Date().toISOString(),
          },
        };
      })
    );
  }, []);

  const runConnectedTargets = useCallback(async (sourceNodeId, options = {}) => {
    const sourceNode = latestNodesRef.current.find((entry) => entry.id === sourceNodeId);
    if (!sourceNode) {
      return [];
    }

    const outputText = typeof options.outputText === 'string'
      ? options.outputText
      : getNodeOutputText(sourceNode);

    const targets = getOutgoingDataflowTargets(sourceNodeId, latestNodesRef.current, latestEdgesRef.current);
    if (targets.length === 0) {
      return [];
    }

    const visitedNodeIds = new Set(options.visitedNodeIds || []);
    visitedNodeIds.add(sourceNodeId);

    const results = [];
    for (const targetNode of targets) {
      if (visitedNodeIds.has(targetNode.id)) {
        results.push({
          ok: false,
          error: `Cycle detected for ${formatWidgetLabel(targetNode)}.`,
        });
        continue;
      }

      const runNodeByType = runNodeByTypeRef.current;
      if (!runNodeByType) {
        break;
      }

      results.push(await runNodeByType(targetNode.id, {
        upstreamOutput: outputText,
        upstreamSourceNodeId: sourceNodeId,
        visitedNodeIds: [...visitedNodeIds],
      }));
    }

    return results;
  }, []);

  const runPromptNode = useCallback(async (nodeId, options = {}) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node) {
      return { ok: false, error: 'Prompt node not found.' };
    }

    const visitedNodeIds = new Set(options.visitedNodeIds || []);
    if (visitedNodeIds.has(nodeId)) {
      return { ok: false, error: `Cycle detected for ${formatWidgetLabel(node)}.` };
    }

    visitedNodeIds.add(nodeId);

    const connectedTarget = getConnectedAgentTarget(nodeId, latestNodesRef.current, latestEdgesRef.current);
    const prompt = buildPromptInvocation(node.data, connectedTarget?.agentName);
    const executionInput = resolveNodeExecutionInput(
      nodeId,
      latestNodesRef.current,
      latestEdgesRef.current,
      options
    );
    if (!prompt) {
      updateNodeData(nodeId, {
        status: 'failed',
        summaryState: 'Enter a prompt before running it.',
      });
      return { ok: false, error: 'Enter a prompt before running it.' };
    }

    updateNodeData(nodeId, { status: 'running', summaryState: '' });

    try {
      const result = await runAction({
        widget_id: nodeId,
        action_type: 'run_prompt',
        prompt,
        input: executionInput || undefined,
        role: node.data.role,
        title: node.data.title,
      });
      const outputText = getActionText(result);
      updateNodeData(nodeId, {
        status: 'success',
        targetAgent: getPromptTargetAgent(node.data, connectedTarget?.agentName),
        lastRunId: result.run_id || result.request_id || node.data.lastRunId,
        summaryState: outputText,
        lastOutput: outputText,
      });
      const downstreamResults = await runConnectedTargets(nodeId, {
        visitedNodeIds: [...visitedNodeIds],
        outputText,
      });
      return {
        ok: true,
        message: outputText || 'Prompt completed.',
        output: outputText,
        downstreamResults,
      };
    } catch (runError) {
      updateNodeData(nodeId, {
        status: 'failed',
        summaryState: runError.message,
      });
      return { ok: false, error: runError.message };
    }
  }, [runAction, runConnectedTargets, updateNodeData]);

  const generateFromPromptNode = useCallback(async (nodeId) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node) {
      return;
    }

    const connectedTarget = getConnectedAgentTarget(nodeId, latestNodesRef.current, latestEdgesRef.current);
    const prompt = buildPromptInvocation(node.data, connectedTarget?.agentName);
    const executionInput = resolveNodeExecutionInput(
      nodeId,
      latestNodesRef.current,
      latestEdgesRef.current
    );
    if (!prompt) {
      updateNodeData(nodeId, {
        status: 'failed',
        summaryState: 'Enter a prompt before generating code.',
      });
      return;
    }

    updateNodeData(nodeId, { status: 'running', summaryState: '' });

    try {
      const result = await generateAction({
        widget_id: nodeId,
        action_type: 'generate_code',
        prompt,
        input: executionInput || undefined,
        role: node.data.role,
        title: node.data.title,
      });
      const generatedContent = getActionText(result);
      const codeNode = createArtefact('code', {
        x: node.position.x + 360,
        y: node.position.y,
      }, {
        title: `${node.data.title} Output`,
        content: generatedContent || '// No code returned',
        originPromptId: nodeId,
      });

      upsertNode(codeNode);
      updateNodeData(nodeId, {
        status: 'success',
        targetAgent: getPromptTargetAgent(node.data, connectedTarget?.agentName),
        summaryState: generatedContent ? 'Code generated via backend.' : 'Generation completed.',
        linkedOutputIds: [...(node.data.linkedOutputIds || []), codeNode.id],
      });
    } catch (generateError) {
      updateNodeData(nodeId, {
        status: 'failed',
        summaryState: generateError.message,
      });
    }
  }, [generateAction, updateNodeData, upsertNode]);

  const runCodeNode = useCallback(async (nodeId, options = {}) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node) {
      return { ok: false, error: 'Code node not found.' };
    }

    const visitedNodeIds = new Set(options.visitedNodeIds || []);
    if (visitedNodeIds.has(nodeId)) {
      return { ok: false, error: `Cycle detected for ${formatWidgetLabel(node)}.` };
    }

    visitedNodeIds.add(nodeId);
    const executionInput = resolveNodeExecutionInput(
      nodeId,
      latestNodesRef.current,
      latestEdgesRef.current,
      options
    );

    updateNodeData(nodeId, { status: 'running' });

    try {
      const result = await runAction({
        widget_id: nodeId,
        action_type: 'run_code',
        language: node.data.language,
        code: node.data.content,
        input: executionInput || undefined,
        title: node.data.title,
      });
      const outputText = getActionText(result);
      updateNodeData(nodeId, {
        status: 'completed',
        lastRunId: result.run_id || result.request_id || node.data.lastRunId,
        outputRefs: [outputText].filter(Boolean),
        lastOutput: outputText,
      });
      const downstreamResults = await runConnectedTargets(nodeId, {
        visitedNodeIds: [...visitedNodeIds],
        outputText,
      });
      return {
        ok: true,
        message: outputText || 'Code executed.',
        output: outputText,
        downstreamResults,
      };
    } catch (runError) {
      updateNodeData(nodeId, {
        status: 'error',
        outputRefs: [runError.message],
      });
      return { ok: false, error: runError.message };
    }
  }, [runAction, runConnectedTargets, updateNodeData]);

  const runUserNode = useCallback(async (nodeId, options = {}) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node || node.type !== 'user') {
      return { ok: false, error: 'User node not found.' };
    }

    const visitedNodeIds = new Set(options.visitedNodeIds || []);
    if (visitedNodeIds.has(nodeId)) {
      return { ok: false, error: `Cycle detected for ${formatWidgetLabel(node)}.` };
    }

    visitedNodeIds.add(nodeId);
    const executionInput = resolveNodeExecutionInput(
      nodeId,
      latestNodesRef.current,
      latestEdgesRef.current,
      options
    );
    const instruction = buildUserMessageInstruction(node.data, executionInput);
    if (instruction.error) {
      updateNodeData(nodeId, {
        status: 'failed',
        summaryState: instruction.error,
      });
      return { ok: false, error: instruction.error };
    }

    updateNodeData(nodeId, { status: 'running', summaryState: '' });

    try {
      const result = await runAction({
        widget_id: nodeId,
        prompt: instruction.prompt,
        broadcast_response: false,
      });
      const responseText = getActionText(result);
      const summary = responseText && responseText !== 'sent'
        ? `Sent via ${instruction.delivery}. ${responseText}`
        : `Sent via ${instruction.delivery}.`;

      updateNodeData(nodeId, {
        status: 'success',
        lastSentAt: new Date().toISOString(),
        summaryState: summary,
        lastOutput: instruction.previewMessage,
      });
      const downstreamResults = await runConnectedTargets(nodeId, {
        visitedNodeIds: [...visitedNodeIds],
        outputText: instruction.previewMessage,
      });

      return {
        ok: true,
        delivery: instruction.delivery,
        previewTitle: instruction.previewTitle,
        previewMessage: instruction.previewMessage,
        level: instruction.level,
        output: instruction.previewMessage,
        downstreamResults,
      };
    } catch (runError) {
      updateNodeData(nodeId, {
        status: 'failed',
        summaryState: runError.message,
      });
      return { ok: false, error: runError.message };
    }
  }, [runAction, runConnectedTargets, updateNodeData]);

  const runScheduleNode = useCallback(async (nodeId, options = {}) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node || node.type !== 'schedule') {
      return { ok: false, error: 'Schedule node not found.' };
    }

    const visitedNodeIds = new Set(options.visitedNodeIds || []);
    if (visitedNodeIds.has(nodeId)) {
      return { ok: false, error: `Cycle detected for ${formatWidgetLabel(node)}.` };
    }

    visitedNodeIds.add(nodeId);
    if (!node.data?.serverManaged) {
      updateNodeData(nodeId, {
        lastRunStatus: 'error',
        summaryState: 'Save the schedule before running it.',
      });
      return { ok: false, error: 'Save the schedule before running it.' };
    }

    const executionInput = resolveNodeExecutionInput(
      nodeId,
      latestNodesRef.current,
      latestEdgesRef.current,
      options
    );
    updateNodeData(nodeId, { lastRunStatus: 'running' });
    try {
      const result = await runAction({
        widget_id: nodeId,
        action_type: 'run_schedule',
        input: executionInput || undefined,
      });
      const outputText = getActionText(result);
      updateNodeData(nodeId, {
        lastRunStatus: 'completed',
        lastRunAt: new Date().toISOString(),
        summaryState: outputText,
        lastOutput: outputText,
      });
      const downstreamResults = await runConnectedTargets(nodeId, {
        visitedNodeIds: [...visitedNodeIds],
        outputText,
      });
      return {
        ok: true,
        message: outputText || 'Schedule completed.',
        output: outputText,
        downstreamResults,
      };
    } catch (runError) {
      updateNodeData(nodeId, {
        lastRunStatus: 'error',
        summaryState: runError.message,
      });
      return { ok: false, error: runError.message };
    }
  }, [runAction, runConnectedTargets, updateNodeData]);

  const runEventNode = useCallback(async (nodeId) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node || node.type !== 'event') {
      return { ok: false, error: 'Event node not found.' };
    }

    const eventPayload = buildManualEventPayload(node);
    const eventOutput = buildEventOutputText(eventPayload);
    const linkedTargets = getOutgoingDataflowTargets(nodeId, latestNodesRef.current, latestEdgesRef.current);
    const initialSummary = linkedTargets.length > 0
      ? `Running ${linkedTargets.length} linked widget${linkedTargets.length === 1 ? '' : 's'}.`
      : `Manual trigger fired for ${eventPayload.event_type}.`;
    const initialLogs = appendEventLog(node.data.logs, initialSummary, eventPayload.triggered_at);

    updateNodeData(nodeId, {
      triggerCount: (node.data.triggerCount || 0) + 1,
      lastTriggeredAt: eventPayload.triggered_at,
      lastPayload: eventPayload,
      lastRunStatus: linkedTargets.length > 0 ? 'running' : 'completed',
      summaryState: initialSummary,
      logs: initialLogs,
      lastOutput: eventOutput,
    });

    if (linkedTargets.length === 0) {
      return { ok: true, triggered: 0, output: eventOutput };
    }

    const results = await runConnectedTargets(nodeId, {
      visitedNodeIds: [nodeId],
      outputText: eventOutput,
    });

    const successfulCount = results.filter((result) => result?.ok).length;
    const failedTargets = linkedTargets.filter((_, index) => !results[index]?.ok);
    const targetLabel = linkedTargets.map((targetNode) => formatWidgetLabel(targetNode)).join(', ');
    const failedLabel = failedTargets.map((targetNode) => formatWidgetLabel(targetNode)).join(', ');
    const summary = failedTargets.length > 0
      ? `Manual trigger ran ${successfulCount}/${linkedTargets.length} linked widgets. Failed: ${failedLabel}.`
      : `Manual trigger ran ${linkedTargets.length} linked widget${linkedTargets.length === 1 ? '' : 's'}: ${targetLabel}.`;

    updateNodeData(nodeId, {
      lastRunStatus: failedTargets.length > 0 ? 'error' : 'completed',
      summaryState: summary,
      logs: appendEventLog(
        initialLogs,
        summary,
        new Date().toISOString(),
        failedTargets.length > 0 ? 'error' : 'info'
      ),
      lastOutput: eventOutput,
    });

    return {
      ok: failedTargets.length === 0,
      triggered: linkedTargets.length,
      successfulCount,
      failedCount: failedTargets.length,
      output: eventOutput,
    };
  }, [runConnectedTargets, updateNodeData]);

  useEffect(() => {
    runNodeByTypeRef.current = async (nodeId, options = {}) => {
      const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
      if (!node) {
        return { ok: false, error: 'Widget not found.' };
      }

      if (node.type === 'prompt') {
        return runPromptNode(nodeId, options);
      }
      if (node.type === 'code') {
        return runCodeNode(nodeId, options);
      }
      if (node.type === 'user') {
        return runUserNode(nodeId, options);
      }
      if (node.type === 'schedule') {
        return runScheduleNode(nodeId, options);
      }

      return {
        ok: false,
        error: `Unsupported linked widget type: ${node.type}`,
      };
    };

    return () => {
      runNodeByTypeRef.current = null;
    };
  }, [runCodeNode, runPromptNode, runScheduleNode, runUserNode]);

  const graphValue = useMemo(() => ({
    nodes,
    edges,
    viewport,
    viewportVersion,
    loading,
    error,
  }), [nodes, edges, viewport, viewportVersion, loading, error]);

  const actionsValue = useMemo(() => ({
    captureViewport,
    onNodesChange,
    onEdgesChange,
    onConnect,
    addNode,
    addPromptFromLibrary,
    updateNodeData,
    connectPromptToAgent,
    removeNodes,
    removeEdges,
    duplicateNode,
    bringToFront,
    sendToBack,
    toggleExpanded,
    refreshWorkspace,
    syncWidgets,
    runPromptNode,
    generateFromPromptNode,
    runCodeNode,
    runUserNode,
    runScheduleNode,
    saveScheduleNode,
    saveEventNode,
    runEventNode,
  }), [
    captureViewport,
    onNodesChange,
    onEdgesChange,
    onConnect,
    addNode,
    addPromptFromLibrary,
    updateNodeData,
    connectPromptToAgent,
    removeNodes,
    removeEdges,
    duplicateNode,
    bringToFront,
    sendToBack,
    toggleExpanded,
    refreshWorkspace,
    syncWidgets,
    runPromptNode,
    generateFromPromptNode,
    runCodeNode,
    runUserNode,
    runScheduleNode,
    saveScheduleNode,
    saveEventNode,
    runEventNode,
  ]);

  const metaValue = useMemo(() => ({
    agentOptions,
    getWidgetSchema,
    getWidgetTabs,
    getWidgetDefaultTab,
  }), [agentOptions, getWidgetSchema, getWidgetTabs, getWidgetDefaultTab]);

  return (
    <WorkspaceGraphContext.Provider value={graphValue}>
      <WorkspaceActionsContext.Provider value={actionsValue}>
        <WorkspaceMetaContext.Provider value={metaValue}>
          {children}
        </WorkspaceMetaContext.Provider>
      </WorkspaceActionsContext.Provider>
    </WorkspaceGraphContext.Provider>
  );
}

export function useWorkspaceGraph() {
  const context = useContext(WorkspaceGraphContext);
  if (!context) {
    throw new Error('useWorkspaceGraph must be used within a WorkspaceProvider');
  }
  return context;
}

export function useWorkspaceActions() {
  const context = useContext(WorkspaceActionsContext);
  if (!context) {
    throw new Error('useWorkspaceActions must be used within a WorkspaceProvider');
  }
  return context;
}

export function useWorkspaceMeta() {
  const context = useContext(WorkspaceMetaContext);
  if (!context) {
    throw new Error('useWorkspaceMeta must be used within a WorkspaceProvider');
  }
  return context;
}
