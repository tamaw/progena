import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../../context/ConfigContext';
import React, { useState, useRef, useEffect, useCallback, useMemo, useLayoutEffect } from 'react';
import {
  selectAgentRoutineRuns,
  selectAgentRunState,
  selectRoutineRunLifecycle,
  selectLatestRoutineRunLifecycle,
  startRoutineRun,
  answerRoutineApproval,
  fetchRoutineRuns,
  fetchRunState,
  selectAgentRoutineRevisionContent,
  fetchRoutineRevision,
  selectAgentProfiles,
  selectAgentRoutineRunsHasMore,
} from '../../store/uiDataSlice';
import {
  randomSessionId,
  parseRunThreadId,
  latestCompletionMs,
} from '../../lib/routineRunPolling';
import { watchRoutineRuns } from '../../store/routineSubscriptions';
import { createOperationRequestId } from '../../store/http';
import { formatAge } from './agentFormat';
import { diffRoutineGraphs } from '../../lib/routineGraphDiff';
import {
  isNodeComplete,
  incompleteNodes,
  incompleteLoopEdges,
  saveStatusWhenInSync,
  validateWorkingGraph,
} from './graphValidation';
import { isFlowEdge, edgeSourceIds, joinTerminals, buildLoopMembership } from './graphTopology';
import {
  uniqueNodeId,
  makeNode,
  normalizeGraphNodes,
  addForkLane,
  removeForkBranch,
  removeForkDefault,
  serializeGraph,
  stableStringify,
} from './graphEdits';
import { titleCase, CONDITION_BRANCHES } from './graphNodeKinds';
import { deriveActiveNodeIds } from './graphRunState';
import { EMPTY_MISSING, GraphEditContext } from './graphEditorContext';
import {
  DetailHeaderActions,
  DetailHeaderAction,
  OpenActionIcon,
  StopActionIcon,
  PlayActionIcon,
  DeleteActionIcon,
} from '../layout/DetailHeader';
import { ChevronLeftSmall, ChevronRightPointSmall, ChevronRightSmall } from './RoutineGraphIcons';
import { RoutineChain } from './RoutineChain';
import { NodeInspector } from './NodeInspector';
import PopupModal from '../common/PopupModal';
import hljs from 'highlight.js/lib/common';
import Select from '../common/Select';
import { PaneListRows } from '../layout/PaneList';
import '../../styles/canvas.css';
import '../../styles/nodes.css';


// Bottom-left zoom control's range/step -- 50%-200% in 10% increments,
// starting (and Reset zoom-ing back to) 100%.
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 2;
const ZOOM_STEP = 0.1;

const EMPTY_RUN_STATE = { run_meta: {}, data: {}, pending_approvals: [], found: false };

// The page watches checkpoint detail; lifecycle events arrive globally.
function useRoutineRunEvents(agentId, parameters, inputSchema) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const agentsUrl = getApiUrl('agentProfiles');
  const [sessionId, setSessionId] = useState(null);
  const [requestId, setRequestId] = useState(null);
  const [requestError, setRequestError] = useState(null);
  const [runStartedAtMs, setRunStartedAtMs] = useState(null);
  const interactiveSessionIdRef = useRef(null);
  const watchRef = useRef(null);
  const [hasInteractiveSession, setHasInteractiveSession] = useState(false);
  const requestedLifecycle = useSelector((state) => selectRoutineRunLifecycle(state, requestId));
  const latestLifecycle = useSelector((state) => selectLatestRoutineRunLifecycle(state, agentId));
  const lifecycle = !requestId ? latestLifecycle
    : requestedLifecycle && latestLifecycle?.session_id === sessionId && latestLifecycle.timestamp > requestedLifecycle.timestamp
      ? latestLifecycle : requestedLifecycle;
  const runs = useSelector((state) => selectAgentRoutineRuns(state, agentId));
  const threadId = lifecycle?.thread_id
    || (sessionId ? runs?.find((run) => run.run_id === sessionId)?.thread_id : null);
  const runState = useSelector((state) => threadId ? selectAgentRunState(state, threadId) : undefined) || EMPTY_RUN_STATE;
  const status = !requestId && !lifecycle ? 'idle'
    : requestError || ['completed', 'failed', 'paused'].includes(lifecycle?.status) ? 'settled'
      : lifecycle?.status === 'started' ? 'running' : 'discovering';

  useEffect(() => {
    setSessionId(null);
    setRequestId(null);
    setRequestError(null);
    interactiveSessionIdRef.current = null;
    setHasInteractiveSession(false);
    const watch = watchRoutineRuns(agentsUrl, agentId);
    watchRef.current = watch;
    return () => {
      watchRef.current = null;
      watch.close();
    };
  }, [agentId, agentsUrl]);

  const watchThread = useCallback((historicalThreadId) => {
    void watchRef.current?.select(historicalThreadId
      ? { thread_id: historicalThreadId }
      : sessionId ? { session_id: sessionId } : {}).catch(() => {});
  }, [sessionId]);

  const startRun = useCallback((targetNode) => {
    const newSessionId = targetNode
      ? (interactiveSessionIdRef.current ??= randomSessionId())
      : randomSessionId();
    const newRequestId = createOperationRequestId();
    if (targetNode) setHasInteractiveSession(true);
    setRunStartedAtMs(Date.now());
    setSessionId(newSessionId);
    setRequestId(newRequestId);
    setRequestError(null);
    const effectiveParameters = { ...parameters };
    for (const name of inputSchema?.required || []) {
      if (inputSchema.properties?.[name]?.type === 'boolean' && effectiveParameters[name] === undefined) {
        effectiveParameters[name] = false;
      }
    }
    const watch = watchRef.current;
    void (async () => {
      try {
        // Register first: even a millisecond-long graph cannot outrun its listener.
        await watch.select({ session_id: newSessionId });
        if (watchRef.current !== watch) return;
        await dispatch(startRoutineRun({ agentsUrl, agentId, sessionId: newSessionId,
          targetNode, parameters: effectiveParameters, requestId: newRequestId })).unwrap();
      } catch (error) {
        if (watchRef.current === watch) setRequestError(error.message);
      }
    })();
    return newSessionId;
  }, [agentsUrl, agentId, dispatch, parameters, inputSchema]);

  const resetInteractiveSession = useCallback(() => {
    interactiveSessionIdRef.current = null;
    setHasInteractiveSession(false);
  }, []);
  const answerApproval = useCallback(async (approvalThreadId, approval, answer) => {
    const newRequestId = createOperationRequestId();
    setSessionId(approvalThreadId.split(':').at(-1));
    setRequestId(newRequestId);
    setRequestError(null);
    try {
      await watchRef.current?.select({ thread_id: approvalThreadId });
      await dispatch(answerRoutineApproval({ agentsUrl, agentId, threadId: approvalThreadId,
        interruptId: approval.interrupt_id, answer, requestId: newRequestId })).unwrap();
    } catch (error) {
      setRequestError(error.message || 'Could not submit the response');
      void watchRef.current?.select({ thread_id: approvalThreadId }).catch(() => {});
      throw error;
    }
  }, [agentsUrl, agentId, dispatch]);
  const runRevisionId = useMemo(() => parseRunThreadId(threadId)?.revisionId ?? null, [threadId]);
  return {
    status, threadId, runMeta: runState.run_meta, runData: runState.data, pendingApprovals: runState.pending_approvals || EMPTY_RUN_STATE.pending_approvals, answerApproval,
    estimatedActiveStartMs: latestCompletionMs(runState.run_meta)
      ?? (lifecycle?.status === 'started' ? Date.parse(lifecycle.timestamp) : runStartedAtMs),
    runRevisionId, startRun, hasInteractiveSession, resetInteractiveSession, watchThread,
    error: requestError || lifecycle?.error,
  };
}

// Drives the canvas's top-right run-history pill: back/forward paging
// through every past execution of this routine (see agent_store's
// `list_routine_runs`, newest-first). Position 0 ("the front") always
// tracks whatever is most current -- the live in-session event state straight
// from useRoutineRunEvents while one is in flight or has ever run this
// session (ticking updates, no extra fetch), falling back to the newest
// list entry on a fresh mount that has not run anything yet. Any other
// position is purely historical: fetched once (it already
// finished) into the same runStateByThreadId cache the live events
// uses, so re-visiting a position already fetched this session is free.
function useRunHistoryNav(agentId, live) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const agentsUrl = getApiUrl('agentProfiles');
  const runsList = useSelector((state) => selectAgentRoutineRuns(state, agentId)) || [];

  useEffect(() => {
    dispatch(fetchRoutineRuns({ agentsUrl, agentId }));
    // Runs once per agent so back/forward works even if the Runs side
    // panel is never opened.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  const [viewedIndex, setViewedIndex] = useState(0);
  useEffect(() => setViewedIndex(0), [agentId]);

  const isLiveActive = live.status === 'discovering' || live.status === 'running';
  const atFront = viewedIndex <= 0;
  const usesLive = atFront && (isLiveActive || Boolean(live.threadId));

  const historicalThreadId = usesLive ? null : (runsList[viewedIndex]?.thread_id ?? null);
  const { watchThread } = live;
  useEffect(() => {
    watchThread(historicalThreadId);
  }, [historicalThreadId, watchThread]);
  const historicalState = useSelector((state) => (
    historicalThreadId ? selectAgentRunState(state, historicalThreadId) : undefined
  ));
  useEffect(() => {
    if (!historicalThreadId || historicalState) return;
    dispatch(fetchRunState({ agentsUrl, agentId, threadId: historicalThreadId }));
  }, [historicalThreadId, historicalState, agentsUrl, agentId, dispatch]);

  const displayEntry = usesLive
    ? (live.threadId ? runsList.find((run) => run.thread_id === live.threadId) : runsList[0])
    : runsList[viewedIndex];
  const isBrowsingHistory = !atFront;

  // Every run is assumed pinned to a revision (see agent_store's
  // list_routine_runs, which bakes revision_id into the thread_id) -- an
  // 'unpinned'/missing one is legacy data from before runs were tagged and
  // is simply treated as having nothing to show, not specially handled.
  // Fetched into the same revisionContentByKey cache the JSON/restore flow
  // already shares, so re-visiting an already-fetched run is free.
  const browsedRevisionId = isBrowsingHistory ? (displayEntry?.revision_id || null) : null;
  const browsedGraph = useSelector((state) => (
    browsedRevisionId ? selectAgentRoutineRevisionContent(state, agentId, browsedRevisionId) : undefined
  ));
  useEffect(() => {
    if (!browsedRevisionId || browsedGraph) return;
    dispatch(fetchRoutineRevision({ agentsUrl, agentId, revisionId: browsedRevisionId }));
  }, [browsedRevisionId, browsedGraph, agentsUrl, agentId, dispatch]);

  const goBack = useCallback(() => {
    setViewedIndex((current) => Math.min(current + 1, Math.max(runsList.length - 1, 0)));
  }, [runsList.length]);
  const goForward = useCallback(() => setViewedIndex((current) => Math.max(current - 1, 0)), []);
  const resetToLatest = useCallback(() => setViewedIndex(0), []);
  // Lets the Runs panel jump straight to any row -- clamped the same way
  // goBack/goForward are, so an out-of-range index (a stale click after the
  // list shrank) can't leave viewedIndex pointing past the end.
  const jumpTo = useCallback((index) => {
    setViewedIndex(Math.max(0, Math.min(index, Math.max(runsList.length - 1, 0))));
  }, [runsList.length]);

  return {
    runsList,
    viewedIndex,
    runMeta: usesLive ? live.runMeta : (historicalState?.run_meta || {}),
    runData: usesLive ? live.runData : (historicalState?.data || {}),
    threadId: usesLive ? live.threadId : historicalThreadId,
    pendingApprovals: usesLive ? live.pendingApprovals : (historicalState?.pending_approvals || EMPTY_RUN_STATE.pending_approvals),
    runRevisionId: usesLive ? live.runRevisionId : (displayEntry?.revision_id || null),
    hasRuns: runsList.length > 0 || isLiveActive,
    canGoBack: runsList.length > 0 && viewedIndex < runsList.length - 1,
    canGoForward: viewedIndex > 0,
    label: displayEntry ? (formatAge(displayEntry.last_checkpoint_at) || 'now') : (isLiveActive ? 'Running…' : null),
    title: displayEntry?.last_checkpoint_at || '',
    // Everything about a browsed run is locked (see the toolbar's Restore
    // button): the graph shown is that run's own pinned snapshot, not the
    // editable draft, so there is nothing sound to edit until either
    // stepping back to the live front position or explicitly Restoring it.
    isBrowsingHistory,
    browsedGraph: isBrowsingHistory ? browsedGraph : null,
    canRestore: isBrowsingHistory && Boolean(browsedGraph),
    goBack,
    goForward,
    jumpTo,
    resetToLatest,
  };
}

// Which node ids' cached run details (timing/tokens/output) are stale
// relative to the CURRENT draft graph -- the run executed against
// `runRevisionId`'s pinned snapshot, and the draft may have been edited
// since (see lib/routineGraphDiff.js). `runRevisionId` is null for a run
// against the live/unpinned document (no revision reader configured) --
// there is no earlier snapshot to diff against in that case, so nothing
// is ever flagged stale for it; a documented limitation, not a soundness
// claim. Only meaningful at the run-nav's front (live draft) position --
// GraphCanvas discards this result while browsing history, since the
// canvas there already shows that run's own exact snapshot (see
// useRunHistoryNav's browsedGraph), which can never diverge from itself.
function useRunStaleness(agentId, runRevisionId, currentGraph) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const agentsUrl = getApiUrl('agentProfiles');
  const baselineGraph = useSelector((state) => (
    runRevisionId ? selectAgentRoutineRevisionContent(state, agentId, runRevisionId) : undefined
  ));
  useEffect(() => {
    if (!runRevisionId || baselineGraph) return;
    dispatch(fetchRoutineRevision({ agentsUrl, agentId, revisionId: runRevisionId }));
  }, [runRevisionId, baselineGraph, agentsUrl, agentId, dispatch]);
  return useMemo(
    () => (runRevisionId && baselineGraph ? diffRoutineGraphs(baselineGraph, currentGraph) : new Set()),
    [runRevisionId, baselineGraph, currentGraph],
  );
}

function GraphCanvas({ agent, graph, confirmedGraph, onPersist, onShowProfile, onDelete, jsonOpen, onOpenJson, onCloseJson, runsOpen, onOpenRuns, onCloseRuns }) {
  const persistRef = useRef(onPersist);
  const lastSavedRef = useRef(null);
  const inFlightSavesRef = useRef(new Map());
  // Values the user fills in for the routine's declared input_schema.properties
  // (see RunParametersModal) -- session-local only, never persisted to the
  // graph document; startRun sends the current snapshot with every POST.
  const [meta, setMeta] = useState({
    status: graph.status || 'Draft',
    version: graph.version || '2.0.0',
    summary: graph.summary || '',
    entry: graph.entry || (graph.nodes || [])[0]?.id || null,
    settings: graph.settings || {},
    input_schema: graph.input_schema || null,
    output_schema: graph.output_schema || null,
    $defs: graph.$defs || {},
  });
  const [parameterValues, setParameterValues] = useState({});
  const runtimeProfiles = useSelector(selectAgentProfiles);
  const {
    status: runStatus,
    threadId: liveThreadId,
    runMeta: liveRunMeta,
    runData: liveRunData,
    estimatedActiveStartMs,
    runRevisionId: liveRunRevisionId,
    startRun,
    hasInteractiveSession,
    resetInteractiveSession,
    watchThread,
    pendingApprovals: livePendingApprovals,
    answerApproval,
  } = useRoutineRunEvents(agent.id, parameterValues, meta.input_schema);
  const runNav = useRunHistoryNav(agent.id, {
    status: runStatus,
    threadId: liveThreadId,
    runMeta: liveRunMeta,
    runData: liveRunData,
    runRevisionId: liveRunRevisionId,
    pendingApprovals: livePendingApprovals,
    watchThread,
  });
  const runMeta = runNav.runMeta;
  const runData = runNav.runData;
  const runRevisionId = runNav.runRevisionId;
  const { resetToLatest: resetRunNavToLatest } = runNav;
  const submitApproval = useCallback(async (approval, answer) => {
    const approvalThreadId = runNav.threadId;
    resetRunNavToLatest();
    await answerApproval(approvalThreadId, approval, answer);
  }, [runNav.threadId, resetRunNavToLatest, answerApproval]);
  const startRunAndTrackLatest = useCallback((targetNode) => {
    resetRunNavToLatest();
    return startRun(targetNode);
  }, [resetRunNavToLatest, startRun]);
  const { isBrowsingHistory } = runNav;
  useEffect(() => {
    if (!isBrowsingHistory) return;
    onCloseJson();
    setRunModalOpen(false);
    // Only reacts to entering browsing itself -- onCloseJson is a fresh
    // closure every render (AgentsPage inlines it), and doesn't need to
    // re-fire this on its own identity change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isBrowsingHistory]);
  // Which iteration a loop's own prev/next nav has explicitly selected,
  // keyed by the guard node's id (see LoopBlock/pickEntryForIteration) --
  // absent means "latest" (today's default). Reset the instant a fresh
  // live run starts so a past browse doesn't strand the canvas on a stale
  // iteration from before this run existed.
  const [loopIterations, setLoopIterations] = useState({});
  const setLoopIteration = useCallback((guardId, iteration) => {
    setLoopIterations((current) => ({ ...current, [guardId]: iteration }));
  }, []);
  useEffect(() => {
    setLoopIterations({});
  }, [liveThreadId]);
  // Plain CSS `zoom` on the scrolling chain content only -- not a canvas-
  // style pan/zoom viewport (this isn't React Flow, see the module doc).
  // Deliberately NOT wired into positionInspector's math: CSS `zoom`
  // reflows layout, so getBoundingClientRect() on a zoomed node already
  // reports its real post-zoom screen position, and that function reads
  // real rects off the DOM rather than any canvas-model coordinates --
  // it keeps working unmodified at any zoom level.
  const [zoom, setZoom] = useState(1);
  useEffect(() => setZoom(1), [agent.id]);
  const zoomOut = useCallback(() => setZoom((z) => Math.max(ZOOM_MIN, Math.round((z - ZOOM_STEP) * 100) / 100)), []);
  const zoomIn = useCallback(() => setZoom((z) => Math.min(ZOOM_MAX, Math.round((z + ZOOM_STEP) * 100) / 100)), []);
  const resetZoom = useCallback(() => setZoom(1), []);
  const [nodes, setNodes] = useState(() => (graph.nodes || []).map((node) => ({ ...node })));
  const [edges, setEdges] = useState(() => (graph.edges || []).map((edge) => ({ ...edge })));
  const [saveState, setSaveState] = useState('idle');
  const [saveError, setSaveError] = useState(null);
  // Snapshotted alongside `saveState` (never read live from the `incomplete`
  // memo at render time) -- `saveState` only updates once the autosave
  // effect below runs, one render behind the `incomplete` count itself, so
  // rendering the live count next to a stale 'incomplete' saveState could
  // briefly show a self-contradictory "Not saved · 0 steps incomplete" the
  // instant the last field is fixed, before the effect catches up. Both are
  // set in the same effect run now, so they can never disagree.
  const [incompleteCount, setIncompleteCount] = useState(0);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [inspectorPosition, setInspectorPosition] = useState(null);
  // Mirrors confirmedNodeIdsRef for the currently open node only, captured
  // once at open time (see positionInspector) -- render can't read a ref
  // directly, and this doesn't need to react to anything else.
  const [selectedNodeIsNew, setSelectedNodeIsNew] = useState(false);
  const canvasRef = useRef(null);
  const panelRef = useRef(null);
  // Which node ids have a legitimate prior state to fall back to -- either
  // loaded from the saved graph, or previously closed here while complete.
  // A freshly inserted node starts outside this set: closing it invalid has
  // nothing sane to revert to, so it deletes instead (see closeInspector).
  const confirmedNodeIdsRef = useRef(new Set((graph.nodes || []).map((node) => node.id)));
  // The selected node's own fields as they stood the moment it was opened,
  // so an invalid close on an already-confirmed node can discard just this
  // edit rather than the whole step.
  const openSnapshotRef = useRef(null);
  const selectedNodeIdRef = useRef(null);
  useEffect(() => { selectedNodeIdRef.current = selectedNodeId; }, [selectedNodeId]);

  useEffect(() => { persistRef.current = onPersist; }, [onPersist]);

  // JSON Apply and the autosave effect can observe the same state update. Share
  // one request for an identical graph signature so a direct Apply doesn't
  // race its own debounced autosave into issuing a second PATCH.
  const persistGraph = useCallback((payload) => {
    const signature = stableStringify(payload);
    const existing = inFlightSavesRef.current.get(signature);
    if (existing) return existing;

    const request = Promise.resolve()
      .then(() => persistRef.current(payload))
      .then((result) => {
        lastSavedRef.current = signature;
        return result;
      });
    inFlightSavesRef.current.set(signature, request);
    request.then(
      () => { if (inFlightSavesRef.current.get(signature) === request) inFlightSavesRef.current.delete(signature); },
      () => { if (inFlightSavesRef.current.get(signature) === request) inFlightSavesRef.current.delete(signature); },
    );
    return request;
  }, []);

  // Refs mirror the latest state synchronously so insertAt and friends
  // can compute a consistent next state without nesting setState calls
  // inside other setState updaters.
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  const metaRef = useRef(meta);
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  const positionInspector = useCallback((id, target) => {
    const canvas = canvasRef.current;
    if (!canvas || !target) return;
    // Switching straight from one card to another (no explicit close in
    // between) is still a "leave it as-is" for whatever was open, provided
    // it was actually complete -- selectNode already refuses to switch away
    // from an incomplete one, so reaching here means it graduates too.
    const previousId = selectedNodeIdRef.current;
    if (previousId && previousId !== id) {
      const previousNode = nodesRef.current.find((node) => node.id === previousId);
      if (previousNode && isNodeComplete(previousNode)) {
        confirmedNodeIdsRef.current.add(previousId);
      }
    }
    const openingNode = nodesRef.current.find((node) => node.id === id);
    openSnapshotRef.current = openingNode ? { ...openingNode } : null;
    const canvasRect = canvas.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const panelWidth = 340;
    const left = Math.max(16, Math.min(
      targetRect.right - canvasRect.left + 14,
      canvasRect.width - panelWidth - 16,
    ));
    const top = Math.max(72, targetRect.top - canvasRect.top);
    // Placed right next to the clicked node first; a node's config fields
    // vary a lot by kind (a bare noop vs. a prompt's two textareas), so
    // there's no single safe height to assume up front. The layout effect
    // below measures the panel's real content once it's mounted and, ONLY
    // if that overflows the canvas, slides the panel up just enough to
    // fit -- rather than guessing a generic budget that either strands the
    // panel far from a small node (too generous) or clips a tall node's
    // fields out of the clickable area (too tight, see prior regressions).
    setSelectedNodeId(id);
    setSelectedNodeIsNew(!confirmedNodeIdsRef.current.has(id));
    setInspectorPosition({ left, top, maxHeight: canvasRect.height - top - 16 });
  }, []);
  // A held-open step refuses selection of any other card, or "held open" would
  // only mean "held open until you click the next one". Insertion deliberately
  // still works and carries the lock to the new step -- the one it leaves
  // behind keeps its badge, and the save stays withheld for both.
  const lockedNodeIdRef = useRef(null);
  useEffect(() => {
    const selected = nodes.find((node) => node.id === selectedNodeId) || null;
    lockedNodeIdRef.current = selected && !isNodeComplete(selected) ? selected.id : null;
  }, [nodes, selectedNodeId]);
  useEffect(() => {
    if (!selectedNodeId) openSnapshotRef.current = null;
  }, [selectedNodeId]);
  const selectNode = useCallback((id, event) => {
    const locked = lockedNodeIdRef.current;
    if (locked && locked !== id) return;
    positionInspector(id, event.currentTarget);
  }, [positionInspector]);
  // A newly inserted node opens its own inspector. Every kind except `noop`
  // lands with its required config empty, so the fields have to be put in
  // front of the author rather than left to be discovered after a rejected
  // save. Positioning needs the card's real geometry, which only exists once
  // React has committed it -- hence the ref handoff instead of selecting
  // inline in insertAt.
  const pendingSelectRef = useRef(null);
  useLayoutEffect(() => {
    const id = pendingSelectRef.current;
    if (!id) return;
    const escaped = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(id) : id;
    const target = canvasRef.current?.querySelector(`[data-node-id="${escaped}"]`);
    if (!target) return;
    pendingSelectRef.current = null;
    positionInspector(id, target);
  }, [nodes, positionInspector]);
  // One-shot correction per selection: measures the panel's actual
  // (unclamped) content height once it's mounted, and only then decides
  // whether it needs to slide up to fit -- see selectNode above for why
  // this can't be guessed ahead of render.
  useLayoutEffect(() => {
    const panel = panelRef.current;
    const canvas = canvasRef.current;
    if (!selectedNodeId || !panel || !canvas) return;
    const head = panel.querySelector('.routine-node-inspector__head');
    const body = panel.querySelector('.routine-node-inspector__body');
    const foot = panel.querySelector('.routine-node-inspector__foot');
    if (!head || !body || !foot) return;
    const canvasRect = canvas.getBoundingClientRect();
    const bottomLimit = canvasRect.height - 16;
    // body has its own overflow:auto, so its scrollHeight is the field
    // content's true extent regardless of how clamped the panel currently
    // renders it -- unlike the outer panel, which is overflow:hidden and
    // would just report back whatever height it's already been clamped to.
    const naturalHeight = head.offsetHeight + body.scrollHeight + foot.offsetHeight;
    setInspectorPosition((current) => {
      if (!current) return current;
      if (current.top + naturalHeight <= bottomLimit) {
        // Fits as placed -- just stop clamping height below what it needs.
        return current.maxHeight >= naturalHeight ? current : { ...current, maxHeight: naturalHeight };
      }
      const top = Math.max(16, bottomLimit - naturalHeight);
      const maxHeight = Math.min(naturalHeight, bottomLimit - top);
      if (top === current.top && maxHeight === current.maxHeight) return current;
      return { ...current, top, maxHeight };
    });
  }, [selectedNodeId]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);
  useEffect(() => { metaRef.current = meta; }, [meta]);

  const updateNode = useCallback((id, patch) => {
    setNodes((current) => current.map((node) => (node.id === id ? { ...node, ...patch } : node)));
  }, []);

  const deleteNode = useCallback((id) => {
    confirmedNodeIdsRef.current.delete(id);
    // A loop's start and tail are its structural boundary: the guard edge
    // is `to` the start (found from the tail) or `from` the tail (found
    // from the start) -- either way it's the same edge, and it never
    // survives either boundary node going. Reattaching through it (its
    // primary `to` is always the loop's own start) would silently turn it
    // into a self-loop; reattaching *as* it (keeping `loop_guard` on a
    // rewritten edge) would leave a guard with nothing left to guard. The
    // loop's actual exit -- its `on_false`, if any -- is what a deleted
    // boundary node's own predecessor should land on instead.
    const guardEdge = edgesRef.current.find((edge) => edge.loop_guard && (edge.to === id || edge.from === id));
    const outgoing = edgesRef.current.find((edge) => isFlowEdge(edge) && edgeSourceIds(edge).includes(id));
    // A router's own edge has no `to` -- its nearest thing to "what happens
    // next" is its `default` arm, the one place a router already names
    // "nothing more specific applied". No default -> no single answer
    // (it had several branches, not one), same as the plain-edge case
    // below when there's nothing to fall back to.
    const singleTarget = guardEdge
      ? (typeof guardEdge.loop_guard.on_false === 'string' ? guardEdge.loop_guard.on_false : null)
      : outgoing?.router
        ? (typeof outgoing.default === 'string' ? outgoing.default : null)
        : (outgoing?.to && typeof outgoing.to === 'string' ? outgoing.to : null);
    // A condition's two arms are structural, not author-defined, so deleting
    // the step sitting on one has to leave a placeholder behind. Dropping the
    // arm instead would leave a one-armed condition the editor offers no
    // "+ Add branch" to repair. Computed up front so the state updaters below
    // stay pure (see buildChainTree's comment on StrictMode double-invokes).
    const armPlaceholders = [];
    if (!singleTarget) {
      const conditionIds = new Set(
        nodesRef.current.filter((node) => node.kind === 'condition').map((node) => node.id),
      );
      edgesRef.current.forEach((edge) => {
        if (!edge.router || !conditionIds.has(edge.from)) return;
        Object.entries(edge.branches || {}).forEach(([value, target]) => {
          if (target !== id) return;
          const armId = uniqueNodeId('noop', [...nodesRef.current, ...armPlaceholders.map((arm) => arm.node)]);
          armPlaceholders.push({
            from: edge.from,
            value,
            node: { ...makeNode('noop'), id: armId, label: titleCase(value) },
          });
        });
      });
    }
    setNodes((current) => [
      ...current.filter((node) => node.id !== id),
      ...armPlaceholders.map((arm) => arm.node),
    ]);
    setEdges((current) => {
      const next = [];
      current.forEach((edge) => {
        // This loop's own guard edge never survives either boundary node
        // going -- see the comment above `guardEdge`.
        if (edge.loop_guard && (edge.to === id || edge.from === id)) return;
        // The empty-list fallback belongs to the foreach and its companion
        // "Join each" marker. Deleting either endpoint drops this hidden
        // routing edge; it must not be spliced onto the body node instead.
        if (edge.level === 'foreach_empty') {
          if (edge.from === id || edge.to === id) return;
          next.push(edge);
          return;
        }
        // The deleted node's own outgoing edge -- plain, fan_in-target, or
        // (a router node deleted outright) its own route edge -- never
        // survives it. What was built inside its branches isn't cascade-
        // deleted; losing this edge just makes it unreachable, exactly
        // like any other content UnreachableTray already exists to catch.
        if (edgeSourceIds(edge).includes(id) && edge.level !== 'fan_in') return;
        if (edge.router) {
          const branches = { ...edge.branches };
          let changed = false;
          Object.entries(branches).forEach(([value, target]) => {
            if (target !== id) return;
            changed = true;
            const placeholder = armPlaceholders.find(
              (arm) => arm.from === edge.from && arm.value === value,
            );
            if (singleTarget) branches[value] = singleTarget;
            else if (placeholder) branches[value] = placeholder.node.id;
            else delete branches[value];
          });
          let defaultTarget = edge.default;
          if (defaultTarget === id) {
            changed = true;
            defaultTarget = singleTarget || undefined;
          }
          if (!changed) { next.push(edge); return; }
          if (!Object.keys(branches).length && !defaultTarget) return; // router now empty, drop it
          if (!Object.keys(branches).length) {
            next.push({ from: edge.from, to: defaultTarget, level: 'normal' });
            return;
          }
          next.push({ ...edge, branches, ...(defaultTarget ? { default: defaultTarget } : { default: undefined } ) });
          return;
        }
        if (edge.level === 'fan_in') {
          if (edge.to === id) {
            if (singleTarget) next.push({ ...edge, to: singleTarget });
            return;
          }
          const sources = edgeSourceIds(edge).filter((source) => source !== id);
          if (sources.length === edgeSourceIds(edge).length) { next.push(edge); return; } // id wasn't a source; unchanged
          if (sources.length >= 2) { next.push({ ...edge, from: sources }); return; }
          if (sources.length === 1) { next.push({ from: sources[0], to: edge.to, level: 'normal' }); return; }
          return; // no sources left, drop the edge
        }
        if (edge.to === id) {
          if (singleTarget) next.push({ ...edge, to: singleTarget });
          return;
        }
        next.push(edge);
      });
      return next;
    });
    // The virtual Start pointing at `entry` is itself an incoming reference
    // to the deleted node -- splice it through to singleTarget exactly like
    // every other incoming edge above, rather than leaving `entry` null.
    setMeta((current) => (current.entry === id ? { ...current, entry: singleTarget } : current));
  }, []);

  // The × button's close: an invalid step behaves like the Delete step
  // button if it never had a valid prior state (a freshly inserted node
  // whose required field was never filled in), or discards just this edit
  // and reverts to the open-time snapshot if it did. A complete step just
  // closes normally, and graduates into "has a valid prior state" for next
  // time.
  const closeInspector = useCallback((node) => {
    if (node) {
      if (isNodeComplete(node)) {
        confirmedNodeIdsRef.current.add(node.id);
      } else if (confirmedNodeIdsRef.current.has(node.id)) {
        if (openSnapshotRef.current) updateNode(node.id, openSnapshotRef.current);
      } else {
        deleteNode(node.id);
      }
    }
    setSelectedNodeId(null);
    setInspectorPosition(null);
  }, [updateNode, deleteNode]);

  // Two kinds arrive pre-wired because their shape is intrinsic, not authored.
  // A Send foreach owns a scoped body: its one normal outgoing edge names the
  // first dynamic target, and the companion noop marks the body boundary so
  // the rigid chain renderer can still show the continuation after the
  // fan-out. A condition is a two-way gate: it is *always* a router over
  // exactly `true`/`false` (see CONDITION_BRANCH_VALUES server-side), so it
  // lands as a fork rather than being authored as one after the fact.
  const insertAt = useCallback((slot, kind) => {
    // `loop` has no backend node kind of its own -- the body-start node it
    // creates is a plain `noop`, exactly like a foreach's "Join each" stop
    // marker is. Looping is entirely a property of the tail's own edge
    // (`loop_guard`), never of a node, so nothing here ever writes a
    // `kind: 'loop'` onto the wire.
    const nodeKind = kind === 'loop' ? 'noop' : kind;
    const id = uniqueNodeId(kind, nodesRef.current);
    const added = [{ ...makeNode(nodeKind), id, ...(kind === 'loop' ? { label: 'Loop' } : {}) }];
    let targetId = null;
    let joinEdge = null;
    const nextEdges = edgesRef.current.map((edge) => (
      edge.branches ? { ...edge, branches: { ...edge.branches } } : { ...edge }
    ));
    if (slot.kind === 'entry') {
      targetId = metaRef.current.entry;
      setMeta((current) => ({ ...current, entry: id }));
    } else if (slot.kind === 'plain' || slot.kind === 'foreach') {
      // `foreach` is the slot inside a fan-out's body. The edge leaving the
      // fan-out node is that body's entry, so it splices like a plain one.
      const existing = nextEdges.find((edge) => isFlowEdge(edge) && edgeSourceIds(edge).includes(slot.sourceId));
      if (existing?.level === 'fan_in') {
        // The step feeds a join, and a join names its sources rather than
        // being pointed at, so there is no `to` here to steal. Splice by
        // handing this lane's slot in the join over to the new step: pushing
        // a second outgoing edge instead leaves the old step with two edge
        // declarations, which the backend rejects outright.
        joinEdge = existing;
        nextEdges.push({ from: slot.sourceId, to: id, level: 'normal' });
      } else if (existing) {
        targetId = existing.to;
        existing.to = id;
      } else {
        nextEdges.push({ from: slot.sourceId, to: id, level: 'normal' });
      }
    } else if (slot.kind === 'branch') {
      const router = nextEdges.find((edge) => edge.from === slot.sourceId);
      if (router) {
        targetId = router.branches[slot.value];
        router.branches[slot.value] = id;
      }
    } else if (slot.kind === 'default') {
      const router = nextEdges.find((edge) => edge.from === slot.sourceId);
      if (router) {
        targetId = router.default;
        router.default = id;
      }
    } else if (slot.kind === 'loop-exit') {
      // The connector after a loop's box: its source is the tail, whose
      // own outgoing edge is the guard itself, so this must not touch
      // `to` (always the loop's own start) the way a plain slot would --
      // only the guard's on_false, its actual "next".
      const tail = nextEdges.find((edge) => edge.from === slot.sourceId && edge.loop_guard);
      if (tail) {
        targetId = tail.loop_guard.on_false;
        tail.loop_guard = { ...tail.loop_guard, on_false: id };
      }
    }
    // Whatever the new step ends on is the lane's terminal from here: a
    // fan-out's companion stop, a condition's two arms, or the step itself.
    let terminalIds = [id];
    if (kind === 'loop') {
      // A stable tail marker the body always flows back through -- never a
      // moving target -- so extending the body later (inserting at the
      // connector just before the tail) is an ordinary splice: body
      // extension only ever touches edges INTO the tail, never the tail's
      // own outgoing (guarded) edge. Mirrors foreach's stop-node shape
      // exactly, just with a back-edge instead of a forward one.
      //
      // There's no dedicated counter node kind: `eval` already IS the
      // general "evaluate an expression, write the result" primitive, and
      // an increment is just an expression. LangGraph's conditional-edge
      // routing function is typed to return only a path name, never also
      // update state (checked against the installed langgraph's own
      // add_conditional_edges signature) -- a plain edge genuinely cannot
      // count itself -- but a node executing right before that edge fires,
      // every single time, is the closest honest equivalent. Its own
      // increment commits before its outgoing (guarded) edge evaluates, so
      // the guard always sees the fresh count.
      //
      // `tailId` is the node's own state key -- expressions resolve names
      // directly against state now (no separate `inputs` binding step), so
      // the expression just references it by name. The only thing `eval`
      // needed to cover this case was a fallback for its own not-yet-
      // written prior output on the loop's first visit -- `config.defaults`
      // supplies that (see EvalNodeConfig.defaults).
      const tailId = uniqueNodeId('count', [...nodesRef.current, ...added]);
      added.push({
        ...makeNode('eval'),
        id: tailId,
        label: '↺ Continue',
        config: { expression: `${tailId} + 1`, defaults: { [tailId]: 0 } },
      });
      nextEdges.push({ from: id, to: tailId, level: 'normal' });
      // A loop spliced in front of a shared join reaches it directly
      // through its own guard's on_false, not by joining the declared
      // fan_in sources -- the tail already has its own outgoing edge
      // (this one), and a node can only declare one.
      const exitTarget = joinEdge ? joinEdge.to : targetId;
      nextEdges.push({
        from: tailId,
        to: id,
        level: 'normal',
        loop_guard: {
          expression: `${tailId} < 3`,
          ...(exitTarget ? { on_false: exitTarget } : {}),
        },
      });
      terminalIds = [];
    } else if (kind === 'foreach') {
      const stopId = uniqueNodeId('noop', [...nodesRef.current, ...added]);
      added.push({ ...makeNode('noop'), id: stopId, label: 'Join each' });
      nextEdges.push({ from: id, to: stopId, level: 'foreach_empty' });
      nextEdges.push({ from: id, to: stopId, level: 'normal' });
      if (targetId) nextEdges.push({ from: stopId, to: targetId, level: 'normal' });
      terminalIds = [stopId];
    } else if (kind === 'condition') {
      // Both arms get their own placeholder step. Whatever the condition was
      // spliced in front of stays reachable as a fan_in join below the fork
      // rather than being stranded on one arbitrary arm.
      const arms = CONDITION_BRANCHES.map((value) => {
        const armId = uniqueNodeId('noop', [...nodesRef.current, ...added]);
        added.push({ ...makeNode('noop'), id: armId, label: titleCase(value) });
        return [value, armId];
      });
      nextEdges.push({
        from: id,
        level: 'route',
        router: 'state_equals',
        state_key: id,
        branches: Object.fromEntries(arms),
      });
      terminalIds = arms.map(([, armId]) => armId);
      if (targetId) {
        nextEdges.push({ from: terminalIds, to: targetId, level: 'fan_in' });
      }
    } else if (kind === 'switch' || kind === 'split') {
      // One growable arm, not two fixed ones: `switch`/`split` are
      // open-ended (author adds more via "+ Add branch"), unlike
      // `condition`'s fixed true/false pair. Whatever the connector was
      // pointing at before insertion carries straight through as the
      // router's `default` rather than needing a separate fan_in join --
      // exactly the shape the old (removed) "Split into branches" action
      // used to produce by hand.
      const armId = uniqueNodeId('noop', [...nodesRef.current, ...added]);
      added.push({ ...makeNode('noop'), id: armId, label: kind === 'switch' ? 'Case 1' : 'Path 1' });
      nextEdges.push({
        from: id,
        level: 'route',
        router: 'state_equals',
        state_key: id,
        branches: { 'case-1': armId },
        ...(targetId ? { default: targetId } : {}),
      });
      terminalIds = [armId];
    } else if (targetId) {
      nextEdges.push({ from: id, to: targetId, level: 'normal' });
    }
    if (joinEdge) {
      joinEdge.from = [
        ...edgeSourceIds(joinEdge).filter((source) => source !== slot.sourceId),
        ...terminalIds,
      ];
    }
    // The loop-start node has nothing of its own to configure (it's a bare
    // `noop`; its badge would misleadingly read "Noop" too) -- the
    // author's next move is the tail's own expression popover, not this
    // inspector, so don't steal focus with it.
    if (kind !== 'loop') pendingSelectRef.current = id;
    setNodes((current) => [...current, ...added]);
    setEdges(nextEdges);
  }, []);

  const onChangeStateKey = useCallback((sourceId, value) => {
    setEdges((current) => current.map((edge) => (
      edge.from === sourceId && edge.router ? { ...edge, state_key: value } : edge
    )));
  }, []);

  const updateLoopGuard = useCallback((fromId, patch) => {
    setEdges((current) => current.map((edge) => (
      edge.from === fromId && edge.loop_guard ? { ...edge, loop_guard: { ...edge.loop_guard, ...patch } } : edge
    )));
  }, []);

  const removeBranching = useCallback((sourceId) => {
    setEdges((current) => current.map((edge) => {
      if (edge.from !== sourceId || !edge.router) return edge;
      return edge.default ? { from: sourceId, to: edge.default, level: 'normal' } : null;
    }).filter(Boolean));
  }, []);

  const addLane = useCallback((sourceId, isDefault) => {
    const laneId = uniqueNodeId('noop', nodesRef.current);
    setEdges((current) => addForkLane(current, sourceId, laneId, isDefault));
    setNodes((current) => [...current, { ...makeNode('noop'), id: laneId }]);
  }, []);

  const onAddBranch = useCallback((sourceId) => addLane(sourceId, false), [addLane]);

  const onAddDefault = useCallback((sourceId) => addLane(sourceId, true), [addLane]);


  const onJoinBranches = useCallback((sourceId, kind = 'noop') => {
    const terminals = joinTerminals(edgesRef.current, sourceId);
    if (!terminals.length) return;
    const joinId = uniqueNodeId(kind, nodesRef.current);
    const nextEdges = [
      ...edgesRef.current,
      { from: terminals, to: joinId, level: 'fan_in' },
    ];
    setNodes((current) => [...current, {
      ...makeNode(kind),
      id: joinId,
      ...(kind === 'noop' ? { label: 'Join branches' } : {}),
    }]);
    setEdges(nextEdges);
  }, []);

  // Both halves of a lane drop have to land together: the edge rewrite decides
  // which steps the lane owned, and those steps are then deleted rather than
  // left behind as unreachable clutter.
  const dropLane = useCallback((drop) => {
    const { edges: nextEdges, removedNodeIds } = drop(edgesRef.current, metaRef.current.entry);
    setEdges(nextEdges);
    if (removedNodeIds.length) {
      const removed = new Set(removedNodeIds);
      setNodes((current) => current.filter((node) => !removed.has(node.id)));
    }
  }, []);

  const onRemoveBranch = useCallback((sourceId, value) => {
    dropLane((edges, entry) => removeForkBranch(edges, entry, sourceId, value));
  }, [dropLane]);

  const onRemoveDefault = useCallback((sourceId) => {
    dropLane((edges, entry) => removeForkDefault(edges, entry, sourceId));
  }, [dropLane]);

  const onRenameBranch = useCallback((sourceId, oldValue, newValue) => {
    setEdges((current) => current.map((edge) => {
      if (edge.from !== sourceId || !edge.router || !(oldValue in (edge.branches || {}))) return edge;
      const branches = {};
      Object.entries(edge.branches).forEach(([key, val]) => {
        branches[key === oldValue ? newValue : key] = val;
      });
      return { ...edge, branches };
    }));
  }, []);

  // Restoring pushes the browsed run's revision straight into the live
  // draft AND persists it immediately (unlike a normal edit, which only
  // autosaves on its usual debounce) -- Restore is the one explicit way
  // out of read-only history browsing, so the moment it's confirmed the
  // canvas has to already be showing, and be editable against, that exact
  // content, not a dirty draft still catching up to it.
  const [restoreConfirmOpen, setRestoreConfirmOpen] = useState(false);
  // The Run button, and the Start cap when input_schema is declared, both
  // open this instead of the old always-docked Params tab -- review/edit
  // the routine's declared input_schema values right where the intent to
  // run was expressed, instead of a separately-discoverable side panel.
  // Local (not lifted to AgentsPage) since nothing outside this canvas
  // needs to know it's open, same as restoreConfirmOpen above.
  const [runModalOpen, setRunModalOpen] = useState(false);
  // The End cap's read-only mirror -- opens RoutineOutputModal to show the
  // declared output_schema resolved against the run currently in view
  // (live or browsed, same runData every other run-aware element here
  // already reads). No edit surface, so no browsing-history close effect
  // needed the way runModalOpen has one below.
  const [outputModalOpen, setOutputModalOpen] = useState(false);
  // Toggled by the validation status icon in the actions row -- shows the
  // full list of errors in a scrollable panel below the actions/history
  // pills. Only ever meaningful while invalid (see validationErrors below);
  // reset shut whenever the graph becomes valid again so it doesn't linger
  // open over an empty list once the last error is fixed.
  const [validationPanelOpen, setValidationPanelOpen] = useState(false);
  const handleRestoreRun = useCallback(async () => {
    const browsed = runNav.browsedGraph;
    if (!browsed) return;
    const nextMeta = {
      status: browsed.status || 'Draft',
      version: browsed.version || '2.0.0',
      summary: browsed.summary || '',
      entry: browsed.entry || (browsed.nodes || [])[0]?.id || null,
      settings: browsed.settings || {},
      input_schema: browsed.input_schema || null,
      output_schema: browsed.output_schema || null,
      $defs: browsed.$defs || {},
    };
    const nextNodes = (browsed.nodes || []).map((node) => ({ ...node }));
    const nextEdges = (browsed.edges || []).map((edge) => ({ ...edge }));
    setMeta(nextMeta);
    setNodes(nextNodes);
    setEdges(nextEdges);
    const payload = serializeGraph(nextNodes, nextEdges, nextMeta);
    await persistGraph(payload);
    setSaveError(null);
    setSaveState('saved');
    runNav.resetToLatest();
    setRestoreConfirmOpen(false);
  }, [runNav, persistGraph]);

  const displayNodes = useMemo(
    () => (runNav.isBrowsingHistory ? (runNav.browsedGraph?.nodes || []) : nodes),
    [runNav.isBrowsingHistory, runNav.browsedGraph, nodes],
  );
  const displayEdges = useMemo(
    () => (runNav.isBrowsingHistory ? (runNav.browsedGraph?.edges || []) : edges),
    [runNav.isBrowsingHistory, runNav.browsedGraph, edges],
  );
  const displayEntry = runNav.isBrowsingHistory ? (runNav.browsedGraph?.entry ?? null) : meta.entry;

  // Which node ids have at least one recorded execution this run, and --
  // derived from that -- which node(s) are probably executing right now
  // (see deriveActiveNodeIds). Recomputed off `runMeta` itself, not
  // `runStatus`, so the sub-headers keep updating on every checkpoint event while
  // `status` stays 'running'.
  const completedNodeIds = useMemo(
    () => new Set(Object.keys(runMeta || {}).filter((id) => (runMeta[id] || []).length > 0)),
    [runMeta],
  );
  const activeNodeIds = useMemo(
    () => (runStatus === 'running' || runStatus === 'discovering'
      ? deriveActiveNodeIds(displayNodes, displayEdges, displayEntry, completedNodeIds)
      : new Set()),
    [runStatus, displayNodes, displayEdges, displayEntry, completedNodeIds],
  );
  const currentGraphForStaleness = useMemo(
    () => ({ entry: meta.entry, nodes, edges }),
    [meta.entry, nodes, edges],
  );
  const rawStaleNodeIds = useRunStaleness(agent.id, runRevisionId, currentGraphForStaleness);
  const staleNodeIds = runNav.isBrowsingHistory ? EMPTY_MISSING : rawStaleNodeIds;
  // True only during the queued run discovery (finding this run's real
  // thread_id) -- the one window with no reliable timestamp to count from
  // at all. Once the thread turns up, `estimatedActiveStartMs` anchors on
  // that real moment instead of the run's click time (see
  // useRoutineRunEvents), so counting can start honestly even before the
  // entry node's own completion exists (see ChainCap/StepRunSummary).
  const warmingUp = !runNav.isBrowsingHistory && runStatus === 'discovering';
  // Drives the top-left Run button's icon (Play -> Stop) and disables it
  // for the run's full duration, not just discovery -- a second click
  // mid-run would mint a second concurrent session against the same
  // routine, and there's no cancel endpoint for an in-flight run to make
  // "Stop" anything but a status indicator.
  const isRunInProgress = !runNav.isBrowsingHistory && (runStatus === 'discovering' || runStatus === 'running');
  const loopMembership = useMemo(() => buildLoopMembership(displayEdges), [displayEdges]);
  const editContext = useMemo(() => ({
    updateNode,
    deleteNode,
    insertAt,
    onChangeStateKey,
    updateLoopGuard,
    onRemoveBranching: removeBranching,
    onAddBranch,
    onJoinBranches,
    selectNode,
    selectedNodeId,
    onAddDefault,
    onRemoveBranch,
    onRemoveDefault,
    onRenameBranch,
    settings: meta.settings,
    edges: displayEdges,
    nodes: displayNodes,
    inputSchema: meta.input_schema,
    outputSchema: meta.output_schema,
    setRunModalOpen,
    setOutputModalOpen,
    readOnly: runNav.isBrowsingHistory,
    runMeta,
    runData,
    activeNodeIds,
    estimatedActiveStartMs,
    staleNodeIds,
    runStatus,
    warmingUp,
    isRunInProgress,
    loopMembership,
    loopIterations,
    setLoopIteration,
    startRun: startRunAndTrackLatest,
    hasInteractiveSession,
    resetInteractiveSession,
    pendingApprovals: runNav.pendingApprovals,
    answerApproval: submitApproval,
  }), [updateNode, deleteNode, insertAt, onChangeStateKey, updateLoopGuard, removeBranching, onAddBranch, onJoinBranches, selectNode, selectedNodeId, onAddDefault, onRemoveBranch, onRemoveDefault, onRenameBranch, meta.settings, displayEdges, displayNodes, meta.input_schema, meta.output_schema, runNav.isBrowsingHistory, runNav.pendingApprovals, submitApproval, runMeta, runData, activeNodeIds, estimatedActiveStartMs, staleNodeIds, runStatus, warmingUp, isRunInProgress, loopMembership, loopIterations, setLoopIteration, startRunAndTrackLatest, hasInteractiveSession, resetInteractiveSession]);

  // Debounced autosave: serialize the working graph and PATCH it. The first
  // run records the baseline so simply opening the tab does not trigger a save.
  // A graph carrying a node with unfilled required config is withheld rather
  // than sent: the backend would reject it, and an author midway through
  // filling in a step they just inserted has not made a mistake worth a red
  // banner. `lastSavedRef` is deliberately left alone so the moment the last
  // field lands the signature still differs and the save goes out on its own.
  const incomplete = useMemo(
    () => incompleteNodes(nodes).length + incompleteLoopEdges(edges).length,
    [nodes, edges],
  );
  // Declared input_schema.required names with no (or blank) value filled in
  // yet -- gates the run-parameters modal's own Run button the same way
  // `incomplete` gates the canvas one for node config, so a routine that
  // needs parameters can't be run silently empty. A `boolean` field is
  // exempt: its checkbox has no third "unset" state to leave blank -- it
  // reads as false the instant it renders, same as `ParameterControl`'s own
  // `checked={value === true}` already treats it -- so demanding an explicit
  // click before Run unblocks would make an unticked required checkbox an
  // unsolvable state.
  const missingRequiredParams = useMemo(
    () => (meta.input_schema?.required || []).filter((name) => {
      if (meta.input_schema?.properties?.[name]?.type === 'boolean') return false;
      const value = parameterValues[name];
      return value === undefined || value === null || value === '';
    }),
    [meta.input_schema, parameterValues],
  );
  // Whether the toolbar Run button opens the parameters modal at all --
  // a routine with nothing declared just runs immediately, same as before.
  const hasDeclaredParams = Object.keys(meta.input_schema?.properties || {}).length > 0;
  useEffect(() => {
    const payload = serializeGraph(nodes, edges, meta);
    const signature = stableStringify(payload);
    if (lastSavedRef.current === null) {
      lastSavedRef.current = signature;
      return undefined;
    }
    if (signature === lastSavedRef.current) {
      setSaveError(null);
      setSaveState(saveStatusWhenInSync);
      return undefined;
    }
    if (incomplete) {
      setSaveError(null);
      setIncompleteCount(incomplete);
      setSaveState('incomplete');
      return undefined;
    }
    const timer = setTimeout(async () => {
      // An explicit JSON Apply may have persisted this exact payload while
      // this debounce was waiting. Re-check at fire time, because that save
      // may have finished and been removed from inFlightSavesRef already.
      if (signature === lastSavedRef.current) {
        setSaveError(null);
        setSaveState(saveStatusWhenInSync);
        return;
      }
      setSaveState('saving');
      try {
        await persistGraph(payload);
        setSaveError(null);
        setSaveState('saved');
      } catch (error) {
        setSaveError(error.message || 'Save failed');
        setSaveState('error');
      }
    }, 700);
    return () => clearTimeout(timer);
  }, [nodes, edges, meta, incomplete, persistGraph]);

  const workingGraph = useMemo(() => serializeGraph(nodes, edges, meta), [nodes, edges, meta]);
  // `nodes`/`edges`/`meta` above are seeded from `graph` only once, at
  // mount (React state initializers don't re-run on prop changes) -- so an
  // `agent.graph.updated` SSE event (progenitor writing /routines/<id>.json
  // directly, another tab saving, a revision restore) lands in Redux and
  // changes `confirmedGraph` under this already-mounted canvas, but nothing
  // adopts it: the JSON sidebar and RunParametersModal both read off this
  // local copy, so they'd stay stale until a full reload. Pull a
  // genuinely external change in here, but only when this tab has no
  // unsaved edit of its own in flight (`workingGraph` still matches
  // `lastSavedRef`) -- otherwise the user's in-progress edit would be
  // silently discarded. `adoptedSignatureRef` tracks the last confirmed
  // signature already reflected locally (by mount or a prior adoption) so
  // this doesn't refire for e.g. this tab's own save simply echoing back.
  const confirmedSignature = useMemo(
    () => stableStringify(serializeGraph(confirmedGraph?.nodes || [], confirmedGraph?.edges || [], confirmedGraph || {})),
    [confirmedGraph],
  );
  const adoptedSignatureRef = useRef(confirmedSignature);
  useEffect(() => {
    if (confirmedSignature === adoptedSignatureRef.current) return;
    adoptedSignatureRef.current = confirmedSignature;
    const localSignature = stableStringify(workingGraph);
    if (confirmedSignature === localSignature) return;
    if (localSignature !== lastSavedRef.current) return;
    const nextNodes = (confirmedGraph.nodes || []).map((node) => ({ ...node }));
    const nextEdges = (confirmedGraph.edges || []).map((edge) => ({ ...edge }));
    setNodes(nextNodes);
    setEdges(nextEdges);
    setMeta({
      status: confirmedGraph.status || 'Draft',
      version: confirmedGraph.version || '2.0.0',
      summary: confirmedGraph.summary || '',
      entry: confirmedGraph.entry || nextNodes[0]?.id || null,
      settings: confirmedGraph.settings || {},
      input_schema: confirmedGraph.input_schema || null,
      output_schema: confirmedGraph.output_schema || null,
      $defs: confirmedGraph.$defs || {},
    });
    confirmedNodeIdsRef.current = new Set(nextNodes.map((node) => node.id));
    lastSavedRef.current = confirmedSignature;
    if (!selectedNodeIdRef.current || !nextNodes.some((node) => node.id === selectedNodeIdRef.current)) {
      setSelectedNodeId(null);
      setInspectorPosition(null);
    }
  }, [confirmedSignature, confirmedGraph, workingGraph]);

  // Run must match what the server actually has, not just what this tab
  // optimistically believes it sent -- `confirmedGraph` is the live
  // Redux-tracked agent graph, updated only by the `agent.graph.updated`
  // SSE round-trip (see AgentsPage's `selectedGraph`), so comparing its
  // signature to the working copy's is the same "did the save round-trip
  // land" check the autosave chip already implies, made explicit for Run.
  const isServerConfirmed = useMemo(
    () => stableStringify(workingGraph)
      === stableStringify(serializeGraph(confirmedGraph?.nodes || [], confirmedGraph?.edges || [], confirmedGraph || {})),
    [workingGraph, confirmedGraph],
  );
  const selectedNode = nodes.find((node) => node.id === selectedNodeId) || null;
  const selectedRouterEdge = selectedNode
    ? edges.find((edge) => edge.from === selectedNode.id && edge.router)
    : null;
  // A loop's own guard expression lives on its tail's edge, not on this
  // node's config -- this is how the currently-selected node is recognized
  // as a loop's start: some edge's `loop_guard` targets it.
  const selectedGuardEdge = selectedNode
    ? edges.find((edge) => edge.loop_guard && edge.to === selectedNode.id)
    : null;
  const validationErrors = useMemo(
    () => validateWorkingGraph(nodes, edges, meta.entry, runtimeProfiles),
    [nodes, edges, meta.entry, runtimeProfiles],
  );
  useEffect(() => {
    if (!validationErrors.length || runNav.isBrowsingHistory) setValidationPanelOpen(false);
  }, [validationErrors.length, runNav.isBrowsingHistory]);

  const handleJsonApply = useCallback(async (parsed) => {
    const nextMeta = {
      status: parsed.status || 'Draft',
      version: parsed.version || '2.0.0',
      summary: parsed.summary || '',
      entry: parsed.entry || (parsed.nodes || [])[0]?.id || null,
      settings: parsed.settings || {},
      input_schema: parsed.input_schema || null,
      output_schema: parsed.output_schema || null,
      $defs: parsed.$defs || {},
    };
    const nextNodes = normalizeGraphNodes(parsed.nodes || []);
    setMeta(nextMeta);
    setNodes(nextNodes);
    setEdges((parsed.edges || []).map((edge) => ({ ...edge })));
    const payload = serializeGraph(nextNodes, parsed.edges || [], nextMeta);
    await persistGraph(payload);
    setSaveError(null);
    setSaveState('saved');
  }, [persistGraph]);

  return (
    <>
      <section className="agents-graph-panel" style={{ '--agent-color': agent.color }}>
        <div className="agents-graph-shell">
          <div ref={canvasRef} className="canvas-surface agents-graph-canvas routine-canvas">
            <div className="agents-graph-floating">
              <DetailHeaderActions menuLabel="Routine actions">
                {onShowProfile ? (
                  <DetailHeaderAction
                    icon={<OpenActionIcon />}
                    label="Details"
                    tooltip="View agent details"
                    ariaLabel="View agent details"
                    onClick={onShowProfile}
                  />
                ) : null}
                <DetailHeaderAction
                  icon={isRunInProgress ? <StopActionIcon /> : <PlayActionIcon />}
                  label={isRunInProgress ? 'Running' : 'Run'}
                  className={isRunInProgress ? 'agents-graph-run-action--pending' : ''}
                  tooltip={
                    isRunInProgress ? 'Running…'
                      : runNav.isBrowsingHistory ? 'Step back to the latest run (or Restore this one) before running'
                      : !isServerConfirmed ? 'Waiting for save to be confirmed…'
                      : hasDeclaredParams ? 'Set parameters and run'
                      : 'Run routine'
                  }
                  ariaLabel={isRunInProgress ? 'Running' : 'Run routine'}
                  onClick={() => { if (hasDeclaredParams) setRunModalOpen(true); else startRunAndTrackLatest(); }}
                  disabled={isRunInProgress || runNav.isBrowsingHistory || validationErrors.length > 0 || incomplete > 0 || !isServerConfirmed}
                />
                <DetailHeaderAction
                  icon={validationErrors.length ? <ValidationErrorIcon /> : <ValidationOkIcon />}
                  label={validationErrors.length ? 'Validation issues' : 'Valid graph'}
                  className={`agents-graph-validation-action ${validationErrors.length ? 'agents-graph-validation-action--error' : 'agents-graph-validation-action--ok'}`}
                  tooltip={
                    validationErrors.length
                      ? `${validationErrors.length} validation issue${validationErrors.length === 1 ? '' : 's'} \u2014 click to view`
                      : 'Graph is valid'
                  }
                  ariaLabel={validationErrors.length ? `${validationErrors.length} validation issues, click to view` : 'Graph is valid'}
                  aria-pressed={validationErrors.length ? validationPanelOpen : undefined}
                  onClick={validationErrors.length ? () => setValidationPanelOpen((open) => !open) : undefined}
                  disabled={!validationErrors.length || runNav.isBrowsingHistory}
                />
                <DetailHeaderAction
                  icon={<RestoreActionIcon />}
                  label="Restore"
                  tooltip={
                    !runNav.isBrowsingHistory ? 'Step back through a previous run to restore it'
                      : runNav.canRestore ? 'Restore this run\u2019s graph as the live draft'
                      : 'Loading this run\u2019s graph…'
                  }
                  ariaLabel="Restore this run's graph"
                  onClick={() => setRestoreConfirmOpen(true)}
                  disabled={!runNav.isBrowsingHistory || !runNav.canRestore}
                />
                {onDelete ? (
                  <DetailHeaderAction
                    icon={<DeleteActionIcon />}
                    label="Delete"
                    tooltip="Delete routine"
                    ariaLabel="Delete routine"
                    variant="danger"
                    onClick={onDelete}
                    disabled={runNav.isBrowsingHistory}
                  />
                ) : null}
              </DetailHeaderActions>
            </div>
            {runNav.hasRuns ? (
              <div className="agents-graph-floating agents-graph-floating--history">
                <DetailHeaderAction
                  icon={<ChevronLeftSmall />}
                  label="Older run"
                  tooltip="View the previous run"
                  ariaLabel="View the previous run"
                  onClick={runNav.goBack}
                  disabled={!runNav.canGoBack}
                />
                <span className="agents-graph-run-nav__label" title={runNav.title}>
                  {runNav.label}
                </span>
                <DetailHeaderAction
                  icon={<ChevronRightPointSmall />}
                  label="Newer run"
                  tooltip="View the next run"
                  ariaLabel="View the next run"
                  onClick={runNav.goForward}
                  disabled={!runNav.canGoForward}
                />
              </div>
            ) : null}
            {runNav.isBrowsingHistory || saveState === 'idle' ? null : (
              <div className="agents-graph-floating agents-graph-floating--status">
                <span className={`agents-graph-save agents-graph-save--${saveState}`} role="status">
                  {saveState === 'saving' ? 'Saving…'
                    : saveState === 'saved' ? 'Saved'
                    : saveState === 'incomplete' ? `Not saved · ${incompleteCount} step${incompleteCount === 1 ? '' : 's'} incomplete`
                        : 'Save failed'}
                </span>
              </div>
            )}
            {!runNav.isBrowsingHistory && validationPanelOpen && validationErrors.length > 0 ? (
              <div className="agents-graph-validation-panel" role="alert">
                <div className="agents-graph-validation-panel__header">
                  {validationErrors.length} validation issue{validationErrors.length === 1 ? '' : 's'}
                </div>
                <ul className="agents-graph-validation-panel__list">
                  {validationErrors.map((message, index) => (
                    <li key={index}>{message}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            <div className="agents-graph-zoom-controls">
              <button
                type="button"
                className="agents-graph-zoom-controls__btn"
                onClick={zoomIn}
                disabled={zoom >= ZOOM_MAX}
                aria-label="Zoom in"
              >
                <ZoomPlusIcon />
              </button>
              <button
                type="button"
                className="agents-graph-zoom-controls__btn"
                onClick={zoomOut}
                disabled={zoom <= ZOOM_MIN}
                aria-label="Zoom out"
              >
                <ZoomMinusIcon />
              </button>
              <button
                type="button"
                className="agents-graph-zoom-controls__btn"
                onClick={resetZoom}
                aria-label="Reset zoom"
              >
                <ZoomFitIcon />
              </button>
            </div>
              <GraphEditContext.Provider value={editContext}>
              <div
                className="routine-canvas__scroll"
                style={{ zoom }}
                onClick={(event) => {
                  // A locked inspector still ignores the background: an
                  // accidental outside click discarding an in-progress edit
                  // (or deleting a brand-new step) would be a nasty surprise
                  // -- that only happens via the explicit × or Delete step.
                  if (selectedNode && !isNodeComplete(selectedNode)) return;
                  if (!event.target.closest('button, input, textarea, [role="button"], .routine-node-inspector')) {
                    closeInspector(selectedNode);
                  }
                }}
              >
                <RoutineChain nodes={displayNodes} edges={displayEdges} entry={displayEntry} />
              </div>
              {selectedNode && inspectorPosition ? (
                <NodeInspector
                  node={selectedNode}
                  routerEdge={selectedRouterEdge}
                  guardEdge={selectedGuardEdge}
                  onClose={() => closeInspector(selectedNode)}
                  onDismiss={() => { setSelectedNodeId(null); setInspectorPosition(null); }}
                  isNew={selectedNodeIsNew}
                  style={inspectorPosition}
                  panelRef={panelRef}
                />
              ) : null}
            </GraphEditContext.Provider>
            {/* Mirrors the toolbar Run button's own disabled conditions (see
                its `disabled` prop above) -- the modal is now reachable from
                a second, otherwise-ungated entry point (the Start cap), so
                its own Run button needs the same guard independently. */}
            {runModalOpen ? (
              <RunParametersModal
                inputSchema={meta.input_schema}
                values={parameterValues}
                onChange={setParameterValues}
                missingRequiredParams={missingRequiredParams}
                blockedReason={
                  isRunInProgress ? 'A run is already in progress'
                    : runNav.isBrowsingHistory ? 'Step back to the latest run (or Restore this one) before running'
                    : validationErrors.length > 0 ? 'Fix validation issues before running'
                    : incomplete > 0 ? 'Finish incomplete steps before running'
                    : !isServerConfirmed ? 'Waiting for save to be confirmed…'
                    : null
                }
                onClose={() => setRunModalOpen(false)}
                onConfirm={() => { setRunModalOpen(false); startRunAndTrackLatest(); }}
              />
            ) : null}
            {outputModalOpen ? (
              <RoutineOutputModal
                outputSchema={meta.output_schema}
                runData={runData}
                onClose={() => setOutputModalOpen(false)}
              />
            ) : null}
          </div>
          {saveError ? <div className="agents-graph-save-error">{saveError}</div> : null}
        </div>
      </section>
      {jsonOpen ? (
        <RoutineJsonEditor
          key={agent.id}
          graph={workingGraph}
          onSave={handleJsonApply}
          onClose={onCloseJson}
        />
      ) : runsOpen ? (
        <RoutineRunsPanel
          key={agent.id}
          agentId={agent.id}
          onClose={onCloseRuns}
          runsList={runNav.runsList}
          viewedIndex={runNav.viewedIndex}
          onSelectIndex={runNav.jumpTo}
        />
      ) : (
        <GraphSideDock
          disabledIds={runNav.isBrowsingHistory ? EDIT_SIDE_ITEM_IDS : EMPTY_MISSING}
          onSelect={(id) => { if (id === 'json') onOpenJson(); if (id === 'runs') onOpenRuns(); }}
        />
      )}
      {restoreConfirmOpen ? (
        <PopupModal
          eyebrow="Restore run"
          title={`Restore the run from ${runNav.label || 'this position'}?`}
          message="This replaces your current draft with the graph this run actually executed against, and saves it immediately as the new live revision. Any unsaved changes to the current draft will be lost."
          confirmLabel="Restore"
          onClose={() => setRestoreConfirmOpen(false)}
          onConfirm={handleRestoreRun}
        />
      ) : null}
    </>
  );
}

// Plain checkmark -- the actions-row status icon between Run and Restore
// when the working graph passes validateWorkingGraph with zero issues.
// Always rendered (never conditionally omitted) so the actions row never
// reflows width when validity flips; disabled/non-interactive in this
// state since there's nothing to expand.
function ValidationOkIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path d="M3.5 8.4 6.3 11.2 12.5 4.6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// Warning triangle -- shown in the same actions-row slot as ValidationOkIcon
// above when validateWorkingGraph reports issues. Clicking it toggles the
// scrollable agents-graph-validation-panel listing every message.
function ValidationErrorIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path d="M8 2.4 14.2 13a1 1 0 0 1-.86 1.5H2.66A1 1 0 0 1 1.8 13L8 2.4z" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
      <path d="M8 6.6v3.1" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <circle cx="8" cy="11.6" r="0.9" fill="currentColor" stroke="none" />
    </svg>
  );
}

// A clock face with a counter-clockwise rewind arrow -- "restore to an
// earlier point in time", not ReconnectActionIcon's plain circular refresh
// loop (that's reconnect/retry, a different concept) and not a download/
// tray glyph either (that reads as "bring a file in", not "wind the clock
// back"). Always rendered in the toolbar (see the Restore button), just
// disabled outside history browsing, so its width never toggles in and out
// and shifts the rest of the toolbar/side dock around.
function RestoreActionIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path d="M4 4.6A5.2 5.2 0 1 1 2.9 9.6" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M4.6 2.4 4 4.6 6.3 5.3" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M8 6v2.6l1.8 1" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// Plain +/-/corner-brackets, matching Workflows' React Flow Controls
// look for the same concept -- not a canvas-viewport zoom here though
// (this isn't React Flow): see the `zoom` state comment in GraphCanvas.
function ZoomPlusIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path d="M8 3v10M3 8h10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function ZoomMinusIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path d="M3 8h10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

// Four open corner brackets -- "reset/fit" (React Flow's fitView glyph);
// here it resets the CSS zoom level back to 100% rather than fitting a
// canvas viewport to content, since there is no viewport to fit.
function ZoomFitIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path d="M2.5 5.5V3.5a1 1 0 0 1 1-1h2" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13.5 5.5V3.5a1 1 0 0 0-1-1h-2" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M2.5 10.5v2a1 1 0 0 0 1 1h2" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13.5 10.5v2a1 1 0 0 1-1 1h-2" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const GRAPH_SIDE_ITEMS = [
  { id: 'json', label: 'JSON', title: 'Edit as JSON' },
  { id: 'runs', label: 'Runs', title: 'Routine run history' },
];

// Locked while browsing history (see GraphSideDock's disabledIds): editing
// surfaces only. 'runs' stays enabled -- it's pure navigation, the same
// category as the toolbar's own back/forward pill, not an edit surface.
const EDIT_SIDE_ITEM_IDS = new Set(['json']);

function GraphSideDock({ onSelect, disabledIds }) {
  return (
    <aside className="agents-graph-side" aria-label="Graph tools">
      {GRAPH_SIDE_ITEMS.map((item) => {
        const disabled = disabledIds?.has(item.id) ?? false;
        return (
          <button
            key={item.id}
            type="button"
            className="agents-graph-side__item"
            title={disabled ? 'Step back to the latest run (or Restore this one) to edit' : item.title}
            aria-label={item.title}
            onClick={() => onSelect(item.id)}
            disabled={disabled}
          >
            <ChevronLeftSmall />
            <span className="agents-graph-side__label">{item.label}</span>
          </button>
        );
      })}
    </aside>
  );
}

export function RoutineGraphEditor({ agent, graph, confirmedGraph, onPersist, onShowProfile, onDelete, jsonOpen, onOpenJson, onCloseJson, runsOpen, onOpenRuns, onCloseRuns }) {
  return (
    <GraphCanvas
      agent={agent}
      graph={graph}
      confirmedGraph={confirmedGraph}
      onPersist={onPersist}
      onShowProfile={onShowProfile}
      onDelete={onDelete}
      jsonOpen={jsonOpen}
      onOpenJson={onOpenJson}
      onCloseJson={onCloseJson}
      runsOpen={runsOpen}
      onOpenRuns={onOpenRuns}
      onCloseRuns={onCloseRuns}
    />
  );
}
function graphToJson(graph) {
  return JSON.stringify(
    {
      status: graph.status || 'Draft',
      version: graph.version || '2.0.0',
      summary: graph.summary || '',
      entry: graph.entry ?? null,
      nodes: graph.nodes || [],
      edges: graph.edges || [],
      settings: graph.settings || {},
      input_schema: graph.input_schema || null,
      output_schema: graph.output_schema || null,
      $defs: graph.$defs || {},
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
// canvas, whose `workingGraph` this panel's `graph` prop already tracks --
// keyed by agent id so switching agents remounts and resets the draft; the
// effect below additionally resyncs a still-mounted draft when `graph`
// changes out from under it (e.g. an external write) with nothing unsaved.
function RoutineJsonEditor({ graph, onSave, onClose }) {
  const initialJson = useMemo(() => graphToJson(graph), [graph]);
  const [draft, setDraft] = useState(initialJson);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const lastAdoptedJsonRef = useRef(initialJson);
  // Same "only pull in an external change if there's nothing unsaved
  // locally" rule as GraphCanvas's own graph-prop resync (see the comment
  // above its `confirmedSignature` effect) -- `graph` here is that
  // canvas's `workingGraph`, so this panel needs its own copy of the rule:
  // a user mid-edit in this textarea should never have their draft
  // silently overwritten by an unrelated external update arriving while
  // the panel happens to be open.
  useEffect(() => {
    if (initialJson === lastAdoptedJsonRef.current) return;
    const previousAdopted = lastAdoptedJsonRef.current;
    lastAdoptedJsonRef.current = initialJson;
    if (draft !== previousAdopted) return;
    setDraft(initialJson);
    setError(null);
    setSaved(false);
  }, [initialJson, draft]);

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
        <button type="button" className="repo-btn repo-btn--ghost" onClick={() => { setDraft(initialJson); setError(null); setSaved(false); }}>Reset</button>
        <button type="button" className="repo-btn" onClick={handleApply} disabled={saving}>
          {saving ? 'Saving…' : 'Apply changes'}
        </button>
      </div>
    </aside>
  );
}

// Values the user fills in for the routine's declared input_schema.properties
// before hitting Run -- session-local (GraphCanvas's parameterValues state),
// never persisted to the graph document. The schema itself (properties/
// required) is authored the same way node-level response_schema is: via the
// JSON editor, not a bespoke properties-builder UI here -- this panel only
// renders what's already declared and collects values for it.
function parameterControlValue(value) {
  return value === undefined || value === null ? '' : value;
}

// array/object-typed properties (e.g. a foreach node's items_from source)
// need a real parsed value, not a string -- the seed node copies whatever
// JS value this sends straight into state.data, and the foreach/eval nodes
// that read it expect an actual list/dict. Buffers its own text so invalid
// JSON mid-edit doesn't clobber the last-valid parsed value (same lesson as
// useBufferedFieldText elsewhere in this file, kept local since this is the
// only text<->JSON round trip in the panel).
function JsonParamControl({ name, value, onChange }) {
  const [text, setText] = useState(() => (value === undefined ? '' : JSON.stringify(value, null, 2)));
  const [error, setError] = useState(null);
  return (
    <>
      <textarea
        className="agents-graph-parameters__json"
        aria-label={name}
        rows={4}
        placeholder='e.g. ["a", "b"]'
        value={text}
        onChange={(event) => {
          const nextText = event.target.value;
          setText(nextText);
          if (nextText.trim() === '') {
            setError(null);
            onChange(undefined);
            return;
          }
          try {
            const parsed = JSON.parse(nextText);
            setError(null);
            onChange(parsed);
          } catch {
            setError('Invalid JSON -- last valid value is still what Run will send.');
          }
        }}
      />
      {error ? <p className="agents-graph-editor__error">{error}</p> : null}
    </>
  );
}

function ParameterControl({ name, def, value, onChange }) {
  if (Array.isArray(def?.enum) && def.enum.length) {
    return (
      <Select
        value={value === undefined ? '' : String(value)}
        onChange={(next) => onChange(next === '' ? undefined : next)}
        options={def.enum.map((option) => ({ value: String(option), label: String(option) }))}
        placeholder="Select…"
        ariaLabel={name}
        className="agents-graph-parameters__input"
      />
    );
  }
  if (def?.type === 'array' || def?.type === 'object') {
    return <JsonParamControl name={name} value={value} onChange={onChange} />;
  }
  if (def?.type === 'boolean') {
    return (
      <button
        type="button"
        role="switch"
        aria-checked={value === true}
        aria-label={name}
        className={`tools-toggle ${value === true ? 'is-on' : 'is-off'}`}
        onClick={() => onChange(value !== true)}
      >
        <span />
      </button>
    );
  }
  if (def?.type === 'number' || def?.type === 'integer') {
    return (
      <input
        type="number"
        className="agents-graph-parameters__input"
        value={parameterControlValue(value)}
        step={def.type === 'integer' ? 1 : 'any'}
        onChange={(event) => onChange(event.target.value === '' ? undefined : Number(event.target.value))}
      />
    );
  }
  return (
    <input
      type="text"
      className="agents-graph-parameters__input"
      value={parameterControlValue(value)}
      placeholder={def?.type ? `(${def.type})` : undefined}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

// Every recorded execution of this routine, each row showing what actually
// happened -- success/failed (from that run's own run_meta, the same
// per-node duration/tokens/error data the canvas cards and NodeInspector
// read), wall-clock duration (from the run summary's own started_at/
// last_checkpoint_at -- no extra fetch needed for that part), total tokens,
// and when it ran. `viewedIndex`/`onSelectIndex` are owned by GraphCanvas's
// run-nav (the same state the toolbar's back/forward pill drives), so
// clicking a row here, clicking back/forward, and this list's highlighted
// row all stay in lockstep -- one selection, three ways to move it.
function RoutineRunsPanel({ agentId, onClose, runsList, viewedIndex, onSelectIndex }) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const agentsUrl = getApiUrl('agentProfiles');
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);
  const hasMore = useSelector((state) => selectAgentRoutineRunsHasMore(state, agentId));

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    dispatch(fetchRoutineRuns({ agentsUrl, agentId }))
      .unwrap()
      .catch((fetchError) => setError(fetchError.message || 'Failed to load runs'))
      .finally(() => setLoading(false));
  }, [agentId, agentsUrl, dispatch]);

  useEffect(() => {
    if (!runsList) load();
    // Only auto-load the first time this agent's runs are uncached; a
    // manual refresh re-fetches explicitly via `load`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  // Pages backward past the oldest run currently loaded, via the cursor
  // the backend's own `last_checkpoint_at` ordering already gives us --
  // see document_store.list_routine_runs. Appends into the same
  // runsByAgentId cache the initial `load()` populates (see the
  // `agent.routine.runs.loaded` reducer), so this list only ever grows,
  // never re-fetches a page it already has.
  const loadMore = useCallback(() => {
    const oldest = runsList?.[runsList.length - 1];
    if (!oldest?.last_checkpoint_at || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    dispatch(fetchRoutineRuns({ agentsUrl, agentId, before: oldest.last_checkpoint_at }))
      .unwrap()
      .catch((fetchError) => setError(fetchError.message || 'Failed to load more runs'))
      .finally(() => setLoadingMore(false));
  }, [agentId, agentsUrl, dispatch, loadingMore, runsList]);

  // Status/duration/tokens need each run's own run_meta, not just the list
  // summary. Deliberately NOT fetched eagerly for the whole list -- opening
  // this panel used to fire one concurrent /run-state request (each opening
  // its own short-lived Postgres checkpoint connection) per run, all at
  // once. Instead this rides useRunHistoryNav's existing `historicalThreadId`
  // effect: selecting a row (via `onSelectIndex` below, or the toolbar's
  // back/forward pill, both of which move the same `viewedIndex`) is what
  // triggers that one fetch, into the same runStateByThreadId cache read
  // here -- a run already browsed to needs no second fetch, and clicking a
  // row this panel hasn't resolved yet needs none added here either.
  const runStateByThreadId = useSelector((state) => state.uiData.agents.runStateByThreadId);

  const virtualItems = useMemo(() => {
    if (!runsList) return [];
    return hasMore ? [...runsList, { loadMoreRow: true }] : runsList;
  }, [runsList, hasMore]);

  return (
    <aside className="agents-graph-editor agents-graph-runs">
      <button
        type="button"
        className="agents-graph-editor__collapse"
        onClick={onClose}
        title="Collapse"
        aria-label="Collapse run history"
      >
        <ChevronRightSmall />
      </button>
      <header className="agents-graph-editor__head">
        <h2>Runs</h2>
        <button type="button" className="repo-btn repo-btn--ghost" onClick={load} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </header>
      {error ? <p className="agents-graph-editor__error">{error}</p> : null}
      {!error && runsList && runsList.length === 0 && !loading ? (
        <p className="agents-graph-runs__empty">No runs recorded yet.</p>
      ) : null}
      {runsList ? (
        <PaneListRows
          className="agents-graph-runs__list"
          items={virtualItems}
          getItemKey={(item) => item.thread_id || 'load-more'}
          renderItem={(item, index) => {
            if (item.loadMoreRow) {
              return (
                <button
                  type="button"
                  className="agents-graph-runs__load-more"
                  onClick={loadMore}
                  disabled={loadingMore}
                >
                  {loadingMore ? 'Loading…' : 'Load more'}
                </button>
              );
            }
            const run = item;
            // Status is free -- `run.failed` comes straight off the list
            // query (a cheap EXISTS against LangGraph's own '__error__'
            // pending-write channel, see document_store.list_routine_runs).
            // Tokens still need the fuller run_meta fetch, which only
            // happens once this row is actually selected (see the comment
            // above runStateByThreadId) -- so tokens stay optional here,
            // status never does.
            const runState = runStateByThreadId[run.thread_id];
            const entries = runState?.run_meta ? Object.values(runState.run_meta).flat() : null;
            const tokens = entries?.reduce((sum, entry) => sum + (entry.tokens?.total_tokens || 0), 0);
            const status = run.failed ? 'failed' : 'success';
            const durationSec = run.started_at && run.last_checkpoint_at
              ? Math.max(0, (new Date(run.last_checkpoint_at) - new Date(run.started_at)) / 1000)
              : null;
            const datetime = run.last_checkpoint_at ? new Date(run.last_checkpoint_at).toLocaleString() : null;
            const isSelected = index === viewedIndex;
            return (
              <button
                type="button"
                className={`agents-graph-runs__item${isSelected ? ' is-selected' : ''}`}
                onClick={() => onSelectIndex(index)}
              >
                <div className="agents-graph-runs__item-head">
                  <span className={`agents-graph-runs__status agents-graph-runs__status--${status}`}>
                    {status}
                  </span>
                  <span className="agents-graph-runs__age">{formatAge(run.last_checkpoint_at) || '—'}</span>
                </div>
                {datetime ? <div className="agents-graph-runs__datetime">{datetime}</div> : null}
                <div className="agents-graph-runs__item-meta">
                  {durationSec !== null ? <span>{durationSec.toFixed(1)}s</span> : null}
                  {typeof tokens === 'number' ? <span>{tokens} tok</span> : null}
                  <span>{run.checkpoint_count} step{run.checkpoint_count === 1 ? '' : 's'}</span>
                </div>
              </button>
            );
          }}
        />
      ) : null}
    </aside>
  );
}

// Values the user fills in for the routine's declared input_schema.properties
// right at the moment they hit Run -- session-local (GraphCanvas's
// parameterValues state), never persisted to the graph document. The
// schema itself (properties/required) is authored the same way node-level
// response_schema is: via the JSON editor, not a bespoke properties-builder
// UI here -- this modal only renders what's already declared and collects
// values for it. Also reachable via the Start cap (ChainCap), independent
// of the toolbar Run button's own disabled state -- `blockedReason` mirrors
// that button's gating (isRunInProgress/isBrowsingHistory/validation/
// isServerConfirmed) so Run-inside-the-modal can't bypass it from that
// second entry point. Deliberately NOT a portal to document.body (see
// PopupModal) -- rendered as a plain child of .canvas-surface (already
// position:relative, already the exact bounds of the routine's own content
// area) so the backdrop only ever covers the graph, never the app chrome
// either side of it.
function RunParametersModal({ inputSchema, values, onChange, missingRequiredParams, blockedReason, onClose, onConfirm }) {
  const properties = inputSchema?.properties || {};
  const required = new Set(inputSchema?.required || []);
  const names = Object.keys(properties);
  const confirmRef = useRef(null);
  const openerRef = useRef(document.activeElement);
  const missing = new Set(missingRequiredParams);

  useEffect(() => {
    const opener = openerRef.current;
    confirmRef.current?.focus();
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      if (opener?.isConnected) opener.focus();
    };
  }, [onClose]);

  const setValue = (name, raw) => {
    onChange((current) => {
      if (raw === undefined || raw === '') {
        if (!(name in (current || {}))) return current;
        const next = { ...current };
        delete next[name];
        return next;
      }
      return { ...current, [name]: raw };
    });
  };

  return (
    <div className="routine-run-modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="routine-run-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="routine-run-modal-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="sidebar-modal__head">
          <div>
            <span className="sidebar-modal__eyebrow">Run routine</span>
            <h3 id="routine-run-modal-title">Set parameters</h3>
          </div>
          <button type="button" className="sidebar-modal__close" aria-label="Close" onClick={onClose}>×</button>
        </header>
        <div className="routine-run-modal__body agents-graph-parameters__list">
          {names.length === 0 ? (
            <p className="agents-graph-parameters__empty">
              This routine has no declared input_schema. Add one via the JSON editor -- a JSON Schema object with `properties` (and optionally `required`) -- to collect values here before Run.
            </p>
          ) : names.map((name) => {
            const def = properties[name] || {};
            const isRequired = required.has(name);
            return (
              <label key={name} className={`agents-graph-node__field${isRequired ? ' agents-graph-node__field--required' : ''}`}>
                <span className="agents-graph-node__field-label">
                  {name}
                  {isRequired ? <span className="agents-graph-node__required" title="Required to run this routine">Required</span> : null}
                </span>
                {def.description ? <span className="agents-graph-parameters__hint">{def.description}</span> : null}
                <ParameterControl name={name} def={def} value={values?.[name]} onChange={(next) => setValue(name, next)} />
              </label>
            );
          })}
        </div>
        <footer className="sidebar-modal__foot">
          <button type="button" className="task-btn task-btn--secondary" onClick={onClose}>Cancel</button>
          <button
            ref={confirmRef}
            type="button"
            className="task-btn task-btn--primary"
            title={missing.size === 0 ? blockedReason || undefined : undefined}
            onClick={onConfirm}
            disabled={missing.size > 0 || Boolean(blockedReason)}
          >
            Run
          </button>
        </footer>
      </section>
    </div>
  );
}

// End cap's read-only mirror of RunParametersModal -- the declared
// output_schema resolved against the run currently in view (live or
// browsed via runNav, same runData every other run-aware element on this
// canvas already reads), not editable, no Run action: this is "what came
// out", not "what to submit". Reuses the same
// .routine-run-modal-backdrop/.routine-run-modal shell (and the
// .routine-node-inspector__run-output pre styling NodeInspector's own
// per-node output already uses) rather than inventing a second modal look.
// output_schema is now the same real-JSON-Schema shape input_schema is
// (``{type: "object", properties: {...}, required: [...]}`` -- see
// AgentGraphModel.output_schema, dict[str, Any] server-side, and
// factory.py's _schema_field_names, shared by both sides) -- reads
// .properties/.required/.description the same way RunParametersModal
// does, just read-only. Deliberately NOT a runData fallback when
// output_schema is absent -- the routine's own data channel carries every
// node's intermediate write, most of which is not "the result"; showing
// all of it isn't clearer, it's noise. Absent schema means an explicit
// "not declared" message instead.
function RoutineOutputModal({ outputSchema, runData, onClose }) {
  const properties = outputSchema?.properties || {};
  const required = new Set(outputSchema?.required || []);
  const names = Object.keys(properties);
  const closeRef = useRef(null);
  const openerRef = useRef(document.activeElement);

  useEffect(() => {
    const opener = openerRef.current;
    closeRef.current?.focus();
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      if (opener?.isConnected) opener.focus();
    };
  }, [onClose]);

  return (
    <div className="routine-run-modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="routine-run-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="routine-output-modal-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="sidebar-modal__head">
          <div>
            <span className="sidebar-modal__eyebrow">Routine result</span>
            <h3 id="routine-output-modal-title">Output</h3>
          </div>
          <button type="button" className="sidebar-modal__close" aria-label="Close" onClick={onClose}>×</button>
        </header>
        <div className="routine-run-modal__body agents-graph-parameters__list">
          {names.length === 0 ? (
            <p className="agents-graph-parameters__empty">
              This routine has no declared output_schema. Add one via the JSON editor -- a JSON Schema object with `properties` (and optionally `required`) -- to see its result here.
            </p>
          ) : names.map((name) => {
            const def = properties[name] || {};
            const isRequired = required.has(name);
            const value = runData?.[name];
            const hasValue = value !== undefined;
            const text = hasValue ? (typeof value === 'string' ? value : JSON.stringify(value, null, 2)) : null;
            return (
              <div key={name} className={`agents-graph-node__field${isRequired ? ' agents-graph-node__field--required' : ''}`}>
                <span className="agents-graph-node__field-label">
                  {name}
                  {isRequired ? <span className="agents-graph-node__required" title="Always set by this routine">Required</span> : null}
                </span>
                {def.description ? <span className="agents-graph-parameters__hint">{def.description}</span> : null}
                {hasValue ? (
                  <div className="routine-node-inspector__run-output">
                    <pre>{text}</pre>
                  </div>
                ) : (
                  <p className="agents-graph-parameters__hint">Not produced yet -- run this routine to see this value.</p>
                )}
              </div>
            );
          })}
        </div>
        <footer className="sidebar-modal__foot">
          <button ref={closeRef} type="button" className="task-btn task-btn--secondary" onClick={onClose}>Close</button>
        </footer>
      </section>
    </div>
  );
}
