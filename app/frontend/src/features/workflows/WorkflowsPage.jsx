import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { selectRealtime } from '../../realtime/realtimeSlice';
import { useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../../lib/routes';
import { addEdge, applyEdgeChanges, applyNodeChanges, Background, Controls, MiniMap, ReactFlow, ReactFlowProvider, useReactFlow } from '@xyflow/react';
import { useConfig } from '../../context/ConfigContext';
import { useNotifications } from '../../context/NotificationContext';
import { deleteWorkflow, fetchWorkflowContent, fetchWorkflows, fetchWorkflowRuns, pauseWorkflow, resumeWorkflow, saveWorkflow, selectWorkflowDetailRequest, selectWorkflowDetailByPath, selectWorkflowLatestRunById, selectWorkflowRuns, selectWorkflows, selectWorkflowsState, triggerWorkflow } from './state/workflows';
import { fetchAgentProfiles } from '../agents/state/agents';
import { fetchDocumentTypes, selectDocumentTypes } from '../documents/state';
import { fetchInstructions } from '../instructions/state';
import { PaneCollapseSeam, PaneEmptyState, PaneExpandRail, PaneListFilters, PaneListFooter, PaneListSearch, PaneListSortButton, PaneListToolbar, PaneListRows, PaneSection } from '../../components/layout/PaneList';
import { usePaneCollapse } from '../../hooks/usePaneCollapse';
import PageShell from '../../components/layout/PageShell';
import DetailPane from '../../components/layout/DetailPane';
import { DeleteActionIcon, DetailHeaderAction, DetailHeaderActions, PauseActionIcon, PlayActionIcon, SaveActionIcon } from '../../components/layout/DetailHeader';
import InlineFailure from '../../components/InlineFailure';
import { reportFailure } from '../../lib/failures';
import useConfirmAction from '../../hooks/useConfirmAction';
import useEntityDraft from '../../hooks/useEntityDraft';
import useMissingEntityRoute from '../../hooks/useMissingEntityRoute';
import useUiAction from '../../hooks/useUiAction';
import { entityDraftKey } from '../../store/editorDraftsSlice';
import JsonCodeEditor from '../../components/JsonCodeEditor';
import PopupModal from '../../components/PopupModal';
import { canvasToolSettings } from './canvas/workflowCanvasTools';
import { canvasMenuAnchor, DEFAULT_NODE_SIZES, DOCK_NODE_DEFAULTS, nodeCurrentSize, nodeColor, nextDockNodePosition } from './models/nodePalette';
import { createPaletteNode, workflowToFlow, applyRunStateToNodes, flowToWorkflow } from './models/graphConversion';
import { workflowPath, workflowEditorDraft } from './editor/draftModel';
import { recomputeUnpackSchemas, sizeUnpackNodes, pruneLostUnpackEdges } from './models/unpack';
import { cloneValue } from './models/values';
import { schemaType, nodeOutputPorts, nodeInputPorts, edgesToRemovedPorts, schemasAreCompatible, isUnpackableSchema } from './models/schema';
import { WorkflowNodeOptionsModal } from './editor/WorkflowNodeOptionsModal';
import { NODE_TYPES } from './canvas/nodeTypes';
import { ChevronRightSmall, WorkflowDock, WorkflowSideDock } from './canvas/WorkflowDock';
import { WorkflowRow } from './WorkflowRow';
import { WorkflowRunsList } from './runs/WorkflowRunsList';
import { DEFAULT_EDGE_OPTIONS, DEFAULT_VIEWPORT } from './models/nodePalette';
import './canvas/canvas.css';
import './canvas/nodes.css';

const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'enabled', label: 'Enabled' },
  { value: 'paused', label: 'Paused' },
];

const SORTS = ['Recent', 'Name'];

const MINIMAP_STYLE = { height: 100, width: 150 };

function WorkflowsInner() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const { show } = useNotifications();
  const workflowsUrl = getApiUrl('workflows');
  const workflowRunsUrl = getApiUrl('workflowRuns');
  const documentTypesUrl = getApiUrl('documentTypes');
  const agentsUrl = getApiUrl('agentProfiles');
  const instructionsUrl = getApiUrl('instructions');
  const realtime = useSelector(selectRealtime);
  const workflows = useSelector(selectWorkflows);
  const workflowsState = useSelector(selectWorkflowsState);
  const workflowRuns = useSelector(selectWorkflowRuns);
  const documentTypes = useSelector(selectDocumentTypes);
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('workflows-sidebar sess-list');
  const reactFlow = useReactFlow();
  const navigate = useNavigate();
  const { workflowId: routeWorkflowId } = useParams();
  const [selectedWorkflowId, setSelectedWorkflowId] = useState('');
  const selectedPath = useMemo(() => workflowPath(selectedWorkflowId), [selectedWorkflowId]);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [sortMode, setSortMode] = useState('Recent');
  const selectedDetail = useSelector((state) => selectWorkflowDetailByPath(state, selectedPath));
  const detailRequest = useSelector((state) => selectWorkflowDetailRequest(state, selectedPath));
  const workflowDraftKey = useMemo(
    () => entityDraftKey('workflow', selectedWorkflowId, 'editor'),
    [selectedWorkflowId],
  );
  const canonicalEditorDraft = useMemo(
    () => workflowEditorDraft(selectedDetail?.workflow),
    [selectedDetail?.workflow],
  );
  const [editorDraft, setEditorDraft, workflowDraftControls] = useEntityDraft(
    workflowDraftKey,
    canonicalEditorDraft,
  );
  const draftWorkflow = editorDraft?.workflow || null;
  const jsonDraft = editorDraft?.jsonDraft || '';
  const jsonError = editorDraft?.jsonError || '';
  const activeSidePanel = editorDraft?.activeSidePanel || null;
  const nodeOptionsId = editorDraft?.nodeOptionsId || null;
  const canvasTool = editorDraft?.canvasTool || 'pointer';
  const [graphNodes, setGraphNodes] = useState([]);
  const [graphEdges, setGraphEdges] = useState([]);
  const workflowAction = useUiAction(selectedWorkflowId);
  const deleteAction = useConfirmAction(selectedWorkflowId);
  const [nodeMenuAnchor, setNodeMenuAnchor] = useState(null);
  const canvasInteraction = canvasToolSettings(canvasTool);
  const rightPointerRef = useRef(null);
  const [rightPanning, setRightPanning] = useState(false);
  const connectingRef = useRef(null);
  const didConnectRef = useRef(false);
  const selectedLatestRun = useSelector((state) => {
    const workflowId = selectedDetail?.workflow?.id || selectedWorkflowId;
    return selectWorkflowLatestRunById(state, workflowId);
  });
  const nodesRef = useRef([]);
  const edgesRef = useRef([]);
  const createUnpackNodeRef = useRef(() => {});
  const viewportRef = useRef(DEFAULT_VIEWPORT);
  const syncedPathRef = useRef('');
  const syncedHashRef = useRef('');
  const hydrationTokenRef = useRef(0);
  const hydratingGraphRef = useRef(false);

  // A row click updates the URL before the selection effect below commits the
  // matching id. Do not let that previous selection reclaim a valid routed
  // workflow if a canonical event lands in that window.
  const routeWorkflowExists = workflows.some((workflow) => workflow.id === routeWorkflowId);
  const workflowUrlSyncBase = (
    workflowsState.loaded
    && routeWorkflowExists
    && selectedWorkflowId !== routeWorkflowId
  ) ? null : '/workflows';
  useEntityUrlSync(workflowUrlSyncBase, selectedWorkflowId, selectedDetail?.workflow?.title);
  useMissingEntityRoute({
    hydrated: workflowsState.loaded,
    entityId: routeWorkflowId,
    entities: workflows,
    getEntityId: (workflow) => workflow.id,
    fallbackPath: '/workflows',
  });

  const loadInitialData = useCallback(() => {
    // The workflow list renders its own failure; supporting catalogues would otherwise look empty.
    const loadSupporting = (operation, thunk) => {
      dispatch(thunk).unwrap().catch((error) => reportFailure(show, operation, error));
    };
    dispatch(fetchWorkflows({ workflowsUrl }));
    loadSupporting('Load workflow runs', fetchWorkflowRuns({ workflowRunsUrl }));
    loadSupporting('Load document types', fetchDocumentTypes({ documentTypesUrl }));
    loadSupporting('Load routines', fetchAgentProfiles({ agentsUrl }));
    loadSupporting('Load instructions', fetchInstructions({ instructionsUrl }));
  }, [agentsUrl, dispatch, documentTypesUrl, instructionsUrl, show, workflowRunsUrl, workflowsUrl]);

  useEffect(() => {
    if (realtime.connected) {
      loadInitialData();
    }
  }, [loadInitialData, realtime.connected]);

  const loadWorkflowDetail = useCallback(() => {
    if (realtime.connected && selectedPath && !detailRequest.loading) {
      dispatch(fetchWorkflowContent({ workflowsUrl, path: selectedPath }));
    }
  }, [detailRequest.loading, dispatch, realtime.connected, selectedPath, workflowsUrl]);

  useEffect(() => {
    if (selectedPath && !selectedDetail && !detailRequest.loading && !detailRequest.error) {
      loadWorkflowDetail();
    }
  }, [detailRequest.error, detailRequest.loading, loadWorkflowDetail, selectedDetail, selectedPath]);

  useEffect(() => {
    if (!workflows.length) {
      setSelectedWorkflowId('');
      return;
    }
    setSelectedWorkflowId((current) => {
      const next = workflows.find((item) => item.id === routeWorkflowId)?.id
        ? routeWorkflowId
        : workflows.find((item) => item.id === current)?.id
          ? current
          : workflows[0].id;
      return next;
    });
  }, [routeWorkflowId, workflows]);
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

  const updateEditorDraft = useCallback((field, nextValue) => {
    setEditorDraft((current) => {
      if (!current) return current;
      const previous = current[field];
      const value = typeof nextValue === 'function' ? nextValue(previous) : nextValue;
      if (Object.is(previous, value)) return current;
      const changesContent = field === 'workflow' || field === 'jsonDraft';
      return {
        ...current,
        [field]: value,
        ...(changesContent ? { isDirty: true } : {}),
        ...(field === 'jsonDraft' ? { jsonDirty: true } : {}),
      };
    });
  }, [setEditorDraft]);
  const setDraftWorkflow = useCallback(
    (nextValue) => updateEditorDraft('workflow', nextValue),
    [updateEditorDraft],
  );
  const setJsonDraft = useCallback(
    (nextValue) => updateEditorDraft('jsonDraft', nextValue),
    [updateEditorDraft],
  );
  const setJsonError = useCallback(
    (nextValue) => updateEditorDraft('jsonError', nextValue),
    [updateEditorDraft],
  );
  const setActiveSidePanel = useCallback(
    (nextValue) => updateEditorDraft('activeSidePanel', nextValue),
    [updateEditorDraft],
  );
  const setNodeOptionsId = useCallback(
    (nextValue) => updateEditorDraft('nodeOptionsId', nextValue),
    [updateEditorDraft],
  );
  const setCanvasTool = useCallback(
    (nextValue) => updateEditorDraft('canvasTool', nextValue),
    [updateEditorDraft],
  );
  const draftHash = useMemo(
    () => (draftWorkflow ? JSON.stringify(draftWorkflow) : ''),
    [draftWorkflow],
  );
  const selectedHash = useMemo(
    () => (selectedDetail?.workflow ? JSON.stringify(selectedDetail.workflow) : ''),
    [selectedDetail],
  );
  const hasUnsavedChanges = Boolean(
    editorDraft?.isDirty && selectedDetail?.workflow && draftHash && draftHash !== selectedHash,
  );
  const nodeOptionsNode = graphNodes.find((node) => node.id === nodeOptionsId) || null;

  const syncWorkflowFromGraph = useCallback((
    nextNodes,
    nextEdges,
    nextViewport = viewportRef.current,
    shouldRetain = true,
  ) => {
    nodesRef.current = nextNodes;
    edgesRef.current = nextEdges;
    viewportRef.current = nextViewport;
    if (!shouldRetain) {
      return;
    }
    setDraftWorkflow((current) => (
      current ? flowToWorkflow(current, nextNodes, nextEdges, nextViewport) : current
    ));
  }, [setDraftWorkflow]);

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
  }, [setGraphEdges, setGraphNodes, setNodeOptionsId, syncWorkflowFromGraph]);

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

  const hydrateWorkflowDraft = useCallback((workflow, latestRun, path) => {
    if (!workflow) {
      nodesRef.current = [];
      edgesRef.current = [];
      setGraphNodes([]);
      setGraphEdges([]);
      return;
    }
    syncedPathRef.current = path;
    syncedHashRef.current = JSON.stringify(workflow);
    hydratingGraphRef.current = true;
    const hydrationToken = hydrationTokenRef.current + 1;
    hydrationTokenRef.current = hydrationToken;
    const flow = workflowToFlow(workflow, nodeCallbacks, latestRun);
    flow.nodes = sizeUnpackNodes(flow.nodes, flow.edges);
    nodesRef.current = flow.nodes;
    edgesRef.current = flow.edges;
    viewportRef.current = flow.viewport;
    setGraphNodes(flow.nodes);
    setGraphEdges(flow.edges);
    requestAnimationFrame(() => {
      if (hydrationTokenRef.current !== hydrationToken) {
        return;
      }
      if (flow.nodes.length) {
        reactFlow.setViewport(flow.viewport || DEFAULT_VIEWPORT);
      }
      setTimeout(() => {
        if (hydrationTokenRef.current === hydrationToken) {
          hydratingGraphRef.current = false;
        }
      }, 250);
    });
  }, [nodeCallbacks, reactFlow]);
  useEffect(() => {
    if (!selectedPath || !workflowDraftKey || !draftWorkflow) {
      return;
    }
    const selectionChanged = syncedPathRef.current !== selectedPath;
    const canonicalChanged = !editorDraft?.isDirty
      && syncedHashRef.current !== selectedHash;
    if (!selectionChanged && !canonicalChanged) {
      return;
    }
    if (canonicalChanged && workflowDraftControls.hasDraft) {
      const refreshedDraft = workflowEditorDraft(selectedDetail?.workflow);
      if (refreshedDraft) {
        setEditorDraft((current) => (current ? {
          ...refreshedDraft,
          activeSidePanel: current.activeSidePanel,
          nodeOptionsId: current.nodeOptionsId,
          canvasTool: current.canvasTool,
        } : current));
        hydrateWorkflowDraft(refreshedDraft.workflow, selectedLatestRun, selectedPath);
        return;
      }
    }
    hydrateWorkflowDraft(draftWorkflow, selectedLatestRun, selectedPath);
  }, [
    draftWorkflow,
    editorDraft?.isDirty,
    hydrateWorkflowDraft,
    selectedDetail,
    selectedHash,
    selectedLatestRun,
    selectedPath,
    setEditorDraft,
    workflowDraftControls.hasDraft,
    workflowDraftKey,
  ]);
  useEffect(() => {
    if (!draftWorkflow || editorDraft?.jsonDirty) {
      return;
    }
    const nextJsonDraft = `${JSON.stringify(draftWorkflow, null, 2)}\n`;
    if (jsonDraft === nextJsonDraft) {
      return;
    }
    setEditorDraft((current) => (
      current ? { ...current, jsonDraft: nextJsonDraft, jsonError: '' } : current
    ));
  }, [draftWorkflow, editorDraft?.jsonDirty, jsonDraft, setEditorDraft]);

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
      if (!current) return current;
      return typeof updater === 'function' ? updater(current) : updater;
    });
  }, [setDraftWorkflow]);

  // Graph handlers compute from the refs synchronously and only then set
  // state. Side effects inside a state updater run later, against the state
  // of that moment: a queued selection change would re-sync a graph that a
  // removal had already left (Cut put the edge back into the draft).
  const onNodesChange = useCallback((changes) => {
    const nextNodes = applyNodeChanges(changes, nodesRef.current);
    nodesRef.current = nextNodes;
    setGraphNodes(nextNodes);
    if (hydratingGraphRef.current) return;
    const changesWorkflow = changes.some((change) => (
      change.type === 'add'
      || change.type === 'remove'
      || change.dragging === true
      || (change.type === 'position' && change.position)
    ));
    if (changesWorkflow) {
      syncWorkflowFromGraph(nextNodes, edgesRef.current, viewportRef.current);
    }
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
    const changesWorkflow = changes.some((change) => change.type === 'add' || change.type === 'remove');
    const recomputed = recomputeUnpackSchemas(nodesRef.current, nextEdges);
    if (recomputed !== nodesRef.current) setGraphNodes(recomputed);
    if (changesWorkflow) {
      syncWorkflowFromGraph(recomputed, nextEdges, viewportRef.current);
    }
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
      reportFailure(show, 'Connect workflow nodes', validationError);
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
    if (hydratingGraphRef.current || !nodesRef.current.length) {
      return;
    }
    setDraftWorkflow((current) => (
      current ? flowToWorkflow(current, nodesRef.current, edgesRef.current, viewport) : current
    ));
  }, [setDraftWorkflow]);

  const handleSelectWorkflow = useCallback((workflowId) => {
    const workflow = workflows.find((w) => w.id === workflowId);
    navigate(buildEntityPath('/workflows', workflowId, workflow?.title), { replace: true });
  }, [navigate, workflows]);

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
      setEditorDraft((current) => (current ? {
        ...current,
        workflow: parsed,
        jsonError: '',
        jsonDirty: false,
        isDirty: true,
      } : current));
      setGraphNodes(nextNodes);
      setGraphEdges(flow.edges);
      nodesRef.current = nextNodes;
      edgesRef.current = flow.edges;
      viewportRef.current = flow.viewport;
      setActiveSidePanel(null);
    } catch (error) {
      setJsonError(error.message || 'Invalid workflow JSON');
    }
  };

  const handleResetJson = () => {
    workflowDraftControls.clear();
    if (selectedDetail?.workflow && selectedPath) {
      hydrateWorkflowDraft(selectedDetail.workflow, selectedLatestRun, selectedPath);
    }
  };

  const handleSaveWorkflow = async () => {
    if (!selectedPath || !draftWorkflow || workflowDraftControls.saving) {
      return;
    }
    const savedPath = selectedPath;
    const savedWorkflow = cloneValue(draftWorkflow);
    const preserveUnappliedJson = Boolean(editorDraft?.jsonDirty);
    const ticket = workflowDraftControls.beginSave();
    if (!ticket) {
      return;
    }
    if (preserveUnappliedJson) {
      // The raw source is not in this graph payload. Retain it as a newer
      // revision so graph confirmation releases the save without discarding it.
      setEditorDraft((current) => (current ? { ...current } : current));
    }
    try {
      const operation = dispatch(saveWorkflow({
        workflowsUrl,
        path: savedPath,
        workflow: savedWorkflow,
      }));
      workflowDraftControls.trackSave(ticket, {
        requestId: operation.requestId,
        events: ['workflow.updated'],
      });
      await operation.unwrap();
    } catch (error) {
      workflowDraftControls.failSave(ticket, error);
    }
  };

  const handlePauseResume = async () => {
    const workflowId = selectedDetail?.workflow?.id;
    if (!workflowId) {
      return;
    }
    const operation = selectedDetail.workflow.paused ? 'Resume workflow' : 'Pause workflow';
    const action = selectedDetail.workflow.paused ? resumeWorkflow : pauseWorkflow;
    const result = await workflowAction.run(operation, async () => dispatch(action({
      workflowsUrl,
      workflowId,
    })).unwrap());
    if (!result.ok && result.error) {
      reportFailure(show, operation, result.error);
    }
  };

  const handleTrigger = async () => {
    const workflowId = selectedDetail?.workflow?.id;
    if (!workflowId) {
      return;
    }
    const result = await workflowAction.run('Run workflow', async () => dispatch(triggerWorkflow({
      workflowsUrl,
      workflowId,
    })).unwrap());
    if (!result.ok && result.error) {
      reportFailure(show, 'Run workflow', result.error);
    }
  };

  const handleDeleteWorkflow = () => {
    const workflowId = selectedDetail?.workflow?.id;
    if (!workflowId) {
      return;
    }
    deleteAction.request('Delete workflow', async () => dispatch(
      deleteWorkflow({ workflowsUrl, workflowId }),
    ).unwrap());
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
            {workflowsState.error ? (
              <InlineFailure
                failure={workflowsState.error}
                operation="Load workflows"
                onRetry={() => dispatch(fetchWorkflows({ workflowsUrl }))}
              />
            ) : null}
            {workflowsState.loading && !workflowsState.loaded ? <PaneEmptyState>Loading workflows...</PaneEmptyState> : null}
            {!workflowsState.loading && !workflowsState.error && !visibleItems.length ? <PaneEmptyState>No workflows match the current filters.</PaneEmptyState> : null}
            {!workflowsState.loading && visibleItems.length ? (
              <PaneListRows
                className="workspace-sidebar__scroll"
                items={visibleItems}
                getItemKey={(item) => item.id}
                renderItem={(item) => (
                  <WorkflowRow
                    item={item}
                    latestRun={workflowRuns.find((run) => run.workflow_id === item.id) || null}
                    selectedWorkflowId={selectedWorkflowId}
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
      {deleteAction.open && selectedDetail?.workflow ? (
        <PopupModal
          eyebrow="Delete workflow"
          title={`Delete “${selectedDetail.workflow.title}”?`}
          message="This workflow and its configuration will be permanently deleted."
          confirmLabel="Delete workflow"
          busyLabel="Deleting…"
          error={deleteAction.error}
          onClose={deleteAction.close}
          onConfirm={deleteAction.confirm}
          busy={deleteAction.busy}
        />
      ) : null}

      <DetailPane flush className={`workflow-detail ${selectedDetail?.workflow ? 'is-graph-mode' : ''}`} label="Workflow detail">
        {!selectedPath && !workflowsState.loading ? (
          <PaneEmptyState detail>Select a workflow to edit or create a new one.</PaneEmptyState>
        ) : null}

        {selectedPath && !selectedDetail && detailRequest.error ? (
          <InlineFailure
            failure={detailRequest.error}
            operation="Load workflow detail"
            onRetry={loadWorkflowDetail}
          />
        ) : null}

        {selectedPath && !selectedDetail && !detailRequest.error ? (
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
                      {workflowDraftControls.error ? (
                        <InlineFailure
                          failure={workflowDraftControls.error}
                          operation="Save workflow"
                          onRetry={handleSaveWorkflow}
                        />
                      ) : null}
                      <DetailHeaderActions menuLabel="Workflow actions">
                        <DetailHeaderAction
                          icon={<PlayActionIcon />}
                          label="Run"
                          tooltip="Run workflow now"
                          ariaLabel="Run workflow now"
                          onClick={handleTrigger}
                          disabled={workflowAction.busy}
                        />
                        <DetailHeaderAction
                          icon={draftWorkflow.paused ? <PlayActionIcon /> : <PauseActionIcon />}
                          label={draftWorkflow.paused ? 'Resume' : 'Pause'}
                          tooltip={draftWorkflow.paused ? 'Resume workflow' : 'Pause workflow'}
                          ariaLabel={draftWorkflow.paused ? 'Resume workflow' : 'Pause workflow'}
                          onClick={handlePauseResume}
                          disabled={workflowAction.busy}
                        />
                        <DetailHeaderAction
                          icon={<SaveActionIcon />}
                          label="Save"
                          tooltip="Save workflow"
                          ariaLabel="Save workflow"
                          onClick={handleSaveWorkflow}
                          disabled={!hasUnsavedChanges || workflowDraftControls.saving}
                        />
                        <DetailHeaderAction
                          icon={<DeleteActionIcon />}
                          label="Delete"
                          tooltip="Delete workflow"
                          ariaLabel="Delete workflow"
                          variant="danger"
                          onClick={handleDeleteWorkflow}
                          disabled={deleteAction.busy}
                        />
                      </DetailHeaderActions>
                    </div>
                    <ReactFlow
                      nodes={graphNodes}
                      edges={graphEdges}
                      nodeTypes={NODE_TYPES}
                      onNodesChange={onNodesChange}
                      onConnect={onConnect}
                      onEdgesChange={onEdgesChange}
                      onConnectStart={onConnectStart}
                      onConnectEnd={onConnectEnd}
                      onEdgeClick={handleEdgeClick}
                      panOnDrag={canvasInteraction.panOnDrag}
                      selectionOnDrag={canvasInteraction.selectionOnDrag}
                      nodesDraggable={canvasInteraction.nodesDraggable}
                      nodesConnectable={canvasInteraction.nodesConnectable}
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

export default function WorkflowsPage() {
  return (
    <ReactFlowProvider>
      <WorkflowsInner />
    </ReactFlowProvider>
  );
}
