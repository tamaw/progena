import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useSearchParams } from 'react-router-dom';
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from '@xyflow/react';
import { useConfig } from '../context/ConfigContext';
import { useNotifications } from '../context/NotificationContext';
import {
  createWorkflow,
  deleteWorkflow,
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
  PaneExpandRail,
  PaneListFilters,
  PaneListFooter,
  PaneListGroup,
  PaneListScroll,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneSection,
} from '../components/layout/PaneList';
import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import { JsonCodeEditor } from '../components/workspace/WorkflowJsonPanel';
import '../styles/workspace.css';
import '../styles/nodes.css';

const WORKFLOW_EVENT_TYPES = [
  'kanban.card.created',
  'kanban.card.updated',
  'kanban.card.moved',
  'kanban.card.deleted',
  'kanban.card.status_changed',
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
const DEFAULT_NODE_SIZES = {
  event: { width: 320, height: 190 },
  prompt: { width: 340, height: 240 },
};

function workflowPath(workflowId) {
  return workflowId ? `/state/workflows/${workflowId}.json` : '';
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

function nodeColor(node) {
  return node.type === 'event' ? '#ffa657' : '#58a6ff';
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

function workflowTone(workflow, latestRun) {
  if (workflow?.paused) return 'muted';
  if (latestRun?.status === 'failed') return 'error';
  if (latestRun?.status === 'running') return 'info';
  if (workflow?.enabled === false) return 'muted';
  return 'ok';
}

function workflowStatusLabel(workflow, latestRun) {
  if (workflow?.paused) return 'Paused';
  if (latestRun?.status === 'running') return 'Running';
  if (latestRun?.status === 'failed') return 'Failed';
  if (workflow?.enabled === false) return 'Disabled';
  return 'Ready';
}

function nextPromptPosition(nodes) {
  const ordered = [...nodes].sort((left, right) => left.position.x - right.position.x);
  const anchor = ordered[ordered.length - 1];
  if (!anchor) {
    return { x: 440, y: 160 };
  }
  return { x: anchor.position.x + 360, y: anchor.position.y };
}

function buildNodeData(kind, node, callbacks, latestRun) {
  if (kind === 'event') {
    return {
      kind,
      title: node.title || 'Event',
      eventType: node.eventType || WORKFLOW_EVENT_TYPES[0],
      currentNodeId: latestRun?.current_node_id || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: false,
    };
  }
  return {
    kind,
    title: node.title || 'Prompt',
    prompt: node.prompt || '',
    agentName: node.agent_name || 'progenitor',
    role: node.role || '',
    currentNodeId: latestRun?.current_node_id || null,
    completedNodeIds: latestRun?.completed_node_ids || [],
    onChange: callbacks.onNodeChange,
    onDelete: callbacks.onDeleteNode,
    canDelete: true,
  };
}

function workflowToFlow(definition, callbacks, latestRun) {
  const uiNodes = definition?.ui?.nodes || {};
  const nodes = Object.entries(definition?.graph?.nodes || {}).map(([nodeId, node]) => {
    const kind = node.kind === 'event' ? 'event' : 'prompt';
    const uiNode = uiNodes[nodeId] || {};
    const size = uiNode.size || DEFAULT_NODE_SIZES[kind];
    const position = uiNode.position || (kind === 'event' ? { x: 96, y: 160 } : { x: 456, y: 160 });
    const enriched = kind === 'event'
      ? { ...node, eventType: definition.trigger?.event_type || WORKFLOW_EVENT_TYPES[0] }
      : node;
    return {
      id: nodeId,
      type: kind,
      position,
      style: { width: size.width || DEFAULT_NODE_SIZES[kind].width, height: size.height || DEFAULT_NODE_SIZES[kind].height },
      data: buildNodeData(kind, enriched, callbacks, latestRun),
    };
  });
  const edges = (definition?.graph?.edges || []).map((edge) => ({
    id: `edge_${edge.from.node}_${edge.to.node}`,
    source: edge.from.node,
    target: edge.to.node,
    type: 'smoothstep',
    animated: true,
  }));
  return {
    nodes,
    edges,
    viewport: definition?.ui?.viewport || DEFAULT_VIEWPORT,
  };
}

function applyRunStateToNodes(nodes, latestRun) {
  const currentNodeId = latestRun?.current_node_id || null;
  const completedNodeIds = latestRun?.completed_node_ids || [];
  return nodes.map((node) => {
    const nextData = node.type === 'event'
      ? { ...node.data, currentNodeId }
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
    if (node.type === 'event') {
      graphNodes[node.id] = {
        kind: 'event',
        title: String(node.data?.title || 'Event'),
      };
    } else {
      graphNodes[node.id] = {
        kind: 'prompt',
        title: String(node.data?.title || 'Prompt'),
        prompt: String(node.data?.prompt || ''),
        agent_name: String(node.data?.agentName || 'progenitor'),
        role: node.data?.role ? String(node.data.role) : null,
      };
    }
    uiNodes[node.id] = {
      position: {
        x: Math.round(node.position?.x || 0),
        y: Math.round(node.position?.y || 0),
      },
      size: {
        width: Math.round(Number(node.style?.width) || DEFAULT_NODE_SIZES[node.type]?.width || 320),
        height: Math.round(Number(node.style?.height) || DEFAULT_NODE_SIZES[node.type]?.height || 200),
      },
    };
  });

  const eventNode = nodes.find((node) => node.type === 'event');
  return {
    ...baseWorkflow,
    trigger: {
      ...baseWorkflow.trigger,
      type: 'event',
      event_type: eventNode?.data?.eventType || baseWorkflow.trigger?.event_type || WORKFLOW_EVENT_TYPES[0],
    },
    graph: {
      nodes: graphNodes,
      edges: edges.map((edge) => ({
        from: { node: edge.source, port: 'default' },
        to: { node: edge.target, port: 'default' },
      })),
    },
    ui: {
      ...(baseWorkflow.ui || {}),
      nodes: uiNodes,
      viewport: viewport || baseWorkflow.ui?.viewport || DEFAULT_VIEWPORT,
    },
  };
}

function EventNode({ id, data, selected }) {
  const isRunning = data.currentNodeId === id;
  return (
    <div className={`workspace-node workflow-graph-node workflow-graph-node--event ${selected ? 'selected' : ''}`}>
      <Handle type="source" position={Position.Right} className="workspace-handle" />
      <div className="node-title-bar drag-handle">
        <span className="node-type-icon event">E</span>
        <input
          className="node-title-input"
          value={data.title}
          onChange={(event) => data.onChange(id, { title: event.target.value })}
          placeholder="Event title"
        />
        <span className={`node-status-badge ${isRunning ? 'running' : 'idle'}`}>{isRunning ? 'Live' : 'Start'}</span>
      </div>
      <div className="node-body">
        <div className="node-body-compact workflow-graph-node__body">
          <label className="workflow-graph-node__field">
            <span>Trigger</span>
            <select
              value={data.eventType}
              onChange={(event) => data.onChange(id, { eventType: event.target.value })}
            >
              {WORKFLOW_EVENT_TYPES.map((eventType) => (
                <option key={eventType} value={eventType}>{eventType}</option>
              ))}
            </select>
          </label>
          <p className="workflow-graph-node__hint">
            One event node starts the v1 workflow path.
          </p>
        </div>
      </div>
    </div>
  );
}

function PromptNode({ id, data, selected }) {
  const isRunning = data.currentNodeId === id;
  const isCompleted = Array.isArray(data.completedNodeIds) && data.completedNodeIds.includes(id);
  const statusClass = isRunning ? 'running' : (isCompleted ? 'completed' : 'idle');
  const statusLabel = isRunning ? 'Running' : (isCompleted ? 'Done' : 'Prompt');
  return (
    <div className={`workspace-node workflow-graph-node workflow-graph-node--prompt ${selected ? 'selected' : ''}`}>
      <Handle type="target" position={Position.Left} className="workspace-handle" />
      <Handle type="source" position={Position.Right} className="workspace-handle" />
      <div className="node-title-bar drag-handle">
        <span className="node-type-icon prompt">P</span>
        <input
          className="node-title-input"
          value={data.title}
          onChange={(event) => data.onChange(id, { title: event.target.value })}
          placeholder="Prompt title"
        />
        <span className={`node-status-badge ${statusClass}`}>{statusLabel}</span>
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
        <div className="node-body-compact workflow-graph-node__body">
          <label className="workflow-graph-node__field">
            <span>Agent</span>
            <input
              value={data.agentName}
              onChange={(event) => data.onChange(id, { agentName: event.target.value })}
              placeholder="progenitor"
            />
          </label>
          <label className="workflow-graph-node__field">
            <span>Role</span>
            <input
              value={data.role}
              onChange={(event) => data.onChange(id, { role: event.target.value })}
              placeholder="Optional role"
            />
          </label>
          <label className="workflow-graph-node__field">
            <span>Prompt</span>
            <textarea
              value={data.prompt}
              onChange={(event) => data.onChange(id, { prompt: event.target.value })}
              placeholder="Handle this workflow event."
            />
          </label>
        </div>
      </div>
    </div>
  );
}

const NODE_TYPES = {
  event: EventNode,
  prompt: PromptNode,
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
// to add at the next slot. Workflows only offer one draggable kind (prompt;
// the event node is the fixed start and isn't dock-addable).
function WorkflowDock({ onAdd }) {
  const onDragStart = (event) => {
    event.dataTransfer.setData('application/workflow-node', 'prompt');
    event.dataTransfer.effectAllowed = 'move';
  };
  return (
    <div className="workspace-dock">
      <div className="workspace-dock-shell">
        <button
          type="button"
          className="workspace-dock-item"
          draggable
          onDragStart={onDragStart}
          onClick={() => onAdd()}
          title="Add a prompt step"
        >
          <span className="workspace-dock-icon prompt">P</span>
          <span className="workspace-dock-tooltip">Prompt</span>
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
  return (
    <button
      type="button"
      className={`workflow-list-row ${selectedPath === path ? 'active' : ''}`}
      onClick={() => onSelect(path)}
      title={item.description || item.title}
    >
      <div className="workflow-list-row__head">
        <span className="workflow-list-row__title">{item.title}</span>
        <span className={`workflow-list-row__status workflow-list-row__status--${workflowTone(item, latestRun)}`}>
          {workflowStatusLabel(item, latestRun)}
        </span>
      </div>
      <div className="workflow-list-row__meta">
        <span>{item.event_type}</span>
        <span>{item.node_count} nodes</span>
      </div>
      <div className="workflow-list-row__foot">
        <span>{item.description || 'No description yet.'}</span>
        <span>{formatRelativeTime(latestRun?.updated_at || item.updated_at)}</span>
      </div>
    </button>
  );
}

function WorkflowRunsList({ runs, loading }) {
  if (loading) {
    return <div className="prompts-empty-state">Loading workflow runs...</div>;
  }
  if (!runs.length) {
    return <div className="prompts-empty-state">No runs yet. Trigger the workflow to inspect its execution path.</div>;
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
  const [statusMessage, setStatusMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const [busyAction, setBusyAction] = useState('');
  const requestedPath = withJsonExtension(searchParams.get('path') || '');
  const selectedDetail = useSelector((state) => selectWorkflowDetailByPath(state, selectedPath));
  const selectedLatestRun = useSelector((state) => {
    const workflowId = selectedDetail?.workflow?.id || selectedPath.replace(/^.*\/|\.json$/g, '');
    return selectWorkflowLatestRunById(state, workflowId);
  });
  const nodesRef = useRef([]);
  const edgesRef = useRef([]);
  const viewportRef = useRef(DEFAULT_VIEWPORT);
  const syncedPathRef = useRef('');
  const syncedHashRef = useRef('');

  const loadWorkflows = useCallback(() => (
    dispatch(fetchWorkflows({ workflowsUrl })).unwrap().catch((error) => {
      show('error', 'Workflow load failed', error.message);
    })
  ), [dispatch, show, workflowsUrl]);

  const loadRuns = useCallback(() => (
    dispatch(fetchWorkflowRuns({ workflowRunsUrl })).unwrap().catch((error) => {
      show('error', 'Workflow runs load failed', error.message);
    })
  ), [dispatch, show, workflowRunsUrl]);

  useEffect(() => {
    loadWorkflows();
    loadRuns();
  }, [loadRuns, loadWorkflows]);

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
    if (!selectedPath || selectedDetail) {
      return;
    }
    dispatch(fetchWorkflowContent({ workflowsUrl, path: selectedPath }))
      .unwrap()
      .catch((error) => show('error', 'Workflow detail load failed', error.message));
  }, [dispatch, selectedDetail, selectedPath, show, workflowsUrl]);

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
  const hasUnsavedChanges = Boolean(selectedDetail?.workflow && draftHash && draftHash !== selectedHash);

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
    syncedHashRef.current = nextHash;
    const cloned = cloneValue(workflow);
    const flow = workflowToFlow(cloned, {
      onNodeChange: (nodeId, patch) => {
        setGraphNodes((currentNodes) => {
          const nextNodes = currentNodes.map((node) => (
            node.id === nodeId ? { ...node, data: { ...node.data, ...patch } } : node
          ));
          nodesRef.current = nextNodes;
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
          setDraftWorkflow((current) => flowToWorkflow(current || cloned, nextNodes, nextEdges, viewportRef.current));
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
    setStatusMessage('');
    requestAnimationFrame(() => {
      if (flow.nodes.length) {
        reactFlow.setViewport(flow.viewport || DEFAULT_VIEWPORT);
        reactFlow.fitView({ padding: 0.18, duration: 200 });
      }
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
      return next;
    });
  }, []);

  const syncWorkflowFromGraph = useCallback((nextNodes, nextEdges, nextViewport = viewportRef.current) => {
    nodesRef.current = nextNodes;
    edgesRef.current = nextEdges;
    viewportRef.current = nextViewport;
    setDraftWorkflow((current) => (current ? flowToWorkflow(current, nextNodes, nextEdges, nextViewport) : current));
  }, []);

  const onNodesChange = useCallback((changes) => {
    setGraphNodes((currentNodes) => {
      const nextNodes = applyNodeChanges(changes, currentNodes);
      syncWorkflowFromGraph(nextNodes, edgesRef.current, viewportRef.current);
      return nextNodes;
    });
  }, [syncWorkflowFromGraph]);

  const onEdgesChange = useCallback((changes) => {
    setGraphEdges((currentEdges) => {
      const nextEdges = applyEdgeChanges(changes, currentEdges);
      syncWorkflowFromGraph(nodesRef.current, nextEdges, viewportRef.current);
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
      return 'Prompt nodes may have only one inbound edge in v1 workflows.';
    }
    return '';
  }, []);

  const onConnect = useCallback((connection) => {
    const validationError = validateConnection(connection);
    if (validationError) {
      setStatusMessage(validationError);
      show('error', 'Invalid workflow connection', validationError);
      return;
    }
    setGraphEdges((currentEdges) => {
      const nextEdges = addEdge({ ...connection, ...DEFAULT_EDGE_OPTIONS }, currentEdges);
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
    setStatusMessage('');
    setJsonError('');
    setSelectedPath(path);
  };

  const handleCreateWorkflow = async () => {
    setBusyAction('create');
    setStatusMessage('');
    try {
      const created = await dispatch(createWorkflow({
        workflowsUrl,
        title: 'Untitled workflow',
        description: '',
        eventType: WORKFLOW_EVENT_TYPES[0],
      })).unwrap();
      setSelectedPath(created.path);
      setActiveSidePanel(null);
      setStatusMessage(`Created ${created.workflow.title}.`);
    } catch (error) {
      show('error', 'Workflow create failed', error.message);
    } finally {
      setBusyAction('');
    }
  };

  const handleRefresh = async () => {
    await Promise.all([loadWorkflows(), loadRuns()]);
  };

  const handleAddPromptNode = useCallback((dropPosition) => {
    if (!draftWorkflow) {
      return;
    }
    const nodeId = `prompt_${Math.random().toString(36).slice(2, 10)}`;
    const position = dropPosition || nextPromptPosition(nodesRef.current);
    const nextNodes = [
      ...nodesRef.current,
      {
        id: nodeId,
        type: 'prompt',
        position,
        style: { ...DEFAULT_NODE_SIZES.prompt },
        data: {
          kind: 'prompt',
          title: 'Prompt',
          prompt: 'Handle this workflow event.',
          agentName: 'progenitor',
          role: '',
          currentNodeId: null,
          completedNodeIds: [],
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
    setStatusMessage('Added a prompt step to the workflow path.');
  }, [draftWorkflow, setGraphEdges, setGraphNodes, syncWorkflowFromGraph]);

  const onCanvasDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onCanvasDrop = useCallback((event) => {
    event.preventDefault();
    const kind = event.dataTransfer.getData('application/workflow-node');
    if (kind !== 'prompt') {
      return;
    }
    const position = reactFlow.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    handleAddPromptNode(position);
  }, [handleAddPromptNode, reactFlow]);

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
        onDeleteNode: (nodeId) => {
          const nextNodes = nodesRef.current.filter((node) => node.id !== nodeId);
          const nextEdges = edgesRef.current.filter((edge) => edge.source !== nodeId && edge.target !== nodeId);
          setGraphNodes(nextNodes);
          setGraphEdges(nextEdges);
          syncWorkflowFromGraph(nextNodes, nextEdges, viewportRef.current);
        },
      }, selectedLatestRun);
      setDraftWorkflow(parsed);
      setGraphNodes(flow.nodes);
      setGraphEdges(flow.edges);
      nodesRef.current = flow.nodes;
      edgesRef.current = flow.edges;
      viewportRef.current = flow.viewport;
      setJsonError('');
      setStatusMessage('Applied JSON changes to the workflow draft.');
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
    setStatusMessage('');
    try {
      const saved = await dispatch(saveWorkflow({
        workflowsUrl,
        path: selectedPath,
        workflow: draftWorkflow,
      })).unwrap();
      syncDraftFromWorkflow(saved.workflow, selectedLatestRun, saved.path);
      setStatusMessage(`Saved ${saved.workflow.title}.`);
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
    setStatusMessage('');
    try {
      const action = selectedDetail.workflow.paused ? resumeWorkflow : pauseWorkflow;
      const updated = await dispatch(action({ workflowsUrl, workflowId })).unwrap();
      syncDraftFromWorkflow(updated.workflow, selectedLatestRun, updated.path);
      setStatusMessage(`${updated.workflow.paused ? 'Paused' : 'Resumed'} ${updated.workflow.title}.`);
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
    setStatusMessage('');
    try {
      const response = await dispatch(triggerWorkflow({
        workflowsUrl,
        workflowId,
        eventType: draftWorkflow?.trigger?.event_type,
      })).unwrap();
      setStatusMessage(response.queued ? 'Workflow queued behind an active run.' : 'Workflow triggered.');
    } catch (error) {
      show('error', 'Workflow trigger failed', error.message);
    } finally {
      setBusyAction('');
    }
  };

  const handleDeleteWorkflow = async () => {
    const workflowId = selectedDetail?.workflow?.id;
    if (!workflowId) {
      return;
    }
    if (!window.confirm(`Delete workflow "${selectedDetail.workflow.title}"?`)) {
      return;
    }
    setBusyAction('delete');
    try {
      await dispatch(deleteWorkflow({ workflowsUrl, workflowId })).unwrap();
      setStatusMessage('Workflow deleted.');
      setSelectedPath('');
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
              onAction={handleCreateWorkflow}
              actionDisabled={busyAction === 'create'}
            />
            <PaneListToolbar>
              <PaneListSearch value={query} placeholder="Search workflows" onChange={setQuery} />
              <PaneListSortButton title="Sort workflows" options={SORTS} value={sortMode} onSelect={setSortMode} />
              <button type="button" className="sess-sort" onClick={handleRefresh}>
                Refresh
              </button>
            </PaneListToolbar>
            <PaneListScroll className="workspace-sidebar__scroll">
              {workflowsState.loading && !workflowsState.loaded ? <div className="prompts-empty-state">Loading workflows...</div> : null}
              {workflowsState.error && !workflows.length ? <div className="prompts-empty-state">Failed to load workflows.</div> : null}
              {!workflowsState.loading && !visibleItems.length ? <div className="prompts-empty-state">No workflows match the current filters.</div> : null}
              {!workflowsState.loading && visibleItems.length ? (
                <PaneListGroup title="Workflow selector" count={visibleItems.length}>
                  <div className="workflows-list">
                    {visibleItems.map((item) => (
                      <WorkflowRow
                        key={item.id}
                        item={item}
                        latestRun={workflowRuns.find((run) => run.workflow_id === item.id) || null}
                        selectedPath={selectedPath}
                        onSelect={handleSelectWorkflow}
                      />
                    ))}
                  </div>
                </PaneListGroup>
              ) : null}
            </PaneListScroll>
            <PaneListFooter>
              <span><span className="num">{workflows.length}</span> workflows</span>
            </PaneListFooter>
          </>
        )}
      </PaneSection>

      <DetailPane flush className={`workflow-detail ${selectedDetail?.workflow ? 'is-graph-mode' : ''}`} label="Workflow detail">
        {!selectedPath && !workflowsState.loading ? (
          <div className="prompts-empty-state">Select a workflow to edit or create a new one.</div>
        ) : null}

        {selectedPath && !selectedDetail ? (
          <div className="prompts-empty-state">Loading workflow detail...</div>
        ) : null}

        {selectedDetail?.workflow && draftWorkflow ? (
          <div className="detail-pane__body detail-pane__body--graph">
            <div className="agents-graph-layout">
              <section className="agents-graph-panel">
                <div className="agents-graph-shell">
                  <div
                    className="workspace-canvas workflow-canvas"
                    onDrop={onCanvasDrop}
                    onDragOver={onCanvasDragOver}
                  >
                    <div className="agents-graph-floating">
                      <span>{draftWorkflow.title || 'Untitled workflow'}</span>
                      <span>{Object.keys(draftWorkflow.graph?.nodes || {}).length} nodes</span>
                      <span>{workflowStatusLabel(draftWorkflow, selectedLatestRun)}</span>
                      {statusMessage ? <span>{statusMessage}</span> : null}
                      <button
                        type="button"
                        onClick={handleTrigger}
                        disabled={busyAction === 'trigger'}
                        title="Run workflow now"
                      >
                        Run
                      </button>
                      <button
                        type="button"
                        onClick={handlePauseResume}
                        disabled={busyAction === 'pause' || busyAction === 'resume'}
                        title={draftWorkflow.paused ? 'Resume workflow' : 'Pause workflow'}
                      >
                        {draftWorkflow.paused ? 'Resume' : 'Pause'}
                      </button>
                      <button
                        type="button"
                        onClick={handleSaveWorkflow}
                        disabled={!hasUnsavedChanges || saving}
                        title="Save workflow"
                      >
                        Save
                      </button>
                      <button
                        type="button"
                        onClick={handleDeleteWorkflow}
                        disabled={busyAction === 'delete'}
                        title="Delete workflow"
                      >
                        Delete
                      </button>
                    </div>
                    <ReactFlow
                      nodes={graphNodes}
                      edges={graphEdges}
                      nodeTypes={NODE_TYPES}
                      onNodesChange={onNodesChange}
                      onEdgesChange={onEdgesChange}
                      onConnect={onConnect}
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
                    <WorkflowDock onAdd={handleAddPromptNode} />
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
                        <label>
                          <span>Event type</span>
                          <select
                            value={draftWorkflow.trigger?.event_type || WORKFLOW_EVENT_TYPES[0]}
                            onChange={(event) => {
                              const nextEventType = event.target.value;
                              handleFieldChange('trigger', {
                                ...draftWorkflow.trigger,
                                type: 'event',
                                event_type: nextEventType,
                              });
                              setGraphNodes((currentNodes) => {
                                const nextNodes = currentNodes.map((node) => (
                                  node.type === 'event'
                                    ? { ...node, data: { ...node.data, eventType: nextEventType } }
                                    : node
                                ));
                                syncWorkflowFromGraph(nextNodes, edgesRef.current, viewportRef.current);
                                return nextNodes;
                              });
                            }}
                          >
                            {WORKFLOW_EVENT_TYPES.map((eventType) => (
                              <option key={eventType} value={eventType}>{eventType}</option>
                            ))}
                          </select>
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

export default function WorkflowsPage() {
  return (
    <ReactFlowProvider>
      <WorkflowsInner />
    </ReactFlowProvider>
  );
}
