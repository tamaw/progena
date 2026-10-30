import { useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
import { dispatchQuickChatOpen } from '../../lib/browserContracts';

import PageShell from '../../components/layout/PageShell';
import DetailPane from '../../components/layout/DetailPane';
import {
  PaneCollapseSeam,
  PaneEmptyState,
  PaneExpandRail,
  PaneListFilters,
  PaneListFooter,
  PaneListRows,
  PaneListSearch,
  PaneListToolbar,
  PaneSection,
} from '../../components/layout/PaneList';
import { usePaneCollapse } from '../../hooks/usePaneCollapse';
import InlineFailure from '../../components/InlineFailure';
import useMissingEntityRoute from '../../hooks/useMissingEntityRoute';
import { formatCompactRelativeAge } from '../../lib/displayTime';
import { selectRealtime } from '../../realtime/realtimeSlice';
import { fetchCanvases, selectCanvases, selectCanvasesState } from './state';
import CanvasPage from './CanvasPage';
import {
  CANVAS_FILTERS, CANVAS_STATE_LABEL, CANVAS_STATE_TONE, filterCanvases, canvasState,
} from './canvasesPageModel';
import { selectAgentName } from '../settings/state/preferences';

function CanvasRow({ canvas, selected, onSelect }) {
  const state = canvasState(canvas);
  return (
    <button
      type="button"
      className={`sess-row agents-agent-row ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(canvas.name)}
    >
      <span className="sess-row__top">
        <span className={`sess-row__dot sess-row__dot--${CANVAS_STATE_TONE[state]}`} title={CANVAS_STATE_LABEL[state]} />
        <span className="sess-row__agent agents-agent-row__name">{canvas.title || canvas.name}</span>
      </span>
      <span className="sess-row__age">{canvas.updated_at ? formatCompactRelativeAge(canvas.updated_at) : ''}</span>
      <span className="sess-row__title">{canvas.description || 'No description'}</span>
      <span className="sess-row__msgs">
        <span><span className="num">{canvas.files?.length || 0}</span> files</span>
        {canvas.libraries?.length ? <span>{canvas.libraries.join(', ')}</span> : null}
      </span>
    </button>
  );
}

const openCanvasChat = (draft) => dispatchQuickChatOpen({ draft });

export default function CanvasesPage() {
  const { name } = useParams();
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const { getApiUrl } = useConfig();
  const canvasesUrl = getApiUrl('canvases');
  const realtime = useSelector(selectRealtime);
  const canvases = useSelector(selectCanvases);
  const state = useSelector(selectCanvasesState);
  const agentName = useSelector(selectAgentName);
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('tasks-list');
  const [filter, setFilter] = useState('All');
  const [query, setQuery] = useState('');

  useEffect(() => {
    if (realtime.connected) dispatch(fetchCanvases({ canvasesUrl }));
  }, [dispatch, canvasesUrl, realtime.connected]);

  useMissingEntityRoute({
    hydrated: state.loaded, entityId: name, entities: canvases, getEntityId: (item) => item.name, fallbackPath: '/canvases',
  });

  // Like the other list/detail pages, land on the first canvas when none is selected.
  useEffect(() => {
    if (!name && canvases.length) navigate(`/canvases/${encodeURIComponent(canvases[0].name)}`, { replace: true });
  }, [canvases, name, navigate]);

  const visible = useMemo(() => filterCanvases(canvases, { filter, query }), [filter, canvases, query]);
  const errorCount = canvases.filter((canvas) => canvasState(canvas) !== 'available').length;
  const select = (canvasName) => navigate(`/canvases/${encodeURIComponent(canvasName)}`);
  const selected = name ? canvases.find((canvas) => canvas.name === name) : null;

  return (
    <PageShell className="tasks-page canvases-page">
      <PaneSection className={paneClassName} aria-label="Canvases" onItemSelect={() => setCollapsed(true)}>
        {collapsed ? (
          <PaneExpandRail label="Canvases" onExpand={() => setCollapsed(false)} />
        ) : (
          <>
            <PaneCollapseSeam label="Canvases" onCollapse={() => setCollapsed(true)} />
            <button type="button" className="pane-mobile-scrim" aria-label="Close canvases list" onClick={() => setCollapsed(true)} />
            <PaneListFilters
              filters={CANVAS_FILTERS}
              activeFilter={filter}
              onFilterChange={setFilter}
              actionLabel="+ New"
              onAction={() => openCanvasChat('Build me a canvas that ')}
            />
            <PaneListToolbar>
              <PaneListSearch value={query} placeholder="Search canvases" onChange={setQuery} />
            </PaneListToolbar>
            {state.error ? (
              <InlineFailure failure={state.error} operation="Load canvases" onRetry={() => dispatch(fetchCanvases({ canvasesUrl }))} />
            ) : !state.loaded ? (
              <PaneEmptyState>Loading canvases…</PaneEmptyState>
            ) : !visible.length ? (
              <PaneEmptyState>{canvases.length ? 'No matching canvases.' : 'No canvases yet.'}</PaneEmptyState>
            ) : (
              <PaneListRows
                items={visible}
                getItemKey={(canvas) => canvas.name}
                renderItem={(canvas) => <CanvasRow canvas={canvas} selected={canvas.name === name} onSelect={select} />}
              />
            )}
            <PaneListFooter>
              <span><span className="num">{canvases.length}</span> canvases</span>
              <span>{errorCount ? `${errorCount} need attention` : 'all available'}</span>
            </PaneListFooter>
          </>
        )}
      </PaneSection>

      <DetailPane flush className="task-detail canvases-detail">
        {selected ? (
          <CanvasPage key={selected.name} name={selected.name} onAskProgena={openCanvasChat} />
        ) : (
          <PaneEmptyState detail>
            {state.loaded && !canvases.length
              ? `Ask ${agentName} in chat for a dashboard, diagram or game; it appears here as soon as it is written.`
              : 'Select a canvas.'}
          </PaneEmptyState>
        )}
      </DetailPane>
    </PageShell>
  );
}
