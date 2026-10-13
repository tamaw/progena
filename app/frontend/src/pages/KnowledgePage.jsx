import { useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../lib/routes';
import { useConfig } from '../context/ConfigContext';
import {
  fetchKnowledge,
  saveKnowledge,
  selectKnowledgeBases,
  selectKnowledgeState,
} from '../store/uiDataSlice';
import KnowledgePagesTab from '../components/knowledge/KnowledgePagesTab';
import { selectRealtime } from '../store/realtimeSlice';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import DetailHeader, {
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
} from '../components/layout/DetailHeader';
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
import {
  KB_FILTERS,
  KB_SORTS,
  formatCount,
  formatRelativeTime,
  fromApiBase,
  groupBases,
  statusMeta,
} from './knowledgePageModel';



function KnowledgeCard({ base, selected, onSelect }) {
  const meta = statusMeta(base.status);
  return (
    <article className={`task-card ${selected ? 'is-selected' : ''}`}>
      <button type="button" className="task-card__button" onClick={() => onSelect(base.id)}>
        <div className="task-card__title kb-card__title">
          <span className={`kb-card__dot kb-card__dot--${meta.tone}`} title={meta.label} aria-label={meta.label} />
          <span className="kb-card__name">{base.name}</span>
          <span className="kb-card__type">{base.storeType}</span>
        </div>
        <div className="task-card__meta">
          <span className="kb-card__desc">{base.description}</span>
        </div>
      </button>
    </article>
  );
}

export default function KnowledgePage() {
  const navigate = useNavigate();
  const { getApiUrl } = useConfig();
  const dispatch = useDispatch();
  // `titleSlug` is display context only (e.g. /knowledge/src-abc123/docs) --
  // selection is driven entirely by the id, never by the slug.
  const { baseId: urlBaseId } = useParams();
  const selectedBaseId = typeof urlBaseId === 'string' && urlBaseId.trim().length > 0
    ? urlBaseId
    : null;
  // The page renders from the realtime store: `knowledge.snapshot` events keep
  // `rawBases` live; the GET on mount just seeds + triggers the first broadcast.
  const rawBases = useSelector(selectKnowledgeBases);
  const knowledgeState = useSelector(selectKnowledgeState);
  const { loaded } = knowledgeState;
  const realtime = useSelector(selectRealtime);
  const [activeFilter, setActiveFilter] = useState('All');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortMode, setSortMode] = useState('Status');
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState('');
  const [savingName, setSavingName] = useState(false);
  const nameInputRef = useRef(null);
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('tasks-list');

  const knowledgeUrl = getApiUrl('/api/ui/knowledge');

  useEffect(() => {
    if (!realtime.connected) {
      return;
    }
    void dispatch(fetchKnowledge({ knowledgeUrl })).unwrap().catch(() => {});
  }, [dispatch, knowledgeUrl, realtime.connected]);

  const bases = useMemo(() => rawBases.map(fromApiBase), [rawBases]);

  // Clear edit state when switching to a different base.
  useEffect(() => {
    setEditingName(false);
    setNameDraft('');
  }, [selectedBaseId]);

  // Focus + select the name field when entering rename mode.
  useEffect(() => {
    if (!editingName) {
      return;
    }
    nameInputRef.current?.focus();
    nameInputRef.current?.select();
  }, [editingName]);

  const groups = useMemo(
    () => groupBases(bases, activeFilter, searchQuery, sortMode),
    [bases, activeFilter, searchQuery, sortMode],
  );

  const visibleBases = useMemo(() => groups.flatMap((group) => group.bases), [groups]);

  const selectedBase = useMemo(
    () => bases.find((base) => base.id === selectedBaseId)
      || (!selectedBaseId ? visibleBases[0] || null : null),
    [bases, selectedBaseId, visibleBases],
  );

  // Keep the URL in sync with whichever base is actually selected. A routed
  // base id stays authoritative; if it disappears after hydration, the effect
  // below clears back to /knowledge instead of rewriting stale detail state.
  // The title slug is cosmetic; only the id segment is ever read back.
  useEntityUrlSync('/knowledge', selectedBase?.id, selectedBase?.name);
  useEffect(() => {
    if (!loaded || !selectedBaseId) {
      return;
    }
    if (!bases.some((base) => base.id === selectedBaseId)) {
      navigate('/knowledge', { replace: true });
    }
  }, [bases, loaded, navigate, selectedBaseId]);


  const connectedCount = bases.filter((base) => base.status === 'connected').length;

  const putBases = (nextRaw) => dispatch(saveKnowledge({ knowledgeUrl, bases: nextRaw })).unwrap();
  const patchSelected = (patch) => putBases(
    rawBases.map((base) => (base.id === selectedBase?.id
      ? { ...base, ...patch, updated_at: new Date().toISOString() }
      : base)),
  );

  // Open the quick chat prefilled to create a knowledge base — Progenitor edits
  // /config/knowledge.json (managing-knowledge skill) and the new base appears
  // here live via the knowledge.snapshot event.
  const openCreateChat = () => {
    window.dispatchEvent(
      new CustomEvent('progenitor:open-quick-chat', { detail: { mode: 'knowledge-create' } }),
    );
  };

  const deleteSelected = async () => {
    if (!selectedBase) {
      return;
    }
    const id = selectedBase.id;
    if (selectedBaseId === id) {
      navigate('/knowledge', { replace: true });
    }
    await putBases(rawBases.filter((base) => base.id !== id));
  };

  const startNameEdit = () => {
    setNameDraft(selectedBase?.name || '');
    setEditingName(true);
  };

  const cancelNameEdit = () => {
    setNameDraft(selectedBase?.name || '');
    setEditingName(false);
  };

  const saveNameEdit = async () => {
    if (!selectedBase || savingName) {
      return;
    }
    const next = nameDraft.replace(/\s+/g, ' ').trim();
    if (!next || next === selectedBase.name) {
      cancelNameEdit();
      return;
    }
    setSavingName(true);
    try {
      await patchSelected({ name: next });
      setEditingName(false);
    } catch {
      // Leave edit mode open so the draft isn't lost on a transient error.
    } finally {
      setSavingName(false);
    }
  };

  const meta = selectedBase ? statusMeta(selectedBase.status) : null;

  return (
    <PageShell className="tasks-page knowledge-page">
      <PaneSection className={paneClassName} aria-label="Knowledge bases" onItemSelect={() => setCollapsed(true)}>
        {collapsed ? (
          <PaneExpandRail label="Knowledge" onExpand={() => setCollapsed(false)} />
        ) : (
          <>
            <PaneCollapseSeam label="Knowledge" onCollapse={() => setCollapsed(true)} />
            <button type="button" className="pane-mobile-scrim" aria-label="Close knowledge list" onClick={() => setCollapsed(true)} />
            <PaneListFilters
              filters={KB_FILTERS}
              activeFilter={activeFilter}
              onFilterChange={setActiveFilter}
              actionLabel="+ New"
              onAction={openCreateChat}
            />
            <PaneListToolbar>
              <PaneListSearch value={searchQuery} placeholder="Search knowledge bases" onChange={setSearchQuery} />
              <PaneListSortButton
                title="Sort knowledge bases"
                options={KB_SORTS}
                value={sortMode}
                onSelect={setSortMode}
              />
            </PaneListToolbar>

            {knowledgeState.error ? <div className="task-alert">{knowledgeState.error}</div> : null}
            {!visibleBases.length && loaded ? (
              <PaneEmptyState>{bases.length ? 'No matching knowledge bases.' : 'No knowledge bases.'}</PaneEmptyState>
            ) : null}
            {groups.length ? (
              <PaneListRows
                groups={groups.map((group) => ({ ...group, items: group.bases }))}
                getItemKey={(base) => base.id}
                renderItem={(base) => (
                  <KnowledgeCard
                    base={base}
                    selected={selectedBase?.id === base.id}
                    onSelect={(id) => navigate(buildEntityPath('/knowledge', id, base.name), { replace: true })}
                  />
                )}
              />
            ) : null}

            <PaneListFooter>
              <span><span className="num">{bases.length}</span> stores</span>
              <span>{connectedCount} connected</span>
            </PaneListFooter>
          </>
        )}
      </PaneSection>

      <DetailPane flush className="task-detail knowledge-detail">
        {selectedBase ? (
          <>
            <DetailHeader
              titleClassName="chat-detail-header__title"
              title={(
                <>
                  {editingName ? (
                    <input
                      ref={nameInputRef}
                      type="text"
                      className="chat-page-header__title-input"
                      value={nameDraft}
                      maxLength={160}
                      aria-label="Knowledge base name"
                      onChange={(event) => setNameDraft(event.target.value)}
                      onBlur={cancelNameEdit}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          event.preventDefault();
                          saveNameEdit();
                        } else if (event.key === 'Escape') {
                          event.preventDefault();
                          cancelNameEdit();
                        }
                      }}
                    />
                  ) : (
                    <span className="chat-detail-header__title-text">{selectedBase.name}</span>
                  )}
                  <button
                    type="button"
                    className="chat-icon-button chat-detail-header__rename"
                    data-tooltip={editingName ? 'Save name' : 'Rename knowledge base'}
                    aria-label={editingName ? 'Save name' : 'Rename knowledge base'}
                    onMouseDown={editingName ? (event) => event.preventDefault() : undefined}
                    onClick={editingName ? saveNameEdit : startNameEdit}
                  >
                    {editingName ? (
                      <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                        <path d="M3.25 8.25 6.5 11.5l6.25-6.75" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    ) : (
                      <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                        <path d="M2 14h3l8-8-3-3-8 8z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
                      </svg>
                    )}
                  </button>
                </>
              )}
              actions={(
                <DetailHeaderActions>
                  <DetailHeaderAction
                    icon={<DeleteActionIcon />}
                    label="Delete"
                    tooltip="Delete knowledge base"
                    ariaLabel="Delete knowledge base"
                    variant="danger"
                    onClick={deleteSelected}
                  />
                </DetailHeaderActions>
              )}
              meta={[
                <DetailStatus key="status" tone={meta.tone}>{meta.label}</DetailStatus>,
                <span key="path" className="muted">{selectedBase.path}</span>,
                <span key="pages" className="muted">{formatCount(selectedBase.pageCount)} pages</span>,
                <span key="synced" className="muted">synced {formatRelativeTime(selectedBase.lastSynced)}</span>,
              ]}
            />

            <div className="task-detail__body">
              <KnowledgePagesTab baseId={selectedBaseId} baseName={selectedBase.name} knowledgeUrl={knowledgeUrl} />
            </div>
          </>
        ) : (
          <PaneEmptyState detail>Select a knowledge base to inspect details.</PaneEmptyState>
        )}
      </DetailPane>
    </PageShell>
  );
}
