import { useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../lib/routes';
import { useConfig } from '../context/ConfigContext';
import { createUuid } from '../lib/uuid';
import {
  fetchKnowledge,
  indexKnowledgeSource,
  saveKnowledge,
  selectKnowledgeBases,
  selectKnowledgeSearch,
  selectKnowledgeState,
} from '../store/uiDataSlice';
import { selectRealtime } from '../store/realtimeSlice';
import { requestAcknowledged } from '../store/http';
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
  PaneListVirtualized,
  PaneSection,
} from '../components/layout/PaneList';
import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import NumberStepper from '../components/common/NumberStepper';
import {
  KB_FILTERS,
  KB_SORTS,
  formatCount,
  formatRelativeTime,
  fromApiBase,
  groupBases,
  statusMeta,
} from './knowledgePageModel';

const SOURCE_CHIP_TONE = { indexed: 'ok', error: 'error', pending: 'info' };

// Embedding configuration options. Selecting a model fills in its native
// dimensions (still overridable).
const EMBED_MODELS = [
  { value: 'progenitor-hash-256', dimensions: 256 },
  { value: 'text-embedding-3-small', dimensions: 1536 },
  { value: 'text-embedding-3-large', dimensions: 3072 },
  { value: 'text-embedding-ada-002', dimensions: 1536 },
];
// Values must match the validator (cosine / l2 / inner_product).
const EMBED_METRICS = [
  { value: 'cosine', label: 'cosine' },
  { value: 'inner_product', label: 'inner product' },
  { value: 'l2', label: 'euclidean' },
];
const EMBED_INDEXES = ['Flat', 'IVFFlat', 'HNSW'];

// Generate a client-side source id so the folder can be registered (PUT) and then
// indexed (POST) in one user action. The validator keeps provided source ids.
function generateSourceId() {
  const raw = (globalThis.crypto?.randomUUID?.() || Math.random().toString(16).slice(2)).replace(
    /-/g,
    '',
  );
  return `src-${raw.slice(0, 10)}`;
}

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

// Headline metric tile, mirroring the Schedules detail pane.
function MetricCard({ label, value, detail }) {
  return (
    <div className="agents-metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

// Icon + title + sub + chip row, mirroring the Schedules connection/activity
// lists. Used for connection details, connected agents, and collections.
function InfoRow({ title, sub, chip }) {
  return (
    <div className="agents-connection-row kb-noicon">
      <div className="agents-connection-row__body">
        <strong>{title}</strong>
        {sub ? <p>{sub}</p> : null}
      </div>
      {chip ? <span className="agents-chip agents-chip--info">{chip}</span> : null}
    </div>
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
  const knowledgeSearch = useSelector(selectKnowledgeSearch);
  const realtime = useSelector(selectRealtime);
  const [activeFilter, setActiveFilter] = useState('All');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortMode, setSortMode] = useState('Status');
  const [detailTab, setDetailTab] = useState('Overview');
  const [testQuery, setTestQuery] = useState('');
  const [testResults, setTestResults] = useState(null);
  const [testLoading, setTestLoading] = useState(false);
  const [testError, setTestError] = useState('');
  const [config, setConfig] = useState({ limit: 5, minScore: 0 });
  const [pendingSearchRequestId, setPendingSearchRequestId] = useState(null);
  const [addingTag, setAddingTag] = useState(false);
  const [tagDraft, setTagDraft] = useState('');
  const [savingTag, setSavingTag] = useState(false);
  const [tagError, setTagError] = useState('');
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState('');
  const [savingName, setSavingName] = useState(false);
  const nameInputRef = useRef(null);
  const [editingConn, setEditingConn] = useState(false);
  const [connDraft, setConnDraft] = useState('');
  const [savingConn, setSavingConn] = useState(false);
  const [connError, setConnError] = useState('');
  const [editingEmbed, setEditingEmbed] = useState(false);
  const [embedDraft, setEmbedDraft] = useState({ embeddingModel: '', dimensions: 256, metric: 'cosine', indexType: 'Flat' });
  const [savingEmbed, setSavingEmbed] = useState(false);
  const [embedError, setEmbedError] = useState('');
  const [editingDesc, setEditingDesc] = useState(false);
  const [descDraft, setDescDraft] = useState('');
  const [savingDesc, setSavingDesc] = useState(false);
  const [descError, setDescError] = useState('');
  const [folderFormOpen, setFolderFormOpen] = useState(false);
  const [pathDraft, setPathDraft] = useState('');
  const [addingFolder, setAddingFolder] = useState(false);
  const [folderError, setFolderError] = useState('');
  const [busySourceId, setBusySourceId] = useState(null);
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('tasks-list');

  const knowledgeUrl = getApiUrl('/api/ui/knowledge');

  useEffect(() => {
    if (!realtime.connected) {
      return;
    }
    void dispatch(fetchKnowledge({ knowledgeUrl })).unwrap().catch(() => {});
  }, [dispatch, knowledgeUrl, realtime.connected]);

  const bases = useMemo(() => rawBases.map(fromApiBase), [rawBases]);

  // Clear any test-query and edit state when switching to a different store.
  useEffect(() => {
    setTestResults(null);
    setTestError('');
    setTestLoading(false);
    setPendingSearchRequestId(null);
    setAddingTag(false);
    setTagDraft('');
    setTagError('');
    setFolderFormOpen(false);
    setPathDraft('');
    setFolderError('');
    setBusySourceId(null);
    setEditingName(false);
    setNameDraft('');
    setEditingEmbed(false);
    setEmbedError('');
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
  const sources = selectedBase?.sources || [];

  // Keep the editable connection-string draft in sync with the selected store.
  const selectedConnectionString = selectedBase?.connectionString || '';
  useEffect(() => {
    setConnDraft(selectedConnectionString);
    setConnError('');
    setEditingConn(false);
  }, [selectedBase?.id, selectedConnectionString]);

  // Keep the editable description draft in sync with the selected store.
  const selectedDescription = selectedBase?.description || '';
  useEffect(() => {
    setDescDraft(selectedDescription);
    setDescError('');
    setEditingDesc(false);
  }, [selectedBase?.id, selectedDescription]);

  const putBases = (nextRaw) => dispatch(saveKnowledge({ knowledgeUrl, bases: nextRaw })).unwrap();
  const patchSelected = (patch) => putBases(
    rawBases.map((base) => (base.id === selectedBase?.id
      ? { ...base, ...patch, updated_at: new Date().toISOString() }
      : base)),
  );

  // Open the quick chat prefilled to create a knowledge base — Progenitor edits
  // /state/knowledge.json (managing-knowledge skill) and the new base appears
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
    try {
      if (selectedBaseId === id) {
        navigate('/knowledge', { replace: true });
      }
      await putBases(rawBases.filter((base) => base.id !== id));
    } catch (deleteError) {
      setFolderError(deleteError.message || 'Could not delete the knowledge base.');
    }
  };

  // The search request only acknowledges acceptance. Its matching
  // `knowledge.search.results` event hydrates Redux and resolves this view.
  const runTestQuery = async (event) => {
    event?.preventDefault?.();
    if (!selectedBase || !testQuery.trim() || testLoading) {
      return;
    }
    const requestId = createUuid();
    setTestLoading(true);
    setTestError('');
    setTestResults(null);
    try {
      const url = `${knowledgeUrl}/${encodeURIComponent(selectedBase.id)}/search`
        + `?q=${encodeURIComponent(testQuery.trim())}&limit=${config.limit}`;
      const acknowledgement = await requestAcknowledged(url, {
        requestId,
        errorLabel: 'Knowledge search failed',
      });
      setPendingSearchRequestId(acknowledgement.request_id);
    } catch (searchError) {
      setTestLoading(false);
      setTestError(searchError.message || 'Could not reach the knowledge database.');
    }
  };

  useEffect(() => {
    if (!pendingSearchRequestId || knowledgeSearch.requestId !== pendingSearchRequestId) {
      return;
    }
    const results = config.minScore > 0
      ? knowledgeSearch.results.filter((row) => Number(row.score || 0) >= config.minScore)
      : knowledgeSearch.results;
    setTestResults({ count: results.length, results });
    setTestLoading(false);
    setPendingSearchRequestId(null);
  }, [config.minScore, knowledgeSearch, pendingSearchRequestId]);

  // Persist the full tag list, then let the broadcast refresh the view.
  const saveTags = async (nextTags) => {
    if (!selectedBase || savingTag) {
      return false;
    }
    setSavingTag(true);
    setTagError('');
    try {
      await patchSelected({ tags: nextTags });
      return true;
    } catch (error) {
      setTagError(error.message || 'Failed to update tags.');
      return false;
    } finally {
      setSavingTag(false);
    }
  };

  const addTag = async (event) => {
    event?.preventDefault?.();
    const label = tagDraft.trim();
    if (!label || !selectedBase) {
      return;
    }
    if (selectedBase.tags.some((tag) => tag.toLowerCase() === label.toLowerCase())) {
      setTagDraft('');
      setAddingTag(false);
      return;
    }
    if (await saveTags([...selectedBase.tags, label])) {
      setTagDraft('');
      setAddingTag(false);
    }
  };

  const removeTag = (label) => {
    if (!selectedBase) {
      return;
    }
    saveTags(selectedBase.tags.filter((tag) => tag !== label));
  };

  const startConnEdit = () => {
    setConnDraft(selectedConnectionString);
    setConnError('');
    setEditingConn(true);
  };

  const cancelConnEdit = () => {
    setConnDraft(selectedConnectionString);
    setConnError('');
    setEditingConn(false);
  };

  const saveConnection = async (event) => {
    event?.preventDefault?.();
    if (!selectedBase || savingConn) {
      return;
    }
    setSavingConn(true);
    setConnError('');
    try {
      await patchSelected({ connection_string: connDraft.trim() });
      setEditingConn(false);
    } catch (error) {
      setConnError(error.message || 'Failed to update connection.');
    } finally {
      setSavingConn(false);
    }
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

  const startEmbedEdit = () => {
    setEmbedDraft({
      embeddingModel: selectedBase?.embeddingModel || EMBED_MODELS[0].value,
      dimensions: Number(selectedBase?.dimensions) || 256,
      metric: selectedBase?.metric || 'cosine',
      indexType: selectedBase?.indexType || 'Flat',
    });
    setEmbedError('');
    setEditingEmbed(true);
  };

  const cancelEmbedEdit = () => {
    setEmbedError('');
    setEditingEmbed(false);
  };

  const saveEmbedding = async () => {
    if (!selectedBase || savingEmbed) {
      return;
    }
    setSavingEmbed(true);
    setEmbedError('');
    try {
      await patchSelected({
        embedding_model: embedDraft.embeddingModel,
        dimensions: embedDraft.dimensions,
        metric: embedDraft.metric,
        index_type: embedDraft.indexType,
      });
      setEditingEmbed(false);
    } catch (error) {
      setEmbedError(error.message || 'Failed to update embedding.');
    } finally {
      setSavingEmbed(false);
    }
  };

  const startDescEdit = () => {
    setDescDraft(selectedDescription);
    setDescError('');
    setEditingDesc(true);
  };

  const cancelDescEdit = () => {
    setDescDraft(selectedDescription);
    setDescError('');
    setEditingDesc(false);
  };

  const saveDescription = async (event) => {
    event?.preventDefault?.();
    if (!selectedBase || savingDesc) {
      return;
    }
    setSavingDesc(true);
    setDescError('');
    try {
      await patchSelected({ description: descDraft.trim() });
      setEditingDesc(false);
    } catch (error) {
      setDescError(error.message || 'Failed to update description.');
    } finally {
      setSavingDesc(false);
    }
  };

  // Register a folder (PUT the new source) then index it (walk + embed on the
  // server). Both update the document, so the snapshot refreshes the view.
  const addFolder = async (event) => {
    event?.preventDefault?.();
    const path = pathDraft.trim();
    if (!path || !selectedBase || addingFolder) {
      return;
    }
    setAddingFolder(true);
    setFolderError('');
    const sourceId = generateSourceId();
    try {
      await patchSelected({ sources: [...sources, { id: sourceId, path }] });
      await dispatch(indexKnowledgeSource({ knowledgeUrl, baseId: selectedBase.id, sourceId })).unwrap();
      setPathDraft('');
      setFolderFormOpen(false);
    } catch (error) {
      setFolderError(error.message || 'Could not reach the knowledge database.');
    } finally {
      setAddingFolder(false);
    }
  };

  const reindexSource = async (sourceId) => {
    if (!selectedBase || busySourceId) {
      return;
    }
    setBusySourceId(sourceId);
    setFolderError('');
    try {
      await dispatch(indexKnowledgeSource({ knowledgeUrl, baseId: selectedBase.id, sourceId })).unwrap();
    } catch (error) {
      setFolderError(error.message || 'Re-index failed.');
    } finally {
      setBusySourceId(null);
    }
  };

  const removeSource = async (sourceId) => {
    if (!selectedBase || busySourceId) {
      return;
    }
    setBusySourceId(sourceId);
    setFolderError('');
    try {
      await patchSelected({ sources: sources.filter((source) => source.id !== sourceId) });
    } catch (error) {
      setFolderError(error.message || 'Could not reach the knowledge database.');
    } finally {
      setBusySourceId(null);
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
              <PaneListSearch value={searchQuery} placeholder="Search vector stores" onChange={setSearchQuery} />
              <PaneListSortButton
                title="Sort knowledge bases"
                options={KB_SORTS}
                value={sortMode}
                onSelect={setSortMode}
              />
            </PaneListToolbar>

            {knowledgeState.error ? <div className="task-alert">{knowledgeState.error}</div> : null}
            {!visibleBases.length && loaded ? (
              <PaneEmptyState>{bases.length ? 'No matching knowledge bases.' : 'No knowledge bases yet — create one with “+ New”.'}</PaneEmptyState>
            ) : null}
            {groups.length ? (
              <PaneListVirtualized
                groups={groups.map((group) => ({ ...group, items: group.bases }))}
                getItemKey={(base) => base.id}
                estimatedItemHeight={72}
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
                <span key="model" className="muted">{selectedBase.embeddingModel}</span>,
                <span key="dims" className="muted">{formatCount(selectedBase.dimensions)} dims</span>,
                <span key="index" className="muted">{selectedBase.indexType} · {selectedBase.metric}</span>,
                <span key="table" className="muted">{selectedBase.database}.{selectedBase.table}</span>,
              ]}
              tabs={['Overview', 'Sources', 'Configure', 'Query']}
              activeTab={detailTab}
              onTabChange={setDetailTab}
              tabLabel="Knowledge base tabs"
            />

            <div className="task-detail__body">
              {detailTab === 'Overview' ? (
                <>
                  <div className="agents-metrics">
                    <MetricCard label="Documents" value={formatCount(selectedBase.documents)} detail="stored passages" />
                    <MetricCard label="Vectors" value={formatCount(selectedBase.documents)} detail={`${formatCount(selectedBase.dimensions)} dims`} />
                    <MetricCard label="Agents" value={formatCount(selectedBase.pluggedInto.length)} detail="connected" />
                    <MetricCard label="Last sync" value={formatRelativeTime(selectedBase.lastSynced)} detail={statusMeta(selectedBase.status).label.toLowerCase()} />
                  </div>

                  <section className="agents-section schedules-summary">
                    <header className="agents-section__head">
                      <h2>Description</h2>
                      <div>
                        {editingDesc ? (
                          <>
                            <button type="button" className="repo-btn repo-btn--ghost" onClick={cancelDescEdit} disabled={savingDesc}>
                              Cancel
                            </button>
                            <button
                              type="button"
                              className="repo-btn"
                              onClick={saveDescription}
                              disabled={savingDesc || descDraft.trim() === selectedDescription}
                            >
                              {savingDesc ? 'Saving…' : 'Save'}
                            </button>
                          </>
                        ) : (
                          <button type="button" className="repo-btn repo-btn--ghost" onClick={startDescEdit}>
                            Edit
                          </button>
                        )}
                      </div>
                    </header>
                    {editingDesc ? (
                      <textarea
                        className="kb-source-form__textarea"
                        rows={4}
                        placeholder="Describe what this knowledge base contains…"
                        value={descDraft}
                        onChange={(event) => setDescDraft(event.target.value)}
                      />
                    ) : (
                      <p>{selectedBase.description}</p>
                    )}
                    {descError ? <div className="task-alert">{descError}</div> : null}
                  </section>

                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Connected agents <span>{selectedBase.pluggedInto.length} agent{selectedBase.pluggedInto.length === 1 ? '' : 's'}</span></h2>
                    </header>
                    {selectedBase.pluggedInto.length ? (
                      <div className="agents-connection-list">
                        {selectedBase.pluggedInto.map((agent) => (
                          <InfoRow key={agent} title={agent} sub="Retrieval source via knowledge_search" chip="connected" />
                        ))}
                      </div>
                    ) : <PaneEmptyState>Not attached to any agent yet.</PaneEmptyState>}
                  </section>

                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Tags <span>{selectedBase.tags.length}</span></h2>
                      <div>
                        <button
                          type="button"
                          className="repo-btn repo-btn--ghost"
                          onClick={() => { setAddingTag((open) => !open); setTagError(''); setTagDraft(''); }}
                        >
                          {addingTag ? 'Cancel' : '+ Add tag'}
                        </button>
                      </div>
                    </header>
                    {addingTag ? (
                      <form className="kb-tag-form" onSubmit={addTag}>
                        <input
                          className="kb-tag-form__input"
                          placeholder="Tag name (e.g. priority, internal)"
                          value={tagDraft}
                          onChange={(event) => setTagDraft(event.target.value)}
                        />
                        <button type="submit" className="repo-btn" disabled={savingTag || !tagDraft.trim()}>
                          {savingTag ? 'Adding…' : 'Add'}
                        </button>
                      </form>
                    ) : null}
                    {tagError ? <div className="task-alert">{tagError}</div> : null}
                    {selectedBase.tags.length ? (
                      <div className="agents-chip-row">
                        {selectedBase.tags.map((tag) => (
                          <span key={tag} className="agents-chip agents-chip--muted kb-tag">
                            {tag}
                            <button
                              type="button"
                              className="kb-tag__remove"
                              aria-label={`Remove tag ${tag}`}
                              disabled={savingTag}
                              onClick={() => removeTag(tag)}
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                    ) : <div className="task-meta-sub">No tags yet.</div>}
                  </section>
                </>
              ) : null}

              {detailTab === 'Sources' ? (
                <>
                <section className="agents-section">
                  <header className="agents-section__head">
                    <h2>Folder sources <span>{sources.length} folder{sources.length === 1 ? '' : 's'}</span></h2>
                    <div>
                      <button
                        type="button"
                        className="repo-btn repo-btn--ghost"
                        onClick={() => { setFolderFormOpen((open) => !open); setFolderError(''); }}
                      >
                        {folderFormOpen ? 'Cancel' : '+ Add folder'}
                      </button>
                    </div>
                  </header>

                  {folderFormOpen ? (
                    <form className="kb-source-form" onSubmit={addFolder}>
                      <input
                        className="kb-source-form__input"
                        placeholder="/absolute/path/to/folder"
                        value={pathDraft}
                        onChange={(event) => setPathDraft(event.target.value)}
                      />
                      <div className="kb-source-form__actions">
                        <button type="submit" className="repo-btn" disabled={addingFolder || !pathDraft.trim()}>
                          {addingFolder ? 'Indexing…' : 'Add & index'}
                        </button>
                      </div>
                    </form>
                  ) : null}
                  {folderError ? <div className="task-alert">{folderError}</div> : null}

                  {sources.length ? (
                    <div className="agents-mini-list">
                      {sources.map((source) => {
                        const busy = busySourceId === source.id;
                        return (
                          <div key={source.id} className="agents-mini-row kb-src-row kb-noicon">
                            <div>
                              <strong>{source.path}</strong>
                              <p>
                                {formatCount(source.document_count || 0)} docs
                                {source.last_indexed_at ? ` · indexed ${formatRelativeTime(source.last_indexed_at)}` : ''}
                                {source.error ? ` · ${source.error}` : ''}
                              </p>
                            </div>
                            <div className="kb-src-row__actions">
                              <span className={`agents-chip agents-chip--${SOURCE_CHIP_TONE[source.status] || 'muted'}`}>{source.status}</span>
                              <button type="button" className="repo-btn repo-btn--ghost" disabled={busy} onClick={() => reindexSource(source.id)}>
                                {busy ? 'Indexing…' : 'Re-index'}
                              </button>
                              <button type="button" className="repo-btn repo-btn--ghost" disabled={busy} onClick={() => removeSource(source.id)}>
                                Remove
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : <PaneEmptyState>No folder sources yet — use “+ Add folder” to index one.</PaneEmptyState>}
                  <p className="task-meta-sub">
                    Folders on the server whose text files are read, embedded, and indexed into this store.
                    Re-index to pick up changes; files land in a collection named after the folder.
                  </p>
                </section>

                <section className="agents-section">
                  <header className="agents-section__head">
                    <h2>Collections <span>{selectedBase.collections.length} collection{selectedBase.collections.length === 1 ? '' : 's'}</span></h2>
                  </header>
                  {selectedBase.collections.length ? (
                    <div className="agents-mini-list">
                      {selectedBase.collections.map((collection) => (
                        <div key={collection.name} className="agents-mini-row kb-noicon">
                          <div>
                            <strong>{collection.name}</strong>
                            <p>synced {formatRelativeTime(collection.updated)}</p>
                          </div>
                          <span className="agents-chip agents-chip--muted">collection</span>
                        </div>
                      ))}
                    </div>
                  ) : <PaneEmptyState>No collections yet — add a folder source to populate this store.</PaneEmptyState>}
                  <p className="task-meta-sub">Named groupings of the documents embedded into this store (from folder sources).</p>
                </section>
                </>
              ) : null}

              {detailTab === 'Configure' ? (
                <>
                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Connection string</h2>
                      <div>
                        {editingConn ? (
                          <>
                            <button type="button" className="repo-btn repo-btn--ghost" onClick={cancelConnEdit} disabled={savingConn}>
                              Cancel
                            </button>
                            <button
                              type="button"
                              className="repo-btn"
                              onClick={saveConnection}
                              disabled={savingConn || connDraft.trim() === selectedConnectionString}
                            >
                              {savingConn ? 'Saving…' : 'Save'}
                            </button>
                          </>
                        ) : (
                          <button type="button" className="repo-btn repo-btn--ghost" onClick={startConnEdit}>
                            Edit
                          </button>
                        )}
                      </div>
                    </header>
                    {editingConn ? (
                      <form className="kb-conn" onSubmit={saveConnection}>
                        <input
                          className="kb-conn__input"
                          placeholder="postgresql://user@host:port/database"
                          value={connDraft}
                          onChange={(event) => setConnDraft(event.target.value)}
                        />
                      </form>
                    ) : (
                      <div className="agents-connection-list">
                        <InfoRow
                          title={selectedBase.connectionString}
                          sub="pgvector database"
                          chip="pgvector"
                        />
                      </div>
                    )}
                    {connError ? <div className="task-alert">{connError}</div> : null}
                  </section>

                  <div className="kb-two-col">
                    <section className="agents-section">
                      <header className="agents-section__head"><h2>Search settings</h2></header>
                      <div className="kb-config">
                        <div className="kb-config__row">
                          <span className="kb-config__label">
                            Max results
                            <span className="kb-config__hint">Passages returned per query (1–20)</span>
                          </span>
                          <NumberStepper
                            value={config.limit}
                            min={1}
                            max={20}
                            step={1}
                            ariaLabel="max results"
                            onChange={(next) => setConfig((current) => ({ ...current, limit: next }))}
                          />
                        </div>
                        <div className="kb-config__row">
                          <span className="kb-config__label">
                            Minimum score
                            <span className="kb-config__hint">Hide results below this similarity (0–1)</span>
                          </span>
                          <NumberStepper
                            value={config.minScore}
                            min={0}
                            max={1}
                            step={0.05}
                            ariaLabel="minimum score"
                            onChange={(next) => setConfig((current) => ({ ...current, minScore: next }))}
                          />
                        </div>
                      </div>
                      <p className="task-meta-sub">
                        Applied to the Query tab — the same knobs the agent&apos;s <code>knowledge_search</code> tool can pass.
                      </p>
                    </section>

                    <section className="agents-section">
                      <header className="agents-section__head">
                        <h2>Embedding</h2>
                        <div>
                          {editingEmbed ? (
                            <>
                              <button type="button" className="repo-btn repo-btn--ghost" onClick={cancelEmbedEdit} disabled={savingEmbed}>
                                Cancel
                              </button>
                              <button type="button" className="repo-btn" onClick={saveEmbedding} disabled={savingEmbed}>
                                {savingEmbed ? 'Saving…' : 'Save'}
                              </button>
                            </>
                          ) : (
                            <button type="button" className="repo-btn repo-btn--ghost" onClick={startEmbedEdit}>
                              Edit
                            </button>
                          )}
                        </div>
                      </header>
                      {editingEmbed ? (
                        <div className="kb-config">
                          <div className="kb-config__row">
                            <span className="kb-config__label">
                              Model
                              <span className="kb-config__hint">Embedding model</span>
                            </span>
                            <select
                              className="app-select"
                              value={embedDraft.embeddingModel}
                              onChange={(event) => {
                                const picked = EMBED_MODELS.find((m) => m.value === event.target.value);
                                setEmbedDraft((draft) => ({
                                  ...draft,
                                  embeddingModel: event.target.value,
                                  dimensions: picked ? picked.dimensions : draft.dimensions,
                                }));
                              }}
                            >
                              {EMBED_MODELS.map((m) => <option key={m.value} value={m.value}>{m.value}</option>)}
                            </select>
                          </div>
                          <div className="kb-config__row">
                            <span className="kb-config__label">
                              Dimensions
                              <span className="kb-config__hint">Vector size (1–4096)</span>
                            </span>
                            <NumberStepper
                              value={embedDraft.dimensions}
                              min={1}
                              max={4096}
                              step={1}
                              ariaLabel="dimensions"
                              onChange={(next) => setEmbedDraft((draft) => ({ ...draft, dimensions: next }))}
                            />
                          </div>
                          <div className="kb-config__row">
                            <span className="kb-config__label">
                              Metric
                              <span className="kb-config__hint">Similarity</span>
                            </span>
                            <select
                              className="app-select"
                              value={embedDraft.metric}
                              onChange={(event) => setEmbedDraft((draft) => ({ ...draft, metric: event.target.value }))}
                            >
                              {EMBED_METRICS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                            </select>
                          </div>
                          <div className="kb-config__row">
                            <span className="kb-config__label">
                              Index
                              <span className="kb-config__hint">Index type</span>
                            </span>
                            <select
                              className="app-select"
                              value={embedDraft.indexType}
                              onChange={(event) => setEmbedDraft((draft) => ({ ...draft, indexType: event.target.value }))}
                            >
                              {EMBED_INDEXES.map((m) => <option key={m} value={m}>{m}</option>)}
                            </select>
                          </div>
                        </div>
                      ) : (
                        <div className="agents-connection-list">
                          <InfoRow
                            title={selectedBase.embeddingModel}
                            sub="Embedding model"
                            chip={`${formatCount(selectedBase.dimensions)} dims`}
                          />
                          <InfoRow
                            title={selectedBase.metric}
                            sub="Similarity metric"
                            chip={selectedBase.indexType}
                          />
                        </div>
                      )}
                      {embedError ? <div className="task-alert">{embedError}</div> : null}
                    </section>
                  </div>

                  <section className="agents-section">
                    <header className="agents-section__head"><h2>Postgres + pgvector</h2></header>
                    <div className="agents-connection-list">
                      <InfoRow
                        title={`${selectedBase.database}.${selectedBase.table}`}
                        sub="Table"
                        chip="pgvector"
                      />
                      <InfoRow
                        title={`${selectedBase.indexType} index`}
                        sub={`${selectedBase.metric} similarity`}
                        chip={`${formatCount(selectedBase.dimensions)} dims`}
                      />
                    </div>
                  </section>
                </>
              ) : null}

              {detailTab === 'Query' ? (
                <section className="agents-section">
                  <header className="agents-section__head"><h2>Test query</h2></header>
                    <p className="task-meta-sub">
                      Run a live vector search against {selectedBase.database}.{selectedBase.table} — the same path the
                      agent&apos;s <code>knowledge_search</code> tool uses. Returns up to {config.limit} result
                      {config.limit === 1 ? '' : 's'}{config.minScore > 0 ? ` scoring ≥ ${config.minScore}` : ''} (set in Configure).
                    </p>
                    <form className="kb-test" onSubmit={runTestQuery}>
                      <input
                        type="text"
                        className="kb-test__input"
                        placeholder="Ask this knowledge base something…"
                        value={testQuery}
                        onChange={(event) => setTestQuery(event.target.value)}
                      />
                      <button type="submit" className="kb-test__run" disabled={testLoading || !testQuery.trim()}>
                        {testLoading ? 'Searching…' : 'Run query'}
                      </button>
                    </form>
                    {testError ? <div className="task-alert">{testError}</div> : null}
                    {testResults ? (
                      testResults.results.length ? (
                        <div className="kb-test__results">
                          <div className="task-meta-sub">{formatCount(testResults.count)} result{testResults.count === 1 ? '' : 's'}</div>
                          {testResults.results.map((row, index) => (
                            <div key={`${row.title || 'row'}-${index}`} className="kb-test__result">
                              <div className="kb-test__result-head">
                                <span className="kb-test__result-title">{row.title || row.collection || 'Untitled'}</span>
                                <span className="kb-test__score">score {Number(row.score || 0).toFixed(3)}</span>
                              </div>
                              <p className="kb-test__result-body">{row.content}</p>
                            </div>
                          ))}
                        </div>
                      ) : <PaneEmptyState>No matching passages.</PaneEmptyState>
                    ) : null}
                  </section>
              ) : null}
            </div>
          </>
        ) : (
          <PaneEmptyState detail>Select a knowledge base to inspect details.</PaneEmptyState>
        )}
      </DetailPane>
    </PageShell>
  );
}
