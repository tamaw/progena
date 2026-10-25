import { cloneValue } from './values';
import { nodeOutputPorts, nodeInputPorts } from './schema';

export function canvasMenuAnchor(event, reactFlow) {
  return {
    screenPosition: { x: event.clientX, y: event.clientY },
    flowPosition: reactFlow.screenToFlowPosition({ x: event.clientX, y: event.clientY }),
  };
}

// Each node kind remembers two independent sizes: the compact "data" view
// (just a read-only preview) and the "settings" view (the full editable
// form). Switching views resizes the node to whichever one the user last
// left it at, instead of forcing one fixed rectangle for both.
export const DEFAULT_NODE_SIZES = {
  event: {
    data: { width: 320, height: 220 },
    settings: { width: 340, height: 260 },
  },
  agent: {
    data: { width: 340, height: 160 },
    settings: { width: 440, height: 320 },
  },
  routine: {
    data: { width: 340, height: 160 },
    settings: { width: 400, height: 260 },
  },
  data: {
    data: { width: 320, height: 140 },
    settings: { width: 380, height: 280 },
  },
  extract: {
    data: { width: 340, height: 160 },
    settings: { width: 440, height: 320 },
  },
  format: {
    data: { width: 340, height: 160 },
    settings: { width: 440, height: 280 },
  },
  condition: {
    data: { width: 300, height: 130 },
    settings: { width: 380, height: 220 },
  },
  approval: {
    data: { width: 320, height: 130 },
    settings: { width: 400, height: 240 },
  },
  document_read: {
    data: { width: 320, height: 140 },
    settings: { width: 380, height: 240 },
  },
  document_query: {
    data: { width: 320, height: 140 },
    settings: { width: 420, height: 300 },
  },
  document_write: {
    data: { width: 320, height: 140 },
    settings: { width: 380, height: 280 },
  },
  document_create: {
    data: { width: 320, height: 140 },
    settings: { width: 380, height: 280 },
  },
  document_delete: {
    data: { width: 320, height: 140 },
    settings: { width: 380, height: 240 },
  },
  unpack: {
    data: { width: 280, height: 120 },
    settings: { width: 340, height: 240 },
  },
  cron: {
    data: { width: 320, height: 220 },
    settings: { width: 380, height: 320 },
  },
  manual: {
    data: { width: 320, height: 160 },
    settings: { width: 380, height: 280 },
  },
};

// Workflow nodes are grouped by what they do, including direct document
// operations. No capability-specific kinds.
export const NODE_MENU_CATEGORIES = [
  {
    id: 'triggers',
    label: 'Triggers',
    items: [
      { id: 'event', kind: 'event', label: 'Event', icon: 'E' },
      { id: 'cron', kind: 'cron', label: 'Cron', icon: 'C' },
      { id: 'manual', kind: 'manual', label: 'Manual', icon: 'M' },
    ],
  },
  {
    id: 'agents',
    label: 'Agents',
    items: [
      { id: 'agent', kind: 'agent', label: 'Agent', icon: 'A' },
      { id: 'routine', kind: 'routine', label: 'Routine', icon: 'R' },
    ],
  },
  {
    id: 'data',
    label: 'Data',
    items: [
      { id: 'data', kind: 'data', label: 'Data', icon: 'D' },
      { id: 'document_read', kind: 'document_read', label: 'Read document', icon: 'R' },
      { id: 'document_query', kind: 'document_query', label: 'Query documents', icon: 'Q' },
      { id: 'document_write', kind: 'document_write', label: 'Write document', icon: 'W' },
      { id: 'document_create', kind: 'document_create', label: 'Create document', icon: '+' },
      { id: 'document_delete', kind: 'document_delete', label: 'Delete document', icon: '×' },
      { id: 'unpack', kind: 'unpack', label: 'Unpack', icon: 'U' },
      { id: 'extract', kind: 'extract', label: 'Extract', icon: 'X' },
    ],
  },
  {
    id: 'logic',
    label: 'Logic',
    items: [
      { id: 'condition', kind: 'condition', label: 'Condition', icon: '◇' },
      { id: 'format', kind: 'format', label: 'Format', icon: 'F' },
      { id: 'approval', kind: 'approval', label: 'Approval', icon: '✓' },
    ],
  },
];

function defaultEventNode(documentTypes) {
  const firstType = documentTypes?.find((t) => t.id === 'kanban_card') || documentTypes?.[0] || null;
  return {
    title: 'Document event',
    documentType: firstType?.id || '',
    key: null,
    operations: [],
    changedFields: [],
    match: {},
    outputSchema: cloneValue(firstType?.event_output_schema) || null,
  };
}

function defaultCronNode() {
  const tz = typeof Intl !== 'undefined' ? Intl.DateTimeFormat().resolvedOptions().timeZone : 'UTC';
  return {
    title: 'Cron',
    expression: '*/5 * * * *',
    timezone: tz,
    overlap: 'skip',
    outputSchema: {
      type: 'object',
      required: ['scheduled_for', 'fired_at', 'timezone'],
      properties: {
        scheduled_for: { type: 'string', format: 'date-time' },
        fired_at: { type: 'string', format: 'date-time' },
        timezone: { type: 'string' },
      },
    },
  };
}

function defaultManualNode() {
  return {
    title: 'Manual trigger',
    outputSchema: { type: 'object', properties: {} },
  };
}

function defaultReadNode() {
  return {
    title: 'Read document',
    documentType: '',
    inputSchema: null,
    outputSchema: null,
  };
}

function defaultQueryNode() {
  return {
    title: 'Query documents',
    documentType: '',
    filters: [],
    orderBy: null,
    order: 'asc',
    limit: 50,
    inputSchema: null,
    outputSchema: null,
  };
}

function defaultWriteNode() {
  return {
    title: 'Write document',
    documentType: '',
    inputSchema: null,
    outputSchema: null,
  };
}

// Mirrors routine_node_port_schemas() in src/progena/workflows.py (the
// backend recomputes these on save): a routine's declared input_schema /
// output_schema fields, else one `message` string, plus the run's thread_id.
function declaredRoutineSchema(schema) {
  const properties = schema?.properties;
  return properties && typeof properties === 'object' && Object.keys(properties).length
    ? cloneValue(schema)
    : null;
}

export function routineNodeSchemas(graph) {
  const definitions = graph?.$defs && Object.keys(graph.$defs).length ? { $defs: cloneValue(graph.$defs) } : {};
  const declaredInput = declaredRoutineSchema(graph?.input_schema);
  const inputSchema = declaredInput
    ? { ...declaredInput, ...definitions }
    : { type: 'object', required: ['message'], properties: { message: { type: 'string' } } };
  const declaredOutput = declaredRoutineSchema(graph?.output_schema);
  const outputSchema = declaredOutput
    ? {
      ...declaredOutput,
      properties: { ...declaredOutput.properties, thread_id: { type: 'string' } },
      required: [...(declaredOutput.required || []), 'thread_id'],
      ...definitions,
    }
    : { type: 'object', required: ['message', 'thread_id'], properties: { message: { type: 'string' }, thread_id: { type: 'string' } } };
  return { inputSchema, outputSchema };
}

function defaultCreateNode() {
  return {
    title: 'Create document',
    documentType: '',
    inputSchema: null,
    outputSchema: null,
  };
}

function defaultDeleteNode() {
  return {
    title: 'Delete document',
    documentType: '',
    inputSchema: null,
    outputSchema: null,
  };
}

export const DOCK_NODE_DEFAULTS = {
  event: defaultEventNode,
  manual: defaultManualNode,
  agent: () => ({
    instruction: null,
    template: '',
    subagent: null,
    inputSchema: { type: 'object', properties: {} },
    outputSchema: { type: 'object', required: ['message', 'thread_id'], properties: { message: { type: 'string' }, thread_id: { type: 'string' } } },
  }),
  routine: () => ({
    title: 'Routine',
    routine: '',
    ...routineNodeSchemas(null),
  }),
  data: () => ({ title: 'Data', value: null, outputSchema: { type: 'object' } }),
  extract: () => ({
    title: 'Extract',
    instruction: null,
    template: '',
    schema: null,
    inputSchema: { type: 'object', properties: {} },
    outputSchema: { type: 'object', required: ['result', 'thread_id'], properties: { result: {}, thread_id: { type: 'string' } } },
  }),
  format: () => ({
    title: 'Format',
    template: '{value}',
    inputSchema: { type: 'object', required: ['value'], properties: { value: {} } },
    outputSchema: { type: 'object', required: ['text'], properties: { text: { type: 'string' } } },
  }),

  condition: () => ({
    title: 'Condition',
    expression: 'ready',
    inputSchema: { type: 'object', properties: {} },
    outputSchema: {
      type: 'object',
      properties: { true: { type: 'boolean' }, false: { type: 'boolean' } },
    },
  }),
  approval: () => ({
    title: 'Approval',
    message: 'Approve this workflow action.',
    inputSchema: { type: 'object', properties: {} },
    outputSchema: {
      type: 'object',
      properties: { approved: { type: 'boolean' }, rejected: { type: 'boolean' } },
    },
  }),
  cron: defaultCronNode,
  document_read: defaultReadNode,
  document_query: defaultQueryNode,
  document_write: defaultWriteNode,
  document_create: defaultCreateNode,
  document_delete: defaultDeleteNode,
  unpack: () => ({
    title: 'Unpack',
    inputSchema: { type: 'object', required: ['value'], properties: { value: { type: 'object' } } },
    outputSchema: { type: 'object', properties: {} },
  }),
};

export function resolveNodeSizes(kind, storedSize, inputSchema, outputSchema) {
  const defaults = DEFAULT_NODE_SIZES[kind] || DEFAULT_NODE_SIZES.agent;
  const inputPortCount = nodeInputPorts(kind, inputSchema).length;
  // Unpack outputs are sized by the ports it shows (sizeUnpackNodes), not by
  // every property of the object it unpacks.
  const outputPortCount = kind === 'unpack' ? 0 : nodeOutputPorts(kind, outputSchema).length;
  const maxPorts = Math.max(inputPortCount, outputPortCount);
  const autoHeight = maxPorts > 0 ? 69 + (maxPorts - 1) * 28 : defaults.data.height;
  return {
    data: {
      width: storedSize?.data?.width || defaults.data.width,
      height: storedSize?.data?.height || Math.max(defaults.data.height, autoHeight),
    },
    settings: {
      width: storedSize?.settings?.width || defaults.settings.width,
      height: storedSize?.settings?.height || defaults.settings.height,
    },
  };
}

export function nodeCurrentSize(node, expanded) {
  const sizeKey = expanded ? 'settingsSize' : 'dataSize';
  if (node.data?.[sizeKey]) return node.data[sizeKey];
  const sizes = resolveNodeSizes(node.type, null, node.data?.inputSchema, node.data?.outputSchema);
  return expanded ? sizes.settings : sizes.data;
}

const NODE_MINIMAP_COLORS = {
  event: '#ffa657',
  agent: '#58a6ff',
  data: '#3fb950',
  extract: '#a371f7',
  condition: '#d29922',
  approval: '#f85149',
  document_read: '#79c0ff',
  document_query: '#a5d6ff',
  routine: '#db61a2',
  document_write: '#56d364',
  document_create: '#56d364',
  document_delete: '#ff7b72',
  unpack: '#bc8cff',
};

export function nodeColor(node) {
  return NODE_MINIMAP_COLORS[node.type] || NODE_MINIMAP_COLORS.agent;
}

export function nextDockNodePosition(nodes) {
  const ordered = [...nodes].sort((left, right) => left.position.x - right.position.x);
  const anchor = ordered[ordered.length - 1];
  if (!anchor) {
    return { x: 440, y: 160 };
  }
  return { x: anchor.position.x + 360, y: anchor.position.y };
}

// ``showAllFields`` is only passed for Unpack nodes (null otherwise): collapsed
// they show just the connected outputs, expanded every property.
export function visibleNodePortProperties(properties, nodeHeight, connectedNames = [], showAllFields = null) {
  const connectedSet = new Set(connectedNames);
  if (showAllFields !== null) {
    return showAllFields ? properties : properties.filter((p) => connectedSet.has(p.name));
  }
  if (!Number.isFinite(nodeHeight)) return properties;
  const connected = properties.filter((p) => connectedSet.has(p.name));
  const unconnected = properties.filter((p) => !connectedSet.has(p.name));
  const maxPorts = Math.max(0, Math.floor((nodeHeight - 8 - 61) / 28) + 1);
  const remainingSlots = Math.max(0, maxPorts - connected.length);
  return [...connected, ...unconnected.slice(0, remainingSlots)];
}

export const DEFAULT_EDGE_OPTIONS = { type: 'smoothstep', animated: true };

export const DEFAULT_VIEWPORT = { x: 0, y: 0, zoom: 1 };
