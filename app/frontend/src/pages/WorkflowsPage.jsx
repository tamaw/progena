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
  fetchDocumentTypes,
  fetchWorkflowContent,
  fetchWorkflows,
  fetchWorkflowRuns,
  pauseWorkflow,
  resumeWorkflow,
  saveWorkflow,
  selectDocumentTypes,
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
  data: {
    data: { width: 320, height: 140 },
    settings: { width: 380, height: 280 },
  },
  mcp: {
    data: { width: 340, height: 160 },
    settings: { width: 420, height: 300 },
  },
  condition: {
    data: { width: 300, height: 130 },
    settings: { width: 380, height: 220 },
  },
  approval: {
    data: { width: 320, height: 130 },
    settings: { width: 400, height: 240 },
  },
  human_input: {
    data: { width: 320, height: 150 },
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
};
// V3 node menu: generic agent, data, event, mcp, condition, approval, and
// human_input nodes only. No capability-specific kinds.
const NODE_MENU_CATEGORIES = [
  { id: 'triggers', label: 'Triggers', items: [{ id: 'event', kind: 'event', label: 'Event', icon: 'E' }] },
  { id: 'agents', label: 'Agents', items: [{ id: 'agent', kind: 'agent', label: 'Agent', icon: 'A' }] },
  { id: 'data', label: 'Data', items: [
    { id: 'data', kind: 'data', label: 'Data', icon: 'D' },
    { id: 'document_read', kind: 'document_read', label: 'Read document', icon: 'R' },
    { id: 'document_query', kind: 'document_query', label: 'Query documents', icon: 'Q' },
  ]} ,
  { id: 'tools', label: 'Tools', items: [{ id: 'mcp', kind: 'mcp', label: 'MCP Tool', icon: 'M' }] },
  {
    id: 'logic',
    label: 'Logic',
    items: [
      { id: 'condition', kind: 'condition', label: 'Condition', icon: '◇' },
      { id: 'approval', kind: 'approval', label: 'Approval', icon: '✓' },
      { id: 'human_input', kind: 'human_input', label: 'Human Input', icon: 'H' },
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

const DOCK_NODE_DEFAULTS = {
  event: defaultEventNode,
  agent: () => ({
    title: 'Agent',
    message: 'Handle this workflow event.',
    inputSchema: { type: 'object', properties: {} },
    outputSchema: { type: 'object', properties: {} },
  }),
  data: () => ({ title: 'Data', value: null, outputSchema: { type: 'object' } }),
  mcp: () => ({
    title: 'MCP Tool',
    inputSchema: {
      type: 'object',
      required: ['tool', 'arguments'],
      properties: {
        tool: {
          title: 'McpTool',
          type: 'object',
          properties: { server_id: { type: 'string' }, tool_name: { type: 'string' } },
        },
        arguments: { type: 'object' },
      },
    },
    outputSchema: { type: 'object' },
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
  human_input: () => ({
    title: 'Human Input',
    message: 'Provide a value to continue.',
    inputSchema: { type: 'object', properties: {} },
    outputSchema: { type: 'object', properties: {} },
  }),
  document_read: defaultReadNode,
  document_query: defaultQueryNode,
};

function resolveNodeSizes(kind, storedSize, inputSchema, outputSchema) {
  const defaults = DEFAULT_NODE_SIZES[kind] || DEFAULT_NODE_SIZES.agent;
  const inputPortCount = nodeInputPorts(kind, inputSchema).length;
  const outputPortCount = nodeOutputPorts(kind, outputSchema).length;
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

const NODE_MINIMAP_COLORS = {
  event: '#ffa657',
  agent: '#58a6ff',
  data: '#3fb950',
  mcp: '#a371f7',
  condition: '#d29922',
  approval: '#f85149',
  human_input: '#39c5cf',
  document_read: '#79c0ff',
  document_query: '#a5d6ff',
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
  if (kind === 'agent') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Agent',
      message: node.message || '',
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
      value: cloneValue(node.value) || null,
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
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || { type: 'string', enum: ['approved', 'rejected'] },
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'mcp') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'MCP Tool',
      inputSchema: cloneValue(node.input_schema) || null,
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
  if (kind === 'human_input') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Human Input',
      message: node.message || '',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
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
      : node;
    return {
      id: nodeId,
      type: kind,
      position,
      width: sizes.data.width,
      height: sizes.data.height,
      style: { width: sizes.data.width, height: sizes.data.height },
      data: buildNodeData(kind, nodeData, callbacks, latestRun),
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

function applyRunStateToNodes(nodes, latestRun) {
  const { currentNodeId, completedNodeIds } = workflowRunState(latestRun);
  return nodes.map((node) => {
    const nextData = node.type === 'event'
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
    } else if (node.type === 'data') {
      graphNodes[node.id] = {
        kind: 'data',
        title: String(node.data?.title || 'Data'),
        value: cloneValue(node.data?.value) ?? null,
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object' },
      };
    } else if (node.type === 'mcp') {
      graphNodes[node.id] = {
        kind: 'mcp',
        title: String(node.data?.title || 'MCP Tool'),
        input_schema: cloneValue(node.data?.inputSchema) || DOCK_NODE_DEFAULTS.mcp().inputSchema,
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object' },
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
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'string', enum: ['approved', 'rejected'] },
      };
    } else if (node.type === 'human_input') {
      graphNodes[node.id] = {
        kind: 'human_input',
        title: String(node.data?.title || 'Human Input'),
        message: String(node.data?.message || ''),
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object', properties: {} },
      };
    } else {
      graphNodes[node.id] = {
        kind: 'agent',
        title: String(node.data?.title || 'Agent'),
        message: String(node.data?.message || ''),
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object', properties: {} },
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
  if (kind === 'data' || kind === 'event') {
    return [];
  }
  return schemaProperties(inputSchema);
}

function schemaTypes(definition) {
  if (Array.isArray(definition?.type)) return definition.type;
  return typeof definition?.type === 'string' ? [definition.type] : [];
}

// Structural compatibility: same JSON type, and matching title if both
// declare one. Mirrors _schema_types_compatible() in app/progenitor/workflows.py.
function schemasAreCompatible(sourceDefinition, targetDefinition) {
  const sourceTypes = schemaTypes(sourceDefinition);
  const targetTypes = schemaTypes(targetDefinition);
  if (sourceTypes.length && targetTypes.length && !sourceTypes.some((type) => targetTypes.includes(type))) {
    return false;
  }
  const sourceTitle = sourceDefinition?.title;
  const targetTitle = targetDefinition?.title;
  return !(sourceTitle && targetTitle && sourceTitle !== targetTitle);
}

function portFromHandle(handle) {
  return handle?.includes(':') ? handle.slice(handle.indexOf(':') + 1) : handle;
}

function visibleNodePortProperties(properties, nodeHeight, connectedNames = []) {
  if (!Number.isFinite(nodeHeight)) return properties;
  const connectedSet = new Set(connectedNames);
  const connected = properties.filter((p) => connectedSet.has(p.name));
  const unconnected = properties.filter((p) => !connectedSet.has(p.name));
  const maxPorts = Math.max(0, Math.floor((nodeHeight - 8 - 61) / 28) + 1);
  const remainingSlots = Math.max(0, maxPorts - connected.length);
  return [...connected, ...unconnected.slice(0, remainingSlots)];
}

function WorkflowNodeHandles({ kind, inputSchema, outputSchema, nodeHeight, connectedInputPorts = [], connectedOutputPorts = [] }) {
  const inputProperties = visibleNodePortProperties(nodeInputPorts(kind, inputSchema), nodeHeight, connectedInputPorts);
  const outputProperties = visibleNodePortProperties(nodeOutputPorts(kind, outputSchema), nodeHeight, connectedOutputPorts);
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
function WorkflowNodePorts({ kind, inputSchema, outputSchema, nodeHeight, connectedInputPorts = [], connectedOutputPorts = [] }) {
  const inputProperties = visibleNodePortProperties(nodeInputPorts(kind, inputSchema), nodeHeight, connectedInputPorts);
  const outputProperties = visibleNodePortProperties(nodeOutputPorts(kind, outputSchema), nodeHeight, connectedOutputPorts);
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
        {outputProperties.length ? outputProperties.map(({ name, definition }) => (
          <div className="workflow-node-port" key={`output-${name}`}>
            <small>{schemaType(definition)}</small>
            <span>{name}</span>
          </div>
        )) : <span className="workflow-node-ports__empty">No outputs</span>}
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
  const documentTypes = useSelector(selectDocumentTypes);

  const documentTypeOptions = useMemo(() =>
    documentTypes.map((dt) => ({ value: dt.id, label: dt.label || dt.id })),
  [documentTypes]);

  const collectionTypeOptions = useMemo(() =>
    documentTypes.filter((dt) => dt.collection).map((dt) => ({ value: dt.id, label: dt.label || dt.id })),
  [documentTypes]);

  const selectedDocumentType = useMemo(() =>
    documentTypes.find((dt) => dt.id === node.data?.documentType) || null,
  [documentTypes, node.data?.documentType]);

  const fieldOptions = useMemo(() => {
    const props = selectedDocumentType?.document_schema?.properties;
    if (!props || typeof props !== 'object') return [];
    return Object.keys(props).map((key) => ({ value: key, label: key }));
  }, [selectedDocumentType]);

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
            {kind === 'document_read' ? (
              <>
                <label className="workflow-node-options__field">
                  Document type
                  <Select value={node.data?.documentType || ''} onChange={handleReadTypeChange} options={documentTypeOptions} ariaLabel="Document type" />
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
            {kind === 'agent' ? (
              <>
                <label className="workflow-node-options__field">Message<textarea value={node.data?.message || ''} onChange={(event) => onChange(node.id, { message: event.target.value })} rows={4} /></label>
                <JsonFieldEditor label="Input schema" value={node.data?.inputSchema} onCommit={(inputSchema) => onChange(node.id, { inputSchema })} />
                <JsonFieldEditor label="Output schema" value={node.data?.outputSchema} onCommit={(outputSchema) => onChange(node.id, { outputSchema })} />
              </>
            ) : null}
            {kind === 'mcp' ? (
              <>
                <JsonFieldEditor label="Input schema (tool + arguments)" value={node.data?.inputSchema} onCommit={(inputSchema) => onChange(node.id, { inputSchema })} />
                <JsonFieldEditor label="Output schema" value={node.data?.outputSchema} onCommit={(outputSchema) => onChange(node.id, { outputSchema })} />
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
            {kind === 'approval' || kind === 'human_input' ? (
              <>
                <label className="workflow-node-options__field">Message<textarea value={node.data?.message || ''} onChange={(event) => onChange(node.id, { message: event.target.value })} rows={3} /></label>
                <JsonFieldEditor label="Input schema" value={node.data?.inputSchema} onCommit={(inputSchema) => onChange(node.id, { inputSchema })} />
                {kind === 'human_input' ? (
                  <JsonFieldEditor label="Output schema" value={node.data?.outputSchema} onCommit={(outputSchema) => onChange(node.id, { outputSchema })} />
                ) : null}
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
          <WorkflowNodePorts kind={data.kind} inputSchema={data.inputSchema} outputSchema={data.outputSchema} nodeHeight={height} connectedInputPorts={connectedInputPorts} connectedOutputPorts={connectedOutputPorts} />
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
          <WorkflowNodePorts kind="event" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} />
        </div>
      </div>
      <WorkflowNodeHandles kind="event" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} />
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
          <WorkflowNodePorts kind="data" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} />
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

const NODE_TYPES = {
  event: DocumentEventNode,
  agent: PromptNode,
  mcp: PromptNode,
  human_input: PromptNode,
  data: WorkflowDataNode,
  condition: WorkflowControlNode,
  approval: WorkflowControlNode,
  document_read: PromptNode,
  document_query: PromptNode,
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
  'mcp-tool': { icon: 'M', label: 'MCP' },
  'ask-user': { icon: 'U', label: 'Ask' },
  'inbox-message': { icon: 'I', label: 'Inbox' },
  routine: { icon: 'R', label: 'Routine' },
  condition: { icon: '◇', label: 'Condition' },
  approval: { icon: '✓', label: 'Approval' },
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
  const selectedLatestRun = useSelector((state) => {
    const workflowId = selectedDetail?.workflow?.id || selectedPath.replace(/^.*\/|\.json$/g, '');
    return selectWorkflowLatestRunById(state, workflowId);
  });
  const nodesRef = useRef([]);
  const edgesRef = useRef([]);
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

  useEffect(() => {
    if (!realtime.connected) {
      return;
    }
    void loadWorkflows();
    void loadRuns();
    void loadDocumentTypes();
  }, [loadDocumentTypes, loadRuns, loadWorkflows, realtime.connected]);

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
      const searchable = `${item.title} ${item.description} ${item.document_type}`.toLowerCase();
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
    const flow = workflowToFlow(cloned, {
      onNodeChange: (nodeId, patch) => {
        setGraphNodes((currentNodes) => {
          const nextNodes = currentNodes.map((node) => (
            node.id === nodeId ? { ...node, data: { ...node.data, ...patch } } : node
          ));
          nodesRef.current = nextNodes;
          if (!hydratingGraphRef.current) setLocalDraftDirty(true);
          setDraftWorkflow((current) => flowToWorkflow(current || cloned, nextNodes, edgesRef.current, viewportRef.current));
          return nextNodes;
        });
      },
      onTriggerChange: (patch) => {
        setGraphNodes((currentNodes) => {
          const nextNodes = currentNodes.map((node) => (
            node.type === 'event' ? { ...node, data: { ...node.data, ...patch } } : node
          ));
          nodesRef.current = nextNodes;
          if (!hydratingGraphRef.current) setLocalDraftDirty(true);
          setDraftWorkflow((current) => flowToWorkflow(current || cloned, nextNodes, edgesRef.current, viewportRef.current));
          return nextNodes;
        });
      },
      onDeleteNode: (nodeId) => {
        setGraphNodes((currentNodes) => {
          const nextNodes = currentNodes.filter((node) => node.id !== nodeId);
          const nextEdges = edgesRef.current.filter((edge) => edge.source !== nodeId && edge.target !== nodeId);
          nodesRef.current = nextNodes;
          edgesRef.current = nextEdges;
          setGraphEdges(nextEdges);
          if (!hydratingGraphRef.current) setLocalDraftDirty(true);
          setDraftWorkflow((current) => flowToWorkflow(current || cloned, nextNodes, nextEdges, viewportRef.current));
          return nextNodes;
        });
      },
      onOpenOptions: setNodeOptionsId,
      onToggleExpanded: (nodeId) => {
        setGraphNodes((currentNodes) => {
          const nextNodes = currentNodes.map((node) => {
            if (node.id !== nodeId) return node;
            const nextExpanded = !node.data.expanded;
            const nextSize = nodeCurrentSize(node, nextExpanded);
            return {
              ...node,
              // React Flow's NodeResizer writes width/height as top-level node
              // fields (taking precedence over style on later renders once a
              // manual resize has happened), so both must be kept in sync here.
              width: nextSize.width,
              height: nextSize.height,
              style: { ...node.style, width: nextSize.width, height: nextSize.height },
              data: { ...node.data, expanded: nextExpanded },
            };
          });
          nodesRef.current = nextNodes;
          if (!hydratingGraphRef.current) setLocalDraftDirty(true);
          setDraftWorkflow((current) => flowToWorkflow(current || cloned, nextNodes, edgesRef.current, viewportRef.current));
          return nextNodes;
        });
      },
      onResize: (nodeId, size) => {
        setGraphNodes((currentNodes) => {
          const nextNodes = currentNodes.map((node) => {
            if (node.id !== nodeId) return node;
            const sizeKey = node.data.expanded ? 'settingsSize' : 'dataSize';
            return {
              ...node,
              width: size.width,
              height: size.height,
              style: { ...node.style, width: size.width, height: size.height },
              data: { ...node.data, [sizeKey]: size },
            };
          });
          nodesRef.current = nextNodes;
          if (!hydratingGraphRef.current) setLocalDraftDirty(true);
          setDraftWorkflow((current) => flowToWorkflow(current || cloned, nextNodes, edgesRef.current, viewportRef.current));
          return nextNodes;
        });
      },
    }, latestRun);
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
  }, [hasUnsavedChanges, reactFlow]);

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

  const syncWorkflowFromGraph = useCallback((nextNodes, nextEdges, nextViewport = viewportRef.current, markDirty = true) => {
    nodesRef.current = nextNodes;
    edgesRef.current = nextEdges;
    viewportRef.current = nextViewport;
    if (markDirty) {
      setLocalDraftDirty(true);
    }
    setDraftWorkflow((current) => (current ? flowToWorkflow(current, nextNodes, nextEdges, nextViewport) : current));
  }, []);

  const onNodesChange = useCallback((changes) => {
    setGraphNodes((currentNodes) => {
      const nextNodes = applyNodeChanges(changes, currentNodes);
      if (!hydratingGraphRef.current) {
        const marksDirty = changes.some((change) => (
          change.type === 'add' || change.type === 'remove' || change.dragging === true
        ));
        syncWorkflowFromGraph(nextNodes, edgesRef.current, viewportRef.current, marksDirty);
      }
      return nextNodes;
    });
  }, [syncWorkflowFromGraph]);

  const onEdgesChange = useCallback((changes) => {
    setGraphEdges((currentEdges) => {
      const nextEdges = applyEdgeChanges(changes, currentEdges);
      if (!hydratingGraphRef.current) {
        const marksDirty = changes.some((change) => change.type === 'add' || change.type === 'remove');
        syncWorkflowFromGraph(nodesRef.current, nextEdges, viewportRef.current, marksDirty);
      }
      return nextEdges;
    });
  }, [syncWorkflowFromGraph]);

  const handleEdgeClick = useCallback((event, edge) => {
    if (canvasTool !== 'cut') return;
    event.preventDefault();
    event.stopPropagation();
    const nextEdges = edgesRef.current.filter((candidate) => candidate.id !== edge.id);
    setGraphEdges(nextEdges);
    syncWorkflowFromGraph(nodesRef.current, nextEdges, viewportRef.current);
  }, [canvasTool, syncWorkflowFromGraph]);

  const portFromHandle = useCallback((handleId) => (
    handleId?.startsWith('out:') || handleId?.startsWith('in:')
      ? handleId.slice(handleId.indexOf(':') + 1)
      : ''
  ), []);

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
    const validationError = validateConnection(connection);
    if (validationError) {
      show('error', 'Invalid workflow connection', validationError);
      return;
    }
    setGraphEdges((currentEdges) => {
      const nextEdges = addEdge(
        { ...connection, ...DEFAULT_EDGE_OPTIONS, className: 'workflow-edge--data' },
        currentEdges,
      );
      syncWorkflowFromGraph(nodesRef.current, nextEdges, viewportRef.current);
      return nextEdges;
    });
  }, [show, syncWorkflowFromGraph, validateConnection]);

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
    const nodeType = kind;
    const nodeId = `${nodeType}_${Math.random().toString(36).slice(2, 10)}`;
    const position = dropPosition || nextDockNodePosition(nodesRef.current);
    const sizes = DEFAULT_NODE_SIZES[nodeType] || DEFAULT_NODE_SIZES.agent;
    const defaultData = DOCK_NODE_DEFAULTS[kind](documentTypes);
    const inputPortCount = nodeInputPorts(kind, defaultData.inputSchema).length;
    const outputPortCount = nodeOutputPorts(kind, defaultData.outputSchema).length;
    const maxPorts = Math.max(inputPortCount, outputPortCount);
    const autoHeight = maxPorts > 0 ? 69 + (maxPorts - 1) * 28 : sizes.data.height;
    const initialHeight = Math.max(sizes.data.height, autoHeight);
    const nextNodes = [
      ...nodesRef.current,
      {
        id: nodeId,
        type: nodeType,
        width: sizes.data.width,
        height: initialHeight,
        style: { width: sizes.data.width, height: initialHeight },
        position,
        data: {
          kind: nodeType,
          ...DOCK_NODE_DEFAULTS[kind](documentTypes),
          currentNodeId: null,
          completedNodeIds: [],
          expanded: true,
          onChange: nodeType === 'event'
            ? (patch) => {
                setGraphNodes((currentNodes) => {
                  const updated = currentNodes.map((node) => (
                    node.id === nodeId ? { ...node, data: { ...node.data, ...patch } } : node
                  ));
                  syncWorkflowFromGraph(updated, edgesRef.current, viewportRef.current);
                  return updated;
                });
              }
            : (targetId, patch) => {
                setGraphNodes((currentNodes) => {
                  const updated = currentNodes.map((node) => (
                    node.id === targetId ? { ...node, data: { ...node.data, ...patch } } : node
                  ));
                  syncWorkflowFromGraph(updated, edgesRef.current, viewportRef.current);
                  return updated;
                });
              },
          onDelete: (targetId) => {
            const updatedNodes = nodesRef.current.filter((node) => node.id !== targetId);
            const updatedEdges = edgesRef.current.filter((edge) => edge.source !== targetId && edge.target !== targetId);
            setGraphNodes(updatedNodes);
            setGraphEdges(updatedEdges);
            syncWorkflowFromGraph(updatedNodes, updatedEdges, viewportRef.current);
          },
          onOpenOptions: setNodeOptionsId,
          onToggleExpanded: (targetId) => {
            setGraphNodes((currentNodes) => {
              const nextNodes = currentNodes.map((node) => {
                if (node.id !== targetId) return node;
                const nextExpanded = !node.data.expanded;
                const nextSize = nodeCurrentSize(node, nextExpanded);
                return {
                  ...node,
                  // React Flow's NodeResizer writes width/height as top-level node
              // fields (taking precedence over style on later renders once a
              // manual resize has happened), so both must be kept in sync here.
              width: nextSize.width,
              height: nextSize.height,
              style: { ...node.style, width: nextSize.width, height: nextSize.height },
                  data: { ...node.data, expanded: nextExpanded },
                };
              });
              syncWorkflowFromGraph(nextNodes, edgesRef.current, viewportRef.current);
              return nextNodes;
            });
          },
          onResize: (targetId, size) => {
            setGraphNodes((currentNodes) => {
              const nextNodes = currentNodes.map((node) => {
                if (node.id !== targetId) return node;
                const sizeKey = node.data.expanded ? 'settingsSize' : 'dataSize';
                return {
                  ...node,
                  width: size.width,
              height: size.height,
              style: { ...node.style, width: size.width, height: size.height },
                  data: { ...node.data, [sizeKey]: size },
                };
              });
              syncWorkflowFromGraph(nextNodes, edgesRef.current, viewportRef.current);
              return nextNodes;
            });
          },
          canDelete: true,
        },
      },
    ];
    const nextEdges = edgesRef.current;
    setGraphNodes(nextNodes);
    setGraphEdges(nextEdges);
    syncWorkflowFromGraph(nextNodes, nextEdges, viewportRef.current);
  }, [draftWorkflow, documentTypes, setGraphEdges, setGraphNodes, setNodeOptionsId, syncWorkflowFromGraph]);

  const onCanvasPaneClick = useCallback((event) => {
    if (event.button === 0) {
      setNodeMenuAnchor(null);
    }
  }, []);

  const onCanvasPointerDown = useCallback((event) => {
    if (event.button === 2) {
      rightPointerRef.current = { x: event.clientX, y: event.clientY, moved: false };
    }
  }, []);

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
      const flow = workflowToFlow(parsed, {
        onNodeChange: (nodeId, patch) => {
          setGraphNodes((currentNodes) => {
            const nextNodes = currentNodes.map((node) => (
              node.id === nodeId ? { ...node, data: { ...node.data, ...patch } } : node
            ));
            syncWorkflowFromGraph(nextNodes, edgesRef.current, viewportRef.current);
            return nextNodes;
          });
        },
        onTriggerChange: (patch) => {
          setGraphNodes((currentNodes) => {
            const nextNodes = currentNodes.map((node) => (
              node.type === 'event' ? { ...node, data: { ...node.data, ...patch } } : node
            ));
            syncWorkflowFromGraph(nextNodes, edgesRef.current, viewportRef.current);
            return nextNodes;
          });
        },
        onDeleteNode: (nodeId) => {
          const nextNodes = nodesRef.current.filter((node) => node.id !== nodeId);
          const nextEdges = edgesRef.current.filter((edge) => edge.source !== nodeId && edge.target !== nodeId);
          setGraphNodes(nextNodes);
          setGraphEdges(nextEdges);
          syncWorkflowFromGraph(nextNodes, nextEdges, viewportRef.current);
        },
        onToggleExpanded: (nodeId) => {
          setGraphNodes((currentNodes) => {
            const nextNodes = currentNodes.map((node) => {
              if (node.id !== nodeId) return node;
              const nextExpanded = !node.data.expanded;
              const nextSize = nodeCurrentSize(node, nextExpanded);
              return {
                ...node,
                // React Flow's NodeResizer writes width/height as top-level node
              // fields (taking precedence over style on later renders once a
              // manual resize has happened), so both must be kept in sync here.
              width: nextSize.width,
              height: nextSize.height,
              style: { ...node.style, width: nextSize.width, height: nextSize.height },
                data: { ...node.data, expanded: nextExpanded },
              };
            });
            syncWorkflowFromGraph(nextNodes, edgesRef.current, viewportRef.current);
            return nextNodes;
          });
        },
        onResize: (nodeId, size) => {
          setGraphNodes((currentNodes) => {
            const nextNodes = currentNodes.map((node) => {
              if (node.id !== nodeId) return node;
              const sizeKey = node.data.expanded ? 'settingsSize' : 'dataSize';
              return {
                ...node,
                width: size.width,
              height: size.height,
              style: { ...node.style, width: size.width, height: size.height },
                data: { ...node.data, [sizeKey]: size },
              };
            });
            syncWorkflowFromGraph(nextNodes, edgesRef.current, viewportRef.current);
            return nextNodes;
          });
        },
      }, selectedLatestRun);
      setDraftWorkflow(parsed);
      setGraphNodes(flow.nodes);
      setGraphEdges(flow.edges);
      nodesRef.current = flow.nodes;
      edgesRef.current = flow.edges;
      viewportRef.current = flow.viewport;
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
                    className={`canvas-surface workflow-canvas workflow-canvas--${canvasTool}`}
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
export { flowToWorkflow, workflowToFlow, NODE_MENU_CATEGORIES, schemaProperties, canvasMenuAnchor, visibleNodePortProperties };
export default function WorkflowsPage() {
  return (
    <ReactFlowProvider>
      <WorkflowsInner />
    </ReactFlowProvider>
  );
}
