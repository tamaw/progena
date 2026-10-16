import { createPortal } from 'react-dom';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { selectRealtime } from '../store/realtimeSlice';
import { useSearchParams } from 'react-router-dom';
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  MiniMap,
  NodeResizer,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdges,
  useReactFlow,
} from '@xyflow/react';
import { useConfig } from '../context/ConfigContext';
import Select from '../components/common/Select';
import { useNotifications } from '../context/NotificationContext';
import {
  deleteWorkflow,
  fetchAgentProfiles,
  fetchDocumentTypes,
  fetchPromptContent,
  fetchPrompts,
  fetchWorkflowContent,
  fetchWorkflows,
  fetchWorkflowRuns,
  pauseWorkflow,
  resumeWorkflow,
  saveWorkflow,
  selectAgentProfiles,
  selectDocumentTypes,
  selectPromptContentEntry,
  selectPrompts,
  selectWorkflowDetailByPath,
  selectWorkflowLatestRunById,
  selectWorkflowRuns,
  selectWorkflows,
  selectWorkflowsState,
  triggerWorkflow,
} from '../store/uiDataSlice';
import {
  PaneCollapseSeam,
  PaneEmptyState,
  PaneExpandRail,
  PaneListFilters,
  PaneListFooter,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListRows,
  PaneSection,
} from '../components/layout/PaneList';
import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import {
  ConfigureActionIcon,
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  PauseActionIcon,
  PlayActionIcon,
  SaveActionIcon,
} from '../components/layout/DetailHeader';
import { JsonCodeEditor } from '../components/canvas/WorkflowJsonPanel';
import WorkflowNodeTitle from '../components/canvas/WorkflowNodeTitle';
import PopupModal from '../components/common/PopupModal';
import '../styles/canvas.css';
import { canvasToolSettings } from './workflowCanvasTools';
import '../styles/nodes.css';

const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'enabled', label: 'Enabled' },
  { value: 'paused', label: 'Paused' },
];
const SORTS = ['Recent', 'Name'];
const DEFAULT_EDGE_OPTIONS = { type: 'smoothstep', animated: true };
const MINIMAP_STYLE = { height: 100, width: 150 };
const DEFAULT_VIEWPORT = { x: 0, y: 0, zoom: 1 };

function canvasMenuAnchor(event, reactFlow) {
  return {
    screenPosition: { x: event.clientX, y: event.clientY },
    flowPosition: reactFlow.screenToFlowPosition({ x: event.clientX, y: event.clientY }),
  };
}
// Each node kind remembers two independent sizes: the compact "data" view
// (just a read-only preview) and the "settings" view (the full editable
// form). Switching views resizes the node to whichever one the user last
// left it at, instead of forcing one fixed rectangle for both.
const DEFAULT_NODE_SIZES = {
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
  condition: {
    data: { width: 300, height: 130 },
    settings: { width: 380, height: 220 },
  },
  approval: {
    data: { width: 320, height: 130 },
    settings: { width: 400, height: 240 },
  },
  notify: {
    data: { width: 320, height: 140 },
    settings: { width: 400, height: 280 },
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
  unpack: {
    data: { width: 280, height: 120 },
    settings: { width: 340, height: 240 },
  },
  cron: {
    data: { width: 320, height: 220 },
    settings: { width: 380, height: 320 },
  },
};
// V3 node menu: generic agent, data, event, extract, condition, approval and
// notify nodes only. No capability-specific kinds.
const NODE_MENU_CATEGORIES = [
  { id: 'triggers', label: 'Triggers', items: [{ id: 'event', kind: 'event', label: 'Event', icon: 'E' }, { id: 'cron', kind: 'cron', label: 'Cron', icon: 'C' }] },
  { id: 'agents', label: 'Agents', items: [
    { id: 'agent', kind: 'agent', label: 'Agent', icon: 'A' },
    { id: 'routine', kind: 'routine', label: 'Routine', icon: 'R' },
  ] },
  { id: 'data', label: 'Data', items: [
    { id: 'data', kind: 'data', label: 'Data', icon: 'D' },
    { id: 'document_read', kind: 'document_read', label: 'Read document', icon: 'R' },
    { id: 'document_query', kind: 'document_query', label: 'Query documents', icon: 'Q' },
    { id: 'document_write', kind: 'document_write', label: 'Write document', icon: 'W' },
    { id: 'unpack', kind: 'unpack', label: 'Unpack', icon: 'U' },
    { id: 'extract', kind: 'extract', label: 'Extract', icon: 'X' },
  ]} ,
  {
    id: 'logic',
    label: 'Logic',
    items: [
      { id: 'condition', kind: 'condition', label: 'Condition', icon: '◇' },
      { id: 'approval', kind: 'approval', label: 'Approval', icon: '✓' },
      { id: 'notify', kind: 'notify', label: 'Notify', icon: '!' },
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
    filterFields: [],
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

// Mirrors routine_node_port_schemas() in app/progenitor/workflows.py (the
// backend recomputes these on save): a routine's declared input_schema /
// output_schema fields, else one `message` string, plus the run's thread_id.
function declaredRoutineSchema(schema) {
  const properties = schema?.properties;
  return properties && typeof properties === 'object' && Object.keys(properties).length
    ? cloneValue(schema)
    : null;
}

function routineNodeSchemas(graph) {
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

const DOCK_NODE_DEFAULTS = {
  event: defaultEventNode,
  agent: () => ({
    title: 'Agent',
    prompt: null,
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
    prompt: null,
    template: '',
    schema: null,
    inputSchema: { type: 'object', properties: {} },
    outputSchema: { type: 'object', required: ['result', 'thread_id'], properties: { result: {}, thread_id: { type: 'string' } } },
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
  notify: () => ({
    title: 'Notify',
    alertTitle: 'Workflow update',
    template: 'Done: {message}',
    level: 'info',
    inputSchema: deriveNotifyInputSchema('Done: {message}'),
    outputSchema: { type: 'object', required: ['alert_id'], properties: { alert_id: { type: 'string' } } },
  }),
  cron: defaultCronNode,
  document_read: defaultReadNode,
  document_query: defaultQueryNode,
  document_write: defaultWriteNode,
  unpack: () => ({
    title: 'Unpack',
    inputSchema: { type: 'object', required: ['value'], properties: { value: { type: 'object' } } },
    outputSchema: { type: 'object', properties: {} },
  }),
};

function createPaletteNode(kind, nodeId, position, documentTypes, callbacks) {
  const sizes = DEFAULT_NODE_SIZES[kind] || DEFAULT_NODE_SIZES.agent;
  const defaultData = DOCK_NODE_DEFAULTS[kind](documentTypes);
  const inputPortCount = nodeInputPorts(kind, defaultData.inputSchema).length;
  const outputPortCount = nodeOutputPorts(kind, defaultData.outputSchema).length;
  const maxPorts = Math.max(inputPortCount, outputPortCount);
  const height = Math.max(sizes.data.height, maxPorts > 0 ? 69 + (maxPorts - 1) * 28 : sizes.data.height);
  return {
    id: nodeId,
    type: kind,
    width: sizes.data.width,
    height,
    style: { width: sizes.data.width, height },
    position,
    data: {
      kind,
      ...defaultData,
      currentNodeId: null,
      completedNodeIds: [],
      expanded: false,
      ...callbacks,
      canDelete: true,
    },
  };
}

function resolveNodeSizes(kind, storedSize, inputSchema, outputSchema) {
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

function nodeCurrentSize(node, expanded) {
  const sizeKey = expanded ? 'settingsSize' : 'dataSize';
  if (node.data?.[sizeKey]) return node.data[sizeKey];
  const sizes = resolveNodeSizes(node.type, null, node.data?.inputSchema, node.data?.outputSchema);
  return expanded ? sizes.settings : sizes.data;
}

function workflowPath(workflowId) {
  return workflowId ? `/workflows/${workflowId}.json` : '';
}

// The `.json` suffix on workflowPath() is the backing virtual-file extension
// used internally (Redux cache keys, backend query params) — it has no
// business showing up in the address bar, where `path` reads as a route
// segment rather than a file reference. These two helpers convert only at
// the URL-sync boundary; everything else keeps using the `.json` form.
function stripJsonExtension(path) {
  return path.endsWith('.json') ? path.slice(0, -'.json'.length) : path;
}

function withJsonExtension(path) {
  return path && !path.endsWith('.json') ? `${path}.json` : path;
}

function cloneValue(value) {
  return value ? JSON.parse(JSON.stringify(value)) : value;
}

// Mirrors the server-side placeholder grammar: {root} or {root.a.b} where each
// segment matches [A-Za-z_][A-Za-z0-9_]*. JSON literals like {"a": 1} are ignored.
// thread_id is reserved and never treated as a placeholder root.
function parsePromptPlaceholders(text) {
  if (!text || typeof text !== 'string') return { roots: [], placeholders: [] };
  const roots = [];
  const placeholders = [];
  const seen = new Set();
  const regex = /\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}/g;
  let match;
  while ((match = regex.exec(text)) !== null) {
    const full = match[1];
    const root = full.split('.')[0];
    if (root === 'thread_id') continue;
    placeholders.push(full);
    if (!seen.has(root)) {
      seen.add(root);
      roots.push(root);
    }
  }
  return { roots, placeholders };
}

function derivePromptInputSchema(text) {
  const { roots } = parsePromptPlaceholders(text);
  const properties = { thread_id: { type: 'string' } };
  roots.forEach((root) => {
    properties[root] = {};
  });
  return {
    type: 'object',
    ...(roots.length ? { required: roots } : {}),
    properties,
  };
}

// Notify messages are plain text: one input per placeholder root, no
// thread_id. Mirrors _derive_notify_input_schema() in app/progenitor/workflows.py.
function deriveNotifyInputSchema(text) {
  const { roots } = parsePromptPlaceholders(text);
  return {
    type: 'object',
    ...(roots.length ? { required: roots } : {}),
    properties: Object.fromEntries(roots.map((root) => [root, {}])),
  };
}

function promptTemplatePatch(text) {
  return { template: text, inputSchema: derivePromptInputSchema(text) };
}

function promptDocumentPatch(path, text) {
  return {
    prompt: path || null,
    template: null,
    inputSchema: derivePromptInputSchema(text),
  };
}

const NODE_MINIMAP_COLORS = {
  event: '#ffa657',
  agent: '#58a6ff',
  data: '#3fb950',
  extract: '#a371f7',
  condition: '#d29922',
  approval: '#f85149',
  notify: '#3fb950',
  document_read: '#79c0ff',
  document_query: '#a5d6ff',
  routine: '#db61a2',
  document_write: '#56d364',
  unpack: '#bc8cff',
};

function nodeColor(node) {
  return NODE_MINIMAP_COLORS[node.type] || NODE_MINIMAP_COLORS.agent;
}

function formatRelativeTime(timestamp) {
  if (!timestamp) {
    return 'No activity yet';
  }
  const time = Date.parse(timestamp);
  if (!Number.isFinite(time)) {
    return timestamp;
  }
  const deltaMs = Date.now() - time;
  const absSeconds = Math.max(0, Math.round(deltaMs / 1000));
  if (absSeconds < 60) return 'Just now';
  if (absSeconds < 3600) return `${Math.round(absSeconds / 60)}m ago`;
  if (absSeconds < 86400) return `${Math.round(absSeconds / 3600)}h ago`;
  return `${Math.round(absSeconds / 86400)}d ago`;
}

// Status dot only carries colour when there's something worth surfacing —
// paused, running, or failed. A ready (or disabled) workflow leaves the rail
// empty, matching the Chat sidebar's session cards.
function workflowDotTone(workflow, latestRun) {
  if (workflow?.paused) return 'paused';
  if (latestRun?.status === 'failed') return 'error';
  if (latestRun?.status === 'running') return 'info';
  return '';
}

function nextDockNodePosition(nodes) {
  const ordered = [...nodes].sort((left, right) => left.position.x - right.position.x);
  const anchor = ordered[ordered.length - 1];
  if (!anchor) {
    return { x: 440, y: 160 };
  }
  return { x: anchor.position.x + 360, y: anchor.position.y };
}
function workflowRunState(latestRun) {
  return {
    currentNodeId: latestRun?.status === 'running' ? latestRun.current_node_id || null : null,
    completedNodeIds: latestRun?.completed_node_ids || [],
  };
}


function buildNodeData(kind, node, callbacks, latestRun) {
  const shared = {
    expanded: false,
    onToggleExpanded: callbacks.onToggleExpanded,
    onOpenOptions: callbacks.onOpenOptions,
    onResize: callbacks.onResize,
    onUnpack: callbacks.onUnpack,
  };
  const runState = workflowRunState(latestRun);
  if (kind === 'event') {
    return {
      ...shared,
      kind,
      title: node.title || 'Document event',
      documentType: node.document_type || '',
      key: node.key || null,
      operations: Array.isArray(node.operations) ? [...node.operations] : [],
      changedFields: Array.isArray(node.changed_fields) ? [...node.changed_fields] : [],
      match: cloneValue(node.match) || {},
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: false,
    };
  }
  if (kind === 'cron') {
    return {
      ...shared,
      kind,
      title: node.title || 'Cron',
      expression: node.expression || '*/5 * * * *',
      timezone: node.timezone || 'UTC',
      overlap: node.overlap || 'skip',
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: false,
    };
  }
  if (kind === 'agent') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Agent',
      prompt: node.prompt ?? null,
      template: node.template ?? null,
      subagent: node.subagent ?? null,
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'extract') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Extract',
      prompt: node.prompt ?? null,
      template: node.template ?? null,
      schema: node.schema ?? null,
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'data') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Data',
      value: cloneValue(node.value) ?? null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'condition') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Condition',
      expression: node.expression || '',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || { type: 'string', enum: ['true', 'false'] },
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'approval') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Approval',
      message: node.message || '',
      approver: node.approver || 'any',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || { type: 'string', enum: ['approved', 'rejected'] },
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'notify') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Notify',
      alertTitle: node.alert_title || '',
      template: node.template || '',
      level: node.level || 'info',
      inputSchema: cloneValue(node.input_schema) || deriveNotifyInputSchema(node.template || ''),
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'document_read') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Read document',
      documentType: node.document_type || '',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'document_query') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Query documents',
      documentType: node.document_type || '',
      filterFields: Array.isArray(node.filter_fields) ? [...node.filter_fields] : [],
      limit: Number.isFinite(node.limit) ? node.limit : 50,
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'document_write') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Write document',
      documentType: node.document_type || '',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'routine') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Routine',
      routine: node.routine || '',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'unpack') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Unpack',
      showAllFields: Boolean(node.show_all_fields),
      inputSchema: cloneValue(node.input_schema) || { type: 'object', required: ['value'], properties: { value: { type: 'object' } } },
      outputSchema: cloneValue(node.output_schema) || { type: 'object', properties: {} },
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  return { ...shared, ...runState, kind, title: node.title || 'Node', onChange: callbacks.onNodeChange, onDelete: callbacks.onDeleteNode, canDelete: true };
}

function workflowToFlow(definition, callbacks, latestRun) {
  const uiNodes = definition?.ui?.nodes || {};
  const graphNodes = Object.entries(definition?.graph?.nodes || {}).map(([nodeId, node]) => {
    const kind = DEFAULT_NODE_SIZES[node.kind] ? node.kind : 'agent';
    const uiNode = uiNodes[nodeId] || {};
    const sizes = resolveNodeSizes(kind, uiNode.size, node.input_schema, node.output_schema);
    const position = uiNode.position || { x: 456, y: 160 };
    const nodeData = kind === 'event'
      ? {
          ...node,
          documentType: node.document_type,
          key: node.key,
          operations: node.operations,
          changedFields: node.changed_fields,
          match: node.match,
          outputSchema: cloneValue(node.output_schema) || null,
        }
      : kind === 'cron'
        ? {
            ...node,
            expression: node.expression,
            timezone: node.timezone,
            overlap: node.overlap,
            outputSchema: cloneValue(node.output_schema) || null,
          }
        : kind === 'agent'
        ? {
          ...node,
          prompt: node.prompt ?? null,
          template: node.template ?? null,
          subagent: node.subagent ?? null,
        }
      : kind === 'extract'
        ? {
          ...node,
          prompt: node.prompt ?? null,
          template: node.template ?? null,
          schema: node.schema ?? null,
        }
      : kind === 'document_read' || kind === 'document_query' || kind === 'document_write'
        ? { ...node, documentType: node.document_type }
        : kind === 'unpack'
        ? { ...node, show_all_fields: Boolean(uiNode.show_all_fields) }
        : node;
    const data = buildNodeData(kind, nodeData, callbacks, latestRun);
    // A stored size is a user resize: keep it in node data so serializing and
    // Unpack auto-sizing treat the node as customized.
    if (uiNode.size) {
      data.dataSize = sizes.data;
      data.settingsSize = sizes.settings;
    }
    return {
      id: nodeId,
      type: kind,
      position,
      width: sizes.data.width,
      height: sizes.data.height,
      style: { width: sizes.data.width, height: sizes.data.height },
      data,
    };
  });
  // A typed value connection is also the execution dependency. There are no
  // separate control edges or connector handles.
  const graphEdges = (definition?.graph?.edges || []).map((edge) => {
    const fromPort = edge.from.port || 'default';
    const toPort = edge.to.port || 'default';
    return {
      id: `edge_${edge.from.node}_${fromPort}_${edge.to.node}_${toPort}`,
      source: edge.from.node,
      sourceHandle: `out:${fromPort}`,
      target: edge.to.node,
      targetHandle: `in:${toPort}`,
      ...DEFAULT_EDGE_OPTIONS,
      className: 'workflow-edge--data',
    };
  });
  return {
    nodes: graphNodes,
    edges: graphEdges,
    viewport: definition?.ui?.viewport || DEFAULT_VIEWPORT,
  };
}

function isTriggerKind(kind) {
  return kind === 'event' || kind === 'cron';
}

function applyRunStateToNodes(nodes, latestRun) {
  const { currentNodeId, completedNodeIds } = workflowRunState(latestRun);
  return nodes.map((node) => {
    const nextData = isTriggerKind(node.type)
      ? node.data
      : { ...node.data, currentNodeId, completedNodeIds };
    if (nextData === node.data) {
      return node;
    }
    return { ...node, data: nextData };
  });
}

function edgeEndpointFromHandle(handle) {
  return handle && (handle.startsWith('out:') || handle.startsWith('in:'))
    ? handle.slice(handle.indexOf(':') + 1)
    : 'default';
}

function flowToWorkflow(baseWorkflow, nodes, edges, viewport) {
  const graphNodes = {};
  const uiNodes = {};
  nodes.forEach((node) => {
    if (node.type === 'event') {
      graphNodes[node.id] = {
        kind: 'event',
        title: String(node.data?.title || 'Document event'),
        document_type: node.data?.documentType || '',
        key: node.data?.key || null,
        operations: Array.isArray(node.data?.operations) ? [...node.data.operations] : [],
        changed_fields: Array.isArray(node.data?.changedFields) ? [...node.data.changedFields] : [],
        match: cloneValue(node.data?.match) || {},
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (node.type === 'cron') {
      graphNodes[node.id] = {
        kind: 'cron',
        title: String(node.data?.title || 'Cron'),
        expression: node.data?.expression || '*/5 * * * *',
        timezone: node.data?.timezone || 'UTC',
        overlap: node.data?.overlap || 'skip',
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (node.type === 'document_read') {
      graphNodes[node.id] = {
        kind: 'document_read',
        title: String(node.data?.title || 'Read document'),
        document_type: node.data?.documentType || '',
        input_schema: cloneValue(node.data?.inputSchema) || null,
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (node.type === 'document_query') {
      graphNodes[node.id] = {
        kind: 'document_query',
        title: String(node.data?.title || 'Query documents'),
        document_type: node.data?.documentType || '',
        filter_fields: Array.isArray(node.data?.filterFields) ? [...node.data.filterFields] : [],
        limit: Number.isFinite(node.data?.limit) ? node.data.limit : 50,
        input_schema: cloneValue(node.data?.inputSchema) || null,
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (node.type === 'document_write') {
      graphNodes[node.id] = {
        kind: 'document_write',
        title: String(node.data?.title || 'Write document'),
        document_type: node.data?.documentType || '',
        input_schema: cloneValue(node.data?.inputSchema) || null,
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (node.type === 'routine') {
      graphNodes[node.id] = {
        kind: 'routine',
        title: String(node.data?.title || 'Routine'),
        routine: node.data?.routine || '',
        input_schema: cloneValue(node.data?.inputSchema) || null,
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (node.type === 'data') {
      graphNodes[node.id] = {
        kind: 'data',
        title: String(node.data?.title || 'Data'),
        value: cloneValue(node.data?.value) ?? null,
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object' },
      };
    } else if (node.type === 'agent') {
      graphNodes[node.id] = {
        kind: 'agent',
        title: String(node.data?.title || 'Agent'),
        prompt: node.data?.prompt ?? null,
        template: node.data?.template ?? null,
        subagent: node.data?.subagent ?? null,
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object', required: ['message', 'thread_id'], properties: { message: { type: 'string' }, thread_id: { type: 'string' } } },
      };
    } else if (node.type === 'extract') {
      graphNodes[node.id] = {
        kind: 'extract',
        title: String(node.data?.title || 'Extract'),
        prompt: node.data?.prompt ?? null,
        template: node.data?.template ?? null,
        schema: node.data?.schema ?? null,
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object', required: ['result', 'thread_id'], properties: { result: {}, thread_id: { type: 'string' } } },
      };
    } else if (node.type === 'condition') {
      graphNodes[node.id] = {
        kind: 'condition',
        title: String(node.data?.title || 'Condition'),
        expression: String(node.data?.expression || ''),
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'string', enum: ['true', 'false'] },
      };
    } else if (node.type === 'approval') {
      graphNodes[node.id] = {
        kind: 'approval',
        title: String(node.data?.title || 'Approval'),
        message: String(node.data?.message || ''),
        approver: node.data?.approver === 'human' ? 'human' : 'any',
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'string', enum: ['approved', 'rejected'] },
      };
    } else if (node.type === 'notify') {
      graphNodes[node.id] = {
        kind: 'notify',
        title: String(node.data?.title || 'Notify'),
        alert_title: String(node.data?.alertTitle || ''),
        template: String(node.data?.template || ''),
        level: node.data?.level || 'info',
        input_schema: cloneValue(node.data?.inputSchema) || deriveNotifyInputSchema(node.data?.template || ''),
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (node.type === 'unpack') {
      graphNodes[node.id] = {
        kind: 'unpack',
        title: String(node.data?.title || 'Unpack'),
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', required: ['value'], properties: { value: { type: 'object' } } },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object', properties: {} },
      };
    } else {
      graphNodes[node.id] = {
        kind: 'agent',
        title: String(node.data?.title || 'Agent'),
        prompt: node.data?.prompt ?? null,
        template: node.data?.template ?? null,
        subagent: node.data?.subagent ?? null,
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object', required: ['message', 'thread_id'], properties: { message: { type: 'string' }, thread_id: { type: 'string' } } },
      };
    }
    const hasCustomSize = node.data?.dataSize || node.data?.settingsSize;
    const sizes = resolveNodeSizes(node.type, hasCustomSize ? {
      data: node.data?.dataSize,
      settings: node.data?.settingsSize,
    } : null, graphNodes[node.id].input_schema, graphNodes[node.id].output_schema);
    uiNodes[node.id] = {
      position: {
        x: Math.round(node.position?.x || 0),
        y: Math.round(node.position?.y || 0),
      },
      ...(hasCustomSize ? {
        size: {
          data: { width: Math.round(sizes.data.width), height: Math.round(sizes.data.height) },
          settings: { width: Math.round(sizes.settings.width), height: Math.round(sizes.settings.height) },
        },
      } : {}),
      // View state only: whether an Unpack node lists every field.
      ...(node.type === 'unpack' && node.data?.showAllFields ? { show_all_fields: true } : {}),
    };
  });
  return {
    ...baseWorkflow,
    version: 3,
    graph: {
      nodes: graphNodes,
      edges: edges.map((edge) => ({
        from: { node: edge.source, port: edgeEndpointFromHandle(edge.sourceHandle) },
        to: { node: edge.target, port: edgeEndpointFromHandle(edge.targetHandle) },
      })),
    },
    ui: {
      ...(baseWorkflow.ui || {}),
      nodes: uiNodes,
      viewport: viewport || baseWorkflow.ui?.viewport || DEFAULT_VIEWPORT,
    },
  };
}
function schemaProperties(schema) {
  if (!schema || typeof schema !== 'object') {
    return [];
  }
  const variants = Array.isArray(schema.oneOf) ? schema.oneOf : [schema];
  const properties = new Map();
  variants.forEach((variant) => {
    if (variant?.type !== 'object' || !variant.properties || typeof variant.properties !== 'object') {
      return;
    }
    const required = new Set(Array.isArray(variant.required) ? variant.required : []);
    Object.entries(variant.properties).forEach(([name, definition]) => {
      const existing = properties.get(name);
      properties.set(name, {
        name,
        definition: definition && typeof definition === 'object' ? definition : {},
        required: Boolean(existing?.required || required.has(name)),
      });
    });
  });
  return [...properties.values()];
}

function schemaType(definition) {
  if (typeof definition?.title === 'string' && definition.title) return definition.title;
  if (Array.isArray(definition?.type)) return definition.type.join(' | ');
  if (typeof definition?.type === 'string') return definition.type;
  if (Array.isArray(definition?.enum)) return definition.enum.join(' | ');
  return 'any';
}

// Mirrors app/progenitor/workflows.py: workflow_node_output_ports /
// workflow_node_input_ports. A data edge connects one existing output port
// to one existing input port; it never mutates either schema.
function nodeOutputPorts(kind, outputSchema) {
  if (kind === 'data') {
    return [{ name: 'value', definition: outputSchema && typeof outputSchema === 'object' ? outputSchema : {}, required: false }];
  }
  return schemaProperties(outputSchema);
}

function nodeInputPorts(kind, inputSchema) {
  if (kind === 'data' || kind === 'event' || kind === 'cron') {
    return [];
  }
  return schemaProperties(inputSchema);
}

// Edges attached to ports a node no longer has after its schemas changed
// (e.g. a routine node pointed at a different routine).
function edgesToRemovedPorts(edges, node) {
  if (!node) return [];
  const kind = node.data?.kind || node.type;
  const inputs = new Set(nodeInputPorts(kind, node.data?.inputSchema).map((port) => port.name));
  const outputs = new Set(nodeOutputPorts(kind, node.data?.outputSchema).map((port) => port.name));
  const port = (handle) => String(handle || '').replace(/^(in|out):/, '');
  return edges.filter((edge) => (
    (edge.target === node.id && !inputs.has(port(edge.targetHandle)))
    || (edge.source === node.id && !outputs.has(port(edge.sourceHandle)))
  ));
}

function schemaTypes(definition) {
  if (Array.isArray(definition?.type)) return definition.type;
  return typeof definition?.type === 'string' ? [definition.type] : [];
}

// Structural compatibility: every type the source can produce is accepted by
// the target, and matching title if both declare one. Mirrors
// _schema_types_compatible() in app/progenitor/workflows.py.
function schemasAreCompatible(sourceDefinition, targetDefinition) {
  const sourceTypes = schemaTypes(sourceDefinition);
  const targetTypes = new Set(schemaTypes(targetDefinition));
  if (targetTypes.has('number')) targetTypes.add('integer');
  if (sourceTypes.length && targetTypes.size && !sourceTypes.every((type) => targetTypes.has(type))) {
    return false;
  }
  const sourceTitle = sourceDefinition?.title;
  const targetTitle = targetDefinition?.title;
  return !(sourceTitle && targetTitle && sourceTitle !== targetTitle);
}

function portFromHandle(handle) {
  return handle?.includes(':') ? handle.slice(handle.indexOf(':') + 1) : handle;
}

function isUnpackableSchema(definition) {
  return schemaTypes(definition).includes('object') && definition?.properties && Object.keys(definition.properties).length > 0;
}

function hoistDefinitions(target, source) {
  const sourceDefs = source?.$defs || source?.definitions;
  if (!sourceDefs || typeof sourceDefs !== 'object') return;
  const targetDefs = target.$defs || target.definitions;
  const merged = { ...targetDefs, ...cloneValue(sourceDefs) };
  if (source.$defs !== undefined || (target.$defs === undefined && target.definitions === undefined)) {
    target.$defs = merged;
  } else {
    target.definitions = merged;
  }
}

function resolveLocalSchemaRef(schema, rootSchema) {
  const seen = new Set();
  let resolved = schema;
  while (resolved && typeof resolved === 'object' && typeof resolved.$ref === 'string') {
    const ref = resolved.$ref;
    const [, container, name] = ref.match(/^#\/([^/]+)\/([^/]+)$/) || [];
    const target = (container === '$defs' || container === 'definitions')
      ? rootSchema?.[container]?.[name]
      : null;
    if (seen.has(ref) || !target || typeof target !== 'object') return resolved;
    seen.add(ref);
    resolved = target;
  }
  return resolved;
}

function deriveUnpackSchemas(nodeId, nodes, edges) {
  const valueEdge = edges.find((e) => e.target === nodeId && portFromHandle(e.targetHandle) === 'value');
  if (!valueEdge) {
    return {
      inputSchema: { type: 'object', required: ['value'], properties: { value: { type: 'object' } } },
      outputSchema: { type: 'object', properties: {} },
    };
  }
  const sourceNode = nodes.find((n) => n.id === valueEdge.source);
  const sourcePort = portFromHandle(valueEdge.sourceHandle);
  const sourceSchema = sourceNode?.data?.outputSchema;
  const sourceProperty = nodeOutputPorts(sourceNode?.data?.kind, sourceSchema)
    .find((p) => p.name === sourcePort);

  const sourceDef = sourceProperty?.definition || { type: 'object' };
  const resolvedSourceDef = resolveLocalSchemaRef(sourceDef, sourceSchema);
  const inputSchema = {
    type: 'object',
    required: ['value'],
    properties: { value: cloneValue(sourceDef) },
  };
  hoistDefinitions(inputSchema, sourceSchema);

  const outputSchema = {
    type: 'object',
    properties: cloneValue(resolvedSourceDef?.properties || {}),
    required: cloneValue(resolvedSourceDef?.required || []),
  };
  hoistDefinitions(outputSchema, sourceDef);
  hoistDefinitions(outputSchema, sourceSchema);

  return { inputSchema, outputSchema };
}

function recomputeUnpackSchemas(nodes, edges) {
  const unpackIds = nodes.filter((n) => n.data?.kind === 'unpack').map((n) => n.id);
  if (!unpackIds.length) return nodes;

  const nodesById = Object.fromEntries(nodes.map((n) => [n.id, n]));
  // Each pass settles one more link of an Unpack chain; a cycle (rejected on
  // save) must not spin forever, so the pass count is bounded like the server.
  for (let pass = 0; pass <= unpackIds.length; pass += 1) {
    let changed = false;
    for (const unpackId of unpackIds) {
      const unpack = nodesById[unpackId];
      const { inputSchema, outputSchema } = deriveUnpackSchemas(unpackId, Object.values(nodesById), edges);
      const currentInput = JSON.stringify(unpack.data?.inputSchema);
      const currentOutput = JSON.stringify(unpack.data?.outputSchema);
      const nextInput = JSON.stringify(inputSchema);
      const nextOutput = JSON.stringify(outputSchema);
      if (currentInput !== nextInput || currentOutput !== nextOutput) {
        nodesById[unpackId] = { ...unpack, data: { ...unpack.data, inputSchema, outputSchema } };
        changed = true;
      }
    }
    if (!changed) break;
  }
  return sizeUnpackNodes(Object.values(nodesById), edges);
}

// Until the user resizes it, an Unpack node is exactly tall enough for its
// shown ports plus the show/hide-fields toggle row.
function sizeUnpackNodes(nodes, edges) {
  return nodes.map((node) => {
    if (node.data?.kind !== 'unpack' || node.data?.dataSize || node.data?.expanded) return node;
    const connected = edges.filter((edge) => edge.source === node.id).map((edge) => portFromHandle(edge.sourceHandle));
    const shown = visibleNodePortProperties(nodeOutputPorts('unpack', node.data?.outputSchema), node.height, connected, Boolean(node.data?.showAllFields)).length;
    const height = Math.max(DEFAULT_NODE_SIZES.unpack.data.height, 69 + shown * 28);
    return node.height === height ? node : { ...node, height, style: { ...node.style, height } };
  });
}

function computeLostEdgesForUnpackChange(currentEdges, nextNodes) {
  const nextNodesById = Object.fromEntries(nextNodes.map((n) => [n.id, n]));
  const lostEdges = [];
  for (const edge of currentEdges) {
    const sourceNode = nextNodesById[edge.source];
    if (sourceNode?.data?.kind !== 'unpack') continue;
    const portName = portFromHandle(edge.sourceHandle);
    const outputProps = sourceNode.data?.outputSchema?.properties || {};
    if (!outputProps[portName]) {
      lostEdges.push(edge);
    }
  }
  return lostEdges;
}

// ``showAllFields`` is only passed for Unpack nodes (null otherwise): collapsed
// they show just the connected outputs, expanded every property.
function visibleNodePortProperties(properties, nodeHeight, connectedNames = [], showAllFields = null) {
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

function pruneLostUnpackEdges(nodes, edges) {
  let nextEdges = edges;
  let nextNodes = recomputeUnpackSchemas(nodes, nextEdges);
  const lostEdges = [];
  while (true) {
    const newlyLost = computeLostEdgesForUnpackChange(nextEdges, nextNodes);
    if (newlyLost.length === 0) break;
    lostEdges.push(...newlyLost);
    const lostIds = new Set(newlyLost.map((edge) => edge.id));
    nextEdges = nextEdges.filter((edge) => !lostIds.has(edge.id));
    nextNodes = recomputeUnpackSchemas(nodes, nextEdges);
  }
  return { nodes: nextNodes, edges: nextEdges, lostEdges };
}

function WorkflowNodeHandles({ kind, inputSchema, outputSchema, nodeHeight, connectedInputPorts = [], connectedOutputPorts = [], showAllFields = null }) {
  const inputProperties = visibleNodePortProperties(nodeInputPorts(kind, inputSchema), nodeHeight, connectedInputPorts);
  const outputProperties = visibleNodePortProperties(nodeOutputPorts(kind, outputSchema), nodeHeight, connectedOutputPorts, showAllFields);
  const top = (index) => `${61 + index * 28}px`;
  return (
    <>
      {inputProperties.map(({ name }, index) => (
        <Handle key={`input-${name}`} type="target" position={Position.Left} id={`in:${name}`} className="canvas-handle workflow-node-external-handle" style={{ top: top(index) }} />
      ))}
      {outputProperties.map(({ name }, index) => (
        <Handle key={`output-${name}`} type="source" position={Position.Right} id={`out:${name}`} className="canvas-handle workflow-node-external-handle" style={{ top: top(index) }} />
      ))}
    </>
  );
}

// Renders port labels in exactly the order and subset WorkflowNodeHandles
// places handles, so every handle (and its edge) lines up with its label.
function WorkflowNodePorts({ kind, inputSchema, outputSchema, nodeHeight, connectedInputPorts = [], connectedOutputPorts = [], showAllFields = null, onToggleShowAllFields, onUnpack }) {
  const inputProperties = visibleNodePortProperties(nodeInputPorts(kind, inputSchema), nodeHeight, connectedInputPorts);
  const outputProperties = visibleNodePortProperties(nodeOutputPorts(kind, outputSchema), nodeHeight, connectedOutputPorts, showAllFields);
  const unconnectedCount = showAllFields !== null
    ? nodeOutputPorts(kind, outputSchema).filter((p) => !connectedOutputPorts.includes(p.name)).length
    : 0;
  return (
    <div className="workflow-node-ports">
      <div className="workflow-node-ports__column workflow-node-ports__column--inputs">
        {inputProperties.length ? inputProperties.map(({ name, definition }) => (
          <div className="workflow-node-port" key={`input-${name}`}>
            <span>{name}</span>
            <small>{schemaType(definition)}</small>
          </div>
        )) : <span className="workflow-node-ports__empty">No inputs</span>}
      </div>
      <div className="workflow-node-ports__column workflow-node-ports__column--outputs">
        {outputProperties.map(({ name, definition }) => (
          <div className="workflow-node-port" key={`output-${name}`}>
            {onUnpack && isUnpackableSchema(definition) ? (
              <button type="button" className="workflow-node-port__unpack" onClick={() => onUnpack(name)} aria-label={`Unpack ${name}`}>▸</button>
            ) : null}
            <small>{schemaType(definition)}</small>
            <span>{name}</span>
          </div>
        ))}
        {unconnectedCount > 0 ? (
          <button type="button" className="workflow-node-port workflow-node-port--toggle nodrag" onClick={onToggleShowAllFields} aria-expanded={Boolean(showAllFields)}>
            {showAllFields ? 'Hide unused fields' : `+ ${unconnectedCount} field${unconnectedCount === 1 ? '' : 's'}`}
          </button>
        ) : null}
        {!outputProperties.length && !unconnectedCount ? <span className="workflow-node-ports__empty">No outputs</span> : null}
      </div>
    </div>
  );
}



// Remounted per-node via `key={node.id}` on WorkflowNodeOptionsModal, so
// lazy initial state is sufficient -- no effect-driven resync needed.
function JsonFieldEditor({ label, value, onCommit }) {
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2));
  const [error, setError] = useState('');
  return (
    <label className="workflow-node-options__field">
      {label}
      <textarea
        value={text}
        rows={6}
        onChange={(event) => {
          const nextText = event.target.value;
          setText(nextText);
          try {
            const parsed = JSON.parse(nextText);
            setError('');
            onCommit(parsed);
          } catch {
            setError('Invalid JSON');
          }
        }}
      />
      {error ? <small className="workflow-node-options__error">{error}</small> : null}
    </label>
  );
}

function buildQueryInputSchema(filterFields, documentSchema) {
  if (!filterFields || filterFields.length === 0) {
    return { type: 'object', required: ['trigger'], properties: { trigger: {} } };
  }
  const properties = {};
  filterFields.forEach((field) => {
    properties[field] = documentSchema?.properties?.[field] || {};
  });
  return { type: 'object', required: filterFields, properties };
}

function WorkflowNodeOptionsModal({ node, onChange, onClose }) {
  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const kind = node.data?.kind || node.type;
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const promptsUrl = getApiUrl('prompts');
  const promptPath = node.data?.prompt || '';
  const promptContentEntry = useSelector((state) => selectPromptContentEntry(state, promptPath));

  // Fetch the selected document once, then derive its input ports once per
  // loaded text: `onChange` is a fresh function every render and the graph
  // may normalise the stored schema, so neither may drive a re-apply.
  const requestedPromptPathRef = useRef(null);
  const appliedPromptContentRef = useRef(null);
  const onChangeRef = useRef(onChange);
  useLayoutEffect(() => {
    onChangeRef.current = onChange;
  });
  useEffect(() => {
    if ((kind !== 'agent' && kind !== 'extract') || !promptPath) return;
    if (promptContentEntry?.path === promptPath) {
      const content = promptContentEntry.content || '';
      const applied = appliedPromptContentRef.current;
      if (applied?.path === promptPath && applied.content === content) return;
      appliedPromptContentRef.current = { path: promptPath, content };
      const patch = promptDocumentPatch(promptPath, content);
      if (JSON.stringify(node.data?.inputSchema) !== JSON.stringify(patch.inputSchema)) {
        onChangeRef.current(node.id, patch);
      }
      return;
    }
    if (requestedPromptPathRef.current === promptPath) return;
    requestedPromptPathRef.current = promptPath;
    void dispatch(fetchPromptContent({ promptsUrl, path: promptPath })).unwrap().catch(() => {});
  }, [dispatch, kind, node.data?.inputSchema, node.id, promptContentEntry, promptPath, promptsUrl]);

  const documentTypes = useSelector(selectDocumentTypes);

  const documentTypeOptions = useMemo(() =>
    documentTypes.map((dt) => ({ value: dt.id, label: dt.label || dt.id })),
  [documentTypes]);

  const readableTypeOptions = useMemo(() =>
    documentTypes.map((dt) => ({ value: dt.id, label: dt.label || dt.id })),
  [documentTypes]);

  const collectionTypeOptions = useMemo(() =>
    documentTypes.filter((dt) => dt.collection).map((dt) => ({ value: dt.id, label: dt.label || dt.id })),
  [documentTypes]);

  const writableTypeOptions = useMemo(() =>
    documentTypes.filter((dt) => dt.writable?.length).map((dt) => ({ value: dt.id, label: dt.label || dt.id })),
  [documentTypes]);

  const selectedDocumentType = useMemo(() =>
    documentTypes.find((dt) => dt.id === node.data?.documentType) || null,
  [documentTypes, node.data?.documentType]);

  const fieldOptions = useMemo(() => {
    const props = selectedDocumentType?.document_schema?.properties;
    if (!props || typeof props !== 'object') return [];
    return Object.keys(props).map((key) => ({ value: key, label: key }));
  }, [selectedDocumentType]);

  const prompts = useSelector(selectPrompts);
  const promptOptions = useMemo(() =>
    prompts.map((p) => ({ value: p.path, label: p.title || p.path })),
  [prompts]);

  const agentProfiles = useSelector(selectAgentProfiles);
  const subagentOptions = useMemo(() => {
    const names = agentProfiles.map((a) => a.name).filter(Boolean);
    return [...new Set(names)].sort().map((name) => ({ value: name, label: name }));
  }, [agentProfiles]);

  const routineOptions = useMemo(() =>
    agentProfiles
      .filter((agent) => agent.kind === 'routine')
      .map((agent) => ({ value: agent.id, label: agent.name || agent.id })),
  [agentProfiles]);

  const handleRoutineChange = (routineId) => {
    const routine = agentProfiles.find((agent) => agent.kind === 'routine' && agent.id === routineId);
    onChange(node.id, { routine: routineId, ...routineNodeSchemas(routine?.graph) });
  };

  const knownSchema = useMemo(() =>
    documentTypeOptions.some((o) => o.value === node.data?.schema),
  [documentTypeOptions, node.data?.schema]);

  const [cronPreview, setCronPreview] = useState({ nextRuns: [], error: '' });
  const cronAbortRef = useRef(null);
  const cronDebounceRef = useRef(null);

  useEffect(() => {
    if (kind !== 'cron') return undefined;
    const expression = node.data?.expression || '';
    const timezone = node.data?.timezone || '';
    clearTimeout(cronDebounceRef.current);
    if (!expression || !timezone) {
      return () => clearTimeout(cronDebounceRef.current);
    }
    cronDebounceRef.current = setTimeout(() => {
      cronAbortRef.current?.abort();
      const controller = new AbortController();
      cronAbortRef.current = controller;
      fetch(`/api/ui/workflows/cron-preview?expression=${encodeURIComponent(expression)}&timezone=${encodeURIComponent(timezone)}&count=3`, {
        signal: controller.signal,
      })
        .then(async (res) => {
          if (res.status === 200) {
            const data = await res.json();
            setCronPreview({ nextRuns: data.next_runs || [], error: '' });
          } else if (res.status === 400) {
            const data = await res.json();
            setCronPreview({ nextRuns: [], error: data.detail || 'Invalid expression' });
          } else {
            setCronPreview({ nextRuns: [], error: `HTTP ${res.status}` });
          }
        })
        .catch((err) => {
          if (err.name !== 'AbortError') {
            setCronPreview({ nextRuns: [], error: err.message });
          }
        });
    }, 300);
    return () => {
      clearTimeout(cronDebounceRef.current);
    };
  }, [kind, node.data?.expression, node.data?.timezone]);

  const handleEventTypeChange = (documentTypeId) => {
    const dt = documentTypes.find((t) => t.id === documentTypeId);
    onChange(node.id, {
      documentType: documentTypeId,
      outputSchema: cloneValue(dt?.event_output_schema) || null,
      changedFields: [],
    });
  };

  const handleReadTypeChange = (documentTypeId) => {
    const dt = documentTypes.find((t) => t.id === documentTypeId);
    onChange(node.id, {
      documentType: documentTypeId,
      inputSchema: cloneValue(dt?.read_input_schema) || null,
      outputSchema: cloneValue(dt?.read_output_schema) || null,
    });
  };

  const handleQueryTypeChange = (documentTypeId) => {
    const dt = documentTypes.find((t) => t.id === documentTypeId);
    const nextFilterFields = [];
    const nextInputSchema = buildQueryInputSchema(nextFilterFields, dt?.document_schema);
    onChange(node.id, {
      documentType: documentTypeId,
      filterFields: nextFilterFields,
      inputSchema: nextInputSchema,
      outputSchema: cloneValue(dt?.query_output_schema) || null,
    });
  };

  const handleQueryFilterFieldsChange = (filterFields) => {
    const nextInputSchema = buildQueryInputSchema(filterFields, selectedDocumentType?.document_schema);
    onChange(node.id, { filterFields, inputSchema: nextInputSchema });
  };

  const handleWriteTypeChange = (documentTypeId) => {
    const dt = documentTypes.find((t) => t.id === documentTypeId);
    onChange(node.id, {
      documentType: documentTypeId,
      inputSchema: cloneValue(dt?.write_input_schema) || null,
      outputSchema: cloneValue(dt?.write_output_schema) || null,
    });
  };

  return createPortal(
    <div className="workflow-node-options-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="workflow-node-options" role="dialog" aria-modal="true" aria-labelledby="workflow-node-options-title" onMouseDown={(event) => event.stopPropagation()}>
        <header className="workflow-node-options__head">
          <div>
            <span className="sidebar-modal__eyebrow">Node options</span>
            <h2 id="workflow-node-options-title">{node.data?.title || 'Workflow node'}</h2>
          </div>
          <button type="button" className="sidebar-modal__close" aria-label="Close node options" onClick={onClose}>×</button>
        </header>
        <div className="workflow-node-options__body">
          <section>
            <h3>Configuration</h3>
            <div className="workflow-node-options__mapping-form">
              <label>Title<input value={node.data?.title || ''} onChange={(event) => onChange(node.id, { title: event.target.value })} /></label>
            </div>
            {kind === 'event' ? (
              <>
                <label className="workflow-node-options__field">
                  Document type
                  <Select value={node.data?.documentType || ''} onChange={handleEventTypeChange} options={documentTypeOptions} ariaLabel="Document type" />
                </label>
                <label className="workflow-node-options__field">
                  Key
                  <input
                    value={node.data?.key || ''}
                    onChange={(event) => onChange(node.id, { key: event.target.value || null })}
                    placeholder="Any key"
                  />
                </label>
                <div className="workflow-node-options__field">
                  <span>Operations</span>
                  <div className="workflow-node-options__checkboxes">
                    {['created', 'updated', 'deleted'].map((op) => (
                      <label key={op} className="workflow-node-options__checkbox">
                        <input
                          type="checkbox"
                          checked={node.data?.operations?.includes(op) || false}
                          onChange={(event) => {
                            const current = new Set(node.data?.operations || []);
                            if (event.target.checked) current.add(op);
                            else current.delete(op);
                            onChange(node.id, { operations: Array.from(current) });
                          }}
                        />
                        {op}
                      </label>
                    ))}
                  </div>
                </div>
                <label className="workflow-node-options__field">
                  Changed fields
                  <Select
                    value=""
                    onChange={(field) => {
                      if (!field) return;
                      const current = new Set(node.data?.changedFields || []);
                      current.add(field);
                      onChange(node.id, { changedFields: Array.from(current) });
                    }}
                    options={[{ value: '', label: 'Add field…' }, ...fieldOptions]}
                    ariaLabel="Add changed field"
                  />
                  <div className="workflow-node-options__tags">
                    {(node.data?.changedFields || []).map((field) => (
                      <span key={field} className="workflow-node-options__tag">
                        {field}
                        <button
                          type="button"
                          onClick={() => {
                            const next = (node.data?.changedFields || []).filter((f) => f !== field);
                            onChange(node.id, { changedFields: next });
                          }}
                          aria-label={`Remove ${field}`}
                        >×</button>
                      </span>
                    ))}
                  </div>
                </label>
                <JsonFieldEditor label="Match" value={node.data?.match} onCommit={(match) => onChange(node.id, { match })} />
              </>
            ) : null}
            {kind === 'cron' ? (
              <>
                <label className="workflow-node-options__field">
                  Expression
                  <input
                    value={node.data?.expression || ''}
                    onChange={(event) => onChange(node.id, { expression: event.target.value })}
                    placeholder="*/5 * * * *"
                  />
                </label>
                <div className="workflow-node-options__field">
                  <span>Presets</span>
                  <div className="workflow-node-options__checkboxes">
                    {[
                      { label: 'Every 5 min', expr: '*/5 * * * *' },
                      { label: 'Hourly', expr: '0 * * * *' },
                      { label: 'Daily 9:00', expr: '0 9 * * *' },
                      { label: 'Weekdays 9:00', expr: '0 9 * * 1-5' },
                    ].map((preset) => (
                      <button
                        key={preset.expr}
                        type="button"
                        className="node-action-btn"
                        onClick={() => onChange(node.id, { expression: preset.expr })}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>
                <label className="workflow-node-options__field">
                  Timezone
                  <input
                    value={node.data?.timezone || ''}
                    onChange={(event) => onChange(node.id, { timezone: event.target.value })}
                    placeholder="Australia/Melbourne"
                  />
                </label>
                <label className="workflow-node-options__field">
                  Overlap
                  <Select
                    value={node.data?.overlap || 'skip'}
                    onChange={(val) => onChange(node.id, { overlap: val })}
                    options={[{ value: 'skip', label: 'Skip if still running' }, { value: 'queue', label: 'Queue' }]}
                    ariaLabel="Overlap behaviour"
                  />
                </label>
                <div className="workflow-node-options__field">
                  <span>Next runs</span>
                  {cronPreview.error ? (
                    <small className="workflow-node-options__error">{cronPreview.error}</small>
                  ) : (
                    <ul className="workflow-node-options__next-runs">
                      {cronPreview.nextRuns.map((run, i) => (
                        <li key={i}>{run}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </>
            ) : null}
            {kind === 'document_read' ? (
              <>
                <label className="workflow-node-options__field">
                  Document type
                  <Select value={node.data?.documentType || ''} onChange={handleReadTypeChange} options={readableTypeOptions} ariaLabel="Document type" />
                </label>
              </>
            ) : null}
            {kind === 'document_query' ? (
              <>
                <label className="workflow-node-options__field">
                  Document type
                  <Select value={node.data?.documentType || ''} onChange={handleQueryTypeChange} options={collectionTypeOptions} ariaLabel="Document type" />
                </label>
                <label className="workflow-node-options__field">
                  Filter fields
                  <Select
                    value=""
                    onChange={(field) => {
                      if (!field) return;
                      const current = new Set(node.data?.filterFields || []);
                      current.add(field);
                      handleQueryFilterFieldsChange(Array.from(current));
                    }}
                    options={[{ value: '', label: 'Add field…' }, ...fieldOptions]}
                    ariaLabel="Add filter field"
                  />
                  <div className="workflow-node-options__tags">
                    {(node.data?.filterFields || []).map((field) => (
                      <span key={field} className="workflow-node-options__tag">
                        {field}
                        <button
                          type="button"
                          onClick={() => {
                            const next = (node.data?.filterFields || []).filter((f) => f !== field);
                            handleQueryFilterFieldsChange(next);
                          }}
                          aria-label={`Remove ${field}`}
                        >×</button>
                      </span>
                    ))}
                  </div>
                </label>
                <label className="workflow-node-options__field">
                  Limit
                  <input
                    type="number"
                    min={1}
                    max={200}
                    value={node.data?.limit || 50}
                    onChange={(event) => {
                      const val = parseInt(event.target.value, 10);
                      onChange(node.id, { limit: Number.isFinite(val) ? Math.max(1, Math.min(200, val)) : 50 });
                    }}
                  />
                </label>
              </>
            ) : null}
            {kind === 'document_write' ? (
              <>
                <label className="workflow-node-options__field">
                  Document type
                  <Select value={node.data?.documentType || ''} onChange={handleWriteTypeChange} options={writableTypeOptions} ariaLabel="Document type" />
                </label>
              </>
            ) : null}
            {kind === 'routine' ? (
              <label className="workflow-node-options__field">
                Routine
                <Select value={node.data?.routine || ''} onChange={handleRoutineChange} options={routineOptions} ariaLabel="Routine" />
              </label>
            ) : null}
            {kind === 'agent' || kind === 'extract' ? (
              <>
                <div className="workflow-node-options__field">
                  <span>Prompt source</span>
                  <div className="workflow-node-options__checkboxes">
                    <label className="workflow-node-options__checkbox">
                      <input
                        type="radio"
                        name={`prompt-source-${node.id}`}
                        checked={node.data?.prompt != null}
                        onChange={() => {
                          // An empty path selects document mode with no prompt chosen yet.
                          onChange(node.id, { prompt: prompts[0]?.path || '', template: null });
                        }}
                      />
                      Prompt document
                    </label>
                    <label className="workflow-node-options__checkbox">
                      <input
                        type="radio"
                        name={`prompt-source-${node.id}`}
                        checked={node.data?.prompt == null}
                        onChange={() => onChange(node.id, { prompt: null, template: '' })}
                      />
                      Inline template
                    </label>
                  </div>
                </div>
                {node.data?.prompt != null ? (
                  <label className="workflow-node-options__field">
                    Prompt
                    <Select
                      value={node.data?.prompt || ''}
                      onChange={(path) => onChange(node.id, { prompt: path || null, template: null })}
                      options={[{ value: '', label: 'Select prompt…' }, ...promptOptions]}
                      ariaLabel="Prompt document"
                    />
                  </label>
                ) : (
                  <label className="workflow-node-options__field">
                    Template
                    <textarea
                      value={node.data?.template || ''}
                      onChange={(event) => onChange(node.id, promptTemplatePatch(event.target.value))}
                      rows={6}
                    />
                  </label>
                )}
              </>
            ) : null}
            {kind === 'agent' ? (
              <label className="workflow-node-options__field">
                Subagent
                <Select
                  value={node.data?.subagent || ''}
                  onChange={(val) => onChange(node.id, { subagent: val || null })}
                  options={[{ value: '', label: 'Progenitor' }, ...subagentOptions]}
                  ariaLabel="Subagent"
                />
              </label>
            ) : null}
            {kind === 'extract' ? (
              <>
                <label className="workflow-node-options__field">
                  Result schema
                  <Select
                    value={knownSchema ? (node.data?.schema || '') : '__custom__'}
                    onChange={(val) => {
                      if (val === '__custom__') {
                        onChange(node.id, { schema: '' });
                      } else {
                        onChange(node.id, { schema: val || null });
                      }
                    }}
                    options={[{ value: '', label: 'Infer' }, ...documentTypeOptions, { value: '__custom__', label: 'Other…' }]}
                    ariaLabel="Result schema"
                  />
                </label>
                {!knownSchema ? (
                  <label className="workflow-node-options__field">
                    Schema name
                    <input
                      value={node.data?.schema || ''}
                      onChange={(event) => onChange(node.id, { schema: event.target.value || null })}
                      placeholder="Type a schema name"
                    />
                  </label>
                ) : null}
              </>
            ) : null}
            {kind === 'data' ? (
              <>
                <JsonFieldEditor label="Value" value={node.data?.value} onCommit={(value) => onChange(node.id, { value })} />
                <JsonFieldEditor label="Output schema" value={node.data?.outputSchema} onCommit={(outputSchema) => onChange(node.id, { outputSchema })} />
              </>
            ) : null}
            {kind === 'condition' ? (
              <>
                <label className="workflow-node-options__field">Expression<textarea value={node.data?.expression || ''} onChange={(event) => onChange(node.id, { expression: event.target.value })} rows={3} /></label>
                <JsonFieldEditor label="Input schema" value={node.data?.inputSchema} onCommit={(inputSchema) => onChange(node.id, { inputSchema })} />
              </>
            ) : null}
            {kind === 'approval' ? (
              <>
                <label className="workflow-node-options__field">Message<textarea value={node.data?.message || ''} onChange={(event) => onChange(node.id, { message: event.target.value })} rows={3} /></label>
                <label className="workflow-node-options__field">
                  Who can decide
                  <Select
                    value={node.data?.approver || 'any'}
                    onChange={(approver) => onChange(node.id, { approver })}
                    options={[{ value: 'any', label: 'A person or Progenitor' }, { value: 'human', label: 'Only a person' }]}
                    ariaLabel="Who can decide"
                  />
                </label>
                <JsonFieldEditor label="Input schema" value={node.data?.inputSchema} onCommit={(inputSchema) => onChange(node.id, { inputSchema })} />
              </>
            ) : null}
            {kind === 'notify' ? (
              <>
                <label className="workflow-node-options__field">Alert title<input value={node.data?.alertTitle || ''} onChange={(event) => onChange(node.id, { alertTitle: event.target.value })} /></label>
                <label className="workflow-node-options__field">Message<textarea value={node.data?.template || ''} onChange={(event) => onChange(node.id, { template: event.target.value, inputSchema: deriveNotifyInputSchema(event.target.value) })} rows={4} /></label>
                <label className="workflow-node-options__field">
                  Level
                  <Select
                    value={node.data?.level || 'info'}
                    onChange={(level) => onChange(node.id, { level })}
                    options={[{ value: 'info', label: 'Info' }, { value: 'warning', label: 'Warning' }, { value: 'error', label: 'Error' }]}
                    ariaLabel="Level"
                  />
                </label>
              </>
            ) : null}
          </section>
        </div>
      </section>
    </div>,
    document.body,
  );
}

function PromptNode({ id, data, selected, height }) {
  const expanded = data.expanded;
  const edges = useEdges();
  const connectedInputPorts = useMemo(() => edges.filter((e) => e.target === id).map((e) => portFromHandle(e.targetHandle)), [edges, id]);
  const connectedOutputPorts = useMemo(() => edges.filter((e) => e.source === id).map((e) => portFromHandle(e.sourceHandle)), [edges, id]);

  return (
    <>
      <NodeResizer
        minWidth={240}
        minHeight={expanded ? 200 : 100}
        isVisible={selected}
        onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })}
      />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--${data.kind} ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className={`node-type-icon ${data.kind}`}>{data.kind[0].toUpperCase()}</span>
          <WorkflowNodeTitle
            expanded={expanded}
            title={data.title}
            placeholder="Node title"
            onChange={(event) => data.onChange(id, { title: event.target.value })}
          />
          <button
            type="button"
            className="node-options-btn nodrag"
            onClick={() => data.onOpenOptions(id)}
            aria-label="Configure node"
            data-tooltip="Configure node"
          >
            <ConfigureActionIcon />
          </button>
          {data.canDelete ? (
            <button
              type="button"
              className="node-remove-btn"
              aria-label="Delete node"
              onClick={() => data.onDelete(id)}
            >
              ×
            </button>
          ) : null}
        </div>
        <div className="node-body">
          <WorkflowNodePorts kind={data.kind} inputSchema={data.inputSchema} outputSchema={data.outputSchema} nodeHeight={height} connectedInputPorts={connectedInputPorts} connectedOutputPorts={connectedOutputPorts} onUnpack={data.onUnpack ? (port) => data.onUnpack(id, port) : undefined} />
        </div>
      </div>
      <WorkflowNodeHandles kind={data.kind} inputSchema={data.inputSchema} outputSchema={data.outputSchema} nodeHeight={height} connectedInputPorts={connectedInputPorts} connectedOutputPorts={connectedOutputPorts} />
    </>
  );
}

function DocumentEventNode({ id, data, selected, height }) {
  const edges = useEdges();
  const connectedOutputPorts = useMemo(() => edges.filter((e) => e.source === id).map((e) => portFromHandle(e.sourceHandle)), [edges, id]);

  return (
    <>
      <NodeResizer minWidth={280} minHeight={110} isVisible={selected} onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })} />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--trigger ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className="node-type-icon event">E</span>
          <WorkflowNodeTitle expanded={false} title={data.title} placeholder="Event title" onChange={(event) => data.onChange(id, { title: event.target.value })} />
          <button type="button" className="node-options-btn nodrag" onClick={() => data.onOpenOptions(id)} aria-label="Configure node" data-tooltip="Configure node"><ConfigureActionIcon /></button>
          {data.canDelete ? <button type="button" className="node-remove-btn" onClick={() => data.onDelete(id)} aria-label="Delete event">×</button> : null}
        </div>
        <div className="node-body">
          <WorkflowNodePorts kind="event" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} onUnpack={data.onUnpack ? (port) => data.onUnpack(id, port) : undefined} />
        </div>
      </div>
      <WorkflowNodeHandles kind="event" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} />
    </>
  );
}

function CronNode({ id, data, selected, height }) {
  const edges = useEdges();
  const connectedOutputPorts = useMemo(() => edges.filter((e) => e.source === id).map((e) => portFromHandle(e.sourceHandle)), [edges, id]);

  return (
    <>
      <NodeResizer minWidth={280} minHeight={110} isVisible={selected} onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })} />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--cron ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className="node-type-icon cron">C</span>
          <WorkflowNodeTitle expanded={false} title={data.title} placeholder="Cron title" onChange={(event) => data.onChange(id, { title: event.target.value })} />
          <button type="button" className="node-options-btn nodrag" onClick={() => data.onOpenOptions(id)} aria-label="Configure node" data-tooltip="Configure node"><ConfigureActionIcon /></button>
          {data.canDelete ? <button type="button" className="node-remove-btn" onClick={() => data.onDelete(id)} aria-label="Delete cron">×</button> : null}
        </div>
        <div className="node-body">
          <div className="workflow-graph-node__field">
            <span>{data.expression || '*/5 * * * *'}</span>
          </div>
          <WorkflowNodePorts kind="cron" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} onUnpack={data.onUnpack ? (port) => data.onUnpack(id, port) : undefined} />
        </div>
      </div>
      <WorkflowNodeHandles kind="cron" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} />
    </>
  );
}

function WorkflowDataNode({ id, data, selected, height }) {
  const edges = useEdges();
  const connectedOutputPorts = useMemo(() => edges.filter((e) => e.source === id).map((e) => portFromHandle(e.sourceHandle)), [edges, id]);

  return (
    <>
      <NodeResizer minWidth={240} minHeight={110} isVisible={selected} onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })} />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--data ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className="node-type-icon data">D</span>
          <WorkflowNodeTitle expanded={false} title={data.title} placeholder="Data title" onChange={(event) => data.onChange(id, { title: event.target.value })} />
          <button type="button" className="node-options-btn nodrag" onClick={() => data.onOpenOptions(id)} aria-label="Configure node" data-tooltip="Configure node"><ConfigureActionIcon /></button>
          <button type="button" className="node-remove-btn" onClick={() => data.onDelete(id)} aria-label="Delete data node">×</button>
        </div>
        <div className="node-body">
          <WorkflowNodePorts kind="data" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} onUnpack={data.onUnpack ? (port) => data.onUnpack(id, port) : undefined} />
        </div>
      </div>
      <WorkflowNodeHandles kind="data" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} />
    </>
  );
}

function WorkflowControlNode({ id, data, selected, height }) {
  const label = data.kind === 'condition' ? 'Condition' : 'Approval';
  const edges = useEdges();
  const connectedInputPorts = useMemo(() => edges.filter((e) => e.target === id).map((e) => portFromHandle(e.targetHandle)), [edges, id]);
  const connectedOutputPorts = useMemo(() => edges.filter((e) => e.source === id).map((e) => portFromHandle(e.sourceHandle)), [edges, id]);

  return (
    <>
      <NodeResizer minWidth={260} minHeight={110} isVisible={selected} onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })} />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--${data.kind} ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className={`node-type-icon ${data.kind}`}>{data.kind[0].toUpperCase()}</span>
          <WorkflowNodeTitle expanded={false} title={data.title} placeholder={`${label} title`} onChange={(event) => data.onChange(id, { title: event.target.value })} />
          <button type="button" className="node-options-btn nodrag" onClick={() => data.onOpenOptions(id)} aria-label="Configure node" data-tooltip="Configure node"><ConfigureActionIcon /></button>
          <button type="button" className="node-remove-btn" onClick={() => data.onDelete(id)} aria-label={`Delete ${label}`}>×</button>
        </div>
        <div className="node-body">
          <WorkflowNodePorts kind={data.kind} inputSchema={data.inputSchema} outputSchema={data.outputSchema} nodeHeight={height} connectedInputPorts={connectedInputPorts} connectedOutputPorts={connectedOutputPorts} />
        </div>
      </div>
      <WorkflowNodeHandles kind={data.kind} inputSchema={data.inputSchema} outputSchema={data.outputSchema} nodeHeight={height} connectedInputPorts={connectedInputPorts} connectedOutputPorts={connectedOutputPorts} />
    </>
  );
}

function UnpackNode({ id, data, selected, height }) {
  const edges = useEdges();
  const connectedInputPorts = useMemo(() => edges.filter((e) => e.target === id).map((e) => portFromHandle(e.targetHandle)), [edges, id]);
  const connectedOutputPorts = useMemo(() => edges.filter((e) => e.source === id).map((e) => portFromHandle(e.sourceHandle)), [edges, id]);

  return (
    <>
      <NodeResizer minWidth={260} minHeight={110} isVisible={selected} onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })} />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--data ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className="node-type-icon unpack">U</span>
          <WorkflowNodeTitle expanded={false} title={data.title} placeholder="Unpack" onChange={(event) => data.onChange(id, { title: event.target.value })} />
          <button type="button" className="node-options-btn nodrag" onClick={() => data.onOpenOptions(id)} aria-label="Configure node" data-tooltip="Configure node"><ConfigureActionIcon /></button>
          <button type="button" className="node-remove-btn" onClick={() => data.onDelete(id)} aria-label="Delete unpack node">×</button>
        </div>
        <div className="node-body">
          <WorkflowNodePorts
            kind={data.kind}
            inputSchema={data.inputSchema}
            outputSchema={data.outputSchema}
            nodeHeight={height}
            connectedInputPorts={connectedInputPorts}
            connectedOutputPorts={connectedOutputPorts}
            showAllFields={Boolean(data.showAllFields)}
            onToggleShowAllFields={() => data.onChange(id, { showAllFields: !data.showAllFields })}
          />
        </div>
      </div>
      <WorkflowNodeHandles kind={data.kind} inputSchema={data.inputSchema} outputSchema={data.outputSchema} nodeHeight={height} connectedInputPorts={connectedInputPorts} connectedOutputPorts={connectedOutputPorts} showAllFields={Boolean(data.showAllFields)} />
    </>
  );
}

const NODE_TYPES = {
  event: DocumentEventNode,
  cron: CronNode,
  agent: PromptNode,
  extract: PromptNode,
  data: WorkflowDataNode,
  condition: WorkflowControlNode,
  approval: WorkflowControlNode,
  notify: PromptNode,
  document_read: PromptNode,
  document_query: PromptNode,
  document_write: PromptNode,
  routine: PromptNode,
  unpack: UnpackNode,
};

// Chevrons for the right tools dock / collapse seam (point toward the action:
// left = expand the panel leftward, right = collapse it away). Ported from the
// Agents/Routines canvas.
function ChevronLeftSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m9.8 4.2-3.6 3.8 3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

function CanvasToolIcon({ tool }) {
  if (tool === 'grab') {
    return <svg viewBox="0 0 16 16" width="17" height="17" aria-hidden="true"><path d="M4.5 8V4.2a1 1 0 0 1 2 0V7m0-3.6a1 1 0 0 1 2 0V7m0-2.8a1 1 0 0 1 2 0V7m0-1.7a1 1 0 0 1 2 0v3.2a4.5 4.5 0 0 1-9 0V8a1 1 0 0 1 2 0Z" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.2" /></svg>;
  }
  if (tool === 'cut') {
    return <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><circle cx="4" cy="12" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.3" /><circle cx="12" cy="12" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.3" /><path d="m5.3 10.7 5.4-7.4M10.7 10.7 5.3 3.3" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.3" /></svg>;
  }
  return <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path d="m3 2 9.5 5.6-5 1.3L6.2 14Z" fill="none" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.3" /></svg>;
}

function ChevronRightSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m6.2 4.2 3.6 3.8-3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}


// Bottom-center floating dock for adding nodes to the canvas — ported directly
// from the Agents/Routines canvas's GraphDock: drag onto the canvas or click
// to add at the next slot. The event node is the fixed start and isn't
// dock-addable.


function WorkflowDock({ menuAnchor, onOpenMenu, onCloseMenu, onAdd, tool, onToolChange }) {
  const [filter, setFilter] = useState('');
  const filterRef = useRef(null);
  const dockRef = useRef(null);
  const addButtonRef = useRef(null);
  const menuRef = useRef(null);
  const menuOpen = Boolean(menuAnchor);
  const normalizedFilter = filter.trim().toLowerCase();
  const categories = NODE_MENU_CATEGORIES
    .map((category) => ({
      ...category,
      items: category.items.filter((item) => !normalizedFilter || item.label.toLowerCase().includes(normalizedFilter)),
    }))
    .filter((category) => category.items.length);

  useEffect(() => {
    if (menuOpen) {
      filterRef.current?.focus();
    }
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const closeFromOutside = (event) => {
      if (!dockRef.current?.contains(event.target)) {
        setFilter('');
        onCloseMenu();
      }
    };
    const closeFromEscape = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setFilter('');
        onCloseMenu();
        addButtonRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', closeFromOutside);
    document.addEventListener('keydown', closeFromEscape);
    return () => {
      document.removeEventListener('pointerdown', closeFromOutside);
      document.removeEventListener('keydown', closeFromEscape);
    };
  }, [menuOpen, onCloseMenu]);

  const handleAdd = (kind) => {
    onAdd(kind, menuAnchor?.flowPosition);
    setFilter('');
    onCloseMenu();
  };

  const screenPosition = menuAnchor?.screenPosition;
  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    if (!screenPosition) {
      menu.style.left = '';
      menu.style.top = '';
      return;
    }
    const dockRect = dockRef.current?.getBoundingClientRect();
    const left = Math.max(16, Math.min(screenPosition.x, window.innerWidth - 346));
    const top = Math.max(16, Math.min(screenPosition.y, window.innerHeight - 510));
    menu.style.left = `${left - (dockRect?.left || 0)}px`;
    menu.style.top = `${top - (dockRect?.top || 0)}px`;
  }, [menuOpen, screenPosition]);

  return (
    <div ref={dockRef} className="canvas-dock workflow-canvas-dock">
      {menuOpen ? (
        <div ref={menuRef} className={`workflow-node-menu${screenPosition ? ' workflow-node-menu--cursor' : ''}`} role="menu" aria-label="Add workflow node">
          <label className="workflow-node-menu__filter">
            <span className="sr-only">Filter nodes</span>
            <input ref={filterRef} type="search" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter nodes…" />
          </label>
          <div className="workflow-node-menu__categories">
            {categories.map((category) => (
              <section key={category.id} className="workflow-node-menu__category" aria-labelledby={`node-menu-${category.id}`}>
                <h3 id={`node-menu-${category.id}`}>{category.label}</h3>
                {category.items.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className="workflow-node-menu__item"
                    role="menuitem"
                    disabled={item.disabled}
                    title={item.detail}
                    onClick={() => handleAdd(item.kind)}
                  >
                    <span className={`canvas-dock-icon ${item.kind || 'event'}`}>{item.icon}</span>
                    <span>{item.label}</span>
                    {item.detail ? <small>{item.detail}</small> : null}
                  </button>
                ))}
              </section>
            ))}
            {!categories.length ? <p className="workflow-node-menu__empty">No matching nodes.</p> : null}
          </div>
        </div>
      ) : null}
      <div className="canvas-dock-shell" aria-label="Canvas toolbar">
        <button type="button" className={`canvas-toolbar-button canvas-toolbar-button--tool ${tool === 'pointer' ? 'is-active' : ''}`} onClick={() => onToolChange('pointer')} aria-pressed={tool === 'pointer'} title="Pointer: select, move, and connect">
          <CanvasToolIcon tool="pointer" />
          <span className="canvas-dock-tooltip">Pointer</span>
        </button>
        <button type="button" className={`canvas-toolbar-button canvas-toolbar-button--tool ${tool === 'grab' ? 'is-active' : ''}`} onClick={() => onToolChange('grab')} aria-pressed={tool === 'grab'} title="Grab: pan with the primary button">
          <CanvasToolIcon tool="grab" />
          <span className="canvas-dock-tooltip">Grab</span>
        </button>
        <button type="button" className={`canvas-toolbar-button canvas-toolbar-button--tool ${tool === 'cut' ? 'is-active' : ''}`} onClick={() => onToolChange('cut')} aria-pressed={tool === 'cut'} title="Cut: click a connection to remove it">
          <CanvasToolIcon tool="cut" />
          <span className="canvas-dock-tooltip">Cut connection</span>
        </button>
        <button ref={addButtonRef} type="button" className={`canvas-toolbar-button canvas-toolbar-button--add ${menuOpen && !menuAnchor?.screenPosition ? 'is-active' : ''}`} onClick={() => onOpenMenu({})} aria-expanded={menuOpen} aria-haspopup="menu" title="Add workflow node">
          <span aria-hidden="true">+</span>
          <span className="canvas-dock-tooltip">Add node</span>
        </button>
      </div>
    </div>
  );
}

// Collapsed right-edge dock holding the workflow's auxiliary views (details
// form, JSON editor, run history) — ported from the Agents/Routines canvas's
// GraphSideDock so the canvas itself can occupy the full page instead of
// sharing space with a permanent header/form.
const WORKFLOW_SIDE_ITEMS = [
  { id: 'details', label: 'Details', title: 'Workflow details' },
  { id: 'json', label: 'JSON', title: 'Edit as JSON' },
  { id: 'runs', label: 'Runs', title: 'Run history' },
];

const DOCK_ITEM_META = {
  prompt: { icon: 'P', label: 'Prompt' },
  'call-agent': { icon: 'A', label: 'Agent' },
  'knowledge-search': { icon: 'K', label: 'Knowledge' },
  'kanban-task': { icon: 'T', label: 'Kanban' },
  extract: { icon: 'X', label: 'Extract' },
  'ask-user': { icon: 'U', label: 'Ask' },
  'inbox-message': { icon: 'I', label: 'Inbox' },
  routine: { icon: 'R', label: 'Routine' },
  condition: { icon: '◇', label: 'Condition' },
  approval: { icon: '✓', label: 'Approval' },
  notify: { icon: '!', label: 'Notify' },
};

function WorkflowSideDock({ onSelect }) {
  return (
    <aside className="workflow-side-dock" aria-label="Workflow tools">
      {WORKFLOW_SIDE_ITEMS.map((item) => (
        <button
          key={item.id}
          type="button"
          className="workflow-side-dock__item"
          title={item.title}
          aria-label={item.title}
          onClick={() => onSelect(item.id)}
        >
          <ChevronLeftSmall />
          <span className="workflow-side-dock__label">{item.label}</span>
        </button>
      ))}
    </aside>
  );
}

function WorkflowRow({ item, latestRun, selectedPath, onSelect }) {
  const path = workflowPath(item.id);
  const dotTone = workflowDotTone(item, latestRun);
  return (
    <button
      type="button"
      className={`pane-card-row ${selectedPath === path ? 'active' : ''}`}
      onClick={() => onSelect(path)}
      title={item.description || item.title}
    >
      <span className={`pane-card-row__dot${dotTone ? ` pane-card-row__dot--${dotTone}` : ''}`} aria-hidden="true" />
      <div className="pane-card-row__body">
        <div className="pane-card-row__top">
          <span className="pane-card-row__title">{item.title}</span>
          <span className="pane-card-row__age">{formatRelativeTime(latestRun?.updated_at)}</span>
        </div>
        <div className="pane-card-row__preview">{item.description || 'No description yet.'}</div>
        {(item.triggers || []).length > 0 ? (
          <div className="pane-card-row__meta">{item.triggers.join(' · ')}</div>
        ) : null}
      </div>
    </button>
  );
}

function WorkflowRunsList({ runs, loading, workflowId }) {
  if (loading) {
    return <PaneEmptyState>Loading workflow runs...</PaneEmptyState>;
  }
  if (!runs.length) {
    return <PaneEmptyState>No runs yet. Trigger the workflow to inspect its execution path.</PaneEmptyState>;
  }
  return (
    <div className="workflow-runs">
      {runs.map((run) => (
        <article key={run.run_id} className="workflow-run-card">
          <div className="workflow-run-card__head">
            <strong>{run.status}</strong>
            <span>{formatRelativeTime(run.updated_at || run.queued_at)}</span>
          </div>
          <div className="workflow-run-card__meta">
            <span>Run {run.run_id}</span>
            {run.current_node_id ? <span>Current: {run.current_node_id}</span> : null}
            {run.thread_id ? <span>Thread: {run.thread_id}</span> : null}
          </div>
          {run.pending_approval ? (
            <div className="workflow-run-card__approval">
              <p>{run.pending_approval.message || 'Approval required.'}</p>
              <button type="button" onClick={() => fetch(`/api/ui/workflows/${workflowId}/runs/${run.run_id}/approval`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ approved: true }),
              })}>Approve</button>
              <button type="button" onClick={() => fetch(`/api/ui/workflows/${workflowId}/runs/${run.run_id}/approval`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ approved: false }),
              })}>Reject</button>
            </div>
          ) : null}
          {run.last_error ? <p className="workflow-run-card__error">{run.last_error}</p> : null}
          {run.last_output ? <pre className="workflow-run-card__output">{run.last_output}</pre> : null}
        </article>
      ))}
    </div>
  );
}


function WorkflowsInner() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const { show } = useNotifications();
  const workflowsUrl = getApiUrl('workflows');
  const workflowRunsUrl = getApiUrl('workflowRuns');
  const documentTypesUrl = getApiUrl('documentTypes');
  const agentsUrl = getApiUrl('agentProfiles');
  const promptsUrl = getApiUrl('prompts');
  const realtime = useSelector(selectRealtime);
  const workflows = useSelector(selectWorkflows);
  const workflowsState = useSelector(selectWorkflowsState);
  const workflowRuns = useSelector(selectWorkflowRuns);
  const documentTypes = useSelector(selectDocumentTypes);
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('workflows-sidebar sess-list');
  const reactFlow = useReactFlow();
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedPath, setSelectedPath] = useState('');
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [sortMode, setSortMode] = useState('Recent');
  const [activeSidePanel, setActiveSidePanel] = useState(null);
  const [draftWorkflow, setDraftWorkflow] = useState(null);
  const [graphNodes, setGraphNodes] = useState([]);
  const [graphEdges, setGraphEdges] = useState([]);
  const [jsonDraft, setJsonDraft] = useState('');
  const [jsonError, setJsonError] = useState('');
  const [saving, setSaving] = useState(false);
  const [busyAction, setBusyAction] = useState('');
  const [localDraftDirty, setLocalDraftDirty] = useState(false);
  const [deleteConfirmationOpen, setDeleteConfirmationOpen] = useState(false);
  const requestedPath = withJsonExtension(searchParams.get('path') || '');
  const selectedDetail = useSelector((state) => selectWorkflowDetailByPath(state, selectedPath));
  const [nodeMenuAnchor, setNodeMenuAnchor] = useState(null);
  const [nodeOptionsId, setNodeOptionsId] = useState(null);
  const [canvasTool, setCanvasTool] = useState('pointer');
  const canvasInteraction = canvasToolSettings(canvasTool);
  const rightPointerRef = useRef(null);
  const [rightPanning, setRightPanning] = useState(false);
  const connectingRef = useRef(null);
  const didConnectRef = useRef(false);
  const selectedLatestRun = useSelector((state) => {
    const workflowId = selectedDetail?.workflow?.id || selectedPath.replace(/^.*\/|\.json$/g, '');
    return selectWorkflowLatestRunById(state, workflowId);
  });
  const nodesRef = useRef([]);
  const edgesRef = useRef([]);
  const createUnpackNodeRef = useRef(() => {});
  const viewportRef = useRef(DEFAULT_VIEWPORT);
  const syncedPathRef = useRef('');
  const syncedHashRef = useRef('');
  const hydratingGraphRef = useRef(false);

  const loadWorkflows = useCallback(async () => {
    try {
      await dispatch(fetchWorkflows({ workflowsUrl })).unwrap();
    } catch (error) {
      show('error', 'Workflow load failed', error.message);
    }
  }, [dispatch, show, workflowsUrl]);

  const loadRuns = useCallback(async () => {
    try {
      await dispatch(fetchWorkflowRuns({ workflowRunsUrl })).unwrap();
    } catch (error) {
      show('error', 'Workflow runs failed', error.message);
    }
  }, [dispatch, show, workflowRunsUrl]);

  const loadDocumentTypes = useCallback(async () => {
    try {
      await dispatch(fetchDocumentTypes({ documentTypesUrl })).unwrap();
    } catch (error) {
      show('error', 'Document types load failed', error.message);
    }
  }, [dispatch, show, documentTypesUrl]);

  // Routine nodes read their ports from the routine graphs in the roster.
  const loadAgentProfiles = useCallback(async () => {
    try {
      await dispatch(fetchAgentProfiles({ agentsUrl })).unwrap();
    } catch (error) {
      show('error', 'Routines load failed', error.message);
    }
  }, [agentsUrl, dispatch, show]);
  // Agent/Extract nodes pick from the prompt catalogue.
  const loadPrompts = useCallback(async () => {
    try {
      await dispatch(fetchPrompts({ promptsUrl })).unwrap();
    } catch (error) {
      show('error', 'Prompts load failed', error.message);
    }
  }, [dispatch, show, promptsUrl]);

  useEffect(() => {
    if (!realtime.connected) {
      return;
    }
    void loadWorkflows();
    void loadRuns();
    void loadDocumentTypes();
    void loadAgentProfiles();
    void loadPrompts();
  }, [loadAgentProfiles, loadDocumentTypes, loadPrompts, loadRuns, loadWorkflows, realtime.connected]);

  useEffect(() => {
    if (!workflows.length) {
      setSelectedPath('');
      return;
    }
    setSelectedPath((current) => {
      const next = workflows.find((item) => workflowPath(item.id) === requestedPath)?.id
        ? requestedPath
        : workflows.find((item) => workflowPath(item.id) === current)?.id
          ? current
          : workflowPath(workflows[0].id);
      return next;
    });
  }, [requestedPath, workflows]);

  useEffect(() => {
    const currentPath = searchParams.get('path') || '';
    const nextUrlPath = stripJsonExtension(selectedPath);
    if (!selectedPath || currentPath === nextUrlPath) {
      return;
    }
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('path', nextUrlPath);
    setSearchParams(nextParams, { replace: true });
  }, [searchParams, selectedPath, setSearchParams]);

  useEffect(() => {
    if (!realtime.connected || !selectedPath || selectedDetail) {
      return;
    }
    dispatch(fetchWorkflowContent({ workflowsUrl, path: selectedPath }))
      .unwrap()
      .catch((error) => show('error', 'Workflow detail load failed', error.message));
  }, [dispatch, realtime.connected, selectedDetail, selectedPath, show, workflowsUrl]);
  const visibleItems = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const filtered = workflows.filter((item) => {
      if (filter === 'enabled' && item.enabled === false) return false;
      if (filter === 'paused' && !item.paused) return false;
      const triggerText = (item.triggers || []).join(' ');
      const searchable = `${item.title} ${item.description} ${triggerText}`.toLowerCase();
      return !normalizedQuery || searchable.includes(normalizedQuery);
    });
    return [...filtered].sort((left, right) => {
      if (sortMode === 'Name') {
        return left.title.localeCompare(right.title);
      }
      const leftTime = Date.parse(left.updated_at || '') || 0;
      const rightTime = Date.parse(right.updated_at || '') || 0;
      return rightTime - leftTime;
    });
  }, [filter, query, sortMode, workflows]);

  const visibleRuns = useMemo(() => {
    const workflowId = selectedDetail?.workflow?.id;
    return workflowRuns.filter((run) => !workflowId || run.workflow_id === workflowId);
  }, [selectedDetail, workflowRuns]);

  const draftHash = useMemo(
    () => (draftWorkflow ? JSON.stringify(draftWorkflow) : ''),
    [draftWorkflow],
  );
  const selectedHash = useMemo(
    () => (selectedDetail?.workflow ? JSON.stringify(selectedDetail.workflow) : ''),
    [selectedDetail],
  );
  const hasUnsavedChanges = Boolean(localDraftDirty && selectedDetail?.workflow && draftHash && draftHash !== selectedHash);
  const nodeOptionsNode = graphNodes.find((node) => node.id === nodeOptionsId) || null;

  const syncWorkflowFromGraph = useCallback((nextNodes, nextEdges, nextViewport = viewportRef.current, markDirty = true) => {
    nodesRef.current = nextNodes;
    edgesRef.current = nextEdges;
    viewportRef.current = nextViewport;
    if (markDirty) {
      setLocalDraftDirty(true);
    }
    setDraftWorkflow((current) => (current ? flowToWorkflow(current, nextNodes, nextEdges, nextViewport) : current));
  }, []);

  // One callback set for every node, whichever path created it (hydration,
  // JSON apply, palette, Unpack). Reads graph state through refs, so it is
  // stable and never captures a stale graph.
  const nodeCallbacks = useMemo(() => {
    const commit = (nextNodes, nextEdges) => {
      if (nextEdges !== edgesRef.current) setGraphEdges(nextEdges);
      setGraphNodes(nextNodes);
      syncWorkflowFromGraph(nextNodes, nextEdges, viewportRef.current, !hydratingGraphRef.current);
    };
    const resizeNode = (nodeId, sizeFor) => {
      commit(nodesRef.current.map((node) => {
        if (node.id !== nodeId) return node;
        const { size, data } = sizeFor(node);
        // React Flow's NodeResizer writes width/height as top-level node
        // fields (taking precedence over style on later renders once a
        // manual resize has happened), so both must be kept in sync here.
        return {
          ...node,
          width: size.width,
          height: size.height,
          style: { ...node.style, width: size.width, height: size.height },
          data: { ...node.data, ...data },
        };
      }), edgesRef.current);
    };
    return {
      onNodeChange: (nodeId, patch) => {
        const node = nodesRef.current.find((candidate) => candidate.id === nodeId);
        if (!node) return;
        const patched = nodesRef.current.map((candidate) => (
          candidate.id === nodeId ? { ...candidate, data: { ...candidate.data, ...patch } } : candidate
        ));
        let nextEdges = edgesRef.current;
        let nextNodes = patched;
        const schemaChanged = patch.outputSchema !== undefined
          && JSON.stringify(node.data?.outputSchema) !== JSON.stringify(patch.outputSchema);
        const portsChanged = schemaChanged || (patch.inputSchema !== undefined
          && JSON.stringify(node.data?.inputSchema) !== JSON.stringify(patch.inputSchema));
        if (portsChanged && !hydratingGraphRef.current) {
          // Edges on ports the node no longer has go first; then every
          // downstream Unpack (chain) is re-derived and its dangling edges go.
          const removedPortEdges = edgesToRemovedPorts(nextEdges, patched.find((candidate) => candidate.id === nodeId));
          const removedIds = new Set(removedPortEdges.map((edge) => edge.id));
          const pruned = pruneLostUnpackEdges(patched, nextEdges.filter((edge) => !removedIds.has(edge.id)));
          const lostEdges = [...removedPortEdges, ...pruned.lostEdges];
          if (lostEdges.length > 0) {
            const edgeList = lostEdges.map((e) => `${e.source} → ${e.target}`).join(', ');
            if (!window.confirm(`This change will remove ${lostEdges.length} connection(s): ${edgeList}. Continue?`)) return;
          }
          nextNodes = pruned.nodes;
          nextEdges = pruned.edges;
        }
        commit(sizeUnpackNodes(nextNodes, nextEdges), nextEdges);
      },
      onDeleteNode: (nodeId) => {
        const directEdges = edgesRef.current.filter((edge) => edge.source !== nodeId && edge.target !== nodeId);
        const remaining = nodesRef.current.filter((node) => node.id !== nodeId);
        const pruned = pruneLostUnpackEdges(remaining, directEdges);
        if (pruned.lostEdges.length > 0) {
          const edgeList = pruned.lostEdges.map((edge) => `${edge.source} → ${edge.target}`).join(', ');
          if (!window.confirm(`Removing this node will disconnect ${pruned.lostEdges.length} connection(s): ${edgeList}. Continue?`)) return;
        }
        commit(sizeUnpackNodes(pruned.nodes, pruned.edges), pruned.edges);
      },
      onUnpack: (sourceNodeId, sourcePort) => {
        const sourceNode = nodesRef.current.find((n) => n.id === sourceNodeId);
        if (!sourceNode) return;
        createUnpackNodeRef.current(sourceNodeId, sourcePort, {
          x: sourceNode.position.x + (sourceNode.width || 320) + 40,
          y: sourceNode.position.y,
        });
      },
      onOpenOptions: setNodeOptionsId,
      onToggleExpanded: (nodeId) => resizeNode(nodeId, (node) => {
        const expanded = !node.data.expanded;
        return { size: nodeCurrentSize(node, expanded), data: { expanded } };
      }),
      onResize: (nodeId, size) => resizeNode(nodeId, (node) => ({
        size,
        data: { [node.data.expanded ? 'settingsSize' : 'dataSize']: size },
      })),
    };
  }, [setGraphEdges, setGraphNodes, syncWorkflowFromGraph]);

  // Node data fields expected by the node components, for nodes built here
  // rather than through workflowToFlow/buildNodeData.
  const nodeDataCallbacks = useMemo(() => ({
    onChange: nodeCallbacks.onNodeChange,
    onDelete: nodeCallbacks.onDeleteNode,
    onUnpack: nodeCallbacks.onUnpack,
    onOpenOptions: nodeCallbacks.onOpenOptions,
    onToggleExpanded: nodeCallbacks.onToggleExpanded,
    onResize: nodeCallbacks.onResize,
  }), [nodeCallbacks]);

  const syncDraftFromWorkflow = useCallback((workflow, latestRun, path, preserveDirty = false) => {
    if (!workflow) {
      setDraftWorkflow(null);
      setGraphNodes([]);
      setGraphEdges([]);
      setJsonDraft('');
      setJsonError('');
      return;
    }
    const nextHash = JSON.stringify(workflow);
    if (preserveDirty && hasUnsavedChanges && syncedPathRef.current === path) {
      return;
    }
    syncedPathRef.current = path;
    hydratingGraphRef.current = true;
    setLocalDraftDirty(false);
    syncedHashRef.current = nextHash;
    const cloned = cloneValue(workflow);
    const flow = workflowToFlow(cloned, nodeCallbacks, latestRun);
    flow.nodes = sizeUnpackNodes(flow.nodes, flow.edges);
    nodesRef.current = flow.nodes;
    edgesRef.current = flow.edges;
    viewportRef.current = flow.viewport;
    setDraftWorkflow(cloned);
    setGraphNodes(flow.nodes);
    setGraphEdges(flow.edges);
    setJsonDraft(`${JSON.stringify(cloned, null, 2)}\n`);
    setJsonError('');
    requestAnimationFrame(() => {
      if (flow.nodes.length) {
        reactFlow.setViewport(flow.viewport || DEFAULT_VIEWPORT);
        // Preserve the workflow's stored viewport without emitting synthetic
        // node-position changes that look like unsaved edits.
      }
      setTimeout(() => {
        hydratingGraphRef.current = false;
      }, 250);
    });
  }, [hasUnsavedChanges, nodeCallbacks, reactFlow]);

  useEffect(() => {
    if (!selectedDetail?.workflow || !selectedPath) {
      return;
    }
    const selectedWorkflowHash = JSON.stringify(selectedDetail.workflow);
    if (syncedPathRef.current === selectedPath && syncedHashRef.current === selectedWorkflowHash && hasUnsavedChanges) {
      return;
    }
    syncDraftFromWorkflow(selectedDetail.workflow, selectedLatestRun, selectedPath, true);
  }, [hasUnsavedChanges, selectedDetail, selectedLatestRun, selectedPath, syncDraftFromWorkflow]);

  useEffect(() => {
    if (!draftWorkflow) {
      setJsonDraft('');
      return;
    }
    setJsonDraft(`${JSON.stringify(draftWorkflow, null, 2)}\n`);
  }, [draftWorkflow]);

  useEffect(() => {
    if (!nodesRef.current.length) {
      return;
    }
    setGraphNodes((currentNodes) => {
      const nextNodes = applyRunStateToNodes(currentNodes, selectedLatestRun);
      nodesRef.current = nextNodes;
      return nextNodes;
    });
  }, [selectedLatestRun]);

  const updateDraft = useCallback((updater) => {
    setDraftWorkflow((current) => {
      if (!current) {
        return current;
      }
      const next = typeof updater === 'function' ? updater(current) : updater;
    setLocalDraftDirty(true);
      return next;
    });
  }, []);

  // Graph handlers compute from the refs synchronously and only then set
  // state. Side effects inside a state updater run later, against the state
  // of that moment: a queued selection change would re-sync a graph that a
  // removal had already left (Cut put the edge back into the draft).
  const onNodesChange = useCallback((changes) => {
    const nextNodes = applyNodeChanges(changes, nodesRef.current);
    nodesRef.current = nextNodes;
    setGraphNodes(nextNodes);
    if (hydratingGraphRef.current) return;
    const marksDirty = changes.some((change) => (
      change.type === 'add' || change.type === 'remove' || change.dragging === true
    ));
    syncWorkflowFromGraph(nextNodes, edgesRef.current, viewportRef.current, marksDirty);
  }, [syncWorkflowFromGraph]);

  const portFromHandle = useCallback((handleId) => (
    handleId?.startsWith('out:') || handleId?.startsWith('in:')
      ? handleId.slice(handleId.indexOf(':') + 1)
      : ''
  ), []);

  const onEdgesChange = useCallback((changes) => {
    const removingValueEdges = changes
      .filter((c) => c.type === 'remove')
      .map((c) => edgesRef.current.find((e) => e.id === c.id))
      .filter((edge) => {
        if (!edge) return false;
        const targetNode = nodesRef.current.find((n) => n.id === edge.target);
        return targetNode?.data?.kind === 'unpack' && portFromHandle(edge.targetHandle) === 'value';
      });

    if (removingValueEdges.length > 0 && !hydratingGraphRef.current) {
      const nextEdges = applyEdgeChanges(changes, edgesRef.current);
      const pruned = pruneLostUnpackEdges(nodesRef.current, nextEdges);
      if (pruned.lostEdges.length > 0) {
        const edgeList = pruned.lostEdges.map((e) => `${e.source} → ${e.target}`).join(', ');
        const confirmed = window.confirm(
          `Removing the value input will disconnect ${pruned.lostEdges.length} connection(s): ${edgeList}. Continue?`,
        );
        if (!confirmed) return;
      }
      const sizedNodes = sizeUnpackNodes(pruned.nodes, pruned.edges);
      setGraphNodes(sizedNodes);
      setGraphEdges(pruned.edges);
      syncWorkflowFromGraph(sizedNodes, pruned.edges);
      return;
    }

    const nextEdges = applyEdgeChanges(changes, edgesRef.current);
    edgesRef.current = nextEdges;
    setGraphEdges(nextEdges);
    if (hydratingGraphRef.current) return;
    const marksDirty = changes.some((change) => change.type === 'add' || change.type === 'remove');
    const recomputed = recomputeUnpackSchemas(nodesRef.current, nextEdges);
    if (recomputed !== nodesRef.current) setGraphNodes(recomputed);
    syncWorkflowFromGraph(recomputed, nextEdges, viewportRef.current, marksDirty);
  }, [syncWorkflowFromGraph, portFromHandle]);

  const handleEdgeClick = useCallback((event, edge) => {
    if (canvasTool !== 'cut') return;
    event.preventDefault();
    event.stopPropagation();
    // Same path as a keyboard/edge-menu removal, so Unpack schemas are
    // recomputed and dependent edges confirmed and pruned.
    onEdgesChange([{ type: 'remove', id: edge.id }]);
  }, [canvasTool, onEdgesChange]);

  const validateConnection = useCallback((connection) => {
    const source = nodesRef.current.find((node) => node.id === connection.source);
    const target = nodesRef.current.find((node) => node.id === connection.target);
    if (!source || !target) return 'Both workflow nodes must exist before connecting them.';
    if (source.id === target.id) return 'A workflow node cannot connect to itself.';
    const sourcePort = portFromHandle(connection.sourceHandle);
    const targetPort = portFromHandle(connection.targetHandle);
    const sourceProperty = nodeOutputPorts(source.data?.kind, source.data?.outputSchema)
      .find((property) => property.name === sourcePort);
    const targetProperty = nodeInputPorts(target.data?.kind, target.data?.inputSchema)
      .find((property) => property.name === targetPort);
    if (!sourceProperty) return `Source node has no output port '${sourcePort}'.`;
    if (!targetProperty) return `Target node has no input port '${targetPort}'.`;
    if (edgesRef.current.some((edge) => edge.target === target.id && edge.targetHandle === connection.targetHandle)) {
      return `Input port '${targetPort}' already has a source.`;
    }
    if (!schemasAreCompatible(sourceProperty.definition, targetProperty.definition)) {
      return `Cannot connect ${schemaType(sourceProperty.definition)} output '${sourceProperty.name}' to ${schemaType(targetProperty.definition)} input '${targetPort}'.`;
    }
    return '';
  }, [portFromHandle]);

  const onConnect = useCallback((connection) => {
    didConnectRef.current = true;
    const validationError = validateConnection(connection);
    if (validationError) {
      show('error', 'Invalid workflow connection', validationError);
      return;
    }
    const nextEdges = addEdge(
      { ...connection, ...DEFAULT_EDGE_OPTIONS, className: 'workflow-edge--data' },
      edgesRef.current,
    );
    const recomputed = recomputeUnpackSchemas(nodesRef.current, nextEdges);
    setGraphEdges(nextEdges);
    if (recomputed !== nodesRef.current) setGraphNodes(recomputed);
    syncWorkflowFromGraph(recomputed, nextEdges, viewportRef.current);
  }, [show, syncWorkflowFromGraph, validateConnection]);

  const onConnectStart = useCallback((_event, { nodeId, handleId, handleType }) => {
    if (handleType === 'source') {
      const node = nodesRef.current.find((n) => n.id === nodeId);
      const port = portFromHandle(handleId);
      const prop = nodeOutputPorts(node?.data?.kind, node?.data?.outputSchema).find((p) => p.name === port);
      if (isUnpackableSchema(prop?.definition)) {
        connectingRef.current = { nodeId, handleId, port };
        didConnectRef.current = false;
      }
    }
  }, [portFromHandle]);

  const onConnectEnd = useCallback((event) => {
    if (!connectingRef.current) return;
    if (!didConnectRef.current) {
      const position = reactFlow.screenToFlowPosition({ x: event.clientX, y: event.clientY });
      createUnpackNodeRef.current(connectingRef.current.nodeId, connectingRef.current.port, position);
    }
    connectingRef.current = null;
    didConnectRef.current = false;
  }, [reactFlow]);

  const handleViewportChange = useCallback((_event, viewport) => {
    viewportRef.current = viewport;
    if (nodesRef.current.length && edgesRef.current.length >= 0) {
      setDraftWorkflow((current) => (current ? flowToWorkflow(current, nodesRef.current, edgesRef.current, viewport) : current));
    }
  }, []);

  const handleSelectWorkflow = (path) => {
    setJsonError('');
    setSelectedPath(path);
  };

  const openCreateWorkflowChat = () => {
    window.dispatchEvent(new CustomEvent('progenitor:open-quick-chat', {
      detail: { mode: 'workflow-create' },
    }));
  };

  const handleAddNode = useCallback((kind, dropPosition) => {
    if (!draftWorkflow || !DOCK_NODE_DEFAULTS[kind]) {
      return;
    }
    const nodeId = `${kind}_${Math.random().toString(36).slice(2, 10)}`;
    const position = dropPosition || nextDockNodePosition(nodesRef.current);
    const nextNodes = [
      ...nodesRef.current,
      createPaletteNode(kind, nodeId, position, documentTypes, nodeDataCallbacks),
    ];
    const nextEdges = edgesRef.current;
    setGraphNodes(nextNodes);
    setGraphEdges(nextEdges);
    syncWorkflowFromGraph(nextNodes, nextEdges, viewportRef.current);
  }, [draftWorkflow, documentTypes, nodeDataCallbacks, setGraphEdges, setGraphNodes, syncWorkflowFromGraph]);

  const createUnpackNode = useCallback((sourceNodeId, sourcePort, position) => {
    const nodeId = `unpack_${Math.random().toString(36).slice(2, 10)}`;
    const sizes = DEFAULT_NODE_SIZES.unpack;
    const newNode = {
      id: nodeId,
      type: 'unpack',
      width: sizes.data.width,
      height: sizes.data.height,
      style: { width: sizes.data.width, height: sizes.data.height },
      position,
      data: {
        kind: 'unpack',
        title: 'Unpack',
        inputSchema: { type: 'object', required: ['value'], properties: { value: { type: 'object' } } },
        outputSchema: { type: 'object', properties: {} },
        currentNodeId: null,
        completedNodeIds: [],
        expanded: false,
        ...nodeDataCallbacks,
        canDelete: true,
      },
    };
    const newEdge = {
      id: `edge_${sourceNodeId}_${sourcePort}_${nodeId}_value`,
      source: sourceNodeId,
      sourceHandle: `out:${sourcePort}`,
      target: nodeId,
      targetHandle: 'in:value',
      ...DEFAULT_EDGE_OPTIONS,
      className: 'workflow-edge--data',
    };
    const nextNodes = [...nodesRef.current, newNode];
    const nextEdges = [...edgesRef.current, newEdge];
    const recomputed = recomputeUnpackSchemas(nextNodes, nextEdges);
    setGraphNodes(recomputed);
    setGraphEdges(nextEdges);
    syncWorkflowFromGraph(recomputed, nextEdges);
  }, [nodeDataCallbacks, setGraphEdges, setGraphNodes, syncWorkflowFromGraph]);
  // nodeCallbacks.onUnpack and drag-to-pane call this through the ref: it
  // builds nodes with nodeDataCallbacks, so it is declared after them.
  createUnpackNodeRef.current = createUnpackNode;

  const onCanvasPaneClick = useCallback((event) => {
    if (event.button === 0) {
      setNodeMenuAnchor(null);
    }
  }, []);

  const onCanvasPointerDown = useCallback((event) => {
    if (event.button === 2) {
      rightPointerRef.current = { x: event.clientX, y: event.clientY, moved: false };
      setRightPanning(true);
    }
  }, []);

  // Right-drag pans the canvas; show the grab cursor for as long as the right
  // button is held, wherever it is released.
  useEffect(() => {
    if (!rightPanning) return undefined;
    const release = (event) => {
      if (event.type === 'blur' || !(event.buttons & 2)) setRightPanning(false);
    };
    window.addEventListener('pointerup', release);
    window.addEventListener('blur', release);
    return () => {
      window.removeEventListener('pointerup', release);
      window.removeEventListener('blur', release);
    };
  }, [rightPanning]);

  const onCanvasPointerMove = useCallback((event) => {
    const pointer = rightPointerRef.current;
    if (!pointer || event.buttons !== 2) return;
    if (Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) > 4) {
      pointer.moved = true;
    }
  }, []);

  const onCanvasPaneContextMenu = useCallback((event) => {
    event.preventDefault();
    const pointer = rightPointerRef.current;
    rightPointerRef.current = null;
    if (pointer?.moved) return;
    setNodeMenuAnchor(canvasMenuAnchor(event, reactFlow));
  }, [reactFlow]);

  const onCanvasDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onCanvasDrop = useCallback((event) => {
    event.preventDefault();
    const kind = event.dataTransfer.getData('application/workflow-node');
    if (!DOCK_NODE_DEFAULTS[kind]) {
      return;
    }
    const position = reactFlow.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    handleAddNode(kind, position);
  }, [handleAddNode, reactFlow]);

  const handleFieldChange = (field, value) => {
    updateDraft((current) => ({ ...current, [field]: value }));
  };

  const handleApplyJson = () => {
    try {
      const parsed = JSON.parse(jsonDraft);
      const flow = workflowToFlow(parsed, nodeCallbacks, selectedLatestRun);
      const nextNodes = sizeUnpackNodes(flow.nodes, flow.edges);
      setDraftWorkflow(parsed);
      setGraphNodes(nextNodes);
      setGraphEdges(flow.edges);
      nodesRef.current = nextNodes;
      edgesRef.current = flow.edges;
      viewportRef.current = flow.viewport;
      setLocalDraftDirty(true);
      setJsonError('');
      setActiveSidePanel(null);
    } catch (error) {
      setJsonError(error.message || 'Invalid workflow JSON');
    }
  };

  const handleResetJson = () => {
    if (!draftWorkflow) {
      return;
    }
    setJsonDraft(`${JSON.stringify(draftWorkflow, null, 2)}\n`);
    setJsonError('');
  };

  const handleSaveWorkflow = async () => {
    if (!selectedPath || !draftWorkflow) {
      return;
    }
    setSaving(true);
    try {
      await dispatch(saveWorkflow({
        workflowsUrl,
        path: selectedPath,
        workflow: draftWorkflow,
      })).unwrap();
    } catch (error) {
      show('error', 'Workflow save failed', error.message);
    } finally {
      setSaving(false);
    }
  };

  const handlePauseResume = async () => {
    const workflowId = selectedDetail?.workflow?.id;
    if (!workflowId) {
      return;
    }
    setBusyAction(selectedDetail.workflow.paused ? 'resume' : 'pause');
    try {
      const action = selectedDetail.workflow.paused ? resumeWorkflow : pauseWorkflow;
      await dispatch(action({ workflowsUrl, workflowId })).unwrap();
    } catch (error) {
      show('error', 'Workflow update failed', error.message);
    } finally {
      setBusyAction('');
    }
  };

  const handleTrigger = async () => {
    const workflowId = selectedDetail?.workflow?.id;
    if (!workflowId) {
      return;
    }
    setBusyAction('trigger');
    try {
      await dispatch(triggerWorkflow({
        workflowsUrl,
        workflowId,
      })).unwrap();
    } catch (error) {
      show('error', 'Workflow trigger failed', error.message);
    } finally {
      setBusyAction('');
    }
  };

  const handleDeleteWorkflow = () => {
    if (selectedDetail?.workflow?.id) {
      setDeleteConfirmationOpen(true);
    }
  };

  const confirmDeleteWorkflow = async () => {
    const workflowId = selectedDetail?.workflow?.id;
    if (!workflowId) {
      setDeleteConfirmationOpen(false);
      return;
    }
    setBusyAction('delete');
    try {
      await dispatch(deleteWorkflow({ workflowsUrl, workflowId })).unwrap();
      setDeleteConfirmationOpen(false);
      setSelectedPath('');
      const nextParams = new URLSearchParams(searchParams);
      nextParams.delete('path');
      setSearchParams(nextParams, { replace: true });
    } catch (error) {
      show('error', 'Workflow delete failed', error.message);
    } finally {
      setBusyAction('');
    }
  };

  return (
    <PageShell className="workflows-page" label="Workflows">
      <PaneSection as="aside" className={paneClassName} aria-label="Workflows" onItemSelect={() => setCollapsed(true)}>
        {collapsed ? (
          <PaneExpandRail label="Workflows" onExpand={() => setCollapsed(false)} />
        ) : (
          <>
            <PaneCollapseSeam label="Workflows" onCollapse={() => setCollapsed(true)} />
            <button type="button" className="pane-mobile-scrim" aria-label="Close workflows list" onClick={() => setCollapsed(true)} />
            <PaneListFilters
              filters={FILTERS}
              activeFilter={filter}
              onFilterChange={setFilter}
              actionLabel="+ New"
              onAction={openCreateWorkflowChat}
            />
            <PaneListToolbar>
              <PaneListSearch value={query} placeholder="Search workflows" onChange={setQuery} />
              <PaneListSortButton title="Sort workflows" options={SORTS} value={sortMode} onSelect={setSortMode} />
            </PaneListToolbar>
            {workflowsState.loading && !workflowsState.loaded ? <PaneEmptyState>Loading workflows...</PaneEmptyState> : null}
            {workflowsState.error && !workflows.length ? <PaneEmptyState>Failed to load workflows.</PaneEmptyState> : null}
            {!workflowsState.loading && !visibleItems.length ? <PaneEmptyState>No workflows match the current filters.</PaneEmptyState> : null}
            {!workflowsState.loading && visibleItems.length ? (
              <PaneListRows
                className="workspace-sidebar__scroll"
                items={visibleItems}
                getItemKey={(item) => item.id}
                renderItem={(item) => (
                  <WorkflowRow
                    item={item}
                    latestRun={workflowRuns.find((run) => run.workflow_id === item.id) || null}
                    selectedPath={selectedPath}
                    onSelect={handleSelectWorkflow}
                  />
                )}
              />
            ) : null}
            <PaneListFooter>
              <span><span className="num">{workflows.length}</span> workflows</span>
            </PaneListFooter>
          </>
        )}
      </PaneSection>
      {deleteConfirmationOpen && selectedDetail?.workflow ? (
        <PopupModal
          eyebrow="Delete workflow"
          title={`Delete “${selectedDetail.workflow.title}”?`}
          message="This workflow and its configuration will be permanently deleted."
          confirmLabel="Delete workflow"
          busyLabel="Deleting…"
        onClose={() => setDeleteConfirmationOpen(false)}
        onConfirm={confirmDeleteWorkflow}
        busy={busyAction === 'delete'}
      />
      ) : null}

      <DetailPane flush className={`workflow-detail ${selectedDetail?.workflow ? 'is-graph-mode' : ''}`} label="Workflow detail">
        {!selectedPath && !workflowsState.loading ? (
          <PaneEmptyState detail>Select a workflow to edit or create a new one.</PaneEmptyState>
        ) : null}

        {selectedPath && !selectedDetail ? (
          <PaneEmptyState detail>Loading workflow detail...</PaneEmptyState>
        ) : null}

        {selectedDetail?.workflow && draftWorkflow ? (
          <div className="detail-pane__body detail-pane__body--graph">
            <div className="agents-graph-layout">
              <section className="agents-graph-panel">
                <div className="agents-graph-shell">
                  <div
                    className={`canvas-surface workflow-canvas workflow-canvas--${canvasTool}${rightPanning ? ' workflow-canvas--right-panning' : ''}`}
                    onDrop={onCanvasDrop}
                    onDragOver={onCanvasDragOver}
                    onPointerDown={onCanvasPointerDown}
                    onPointerMove={onCanvasPointerMove}
                  >
                    <div className="agents-graph-floating">
                      <DetailHeaderActions menuLabel="Workflow actions">
                        <DetailHeaderAction
                          icon={<PlayActionIcon />}
                          label="Run"
                          tooltip="Run workflow now"
                          ariaLabel="Run workflow now"
                          onClick={handleTrigger}
                          disabled={busyAction === 'trigger'}
                        />
                        <DetailHeaderAction
                          icon={draftWorkflow.paused ? <PlayActionIcon /> : <PauseActionIcon />}
                          label={draftWorkflow.paused ? 'Resume' : 'Pause'}
                          tooltip={draftWorkflow.paused ? 'Resume workflow' : 'Pause workflow'}
                          ariaLabel={draftWorkflow.paused ? 'Resume workflow' : 'Pause workflow'}
                          onClick={handlePauseResume}
                          disabled={busyAction === 'pause' || busyAction === 'resume'}
                        />
                        <DetailHeaderAction
                          icon={<SaveActionIcon />}
                          label="Save"
                          tooltip="Save workflow"
                          ariaLabel="Save workflow"
                          onClick={handleSaveWorkflow}
                          disabled={!hasUnsavedChanges || saving}
                        />
                        <DetailHeaderAction
                          icon={<DeleteActionIcon />}
                          label="Delete"
                          tooltip="Delete workflow"
                          ariaLabel="Delete workflow"
                          variant="danger"
                          onClick={handleDeleteWorkflow}
                          disabled={busyAction === 'delete'}
                        />
                      </DetailHeaderActions>
                    </div>
                    <ReactFlow
                      nodes={graphNodes}
                      edges={graphEdges}
                      nodeTypes={NODE_TYPES}
                      onNodesChange={onNodesChange}
                      onEdgesChange={onEdgesChange}
                      onConnect={onConnect}
                      onConnectStart={onConnectStart}
                      onConnectEnd={onConnectEnd}
                      onEdgeClick={handleEdgeClick}
                      panOnDrag={canvasInteraction.panOnDrag}
                      selectionOnDrag={canvasInteraction.selectionOnDrag}
                      nodesDraggable={canvasInteraction.nodesDraggable}
                      nodesConnectable={canvasInteraction.nodesConnectable}
                      nodesFocusable
                      deleteKeyCode={['Backspace', 'Delete']}
                      onPaneClick={onCanvasPaneClick}
                      onPaneContextMenu={onCanvasPaneContextMenu}
                      onNodeDragStart={() => setNodeMenuAnchor(null)}
                      snapToGrid
                      snapGrid={[16, 16]}
                      onMoveEnd={handleViewportChange}
                      defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
                      defaultViewport={DEFAULT_VIEWPORT}
                      fitView={false}
                      minZoom={0.2}
                      maxZoom={2.5}
                    >
                      <Background gap={16} size={1} color="var(--color-border)" variant="dots" />
                      <Controls showInteractive={false} />
                      <MiniMap
                        style={MINIMAP_STYLE}
                        nodeColor={nodeColor}
                        maskColor="rgba(0, 0, 0, 0.55)"
                        pannable
                        zoomable
                      />
                    </ReactFlow>
                    {nodeOptionsNode ? (
                      <WorkflowNodeOptionsModal
                        key={nodeOptionsNode.id}
                        node={nodeOptionsNode}
                        onChange={(nodeId, patch) => nodeOptionsNode.data.onChange(nodeId, patch)}
                        onClose={() => setNodeOptionsId(null)}
                      />
                    ) : null}
                    <WorkflowDock menuAnchor={nodeMenuAnchor} onOpenMenu={setNodeMenuAnchor} onCloseMenu={() => setNodeMenuAnchor(null)} onAdd={handleAddNode} tool={canvasTool} onToolChange={setCanvasTool} />
                  </div>
                </div>
              </section>

              {activeSidePanel ? (
                <aside className="workflow-details-panel">
                  <button
                    type="button"
                    className="workflow-details-panel__collapse"
                    onClick={() => setActiveSidePanel(null)}
                    title="Collapse"
                    aria-label="Collapse panel"
                  >
                    <ChevronRightSmall />
                  </button>
                  <header className="workflow-details-panel__head">
                    <h2>
                      {activeSidePanel === 'details' ? 'Workflow details' : null}
                      {activeSidePanel === 'json' ? 'Edit workflow as JSON' : null}
                      {activeSidePanel === 'runs' ? 'Run history' : null}
                    </h2>
                  </header>
                  <div className="workflow-details-panel__content">
                    {activeSidePanel === 'details' ? (
                      <section className="workflow-form">
                        <label>
                          <span>Title</span>
                          <input
                            value={draftWorkflow.title}
                            onChange={(event) => handleFieldChange('title', event.target.value)}
                            placeholder="Workflow title"
                          />
                        </label>
                        <label className="workflow-form__toggle">
                          <span>Enabled</span>
                          <input
                            type="checkbox"
                            checked={draftWorkflow.enabled !== false}
                            onChange={(event) => handleFieldChange('enabled', event.target.checked)}
                          />
                        </label>
                        <label className="workflow-form__toggle">
                          <span>Notify when paused</span>
                          <input
                            type="checkbox"
                            aria-label="Notify when paused"
                            checked={draftWorkflow.notify_on_pause === true}
                            onChange={(event) => handleFieldChange('notify_on_pause', event.target.checked)}
                          />
                        </label>
                        <label className="workflow-form__full">
                          <span>Description</span>
                          <textarea
                            value={draftWorkflow.description || ''}
                            onChange={(event) => handleFieldChange('description', event.target.value)}
                            placeholder="Describe what this workflow does."
                          />
                        </label>
                      </section>
                    ) : null}

                    {activeSidePanel === 'json' ? (
                      <>
                        <JsonCodeEditor value={jsonDraft} onChange={(event) => setJsonDraft(event.target.value)} />
                        {jsonError ? <p className="workflow-json-tab__error">{jsonError}</p> : null}
                        <div className="workflow-json-tab__actions">
                          <button type="button" className="node-action-btn" onClick={handleResetJson}>
                            Reset
                          </button>
                          <button type="button" className="node-action-btn primary" onClick={handleApplyJson}>
                            Apply
                          </button>
                        </div>
                      </>
                    ) : null}

                    {activeSidePanel === 'runs' ? (
                      <WorkflowRunsList
                        runs={visibleRuns}
                        loading={workflowsState.runsLoading && !workflowsState.runsLoaded}
                        workflowId={selectedDetail?.workflow?.id}
                      />
                    ) : null}
                  </div>
                </aside>
              ) : (
                <WorkflowSideDock onSelect={setActiveSidePanel} />
              )}
            </div>
          </div>
        ) : null}
      </DetailPane>
    </PageShell>
  );
}

// This helper is exported for the focused workflow graph unit test.
// eslint-disable-next-line react-refresh/only-export-components
export { flowToWorkflow, workflowToFlow, NODE_MENU_CATEGORIES, schemaProperties, canvasMenuAnchor, visibleNodePortProperties, deriveUnpackSchemas, recomputeUnpackSchemas, computeLostEdgesForUnpackChange, pruneLostUnpackEdges, isUnpackableSchema, parsePromptPlaceholders, derivePromptInputSchema, promptTemplatePatch, promptDocumentPatch, createPaletteNode, nodeInputPorts, nodeOutputPorts, schemasAreCompatible };
export default function WorkflowsPage() {
  return (
    <ReactFlowProvider>
      <WorkflowsInner />
    </ReactFlowProvider>
  );
}
