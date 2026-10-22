import { useCallback, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate } from 'react-router-dom';
import { entityDraftKey } from '../../store/editorDraftsSlice';
import useEntityDraft from '../../hooks/useEntityDraft';
import InlineFailure from '../../components/InlineFailure';
import PopupModal from '../../components/PopupModal';
import useAgentCatalogue from '../agents/useAgentCatalogue';
import useConfirmAction from '../../hooks/useConfirmAction';
import { deleteAgentProfile, selectRoutineLatestRunStatusByAgentId, updateAgentGraph } from '../agents/state/agents';
import {
  PaneEmptyState,
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListRows,
} from '../../components/layout/PaneList';
import PageShell from '../../components/layout/PageShell';
import DetailPane from '../../components/layout/DetailPane';
import { RoutineGraphEditor } from './editor/RoutineGraphEditor';
import { formatAge } from '../agents/agentFormat';
import { catalogueRoutines } from '../agents/agentCatalogueModel';

const ROUTINE_FILTERS = ['All', 'Running', 'Paused', 'Failed'];
const SORT_MODES = ['Name', 'Recent'];
const FALLBACK_GRAPH = {
  status: 'Draft',
  summary: '',
  entry: null,
  nodes: [],
  edges: [],
};

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
  const navigate = useNavigate();
  const {
    agents: routines,
    agentsState,
    agentsUrl,
    detailFailure,
    detailPending,
    effectiveAgentId,
    rosterFailure,
    retryDetail,
    retryRoster,
    selectAgent,
    selectedAgent,
  } = useAgentCatalogue({ kind: 'routines', routeBase: '/routines' });
  const runStatusById = useSelector(selectRoutineLatestRunStatusByAgentId);
  const [activeFilter, setActiveFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [sortMode, setSortMode] = useState('Name');
  const viewKey = entityDraftKey('routine', effectiveAgentId, 'view');
  const [editorView, setEditorView] = useEntityDraft(viewKey, { jsonOpen: false, runsOpen: false });
  const graphEditorOpen = editorView.jsonOpen;
  const routineRunsOpen = editorView.runsOpen;
  const [deleteTarget, setDeleteTarget] = useState(null);
  const deleteAction = useConfirmAction(effectiveAgentId);

  const handleNewAgent = useCallback(() => {
    window.dispatchEvent(new CustomEvent('progenitor:open-quick-chat', {
      detail: { mode: 'routine-create' },
    }));
  }, []);

  const requestDeleteAgent = useCallback(() => {
    if (!selectedAgent) return;
    const target = { id: selectedAgent.id, name: selectedAgent.name };
    setDeleteTarget(target);
    deleteAction.request('Delete routine', async ({ isCurrent }) => {
      await dispatch(deleteAgentProfile({ agentsUrl, agentId: target.id })).unwrap();
      if (isCurrent()) navigate('/routines', { replace: true });
    });
  }, [agentsUrl, deleteAction, dispatch, navigate, selectedAgent]);

  const closeDeleteAgent = useCallback(() => {
    if (deleteAction.busy) return;
    deleteAction.close();
    setDeleteTarget(null);
  }, [deleteAction]);

  const { visible: visibleRoutines, sections } = useMemo(
    () => catalogueRoutines(routines, {
      filter: activeFilter,
      search,
      sortMode,
      runStatusById,
    }),
    [activeFilter, routines, runStatusById, search, sortMode],
  );
  // Footer counts use the same latest-run status as the filters and dots.
  const countByStatus = (status) => routines.filter((routine) => runStatusById[routine.id] === status).length;
  const runningCount = countByStatus('Running');
  const pausedCount = countByStatus('Paused');
  const failedCount = countByStatus('Failed');
  const selectedGraph = selectedAgent?.graph || FALLBACK_GRAPH;
  const hasCachedGraph = selectedAgent?.graph != null;

  function handleSaveGraph(parsed, agentId) {
    return dispatch(updateAgentGraph({
      agentsUrl,
      agentId,
      entry: parsed.entry,
      nodes: parsed.nodes,
      edges: parsed.edges,
      status: parsed.status,
      summary: parsed.summary,
      policy_id: parsed.policy_id,
      input_schema: parsed.input_schema,
      output_schema: parsed.output_schema,
      $defs: parsed.$defs,
    }));
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
        {rosterFailure ? <InlineFailure failure={rosterFailure} onRetry={retryRoster} /> : null}
        {visibleRoutines.length ? (
          <PaneListRows
            groups={sections}
            getGroupItems={(section) => section.items}
            renderItem={(routine) => (
              <RoutineRow
                routine={routine}
                selected={selectedAgent?.id === routine.id}
                onSelect={selectAgent}
                runStatus={runStatusById[routine.id]}
              />
            )}
          />
        ) : !rosterFailure ? (
          <PaneEmptyState>{agentsState.loading || !agentsState.loaded ? 'Loading routines…' : 'No routines match this view.'}</PaneEmptyState>
        ) : null}
        <PaneListFooter>
          <span><span className="num">{runningCount}</span> running</span>
          <span><span className="num">{pausedCount}</span> paused</span>
          <span><span className="num">{failedCount}</span> failed</span>
        </PaneListFooter>
      </PaneList>

      {selectedAgent ? (
        <DetailPane className="detail-pane--dense agents-detail is-graph-mode" label={`${selectedAgent.name} profile`}>
          <div className="detail-pane__body detail-pane__body--graph">
            {detailFailure && hasCachedGraph ? <InlineFailure failure={detailFailure} onRetry={retryDetail} /> : null}
            {detailFailure && !hasCachedGraph ? (
              <InlineFailure failure={detailFailure} onRetry={retryDetail} />
            ) : detailPending ? (
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
                  onDelete={requestDeleteAgent}
                  jsonOpen={graphEditorOpen}
                  onOpenJson={() => setEditorView((current) => ({ ...current, jsonOpen: true, runsOpen: false }))}
                  onCloseJson={() => setEditorView((current) => ({ ...current, jsonOpen: false }))}
                  runsOpen={routineRunsOpen}
                  onOpenRuns={() => setEditorView((current) => ({ ...current, runsOpen: true, jsonOpen: false }))}
                  onCloseRuns={() => setEditorView((current) => ({ ...current, runsOpen: false }))}
                />
              </div>
            )}
          </div>
        </DetailPane>
      ) : (
        <DetailPane className="detail-pane--dense agents-detail" label="Routine profile">
          {detailFailure
            ? <InlineFailure failure={detailFailure} onRetry={retryDetail} />
            : <PaneEmptyState detail>{agentsState.loading || !agentsState.loaded ? 'Loading routines…' : 'Select a routine to view its graph.'}</PaneEmptyState>}
        </DetailPane>
      )}

      {deleteAction.open && deleteTarget ? (
        <PopupModal
          eyebrow="Delete routine"
          title={`Delete “${deleteTarget.name}”?`}
          message="This routine and its graph will be permanently deleted."
          error={deleteAction.error}
          confirmLabel="Delete routine"
          busyLabel="Deleting…"
          onClose={closeDeleteAgent}
          onConfirm={deleteAction.confirm}
          busy={deleteAction.busy}
        />
      ) : null}
    </PageShell>
  );
}
