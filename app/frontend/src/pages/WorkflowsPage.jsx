import { createPortal } from 'react-dom';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  useReactFlow,
} from '@xyflow/react';
import { useConfig } from '../context/ConfigContext';
import Select from '../components/common/Select';
import { useNotifications } from '../context/NotificationContext';
import {
  deleteWorkflow,
  fetchAgentProfiles,
  fetchInbox,
  fetchKanbanBoard,
  fetchKnowledge,
  fetchMcp,
  fetchPrompts,
  fetchRepos,
  fetchRoutines,
  fetchWorkflowContent,
  fetchWorkflows,
  fetchWorkflowRuns,
  pauseWorkflow,
  resumeWorkflow,
  saveWorkflow,
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
import McpNode from '../components/canvas/McpNode';
import WorkflowReferencePicker from '../components/canvas/WorkflowReferencePicker';
import WorkflowNodeTitle from '../components/canvas/WorkflowNodeTitle';
import PopupModal from '../components/common/PopupModal';
import '../styles/canvas.css';
import '../styles/nodes.css';

const WORKFLOW_EVENT_TYPES = [
  'kanban.card.created',
  'kanban.card.updated',
  'kanban.card.moved',
  'kanban.card.deleted',
  'kanban.card.status_changed',
];
const WORKFLOW_EVENT_TYPE_OPTIONS = WORKFLOW_EVENT_TYPES.map((eventType) => ({ value: eventType, label: eventType }));
// Mirrors the fixed kanban pipeline from _kanban_default_columns() in
// app/backend/ui_routes.py. Only meaningful for the kanban.card.moved trigger.
const WORKFLOW_KANBAN_COLUMNS = [
  { value: '', label: 'Any column' },
  { value: 'backlog', label: 'Backlog' },
  { value: 'plan', label: 'Plan' },
  { value: 'build', label: 'Build' },
  { value: 'validate', label: 'Validate' },
  { value: 'done', label: 'Done' },
];
const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'enabled', label: 'Enabled' },
  { value: 'paused', label: 'Paused' },
];
const SORTS = ['Recent', 'Name'];
const DEFAULT_EDGE_OPTIONS = { type: 'smoothstep', animated: true };
const MINIMAP_STYLE = { height: 100, width: 150 };
const DEFAULT_VIEWPORT = { x: 0, y: 0, zoom: 1 };
// Each node kind remembers two independent sizes: the compact "data" view
// (just a read-only preview) and the "settings" view (the full editable
// form). Switching views resizes the node to whichever one the user last
// left it at, instead of forcing one fixed rectangle for both.
const DEFAULT_NODE_SIZES = {
  kanban_trigger: {
    data: { width: 320, height: 220 },
    settings: { width: 340, height: 260 },
  },
  prompt: {
    data: { width: 340, height: 160 },
    settings: { width: 340, height: 280 },
  },
  user: {
    data: { width: 320, height: 130 },
    settings: { width: 340, height: 280 },
  },
  code: {
    data: { width: 360, height: 160 },
    settings: { width: 460, height: 340 },
  },
  condition: {
    data: { width: 300, height: 130 },
    settings: { width: 380, height: 220 },
  },
  approval: {
    data: { width: 320, height: 130 },
    settings: { width: 400, height: 240 },
  },
  inbox: {
    data: { width: 320, height: 130 },
    settings: { width: 400, height: 260 },
  },
  agent: {
    data: { width: 340, height: 140 },
    settings: { width: 440, height: 280 },
  },
  mcp: {
    data: { width: 340, height: 150 },
    settings: { width: 420, height: 360 },
  },
};
// The add menu keeps the complete node inventory in one place while the
// bottom toolbar remains small. Triggers are configured by the workflow's
// fixed start node, so they are visible but deliberately not addable.
const NODE_MENU_CATEGORIES = [
  { id: 'triggers', label: 'Triggers', items: [{ id: 'kanban-trigger', label: 'Kanban event', icon: 'K', disabled: true, detail: 'Configured at the start node' }] },
  { id: 'agents', label: 'Agents', items: [{ id: 'prompt', kind: 'prompt', label: 'Prompt', icon: 'P' }, { id: 'call-agent', kind: 'call-agent', label: 'Agent', icon: 'A' }] },
  { id: 'logic', label: 'Logic', items: [{ id: 'condition', kind: 'condition', label: 'Condition', icon: '◇' }, { id: 'approval', kind: 'approval', label: 'Approval', icon: '✓' }] },
  { id: 'tools', label: 'Tools & I/O', items: [{ id: 'mcp-tool', kind: 'mcp-tool', label: 'MCP', icon: 'M' }, { id: 'ask-user', kind: 'ask-user', label: 'Ask user', icon: 'U' }, { id: 'inbox-message', kind: 'inbox-message', label: 'Inbox', icon: 'I' }, { id: 'routine', kind: 'routine', label: 'Routine', icon: 'R' }] },
  { id: 'data', label: 'Data', items: [{ id: 'knowledge-search', kind: 'knowledge-search', label: 'Knowledge', icon: 'K' }, { id: 'repository-task', kind: 'repository-task', label: 'Repository', icon: 'R' }, { id: 'kanban-task', kind: 'kanban-task', label: 'Kanban', icon: 'T' }] },
];

const DOCK_NODE_DEFAULTS = {
  prompt: () => ({ title: 'Prompt', prompt: 'Handle this workflow event.', agentName: 'progenitor', references: {}, inputs: {}, outputSchema: null }),
  'call-agent': () => ({ title: 'Call Agent', prompt: 'Use the selected agent to handle this workflow context.', agentName: 'progenitor', references: {}, inputs: {}, outputSchema: null }),
  'knowledge-search': () => ({ title: 'Search Knowledge', prompt: 'Search the selected knowledge base and answer using the relevant findings.', agentName: 'progenitor', references: {}, inputs: {}, outputSchema: null }),
  'repository-task': () => ({ title: 'Repository Task', prompt: 'Complete this task using the selected repository context.', agentName: 'progenitor', references: {}, inputs: {}, outputSchema: null }),
  'kanban-task': () => ({ title: 'Kanban Task', prompt: 'Complete the selected Kanban task.', agentName: 'progenitor', references: {}, inputs: {}, outputSchema: null }),
  'mcp-tool': () => ({ title: 'MCP Tool', serverId: '', toolName: '', inputs: {}, outputSchema: null }),
  'ask-user': () => ({ title: 'Ask User', prompt: 'Ask the user for the information needed to continue.', agentName: 'progenitor', references: {}, inputs: {}, outputSchema: null }),
  'inbox-message': () => ({ title: 'Use Inbox Message', prompt: 'Use the selected inbox message as context and complete the requested task.', agentName: 'progenitor', references: {}, inputs: {}, outputSchema: null }),
  routine: () => ({ title: 'Run Routine', prompt: 'Run the selected routine with the configured input.', agentName: 'progenitor', references: {}, inputs: {}, outputSchema: null }),
  condition: () => ({ title: 'Condition', expression: 'payload.card.status == "done"' }),
  approval: () => ({ title: 'Approval', message: 'Approve this workflow action.' }),
};

const CAPABILITY_NODE_KINDS = [
  'agent',
  'knowledge',
  'repository',
  'kanban',
  'ask_user',
  'inbox',
  'routine',
];

const CAPABILITY_REFERENCE_KEYS = {
  agent: 'agent_id',
  knowledge: 'knowledge_base_id',
  repository: 'repository_id',
  kanban: 'kanban_card_id',
  inbox: 'inbox_message_id',
};

const CAPABILITY_FIELD = {
  prompt: { source: 'prompt', label: 'Saved prompt', dataKey: 'promptPath' },
  agent: { source: 'agent', label: 'Agent profile', dataKey: 'agentId' },
  knowledge: { source: 'knowledge_base', label: 'Knowledge base', dataKey: 'knowledgeBaseId' },
  repository: { source: 'repository', label: 'Repository', dataKey: 'repositoryId' },
  kanban: { source: 'kanban_card', label: 'Kanban card', dataKey: 'kanbanCardId' },
  inbox: { source: 'inbox_message', label: 'Inbox message', dataKey: 'inboxMessageId' },
  routine: { source: 'routine', label: 'Routine', dataKey: 'routineId' },
};


const NODE_KIND_LABELS = {
  prompt: 'Prompt',
  agent: 'Agent',
  knowledge: 'Knowledge',
  repository: 'Repository',
  kanban: 'Kanban',
  ask_user: 'Ask user',
  inbox: 'Inbox',
  routine: 'Routine',
};
const DOCK_TO_NODE_KIND = {
  'call-agent': 'agent',
  'knowledge-search': 'knowledge',
  'repository-task': 'repository',
  'kanban-task': 'kanban',
  'ask-user': 'ask_user',
  'inbox-message': 'inbox',
  routine: 'routine',
  'mcp-tool': 'mcp',
};

function resolveNodeSizes(kind, storedSize) {
  const defaults = DEFAULT_NODE_SIZES[kind] || DEFAULT_NODE_SIZES.prompt;
  return {
    data: {
      width: storedSize?.data?.width || defaults.data.width,
      height: kind === 'kanban_trigger'
        ? Math.max(storedSize?.data?.height || 0, defaults.data.height)
        : storedSize?.data?.height || defaults.data.height,
    },
    settings: {
      width: storedSize?.settings?.width || defaults.settings.width,
      height: storedSize?.settings?.height || defaults.settings.height,
    },
  };
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
  kanban_trigger: '#ffa657',
  event: '#ffa657',
  prompt: '#58a6ff',
  user: '#39c5cf',
  code: '#3fb950',
  mcp: '#a371f7',
};

function nodeColor(node) {
  return NODE_MINIMAP_COLORS[node.type] || NODE_MINIMAP_COLORS.prompt;
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


function buildNodeData(kind, node, callbacks, latestRun, sizes) {
  const shared = {
    expanded: false,
    dataSize: sizes.data,
    settingsSize: sizes.settings,
    onToggleExpanded: callbacks.onToggleExpanded,
    onOpenOptions: callbacks.onOpenOptions,
    onResize: callbacks.onResize,
  };
  if (kind === 'kanban_trigger') {
    return {
      ...shared,
      kind,
      title: 'Kanban trigger',
      eventType: node.eventType || WORKFLOW_EVENT_TYPES[0],
      fromColumn: node.fromColumn || '',
      toColumn: node.toColumn || '',
      outputSchema: cloneValue(node.outputSchema) || null,
      onChange: callbacks.onTriggerChange,
      canDelete: false,
    };
  }
  if (kind === 'event') {
    return {
      ...shared,
      kind,
      title: node.title || 'Event',
      eventType: node.eventType || WORKFLOW_EVENT_TYPES[0],
      fromColumn: node.fromColumn || '',
      toColumn: node.toColumn || '',
      currentNodeId: latestRun?.current_node_id || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: false,
    };
  }
  const runState = workflowRunState(latestRun);
  if (kind === 'user') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'User',
      messageType: node.message_type || 'say_to_user',
      message: node.message || '',
      notificationTitle: node.notification_title || '',
      level: node.level || 'info',
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'code') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Code',
      language: node.language || 'javascript',
      content: node.content || '',
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'condition') {
    return {
      ...shared, ...runState, kind, title: node.title || 'Condition',
      expression: node.expression || '',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || { type: 'string', enum: ['true', 'false'] },
      onChange: callbacks.onNodeChange, onDelete: callbacks.onDeleteNode, canDelete: true,
    };
  }
  if (kind === 'approval') {
    return {
      ...shared, ...runState, kind, title: node.title || 'Approval',
      message: node.message || '',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || { type: 'string', enum: ['approved', 'rejected'] },
      onChange: callbacks.onNodeChange, onDelete: callbacks.onDeleteNode, canDelete: true,
    };
  }
  if (kind === 'mcp') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'MCP Tool',
      serverId: node.server_id || '',
      toolName: node.tool_name || '',
      inputs: cloneValue(node.inputs) || {},
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  return {
    ...shared,
    ...runState,
    kind,
    title: node.title || 'Prompt',
    prompt: node.prompt || '',
    agentName: node.agent_name || 'progenitor',
    references: cloneValue(node.references) || {},
    inputs: cloneValue(node.inputs) || {},
    inputSchema: cloneValue(node.input_schema) || null,
    outputSchema: cloneValue(node.output_schema) || null,
    ...(kind === 'prompt' ? { promptPath: node.prompt_path || node.references?.prompt_path || '' } : {}),
    ...(kind === 'agent' ? { agentId: node.agent_id || '' } : {}),
    ...(kind === 'knowledge' ? { knowledgeBaseId: node.knowledge_base_id || '' } : {}),
    ...(kind === 'repository' ? { repositoryId: node.repository_id || '' } : {}),
    ...(kind === 'kanban' ? { kanbanCardId: node.kanban_card_id || '' } : {}),
    ...(kind === 'inbox' ? { inboxMessageId: node.inbox_message_id || '' } : {}),
    ...(kind === 'routine' ? {
      routineId: node.routine_id || '',
      routineRevision: node.routine_revision || 1,
    } : {}),
    onChange: callbacks.onNodeChange,
    onDelete: callbacks.onDeleteNode,
    canDelete: true,
  };
}

function workflowToFlow(definition, callbacks, latestRun) {
  const uiNodes = definition?.ui?.nodes || {};
  const triggerUi = definition?.ui?.trigger || {};
  const triggerSizes = resolveNodeSizes('kanban_trigger', triggerUi.size);
  const triggerNode = {
    id: 'trigger_kanban',
    type: 'kanban_trigger',
    position: triggerUi.position || { x: 96, y: 160 },
    width: triggerSizes.data.width,
    height: triggerSizes.data.height,
    style: { width: triggerSizes.data.width, height: triggerSizes.data.height },
    data: buildNodeData('kanban_trigger', {
      eventType: definition.trigger?.event_type,
      fromColumn: definition.trigger?.from_column,
      toColumn: definition.trigger?.to_column,
      outputSchema: cloneValue(definition.trigger?.output_schema) || null,
    }, callbacks, latestRun, triggerSizes),
  };
  const graphNodes = Object.entries(definition?.graph?.nodes || {}).map(([nodeId, node]) => {
    const kind = CAPABILITY_NODE_KINDS.includes(node.kind) || DEFAULT_NODE_SIZES[node.kind]
      ? node.kind
      : 'prompt';
    const uiNode = uiNodes[nodeId] || {};
    const sizes = resolveNodeSizes(kind, uiNode.size);
    const position = uiNode.position || { x: 456, y: 160 };
    return {
      id: nodeId,
      type: kind,
      position,
      width: sizes.data.width,
      height: sizes.data.height,
      style: { width: sizes.data.width, height: sizes.data.height },
      data: buildNodeData(kind, node, callbacks, latestRun, sizes),
    };
  });
  const graphEdges = (definition?.graph?.edges || []).map((edge) => ({
    id: `edge_${edge.from.node}_${edge.to.node}_${edge.from.port || 'default'}`,
    source: edge.from.node,
    ...(edge.from.port && edge.from.port !== 'default' ? { sourceHandle: edge.from.port } : {}),
    target: edge.to.node,
    ...(edge.to.port && edge.to.port !== 'default' ? { targetHandle: edge.to.port } : {}),
    type: 'smoothstep',
    animated: true,
  }));
  const inboundNodeIds = new Set(graphEdges.map((edge) => edge.target));
  const rootNode = graphNodes.find((node) => !inboundNodeIds.has(node.id));
  const triggerEdge = rootNode ? [{
    id: `edge_trigger_kanban_${rootNode.id}`,
    source: triggerNode.id,
    target: rootNode.id,
    type: 'smoothstep',
    animated: true,
  }] : [];
  return {
    nodes: [triggerNode, ...graphNodes],
    edges: [...triggerEdge, ...graphEdges],
    viewport: definition?.ui?.viewport || DEFAULT_VIEWPORT,
  };
}

function applyRunStateToNodes(nodes, latestRun) {
  const { currentNodeId, completedNodeIds } = workflowRunState(latestRun);
  return nodes.map((node) => {
    const nextData = node.type === 'kanban_trigger'
      ? node.data
      : { ...node.data, currentNodeId, completedNodeIds };
    if (nextData === node.data) {
      return node;
    }
    return { ...node, data: nextData };
  });
}

function flowToWorkflow(baseWorkflow, nodes, edges, viewport) {
  const graphNodes = {};
  const uiNodes = {};
  nodes.forEach((node) => {
    if (node.type === 'kanban_trigger') {
      const sizes = resolveNodeSizes(node.type, {
        data: node.data?.dataSize,
        settings: node.data?.settingsSize,
      });
      uiNodes.trigger = {
        position: {
          x: Math.round(node.position?.x || 0),
          y: Math.round(node.position?.y || 0),
        },
        size: {
          data: { width: Math.round(sizes.data.width), height: Math.round(sizes.data.height) },
          settings: { width: Math.round(sizes.settings.width), height: Math.round(sizes.settings.height) },
        },
      };
      return;
    }
    if (node.type === 'condition') {
      graphNodes[node.id] = {
        kind: 'condition',
        title: String(node.data?.title || 'Condition'),
        input_schema: cloneValue(node.data?.inputSchema) || null,
        output_schema: cloneValue(node.data?.outputSchema) || null,
        expression: String(node.data?.expression || ''),
      };
    } else if (node.type === 'approval') {
      graphNodes[node.id] = {
        kind: 'approval',
        title: String(node.data?.title || 'Approval'),
        input_schema: cloneValue(node.data?.inputSchema) || null,
        output_schema: cloneValue(node.data?.outputSchema) || null,
        message: String(node.data?.message || ''),
      };
    } else if (node.type === 'mcp') {
      graphNodes[node.id] = {
        kind: 'mcp',
        title: String(node.data?.title || 'MCP Tool'),
        server_id: String(node.data?.serverId || ''),
        tool_name: String(node.data?.toolName || ''),
        inputs: cloneValue(node.data?.inputs) || {},
        input_schema: cloneValue(node.data?.inputSchema) || null,
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (CAPABILITY_NODE_KINDS.includes(node.type)) {
      const references = cloneValue(node.data?.references) || {};
      const capability = {
        kind: node.type,
        title: String(node.data?.title || 'Prompt'),
        prompt: String(node.data?.prompt || ''),
        agent_name: String(node.data?.agentName || 'progenitor'),
        references,
        inputs: cloneValue(node.data?.inputs) || {},
        input_schema: cloneValue(node.data?.inputSchema) || null,
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
      const referenceKey = CAPABILITY_REFERENCE_KEYS[node.type];
      if (referenceKey) {
        const dataKey = referenceKey.replace(/_([a-z])/g, (_match, letter) => letter.toUpperCase());
        capability[referenceKey] = String(node.data?.[dataKey] || references[referenceKey] || '');
      } else if (node.type === 'routine') {
        capability.routine_id = String(node.data?.routineId || references.routine?.id || '');
        capability.routine_revision = Number(node.data?.routineRevision || references.routine?.revision || 1);
      }
      graphNodes[node.id] = capability;
    } else {
      const references = cloneValue(node.data?.references) || {};
      const promptPath = String(node.data?.promptPath || '').trim();
      if (promptPath) {
        references.prompt_path = promptPath;
      } else {
        delete references.prompt_path;
      }
      graphNodes[node.id] = {
        kind: 'prompt',
        title: String(node.data?.title || 'Prompt'),
        prompt: String(node.data?.prompt || ''),
        agent_name: String(node.data?.agentName || 'progenitor'),
        references,
        inputs: cloneValue(node.data?.inputs) || {},
        input_schema: cloneValue(node.data?.inputSchema) || null,
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    }
    const sizes = resolveNodeSizes(node.type, {
      data: node.data?.dataSize,
      settings: node.data?.settingsSize,
    });
    uiNodes[node.id] = {
      position: {
        x: Math.round(node.position?.x || 0),
        y: Math.round(node.position?.y || 0),
      },
      size: {
        data: { width: Math.round(sizes.data.width), height: Math.round(sizes.data.height) },
        settings: { width: Math.round(sizes.settings.width), height: Math.round(sizes.settings.height) },
      },
    };
  });

  const triggerNode = nodes.find((node) => node.type === 'kanban_trigger');
  return {
    ...baseWorkflow,
    trigger: {
      ...baseWorkflow.trigger,
      type: 'event',
      event_type: triggerNode?.data?.eventType || baseWorkflow.trigger?.event_type || WORKFLOW_EVENT_TYPES[0],
      from_column: triggerNode?.data?.fromColumn || null,
      to_column: triggerNode?.data?.toColumn || null,
    },
    graph: {
      nodes: graphNodes,
      edges: edges
        .filter((edge) => edge.source !== 'trigger_kanban')
        .map((edge) => ({
          from: { node: edge.source, port: edge.sourceHandle || 'default' },
          to: { node: edge.target, port: edge.targetHandle || 'default' },
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
  if (Array.isArray(definition?.type)) return definition.type.join(' | ');
  if (typeof definition?.type === 'string') return definition.type;
  if (Array.isArray(definition?.enum)) return definition.enum.join(' | ');
  return 'any';
}

function nodePortProperties(schema, fallback = []) {
  const properties = schemaProperties(schema);
  return properties.length ? properties : fallback.map((name) => ({ name, definition: {}, required: false }));
}

function payloadMappingProperties(schema) {
  if (!schema || typeof schema !== 'object') return [];
  const mappings = new Map();
  const variants = Array.isArray(schema.oneOf) ? schema.oneOf : [schema];
  variants.forEach((variant) => {
    const payload = variant?.properties?.payload;
    if (payload?.type !== 'object' || !payload.properties) return;
    Object.entries(payload.properties).forEach(([name, definition]) => {
      if (['event_type', 'timestamp', 'source'].includes(name)) return;
      mappings.set(name, {
        name,
        path: `/payload/${name}`,
        definition: definition && typeof definition === 'object' ? definition : {},
      });
    });
  });
  return [...mappings.values()];
}
function WorkflowNodeHandles({
  inputSchema,
  outputSchema,
  inputs = {},
  outputFallback = ['output'],
  outputProperties: suppliedOutputProperties,
  outputHandle = (property) => `out:${property.path || `/${property.name}`}`,
}) {
  const inputProperties = nodePortProperties(inputSchema, Object.keys(inputs));
  const outputProperties = suppliedOutputProperties || nodePortProperties(outputSchema, outputFallback);
  const top = (index) => `${61 + index * 28}px`;
  return (
    <>
      {inputProperties.map(({ name }, index) => (
        <Handle key={`input-${name}`} type="target" position={Position.Left} id={`in:${name}`} className="canvas-handle workflow-node-external-handle" style={{ top: top(index) }} />
      ))}
      {outputProperties.map((property, index) => (
        <Handle key={`output-${property.name}`} type="source" position={Position.Right} id={outputHandle(property)} className="canvas-handle workflow-node-external-handle" style={{ top: top(index) }} />
      ))}
    </>
  );
}

function WorkflowNodePorts({
  inputSchema,
  outputSchema,
  inputs = {},
  outputFallback = ['output'],
  outputProperties: suppliedOutputProperties,
}) {
  const inputProperties = nodePortProperties(inputSchema, Object.keys(inputs));
  const outputProperties = suppliedOutputProperties || nodePortProperties(outputSchema, outputFallback);
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
            <small>{schemaType(definition)}</small>
            <span>{name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function WorkflowNodeOptionsModal({ node, onChange, onClose }) {
  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

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
              {node.type === 'mcp' ? <><label>Server ID<input value={node.data?.serverId || ''} onChange={(event) => onChange(node.id, { serverId: event.target.value })} /></label><label>Tool name<input value={node.data?.toolName || ''} onChange={(event) => onChange(node.id, { toolName: event.target.value })} /></label></> : null}
            </div>
            {node.type === 'kanban_trigger' ? <label className="workflow-node-options__field">Event type<Select value={node.data?.eventType} onChange={(eventType) => onChange(node.id, { eventType })} options={WORKFLOW_EVENT_TYPE_OPTIONS} ariaLabel="Kanban event type" /></label> : null}
            {['prompt', ...CAPABILITY_NODE_KINDS].includes(node.type) ? <label className="workflow-node-options__field">{node.type === 'ask_user' ? 'Question' : 'Instruction'}<textarea value={node.data?.prompt || ''} onChange={(event) => onChange(node.id, { prompt: event.target.value })} rows={4} /></label> : null}
            {node.type === 'condition' || node.type === 'approval' ? <label className="workflow-node-options__field">{node.type === 'condition' ? 'Expression' : 'Message'}<textarea value={node.data?.[node.type === 'condition' ? 'expression' : 'message'] || ''} onChange={(event) => onChange(node.id, { [node.type === 'condition' ? 'expression' : 'message']: event.target.value })} rows={3} /></label> : null}
          </section>
        </div>
      </section>
    </div>,
    document.body,
  );
}

function PromptNode({ id, data, selected }) {


  const isRunning = data.currentNodeId === id;
  const isCompleted = Array.isArray(data.completedNodeIds) && data.completedNodeIds.includes(id);
  const statusClass = isRunning ? 'running' : (isCompleted ? 'completed' : 'idle');
  const statusLabel = isRunning ? 'Running' : (isCompleted ? 'Done' : NODE_KIND_LABELS[data.kind] || 'Prompt');
  const expanded = data.expanded;


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
          <span className={`node-type-icon ${data.kind}`}>{data.kind === 'ask_user' ? 'U' : data.kind[0].toUpperCase()}</span>
          <WorkflowNodeTitle
            expanded={expanded}
            title={data.title}
            placeholder="Prompt title"
            onChange={(event) => data.onChange(id, { title: event.target.value })}
          />
          <span className={`node-status-badge workflow-graph-node__kind ${statusClass}`}>{statusLabel}</span>
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
              aria-label="Delete prompt node"
              onClick={() => data.onDelete(id)}
            >
              ×
            </button>
          ) : null}
        </div>
        <div className="node-body">
          <WorkflowNodePorts
            inputSchema={data.inputSchema}
            outputSchema={data.outputSchema}
            inputs={data.inputs}
          />
        </div>
      </div>
      <WorkflowNodeHandles inputSchema={data.inputSchema} outputSchema={data.outputSchema} inputs={data.inputs} />
    </>
  );
}


function KanbanTriggerNode({ id, data, selected }) {
  const outputProperties = payloadMappingProperties(data.outputSchema);
  return (
    <>
      <NodeResizer minWidth={280} minHeight={110} isVisible={selected} onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })} />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--trigger ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className="node-type-icon event">K</span>
          <span className="node-title">Kanban trigger</span>
          <button type="button" className="node-options-btn nodrag" onClick={() => data.onOpenOptions(id)} aria-label="Configure node" data-tooltip="Configure node"><ConfigureActionIcon /></button>
        </div>
        <div className="node-body">
          <WorkflowNodePorts inputSchema={null} outputSchema={data.outputSchema} outputFallback={['payload']} outputProperties={outputProperties} />
        </div>
      </div>
      <WorkflowNodeHandles inputSchema={null} outputSchema={data.outputSchema} outputFallback={['payload']} outputProperties={outputProperties} />
    </>
  );
}

function WorkflowControlNode({ id, data, selected }) {
  const label = data.kind === 'condition' ? 'Condition' : 'Approval';
  const outputFallback = data.kind === 'condition' ? ['true', 'false'] : ['approved', 'rejected'];
  return (
    <>
      <NodeResizer minWidth={260} minHeight={110} isVisible={selected} onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })} />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--${data.kind} ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className={`node-type-icon ${data.kind}`}>{data.kind[0].toUpperCase()}</span>
          <WorkflowNodeTitle expanded={false} title={data.title} placeholder={`${label} title`} onChange={(event) => data.onChange(id, { title: event.target.value })} />
          <span className="node-status-badge workflow-graph-node__kind">{label}</span>
          <button type="button" className="node-options-btn nodrag" onClick={() => data.onOpenOptions(id)} aria-label="Configure node" data-tooltip="Configure node"><ConfigureActionIcon /></button>
          <button type="button" className="node-delete-btn" onClick={() => data.onDelete(id)} aria-label={`Delete ${label}`}>×</button>
        </div>
        <div className="node-body">
          <WorkflowNodePorts inputSchema={data.inputSchema} outputSchema={data.outputSchema} outputFallback={outputFallback} />
        </div>
      </div>
      <WorkflowNodeHandles inputSchema={data.inputSchema} outputSchema={data.outputSchema} outputFallback={outputFallback} outputHandle={(property) => property.name} />
    </>
  );
}

const NODE_TYPES = {
  kanban_trigger: KanbanTriggerNode,
  prompt: PromptNode,
  agent: PromptNode,
  knowledge: PromptNode,
  repository: PromptNode,
  kanban: PromptNode,
  ask_user: PromptNode,
  inbox: PromptNode,
  routine: PromptNode,
  condition: WorkflowControlNode,
  approval: WorkflowControlNode,
  mcp: McpNode,
};

// Chevrons for the right tools dock / collapse seam (point toward the action:
// left = expand the panel leftward, right = collapse it away). Ported from the
// Agents/Sequences canvas.
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


// Bottom-center floating dock for adding nodes to the canvas — ported directly
// from the Agents/Sequences canvas's GraphDock: drag onto the canvas or click
// to add at the next slot. The event node is the fixed start and isn't
// dock-addable.
function CanvasPointerIcon() {
  return (
    <svg viewBox="0 0 16 16" width="17" height="17" aria-hidden="true">
      <path d="m3 1.8 9.1 5.5-4.1.9 2.4 4.4-2.2 1.2-2.4-4.4-2.8 3.1Z" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.45" />
    </svg>
  );
}

function CanvasHandIcon() {
  return (
    <svg viewBox="0 0 16 16" width="17" height="17" aria-hidden="true">
      <path d="M5.1 7.3V3.6a1 1 0 0 1 2 0v3.1M7.1 6.2V2.8a1 1 0 0 1 2 0v3.4m0 .2V3.7a1 1 0 0 1 2 0v3.5m0 .4V5.2a1 1 0 0 1 2 0v4.1c0 2.6-1.9 4.7-4.4 4.7H7.4c-1.1 0-2.1-.5-2.7-1.4L3 10.1a1 1 0 0 1 1.6-1.2Z" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.3" />
    </svg>
  );
}

function WorkflowDock({ mode, onModeChange, onAdd }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [filter, setFilter] = useState('');
  const filterRef = useRef(null);
  const dockRef = useRef(null);
  const addButtonRef = useRef(null);
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
    if (!menuOpen) {
      return undefined;
    }
    const closeFromOutside = (event) => {
      if (!dockRef.current?.contains(event.target)) {
        setFilter('');
        setMenuOpen(false);
      }
    };
    const closeFromEscape = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setFilter('');
        setMenuOpen(false);
        addButtonRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', closeFromOutside);
    document.addEventListener('keydown', closeFromEscape);
    return () => {
      document.removeEventListener('pointerdown', closeFromOutside);
      document.removeEventListener('keydown', closeFromEscape);
    };
  }, [menuOpen]);

  const handleAdd = (kind) => {
    onAdd(kind);
    setFilter('');
    setMenuOpen(false);
  };

  const toggleMenu = () => {
    if (menuOpen) {
      setFilter('');
    }
    setMenuOpen(!menuOpen);
  };

  return (
    <div ref={dockRef} className="canvas-dock workflow-canvas-dock">
      {menuOpen ? (
        <div className="workflow-node-menu" role="menu" aria-label="Add workflow node">
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
        <button type="button" className={`canvas-toolbar-button ${mode === 'select' ? 'is-active' : ''}`} onClick={() => onModeChange('select')} aria-pressed={mode === 'select'} title="Select and move nodes">
          <CanvasPointerIcon />
          <span className="canvas-dock-tooltip">Select</span>
        </button>
        <button type="button" className={`canvas-toolbar-button ${mode === 'pan' ? 'is-active' : ''}`} onClick={() => onModeChange('pan')} aria-pressed={mode === 'pan'} title="Grab canvas">
          <CanvasHandIcon />
          <span className="canvas-dock-tooltip">Grab canvas</span>
        </button>
        <span className="canvas-toolbar-divider" aria-hidden="true" />
        <button ref={addButtonRef} type="button" className={`canvas-toolbar-button canvas-toolbar-button--add ${menuOpen ? 'is-active' : ''}`} onClick={toggleMenu} aria-expanded={menuOpen} aria-haspopup="menu" title="Add workflow node">
          <span aria-hidden="true">+</span>
          <span className="canvas-dock-tooltip">Add node</span>
        </button>
      </div>
    </div>
  );
}

// Collapsed right-edge dock holding the workflow's auxiliary views (details
// form, JSON editor, run history) — ported from the Agents/Sequences canvas's
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
  const referenceUrls = {
    agentsUrl: getApiUrl('agentProfiles'),
    inboxUrl: getApiUrl('inbox'),
    kanbanUrl: getApiUrl('kanban'),
    knowledgeUrl: getApiUrl('/api/ui/knowledge'),
    mcpUrl: getApiUrl('mcp'),
    promptsUrl: getApiUrl('prompts'),
    reposUrl: getApiUrl('repos'),
    routinesUrl: getApiUrl('routines'),
  };
  const realtime = useSelector(selectRealtime);
  const workflows = useSelector(selectWorkflows);
  const workflowsState = useSelector(selectWorkflowsState);
  const workflowRuns = useSelector(selectWorkflowRuns);
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
  const [canvasMode, setCanvasMode] = useState('select');
  const [nodeOptionsId, setNodeOptionsId] = useState(null);
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
      show('error', 'Workflow runs load failed', error.message);
    }
  }, [dispatch, show, workflowRunsUrl]);

  useEffect(() => {
    if (!realtime.connected) {
      return;
    }
    void loadWorkflows();
    void loadRuns();
  }, [loadRuns, loadWorkflows, realtime.connected]);
  useEffect(() => {
    if (!realtime.connected) {
      return;
    }
    void Promise.allSettled([
      dispatch(fetchAgentProfiles({ agentsUrl: referenceUrls.agentsUrl })),
      dispatch(fetchInbox({ inboxUrl: referenceUrls.inboxUrl })),
      dispatch(fetchKanbanBoard({ kanbanUrl: referenceUrls.kanbanUrl })),
      dispatch(fetchKnowledge({ knowledgeUrl: referenceUrls.knowledgeUrl })),
      dispatch(fetchMcp({ mcpUrl: referenceUrls.mcpUrl })),
      dispatch(fetchPrompts({ promptsUrl: referenceUrls.promptsUrl })),
      dispatch(fetchRepos({ reposUrl: referenceUrls.reposUrl })),
      dispatch(fetchRoutines({ routinesUrl: referenceUrls.routinesUrl })),
    ]);
  }, [dispatch, realtime.connected, referenceUrls.agentsUrl, referenceUrls.inboxUrl, referenceUrls.kanbanUrl, referenceUrls.knowledgeUrl, referenceUrls.mcpUrl, referenceUrls.promptsUrl, referenceUrls.reposUrl, referenceUrls.routinesUrl]);

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
      const searchable = `${item.title} ${item.description} ${item.event_type}`.toLowerCase();
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
  const nodeOptionsUpstream = nodeOptionsNode
    ? graphNodes.find((node) => graphEdges.some((edge) => edge.source === node.id && edge.target === nodeOptionsNode.id)) || null
    : null;

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
            node.type === 'kanban_trigger' ? { ...node, data: { ...node.data, ...patch } } : node
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
            const nextSize = nextExpanded ? node.data.settingsSize : node.data.dataSize;
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

  const validateConnection = useCallback((connection) => {
    const source = nodesRef.current.find((node) => node.id === connection.source);
    const target = nodesRef.current.find((node) => node.id === connection.target);
    if (!source || !target) {
      return 'Both workflow nodes must exist before connecting them.';
    }
    if (source.id === target.id) {
      return 'A workflow node cannot connect to itself.';
    }
    if (target.type === 'event') {
      return 'Event nodes cannot accept inbound edges in v1 workflows.';
    }
    const existingOutgoing = edgesRef.current.filter((edge) => edge.source === source.id && edge.target !== target.id);
    if (existingOutgoing.length > 0) {
      return 'Each workflow node may have only one outgoing edge in v1 workflows.';
    }
    const existingInbound = edgesRef.current.filter((edge) => edge.target === target.id && edge.source !== source.id);
    if (existingInbound.length > 0) {
      return 'Each workflow node may have only one inbound edge in v1 workflows.';
    }
    return '';
  }, []);

  const onConnect = useCallback((connection) => {
    const validationError = validateConnection(connection);
    if (validationError) {
      show('error', 'Invalid workflow connection', validationError);
      return;
    }
    const source = nodesRef.current.find((node) => node.id === connection.source);
    const sourceSchema = cloneValue(source?.data?.outputSchema);
    if (!sourceSchema) {
      show('error', 'Missing output contract', 'Connect a node only after it has an output schema.');
      return;
    }
    const target = nodesRef.current.find((node) => node.id === connection.target);
    const targetUsesBindings = ['prompt', ...CAPABILITY_NODE_KINDS, 'mcp'].includes(target?.type);
    const sourcePath = connection.sourceHandle?.replace(/^out:/, '') || '/';
    const inputName = connection.targetHandle?.replace(/^in:/, '') || 'input';
    const nextNodes = nodesRef.current.map((node) => {
      if (node.id !== connection.target || ![
        'prompt', ...CAPABILITY_NODE_KINDS, 'mcp', 'condition', 'approval',
      ].includes(node.type)) {
        return node;
      }
      return {
        ...node,
        data: {
          ...node.data,
          inputSchema: sourceSchema,
          ...(targetUsesBindings ? {
            inputs: {
              ...(node.data?.inputs || {}),
              [inputName]: {
                source: source?.type === 'kanban_trigger' ? 'trigger' : 'previous_output',
                path: sourcePath,
              },
            },
          } : {}),
        },
      };
    });
    setGraphNodes(nextNodes);
    setGraphEdges((currentEdges) => {
      const nextEdges = addEdge({ ...connection, ...DEFAULT_EDGE_OPTIONS }, currentEdges);
      syncWorkflowFromGraph(nextNodes, nextEdges, viewportRef.current);
      return nextEdges;
    });
  }, [setGraphNodes, show, syncWorkflowFromGraph, validateConnection]);

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
    const nodeType = DOCK_TO_NODE_KIND[kind] || kind;
    const nodeId = `${nodeType}_${Math.random().toString(36).slice(2, 10)}`;
    const position = dropPosition || nextDockNodePosition(nodesRef.current);
    const sizes = DEFAULT_NODE_SIZES[nodeType] || DEFAULT_NODE_SIZES.prompt;
    const nextNodes = [
      ...nodesRef.current,
      {
        id: nodeId,
        type: nodeType,
        // New nodes start in the settings view (sized accordingly) so their
        // fields are immediately editable.
        width: sizes.settings.width,
        height: sizes.settings.height,
        style: { ...sizes.settings },
        position,
        data: {
          kind: nodeType,
          ...DOCK_NODE_DEFAULTS[kind](),
          currentNodeId: null,
          completedNodeIds: [],
          expanded: true,
          dataSize: { ...sizes.data },
          settingsSize: { ...sizes.settings },
          onChange: (targetId, patch) => {
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
                const nextSize = nextExpanded ? node.data.settingsSize : node.data.dataSize;
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
    const anchor = [...nodesRef.current]
      .sort((left, right) => left.position.x - right.position.x)
      .at(-1);
    const nextEdges = anchor ? [...edgesRef.current, {
      id: `edge_${anchor.id}_${nodeId}`,
      source: anchor.id,
      target: nodeId,
      ...DEFAULT_EDGE_OPTIONS,
    }] : edgesRef.current;
    setGraphNodes(nextNodes);
    setGraphEdges(nextEdges);
    syncWorkflowFromGraph(nextNodes, nextEdges, viewportRef.current);
  }, [draftWorkflow, setGraphEdges, setGraphNodes, setNodeOptionsId, syncWorkflowFromGraph]);

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
              node.type === 'kanban_trigger' ? { ...node, data: { ...node.data, ...patch } } : node
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
              const nextSize = nextExpanded ? node.data.settingsSize : node.data.dataSize;
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
        eventType: draftWorkflow?.trigger?.event_type,
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
                    className="canvas-surface workflow-canvas"
                    onDrop={onCanvasDrop}
                    onDragOver={onCanvasDragOver}
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
                      panOnDrag={canvasMode === 'pan'}
                      selectionOnDrag={canvasMode === 'select'}
                      nodesDraggable={canvasMode === 'select'}
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
                        node={nodeOptionsNode}
                        upstreamNode={nodeOptionsUpstream}
                        onChange={(nodeId, patch) => {
                          if (nodeOptionsNode.type === 'kanban_trigger') {
                            nodeOptionsNode.data.onChange(patch);
                          } else {
                            nodeOptionsNode.data.onChange(nodeId, patch);
                          }
                        }}
                        onClose={() => setNodeOptionsId(null)}
                      />
                    ) : null}
                    <WorkflowDock mode={canvasMode} onModeChange={setCanvasMode} onAdd={handleAddNode} />
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
export { flowToWorkflow, workflowToFlow, NODE_MENU_CATEGORIES, payloadMappingProperties, schemaProperties };
export default function WorkflowsPage() {
  return (
    <ReactFlowProvider>
      <WorkflowsInner />
    </ReactFlowProvider>
  );
}
