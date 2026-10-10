import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { getTimeBucket } from '../components/chat/conversationModel';
import PopupModal from '../components/common/PopupModal';
import { useConfig } from '../context/ConfigContext';
import { selectRealtimeConnected, selectRealtimeStreamReady } from '../store/realtimeSlice';
import {
  fetchAgentProfiles,
  requestAgentDetail,
  selectActiveAgentProfile,
  selectActiveAgentProfileState,
  selectAgentProfiles,
  selectAgentProfilesState,
  updateAgentGraph,
  deleteAgentProfile,
  selectRoutineLatestRunStatusByAgentId,
} from '../store/uiDataSlice';
import {
  PaneEmptyState,
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListRows,
} from '../components/layout/PaneList';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import { RoutineGraphEditor } from '../components/agents/RoutineGraphEditor';
import { formatAge } from '../components/agents/agentFormat';

const ROUTINE_FILTERS = ['All', 'Running', 'Paused', 'Failed'];

// Every row is a routine, so there is no Type sort.
const SORT_MODES = ['Name', 'Recent'];

const RECENCY_BUCKETS = [
  ['today', 'Today'],
  ['yesterday', 'Yesterday'],
  ['this-week', 'This Week'],
  ['last-week', 'Last Week'],
  ['later', 'Later'],
];

const FALLBACK_GRAPH = {
  status: 'Draft',
  summary: '',
  entry: null,
  nodes: [],
  edges: [],
};

function updatedTime(agent) {
  const ms = new Date(agent.updated || 0).getTime();
  return Number.isNaN(ms) ? 0 : ms;
}

function sortRoutines(routines, mode) {
  const byName = (a, b) => (a.name || '').localeCompare(b.name || '');
  const comparators = {
    Name: byName,
    Recent: (a, b) => updatedTime(b) - updatedTime(a) || byName(a, b),
  };
  return [...routines].sort(comparators[mode] || comparators.Name);
}

function matchesRoutine(routine, filter, search, runStatus) {
  const matchesFilter = filter === 'All'
    || (filter === 'Running' && runStatus === 'Running')
    || (filter === 'Paused' && runStatus === 'Paused')
    || (filter === 'Failed' && runStatus === 'Failed');
  if (!matchesFilter) {
    return false;
  }
  if (!search) {
    return true;
  }
  const haystack = [
    routine.name,
    routine.handle,
    routine.model,
    routine.role,
    routine.summary,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return haystack.includes(search.toLowerCase());
}

// Routines card list: a two-line card matching the Chat/Workflows pattern —
// a leading status dot (empty unless running/failed/paused), name + last-activity
// on the header line, and the agent's role as a single preview line below.
function RoutineRow({ routine, selected, onSelect, runStatus }) {
  const dotTone = runStatus === 'Running' ? 'info' : runStatus === 'Failed' ? 'error' : runStatus === 'Paused' ? 'paused' : '';
  return (
    <button
      type="button"
      className={`pane-card-row ${selected ? 'active' : ''}`}
      onClick={() => onSelect(routine.id)}
    >
      <span
        className={`pane-card-row__dot${dotTone ? ` pane-card-row__dot--${dotTone}` : ''}`}
        aria-hidden="true"
      />
      <div className="pane-card-row__body">
        <div className="pane-card-row__top">
          <span className="pane-card-row__title">{routine.name}</span>
          <span className="pane-card-row__age">{formatAge(routine.updated)}</span>
        </div>
        <div className="pane-card-row__preview">{routine.role || routine.summary || 'No description yet.'}</div>
      </div>
    </button>
  );
}

export default function RoutinesPage() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const realtimeConnected = useSelector(selectRealtimeConnected);
  const fetchedAgents = useSelector(selectAgentProfiles);
  const { agentId: routeAgentId } = useParams();
  const routines = useMemo(
    () => fetchedAgents.filter((agent) => agent.kind === 'routine'),
    [fetchedAgents],
  );
  const agentsState = useSelector(selectAgentProfilesState);
  const activeAgent = useSelector(selectActiveAgentProfile);
  const activeAgentState = useSelector(selectActiveAgentProfileState);
  const streamReady = useSelector(selectRealtimeStreamReady);
  const runStatusById = useSelector(selectRoutineLatestRunStatusByAgentId);

  const navigate = useNavigate();

  const [activeFilter, setActiveFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [sortMode, setSortMode] = useState('Name');
  const [graphEditorOpen, setGraphEditorOpen] = useState(false);
  const [routineRunsOpen, setRoutineRunsOpen] = useState(false);
  const [deleteConfirmationOpen, setDeleteConfirmationOpen] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const agentsUrl = getApiUrl('agentProfiles');

  const handleNewAgent = useCallback(() => {
    window.dispatchEvent(new CustomEvent('progenitor:open-quick-chat', {
      detail: { mode: 'routine-create' },
    }));
  }, []);

  // Selection is derived from the URL (/routines/<id>) so the view is
  // refreshable and shareable; user actions navigate rather than setState.
  const selectedAgentId = routeAgentId ?? null;

  const requestedListRef = useRef(null);

  // Load profiles once the realtime stream is connected. The GET acknowledges
  // the request and the snapshot arrives on the stream.
  useEffect(() => {
    if (!realtimeConnected) {
      requestedListRef.current = null;
      return;
    }
    if (requestedListRef.current === agentsUrl) {
      return;
    }
    requestedListRef.current = agentsUrl;
    dispatch(fetchAgentProfiles({ agentsUrl }));
  }, [agentsUrl, dispatch, realtimeConnected]);

  // Selection defaults to the first routine without writing state in an effect.
  const effectiveAgentId = selectedAgentId ?? routines[0]?.id ?? null;

  const selectAgent = useCallback((id) => {
    navigate(`/routines/${encodeURIComponent(id)}`);
  }, [navigate]);

  const handleDeleteAgent = useCallback(() => {
    if (effectiveAgentId) {
      setDeleteError('');
      setDeleteConfirmationOpen(true);
    }
  }, [effectiveAgentId]);

  const confirmDeleteAgent = useCallback(async () => {
    if (!effectiveAgentId) {
      setDeleteConfirmationOpen(false);
      return;
    }
    setDeleteBusy(true);
    try {
      await dispatch(deleteAgentProfile({ agentsUrl, agentId: effectiveAgentId })).unwrap();
      setDeleteConfirmationOpen(false);
      navigate('/routines', { replace: true });
    } catch (error) {
      setDeleteError(error.message || 'Failed to delete.');
    } finally {
      setDeleteBusy(false);
    }
  }, [agentsUrl, dispatch, effectiveAgentId, navigate]);

  // The detail only ever comes back as a one-shot `agent.selected` broadcast,
  // and the backend replays nothing to a subscriber it did not have at publish
  // time (replay needs a Last-Event-ID, which a first load has none of). Ask
  // before the stream is ready and the answer is published into the void: the
  // graph pane then waits on `graphDetailPending` forever, since nothing
  // retries. Wait for the subscriber to exist, then ask.
  useEffect(() => {
    if (!effectiveAgentId || !streamReady) {
      return;
    }
    if (activeAgent?.id === effectiveAgentId) {
      return;
    }
    dispatch(requestAgentDetail({ agentsUrl, agentId: effectiveAgentId }));
  }, [activeAgent?.id, agentsUrl, dispatch, effectiveAgentId, streamReady]);

  const listAgent = useMemo(
    () => routines.find((agent) => agent.id === effectiveAgentId) || null,
    [routines, effectiveAgentId],
  );
  const selectedAgent = activeAgent && activeAgent.id === effectiveAgentId ? activeAgent : listAgent;
  const selectedGraph = selectedAgent?.graph || FALLBACK_GRAPH;
  const graphDetailPending = !activeAgentState.error
    && (!activeAgent || activeAgent.id !== effectiveAgentId);

  // Reflect the active routine in the URL so the view is refreshable and shareable.
  useEffect(() => {
    if (!effectiveAgentId) return;
    const target = `/routines/${encodeURIComponent(effectiveAgentId)}`;
    if (`${window.location.pathname}` !== target) {
      navigate(target, { replace: true });
    }
  }, [effectiveAgentId, navigate]);

  const visibleRoutines = useMemo(
    () => sortRoutines(
      routines.filter((routine) => matchesRoutine(routine, activeFilter, search, runStatusById[routine.id])),
      sortMode,
    ),
    [activeFilter, routines, search, sortMode, runStatusById],
  );

  const sections = useMemo(() => {
    if (sortMode === 'Recent') {
      const buckets = new Map(RECENCY_BUCKETS.map(([key]) => [key, []]));
      visibleRoutines.forEach((routine) => {
        const bucket = getTimeBucket(routine.updated);
        (buckets.get(bucket) || buckets.get('later')).push(routine);
      });
      return RECENCY_BUCKETS
        .map(([key, label]) => ({ key, title: label, routines: buckets.get(key) }))
        .filter((section) => section.routines.length);
    }
    return [{ key: 'all', title: null, routines: visibleRoutines }];
  }, [sortMode, visibleRoutines]);

  // Footer counts use the same latest-run status as the filters and dots.
  const countByStatus = (status) => routines.filter((routine) => runStatusById[routine.id] === status).length;
  const runningCount = countByStatus('Running');
  const pausedCount = countByStatus('Paused');
  const failedCount = countByStatus('Failed');

  async function handleSaveGraph(parsed) {
    return dispatch(updateAgentGraph({
      agentsUrl,
      agentId: effectiveAgentId,
      entry: parsed.entry,
      nodes: parsed.nodes,
      edges: parsed.edges,
      status: parsed.status,
      summary: parsed.summary,
      policy_id: parsed.policy_id,
      input_schema: parsed.input_schema,
      output_schema: parsed.output_schema,
      $defs: parsed.$defs,
    })).unwrap();
  }

  return (
    <PageShell>
      <PaneList label="Routines" className="routines-pane">
        <PaneListFilters
          filters={ROUTINE_FILTERS}
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
          actionLabel="+ New"
          onAction={handleNewAgent}
        />
        <PaneListToolbar>
          <PaneListSearch value={search} placeholder="Search routines..." onChange={setSearch} />
          <PaneListSortButton
            title="Sort routines"
            options={SORT_MODES}
            value={sortMode}
            onSelect={setSortMode}
          />
        </PaneListToolbar>
        {visibleRoutines.length ? (
          <PaneListRows
            groups={sections}
            getGroupItems={(section) => section.routines}
            renderItem={(routine) => (
              <RoutineRow
                routine={routine}
                selected={selectedAgent?.id === routine.id}
                onSelect={selectAgent}
                runStatus={runStatusById[routine.id]}
              />
            )}
          />
        ) : (
          <PaneEmptyState>{agentsState.loading || !agentsState.loaded ? 'Loading routines…' : 'No routines match this view.'}</PaneEmptyState>
        )}
        <PaneListFooter>
          <span><span className="num">{runningCount}</span> running</span>
          <span><span className="num">{pausedCount}</span> paused</span>
          <span><span className="num">{failedCount}</span> failed</span>
        </PaneListFooter>
      </PaneList>

      {selectedAgent ? (
        <DetailPane className="detail-pane--dense agents-detail is-graph-mode" label={`${selectedAgent.name} profile`}>
          <div className="detail-pane__body detail-pane__body--graph">
            {graphDetailPending ? (
              <PaneEmptyState detail>Loading graph…</PaneEmptyState>
            ) : (
              <div className={`agents-graph-layout ${graphEditorOpen || routineRunsOpen ? 'is-editing' : ''}`}>
                <RoutineGraphEditor
                  key={selectedAgent.id}
                  agent={selectedAgent}
                  graph={selectedGraph}
                  confirmedGraph={selectedGraph}
                  onPersist={handleSaveGraph}
                  onShowProfile={null}
                  onDelete={handleDeleteAgent}
                  jsonOpen={graphEditorOpen}
                  onOpenJson={() => { setGraphEditorOpen(true); setRoutineRunsOpen(false); }}
                  onCloseJson={() => setGraphEditorOpen(false)}
                  runsOpen={routineRunsOpen}
                  onOpenRuns={() => { setRoutineRunsOpen(true); setGraphEditorOpen(false); }}
                  onCloseRuns={() => setRoutineRunsOpen(false)}
                />
              </div>
            )}
          </div>
        </DetailPane>
      ) : (
        <DetailPane className="detail-pane--dense agents-detail" label="Routine profile">
          <PaneEmptyState detail>{agentsState.loading || !agentsState.loaded ? 'Loading routines…' : 'Select a routine to view its graph.'}</PaneEmptyState>
        </DetailPane>
      )}

      {deleteConfirmationOpen && selectedAgent ? (
        <PopupModal
          eyebrow="Delete routine"
          title={`Delete “${selectedAgent.name}”?`}
          message={deleteError || 'This routine and its graph will be permanently deleted.'}
          confirmLabel="Delete routine"
          busyLabel="Deleting…"
          onClose={() => setDeleteConfirmationOpen(false)}
          onConfirm={confirmDeleteAgent}
          busy={deleteBusy}
        />
      ) : null}
    </PageShell>
  );
}
