import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useConfig } from '../context/ConfigContext';
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
import NumberStepper from '../components/common/NumberStepper';
import {
  KB_FILTERS,
  KB_SORTS,
  formatBytes,
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
const EMBED_METRICS = ['cosine', 'inner product', 'euclidean'];
const EMBED_INDEXES = ['Flat', 'IVFFlat', 'HNSW'];

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
  const { getApiUrl } = useConfig();
  const [bases, setBases] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [activeFilter, setActiveFilter] = useState('All');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortMode, setSortMode] = useState('Status');
  const [selectedBaseId, setSelectedBaseId] = useState(null);
  const [detailTab, setDetailTab] = useState('Overview');
  const [creating, setCreating] = useState(false);
  const [testQuery, setTestQuery] = useState('');
  const [testResults, setTestResults] = useState(null);
  const [testLoading, setTestLoading] = useState(false);
  const [testError, setTestError] = useState('');
  const [config, setConfig] = useState({ limit: 5, minScore: 0 });
  const [addingSource, setAddingSource] = useState(false);
  const [sourceDraft, setSourceDraft] = useState({ name: '', title: '', content: '' });
  const [savingSource, setSavingSource] = useState(false);
  const [sourceError, setSourceError] = useState('');
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
  const [sources, setSources] = useState([]);
  const [folderFormOpen, setFolderFormOpen] = useState(false);
  const [pathDraft, setPathDraft] = useState('');
  const [addingFolder, setAddingFolder] = useState(false);
  const [folderError, setFolderError] = useState('');
  const [busySourceId, setBusySourceId] = useState(null);
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('tasks-list');

  const knowledgeUrl = getApiUrl('/api/ui/knowledge');

  // Load the live pgvector-backed bases. No mock fallback — the page reflects
  // exactly what exists in Postgres.
  const loadBases = useCallback(async (selectId = null) => {
    try {
      const response = await fetch(knowledgeUrl);
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      const apiBases = Array.isArray(payload.bases) ? payload.bases.map(fromApiBase) : [];
      setBases(apiBases);
      setSelectedBaseId((current) => {
        if (selectId) {
          return selectId;
        }
        if (current && apiBases.some((base) => base.id === current)) {
          return current;
        }
        return apiBases.length ? apiBases[0].id : null;
      });
    } catch {
      // Leave the current list in place on a transient network error.
    } finally {
      setLoaded(true);
    }
  }, [knowledgeUrl]);

  useEffect(() => {
    loadBases();
  }, [loadBases]);

  // Folder sources for the selected store (loaded per base, shown on the Sources tab).
  const loadSources = useCallback(async (baseId) => {
    if (!baseId) {
      setSources([]);
      return;
    }
    try {
      const response = await fetch(`${knowledgeUrl}/${encodeURIComponent(baseId)}/sources`);
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      setSources(Array.isArray(payload.sources) ? payload.sources : []);
    } catch {
      // Leave the current list on a transient error.
    }
  }, [knowledgeUrl]);

  // Clear any test-query and add-source state when switching to a different store.
  useEffect(() => {
    setTestResults(null);
    setTestError('');
    setAddingSource(false);
    setSourceDraft({ name: '', title: '', content: '' });
    setSourceError('');
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
    () => bases.find((base) => base.id === selectedBaseId) || visibleBases[0] || null,
    [bases, selectedBaseId, visibleBases],
  );

  const connectedCount = bases.filter((base) => base.status === 'connected').length;

  // Keep the editable connection-string draft in sync with the selected store.
  const selectedConnectionString = selectedBase?.connectionString || '';
  useEffect(() => {
    setConnDraft(selectedConnectionString);
    setConnError('');
    setEditingConn(false);
  }, [selectedConnectionString, selectedBaseId]);

  // Keep the editable description draft in sync with the selected store.
  const selectedDescription = selectedBase?.description || '';
  useEffect(() => {
    setDescDraft(selectedDescription);
    setDescError('');
    setEditingDesc(false);
  }, [selectedDescription, selectedBaseId]);

  // Load folder sources whenever the selected store changes.
  const selectedBaseRef = selectedBase?.id || null;
  useEffect(() => {
    loadSources(selectedBaseRef);
  }, [selectedBaseRef, loadSources]);

  // Create an empty pgvector store in Postgres, then refresh the list and select
  // it. Populate it via the Collections / Sources tabs.
  const addBase = async () => {
    if (creating) {
      return;
    }
    setCreating(true);
    // Next free number, based on the highest existing "Knowledge base N".
    const used = bases
      .map((base) => /knowledge base (\d+)$/i.exec(base.name))
      .filter(Boolean)
      .map((match) => Number(match[1]));
    const index = (used.length ? Math.max(...used) : 0) + 1;
    try {
      const response = await fetch(knowledgeUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: `Knowledge base ${index}` }),
      });
      if (response.ok) {
        const payload = await response.json();
        const created = payload.base ? fromApiBase(payload.base) : null;
        setActiveFilter('All');
        setSearchQuery('');
        await loadBases(created?.id || null);
      }
    } catch {
      // Surface nothing destructive; the list simply won't change on failure.
    } finally {
      setCreating(false);
    }
  };

  const deleteSelected = async () => {
    if (!selectedBase) {
      return;
    }
    const id = selectedBase.id;
    if (selectedBase.persisted) {
      try {
        await fetch(`${knowledgeUrl}/${encodeURIComponent(id)}`, { method: 'DELETE' });
      } catch {
        // ignore; fall through to optimistic local removal
      }
    }
    setBases((current) => current.filter((base) => base.id !== id));
    setSelectedBaseId(null);
    loadBases();
  };

  // Run a live pgvector search against this store via the backend, which queries
  // Postgres directly. Lets you sanity-check what the agent's knowledge_search
  // tool would return.
  const runTestQuery = async (event) => {
    event?.preventDefault?.();
    if (!selectedBase || !testQuery.trim() || testLoading) {
      return;
    }
    setTestLoading(true);
    setTestError('');
    try {
      const url = `${knowledgeUrl}/${encodeURIComponent(selectedBase.id)}/search`
        + `?q=${encodeURIComponent(testQuery.trim())}&limit=${config.limit}`;
      const response = await fetch(url);
      if (!response.ok) {
        setTestResults(null);
        setTestError(response.status === 404
          ? 'This store is not persisted in Postgres yet — create one with "+ New" to run a live query.'
          : `Search failed (${response.status}).`);
        return;
      }
      const payload = await response.json();
      let results = Array.isArray(payload.results) ? payload.results : [];
      if (config.minScore > 0) {
        results = results.filter((row) => Number(row.score || 0) >= config.minScore);
      }
      setTestResults({ count: results.length, results });
    } catch {
      setTestResults(null);
      setTestError('Could not reach the knowledge database.');
    } finally {
      setTestLoading(false);
    }
  };

  // Add a source: a named grouping (collection) the pasted document is embedded
  // under. Hits the backend ingest endpoint, so the new passage is immediately
  // searchable by the agent's knowledge_search tool.
  const addSource = async (event) => {
    event?.preventDefault?.();
    if (!selectedBase || savingSource) {
      return;
    }
    const content = sourceDraft.content.trim();
    if (!content) {
      setSourceError('Add some document text for the source.');
      return;
    }
    const collection = sourceDraft.name.trim() || 'default';
    setSavingSource(true);
    setSourceError('');
    try {
      const response = await fetch(`${knowledgeUrl}/${encodeURIComponent(selectedBase.id)}/documents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          documents: [{ collection, title: sourceDraft.title.trim(), content }],
        }),
      });
      if (!response.ok) {
        setSourceError(response.status === 404
          ? 'This store is not persisted in Postgres yet.'
          : `Failed to add source (${response.status}).`);
        return;
      }
      setSourceDraft({ name: '', title: '', content: '' });
      setAddingSource(false);
      await loadBases(selectedBase.id);
    } catch {
      setSourceError('Could not reach the knowledge database.');
    } finally {
      setSavingSource(false);
    }
  };

  // Persist the full tag list to Postgres, then refresh from the server.
  const saveTags = async (nextTags) => {
    if (!selectedBase || savingTag) {
      return false;
    }
    setSavingTag(true);
    setTagError('');
    try {
      const response = await fetch(`${knowledgeUrl}/${encodeURIComponent(selectedBase.id)}/tags`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tags: nextTags }),
      });
      if (!response.ok) {
        setTagError(response.status === 404
          ? 'This store is not persisted in Postgres yet.'
          : `Failed to update tags (${response.status}).`);
        return false;
      }
      await loadBases(selectedBase.id);
      return true;
    } catch {
      setTagError('Could not reach the knowledge database.');
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

  // Persist the (metadata) connection string for this store.
  const saveConnection = async (event) => {
    event?.preventDefault?.();
    if (!selectedBase || savingConn) {
      return;
    }
    setSavingConn(true);
    setConnError('');
    try {
      const response = await fetch(`${knowledgeUrl}/${encodeURIComponent(selectedBase.id)}/connection`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connection_string: connDraft.trim() }),
      });
      if (!response.ok) {
        setConnError(response.status === 404
          ? 'This store is not persisted in Postgres yet.'
          : `Failed to update connection (${response.status}).`);
        return;
      }
      setEditingConn(false);
      await loadBases(selectedBase.id);
    } catch {
      setConnError('Could not reach the knowledge database.');
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
      const response = await fetch(`${knowledgeUrl}/${encodeURIComponent(selectedBase.id)}/name`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: next }),
      });
      if (response.ok) {
        setEditingName(false);
        await loadBases(selectedBase.id);
      }
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
      const response = await fetch(`${knowledgeUrl}/${encodeURIComponent(selectedBase.id)}/embedding`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          embedding_model: embedDraft.embeddingModel,
          dimensions: embedDraft.dimensions,
          metric: embedDraft.metric,
          index_type: embedDraft.indexType,
        }),
      });
      if (!response.ok) {
        setEmbedError(response.status === 404
          ? 'This store is not persisted in Postgres yet.'
          : `Failed to update embedding (${response.status}).`);
        return;
      }
      setEditingEmbed(false);
      await loadBases(selectedBase.id);
    } catch {
      setEmbedError('Could not reach the knowledge database.');
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
      const response = await fetch(`${knowledgeUrl}/${encodeURIComponent(selectedBase.id)}/description`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: descDraft.trim() }),
      });
      if (!response.ok) {
        setDescError(response.status === 404
          ? 'This store is not persisted in Postgres yet.'
          : `Failed to update description (${response.status}).`);
        return;
      }
      setEditingDesc(false);
      await loadBases(selectedBase.id);
    } catch {
      setDescError('Could not reach the knowledge database.');
    } finally {
      setSavingDesc(false);
    }
  };

  // Register a folder and index its files immediately. Refreshes both the source
  // list and the base (its document/collection counts change).
  const addFolder = async (event) => {
    event?.preventDefault?.();
    const path = pathDraft.trim();
    if (!path || !selectedBase || addingFolder) {
      return;
    }
    setAddingFolder(true);
    setFolderError('');
    try {
      const response = await fetch(`${knowledgeUrl}/${encodeURIComponent(selectedBase.id)}/sources`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      if (!response.ok) {
        setFolderError(response.status === 404
          ? 'This store is not persisted in Postgres yet.'
          : `Failed to add folder (${response.status}).`);
        return;
      }
      setPathDraft('');
      setFolderFormOpen(false);
      await Promise.all([loadSources(selectedBase.id), loadBases(selectedBase.id)]);
    } catch {
      setFolderError('Could not reach the knowledge database.');
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
      const response = await fetch(
        `${knowledgeUrl}/${encodeURIComponent(selectedBase.id)}/sources/${encodeURIComponent(sourceId)}/index`,
        { method: 'POST' },
      );
      if (!response.ok) {
        setFolderError(`Re-index failed (${response.status}).`);
        return;
      }
      await Promise.all([loadSources(selectedBase.id), loadBases(selectedBase.id)]);
    } catch {
      setFolderError('Could not reach the knowledge database.');
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
      await fetch(
        `${knowledgeUrl}/${encodeURIComponent(selectedBase.id)}/sources/${encodeURIComponent(sourceId)}`,
        { method: 'DELETE' },
      );
      await Promise.all([loadSources(selectedBase.id), loadBases(selectedBase.id)]);
    } catch {
      setFolderError('Could not reach the knowledge database.');
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
              onAction={addBase}
              actionDisabled={creating}
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

            <PaneListScroll>
              {!visibleBases.length && loaded ? (
                <div className="task-empty">
                  {bases.length ? 'No matching knowledge bases.' : 'No knowledge bases yet — create one with “+ New”.'}
                </div>
              ) : null}
              {groups.map((group) => (
                <PaneListGroup key={group.key} title={group.label} count={group.bases.length}>
                  {group.bases.map((base) => (
                    <KnowledgeCard
                      key={base.id}
                      base={base}
                      selected={selectedBase?.id === base.id}
                      onSelect={setSelectedBaseId}
                    />
                  ))}
                </PaneListGroup>
              ))}
            </PaneListScroll>

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
                  {selectedBase.lastError ? (
                    <div className="agents-empty-state">{selectedBase.lastError}</div>
                  ) : null}

                  <div className="agents-metrics">
                    <MetricCard label="Documents" value={formatCount(selectedBase.documents)} detail="stored passages" />
                    <MetricCard label="Vectors" value={formatCount(selectedBase.chunks)} detail={`${formatCount(selectedBase.dimensions)} dims`} />
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
                    ) : <div className="agents-empty-state">Not attached to any agent yet.</div>}
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
                  ) : <div className="agents-empty-state">No folder sources yet — use “+ Add folder” to index one.</div>}
                  <p className="task-meta-sub">
                    Folders on the server whose text files are read, embedded, and indexed into this store.
                    Re-index to pick up changes; files land in a collection named after the folder.
                  </p>
                </section>

                <section className="agents-section">
                  <header className="agents-section__head">
                    <h2>Collections <span>{selectedBase.collections.length} collection{selectedBase.collections.length === 1 ? '' : 's'}</span></h2>
                    <div>
                      <button
                        type="button"
                        className="repo-btn repo-btn--ghost"
                        onClick={() => { setAddingSource((open) => !open); setSourceError(''); }}
                      >
                        {addingSource ? 'Cancel' : '+ Add collection'}
                      </button>
                    </div>
                  </header>

                  {addingSource ? (
                    <form className="kb-source-form" onSubmit={addSource}>
                      <input
                        className="kb-source-form__input"
                        placeholder="Collection name (e.g. handbook, faq, notes)"
                        value={sourceDraft.name}
                        onChange={(event) => setSourceDraft((draft) => ({ ...draft, name: event.target.value }))}
                      />
                      <input
                        className="kb-source-form__input"
                        placeholder="Document title (optional)"
                        value={sourceDraft.title}
                        onChange={(event) => setSourceDraft((draft) => ({ ...draft, title: event.target.value }))}
                      />
                      <textarea
                        className="kb-source-form__textarea"
                        rows={4}
                        placeholder="Paste the document text to embed into this collection…"
                        value={sourceDraft.content}
                        onChange={(event) => setSourceDraft((draft) => ({ ...draft, content: event.target.value }))}
                      />
                      {sourceError ? <div className="task-alert">{sourceError}</div> : null}
                      <div className="kb-source-form__actions">
                        <button type="submit" className="repo-btn" disabled={savingSource || !sourceDraft.content.trim()}>
                          {savingSource ? 'Adding…' : 'Add collection'}
                        </button>
                      </div>
                    </form>
                  ) : null}

                  {selectedBase.collections.length ? (
                    <div className="agents-mini-list">
                      {selectedBase.collections.map((collection) => (
                        <div key={collection.name} className="agents-mini-row kb-noicon">
                          <div>
                            <strong>{collection.name}</strong>
                            <p>synced {formatRelativeTime(collection.updated)}</p>
                          </div>
                          {collection.chunks > 0 ? (
                            <span className="agents-chip agents-chip--info">{formatCount(collection.chunks)} vectors</span>
                          ) : <span className="agents-chip agents-chip--muted">collection</span>}
                        </div>
                      ))}
                    </div>
                  ) : <div className="agents-empty-state">No collections yet — add one to start populating this store.</div>}
                  <p className="task-meta-sub">Named groupings of the documents embedded into this store (including those from folder sources).</p>
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
                              {EMBED_METRICS.map((m) => <option key={m} value={m}>{m}</option>)}
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
                      <InfoRow
                        title={formatBytes(selectedBase.sizeBytes)}
                        sub={`On disk · owner ${selectedBase.owner}`}
                        chip="storage"
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
                      ) : <div className="agents-empty-state">No matching passages.</div>
                    ) : null}
                  </section>
              ) : null}
            </div>
          </>
        ) : (
          <div className="task-detail__empty">Select a knowledge base to inspect details.</div>
        )}
      </DetailPane>
    </PageShell>
  );
}
