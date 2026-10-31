import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector, useStore } from 'react-redux';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../../lib/routes';
import { useConfig } from '../../context/ConfigContext';
import { dispatchQuickChatOpen, QUICK_CHAT_MODE } from '../../lib/browserContracts';

import { selectRealtime } from '../../realtime/realtimeSlice';
import { fetchKnowledge, saveKnowledge, selectKnowledgeBases, selectKnowledgePage, selectKnowledgePages, selectKnowledgeRevision, selectKnowledgeRevisions, selectKnowledgeState } from './state';
import KnowledgePagesTab from './KnowledgePagesTab';
import InlineFailure from '../../components/InlineFailure';
import PopupModal from '../../components/PopupModal';
import useConfirmAction from '../../hooks/useConfirmAction';
import useMissingEntityRoute from '../../hooks/useMissingEntityRoute';
import useEntityDraft from '../../hooks/useEntityDraft';
import PageShell from '../../components/layout/PageShell';
import DetailPane from '../../components/layout/DetailPane';
import DetailHeader, {
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
} from '../../components/layout/DetailHeader';
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
} from '../../components/layout/PaneList';
import { usePaneCollapse } from '../../hooks/usePaneCollapse';
import {
  KB_FILTERS,
  KB_SORTS,
  fromApiBase,
  groupBases,
  statusMeta,
} from './knowledgePageModel';
import { draftDiscarded, entityDraftKey } from '../../store/editorDraftsSlice';
import { formatCompactRelativeAge, formatShortLocalDateTime } from '../../lib/displayTime';

const baseIdOf = (base) => base.id;

// Search-index values for the base root, from the live base record (updated by
// knowledge.index.* events), not the cached index.md front matter.
function searchIndexFacts(base) {
  if (!base) return [];
  const pages = base.indexPages;
  const at = formatShortLocalDateTime(Date.parse(base.indexedAt));
  return [
    `${base.indexedPages}/${pages} page${pages === 1 ? '' : 's'} indexed`,
    ...(at ? [`indexed ${at}`] : []),
  ];
}

// Header facts for the open page. A concept page reports its front matter; a
// generated index reports when the newest page in its folder (and subfolders)
// changed, i.e. what the index is up to date with, plus (at the base root) the
// base's search-index progress.
function pageHeaderFacts(path, frontMatter, pages, base) {
  const fm = frontMatter && typeof frontMatter === 'object' ? frontMatter : {};
  if (path.split('/').at(-1) === 'index.md') {
    const prefix = path.slice(0, -'index.md'.length);
    const newest = Math.max(...pages
      .filter((page) => page.kind === 'concept' && page.path.startsWith(prefix))
      .map((page) => Date.parse(page.updated_at))
      .filter(Number.isFinite));
    return {
      type: 'index',
      tags: [],
      generatedAt: formatShortLocalDateTime(newest),
      extra: path === 'index.md' ? searchIndexFacts(base) : [],
    };
  }
  return {
    type: fm.type ? String(fm.type) : null,
    tags: Array.isArray(fm.tags) ? fm.tags.map(String) : fm.tags ? [String(fm.tags)] : [],
    generatedAt: formatShortLocalDateTime(fm.generated && typeof fm.generated === 'object' ? Date.parse(fm.generated.at) : NaN),
    extra: [],
  };
}

function KnowledgeCard({ base, selected, onSelect }) {
  const meta = statusMeta(base.status, { indexedPages: base.indexedPages, indexPages: base.indexPages });
  return (
    <article className={`task-card ${selected ? 'is-selected' : ''}`}>
      <button type="button" className="task-card__button" onClick={() => onSelect(base.id)}>
        <div className="task-card__title kb-card__title">
          <span className={`kb-card__dot sess-row__dot--${meta.tone}`} title={meta.label} aria-label={meta.label} />
          <span className="kb-card__name">{base.name}</span>
        </div>
        <div className="task-card__meta">
          <span className="kb-card__desc">{base.description}</span>
        </div>
        <div className="task-card__meta kb-card__info">
          <span>synced {formatCompactRelativeAge(base.lastSynced)}</span>
        </div>
      </button>
    </article>
  );
}

export default function KnowledgePage() {
  const navigate = useNavigate();
  const { getApiUrl } = useConfig();
  const dispatch = useDispatch();
  const store = useStore();
  // selection is driven entirely by the id, never by the slug.
  const { baseId: urlBaseId } = useParams();
  const [searchParams] = useSearchParams();
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
  // Keyed by base so a failure never follows navigation to another base.
  const [nameValidationState, setNameValidationState] = useState(null);
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
  const [nameDraft, setNameDraft, nameDraftControls] = useEntityDraft(
    entityDraftKey('knowledge', selectedBase?.id, 'name'),
    { editing: false, name: selectedBase?.name || '' },
  );
  const {
    beginSave: beginNameSave,
    clear: clearNameDraft,
    error: nameSaveError,
    failSave: failNameSave,
    saving: nameSaving,
    trackSave: trackNameSave,
  } = nameDraftControls;
  const nameValidation = nameValidationState && nameValidationState.baseId === selectedBase?.id ? nameValidationState.message : null;
  const editingName = Boolean(nameDraft.editing);
  // Focus + select the name field when entering rename mode.
  useEffect(() => {
    if (!editingName) {
      return;
    }
    nameInputRef.current?.focus();
    nameInputRef.current?.select();
  }, [editingName]);

  // Keep the URL in sync with whichever base is actually selected. A routed
  // base id stays authoritative; only an absent hydrated base routes back.
  useEntityUrlSync('/knowledge', selectedBase?.id, selectedBase?.name);
  useMissingEntityRoute({
    hydrated: loaded,
    entityId: selectedBaseId,
    entities: bases,
    getEntityId: baseIdOf,
    fallbackPath: '/knowledge',
  });

  const indexedCount = bases.filter((base) => base.status === 'indexed').length;

  const putBases = useCallback(
    (nextRaw) => dispatch(saveKnowledge({ knowledgeUrl, bases: nextRaw })).unwrap(),
    [dispatch, knowledgeUrl],
  );
  const deleteAction = useConfirmAction(selectedBase?.id || 'knowledge');

  // Open the quick chat prefilled to create a knowledge base — Progena edits
  // /config/knowledge.json (managing-knowledge skill) and the new base appears
  // here live via the knowledge.snapshot event.
  const openCreateChat = () => {
    dispatchQuickChatOpen({ mode: QUICK_CHAT_MODE.KNOWLEDGE_CREATE });
  };

  const requestDelete = () => {
    if (!selectedBase) return;
    const baseId = selectedBase.id;
    deleteAction.request('Delete knowledge base', async ({ isCurrent }) => {
      await putBases(selectKnowledgeBases(store.getState()).filter((item) => item.id !== baseId));
      dispatch(draftDiscarded(entityDraftKey('knowledge', baseId, 'name')));
      if (isCurrent()) navigate('/knowledge', { replace: true });
    });
  };

  const startNameEdit = () => {
    setNameValidationState(null);
    setNameDraft({ editing: true, name: selectedBase?.name || '' });
  };

  const cancelNameEdit = () => {
    if (nameSaving) return;
    setNameValidationState(null);
    clearNameDraft();
  };

  const saveNameEdit = () => {
    if (!selectedBase || nameSaving) return;
    const base = selectedBase;
    const next = nameDraft.name.replace(/\s+/g, ' ').trim();
    if (!next) {
      setNameValidationState({ baseId: base.id, message: 'A knowledge base name is required.' });
      return;
    }
    if (next === base.name) {
      cancelNameEdit();
      return;
    }
    setNameValidationState(null);
    const ticket = beginNameSave();
    if (!ticket) return;
    const operation = dispatch(saveKnowledge({
      knowledgeUrl,
      bases: selectKnowledgeBases(store.getState()).map((item) => (item.id === base.id
        ? { ...item, name: next, updated_at: new Date().toISOString() }
        : item)),
    }));
    trackNameSave(ticket, {
      requestId: operation.requestId,
      events: [],
    });
    void operation.unwrap().then((acknowledgement) => {
      trackNameSave(ticket, {
        requestId: acknowledgement.request_id,
        events: acknowledgement.event_types,
      });
    }).catch((error) => {
      failNameSave(ticket, error);
    });
  };

  const meta = selectedBase
    ? statusMeta(selectedBase.status, { indexedPages: selectedBase.indexedPages, indexPages: selectedBase.indexPages })
    : null;
  const openPagePath = searchParams.get('page') || 'index.md';
  const openBaseId = selectedBase?.id || null;
  const openPage = useSelector((state) => (openBaseId
    ? selectKnowledgePage(state, openBaseId, openPagePath)
    : null));
  const basePages = useSelector((state) => (openBaseId ? selectKnowledgePages(state, openBaseId) : null));
  const viewingRevisionId = searchParams.get('revision');
  const viewingRevision = useSelector((state) => (openBaseId && viewingRevisionId
    ? selectKnowledgeRevisions(state, openBaseId, openPagePath).find((rev) => rev.revision_id === viewingRevisionId) || null
    : null));
  const revisionDocument = useSelector((state) => (openBaseId && viewingRevisionId
    ? selectKnowledgeRevision(state, openBaseId, openPagePath, viewingRevisionId)
    : null));
  // An open revision shows its own type and tags, not the current page's.
  const headerFrontMatter = viewingRevisionId ? revisionDocument?.front_matter : openPage?.front_matter;
  const pageFacts = openPage
    ? pageHeaderFacts(openPagePath, headerFrontMatter, basePages || [], selectedBase)
    : { type: null, tags: [], generatedAt: null, extra: [] };
  // An open revision reports when it was captured, in the same "generated" slot.
  const generatedAt = viewingRevisionId
    ? formatShortLocalDateTime(Date.parse(viewingRevision?.captured_at))
    : pageFacts.generatedAt;

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

            {knowledgeState.error ? (
              <InlineFailure failure={knowledgeState.error} operation="Load knowledge bases" onRetry={() => dispatch(fetchKnowledge({ knowledgeUrl }))} />
            ) : !visibleBases.length && loaded ? (
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
              <span><span className="num">{bases.length}</span> bases</span>
              <span>{indexedCount} indexed</span>
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
                      value={nameDraft.name}
                      maxLength={160}
                      aria-label="Knowledge base name"
                      disabled={nameSaving}
                      onChange={(event) => {
                        setNameValidationState(null);
                        setNameDraft((current) => ({ ...current, name: event.target.value }));
                      }}
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
                    disabled={nameSaving}
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
                    disabled={deleteAction.busy}
                    onClick={requestDelete}
                  />
                </DetailHeaderActions>
              )}
              meta={[
                meta && meta.tone === 'error' ? (
                  <span key="status" title={selectedBase.indexError || ''}>
                    <DetailStatus tone={meta.tone}>{meta.label}</DetailStatus>
                  </span>
                ) : (
                  <DetailStatus key="status" tone={meta.tone}>{meta.label}</DetailStatus>
                ),
                ...(pageFacts.type ? [<span key="type" className="muted">{pageFacts.type}</span>] : []),
                ...(pageFacts.tags.length ? [<span key="tags" className="muted">{pageFacts.tags.join(', ')}</span>] : []),
                ...(generatedAt ? [<span key="generated" className="muted">generated {generatedAt}</span>] : []),
                ...(viewingRevisionId ? [] : pageFacts.extra.map((fact) => <span key={fact} className="muted">{fact}</span>)),
              ]}
            />

            <div className="task-detail__body">
              {editingName ? <InlineFailure failure={nameValidation || nameSaveError} operation="Rename knowledge base" /> : null}
              <KnowledgePagesTab baseId={selectedBase.id} baseName={selectedBase.name} knowledgeUrl={knowledgeUrl} />
            </div>
          </>
        ) : (
          <PaneEmptyState detail>Select a knowledge base to inspect details.</PaneEmptyState>
        )}
      </DetailPane>
      {deleteAction.open && selectedBase ? (
        <PopupModal
          eyebrow="Delete knowledge base"
          title={`Delete “${selectedBase.name}”?`}
          message="The base is removed from the catalog and its pages are hidden. Pages are kept and come back if a base with the same id is created again."
          error={deleteAction.error}
          confirmLabel="Delete knowledge base"
          busyLabel="Deleting…"
          onClose={deleteAction.close}
          onConfirm={deleteAction.confirm}
          busy={deleteAction.busy}
        />
      ) : null}
    </PageShell>
  );
}
