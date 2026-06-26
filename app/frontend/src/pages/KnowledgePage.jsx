import { useMemo, useState } from 'react';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import DetailHeader, {
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  PlayActionIcon,
  SaveActionIcon,
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
import {
  KB_FILTERS,
  KB_SORTS,
  formatBytes,
  formatCount,
  formatRelativeTime,
  groupBases,
  statusMeta,
} from './knowledgePageModel';

// Seed Postgres (pgvector) knowledge bases. Each one is a vector store that can
// be plugged into a Progenitor agent for retrieval-augmented answers. The shape
// mirrors what a `/api/ui/knowledge` snapshot would return, so swapping the seed
// for a live fetch later is a drop-in change.
const KNOWLEDGE_BASES = [
  {
    id: 'architecture',
    name: 'Progenitor architecture',
    status: 'connected',
    scope: 'Shared',
    owner: 'Builder',
    color: '#2f7dff',
    description:
      'Embeddings of the runtime, graph nodes, IPC protocol and node-factory '
      + 'patterns. Lets agents ground answers about how Progenitor is wired '
      + 'before they touch code.',
    embeddingModel: 'text-embedding-3-large',
    dimensions: 3072,
    indexType: 'HNSW',
    metric: 'cosine',
    host: '127.0.0.1:15433',
    database: 'progenitor',
    table: 'kb_architecture',
    documents: 412,
    chunks: 5840,
    sizeBytes: 92 * 1024 * 1024,
    lastSynced: '2026-06-27T06:10:00Z',
    tags: ['runtime', 'design', 'langgraph'],
    collections: [
      { name: 'graph_nodes', chunks: 1820, updated: '2026-06-27T06:10:00Z' },
      { name: 'ipc_protocol', chunks: 940, updated: '2026-06-26T22:00:00Z' },
      { name: 'state_models', chunks: 1310, updated: '2026-06-27T05:00:00Z' },
      { name: 'tool_factories', chunks: 1770, updated: '2026-06-27T04:30:00Z' },
    ],
    pluggedInto: ['Progenitor', 'Builder Agent'],
  },
  {
    id: 'runbooks',
    name: 'Agent runbooks',
    status: 'connected',
    scope: 'Shared',
    owner: 'Observer',
    color: '#9b7cff',
    description:
      'Operational playbooks for the A2A fleet: how to restart the builder, '
      + 'rotate keys, and triage failed cycles. Retrieved by the Observe agent '
      + 'during incidents.',
    embeddingModel: 'text-embedding-3-large',
    dimensions: 3072,
    indexType: 'HNSW',
    metric: 'cosine',
    host: '127.0.0.1:15433',
    database: 'progenitor',
    table: 'kb_runbooks',
    documents: 142,
    chunks: 1880,
    sizeBytes: 28 * 1024 * 1024,
    lastSynced: '2026-06-26T18:40:00Z',
    tags: ['ops', 'incident', 'a2a'],
    collections: [
      { name: 'restart_procedures', chunks: 620, updated: '2026-06-26T18:40:00Z' },
      { name: 'key_rotation', chunks: 410, updated: '2026-06-25T12:00:00Z' },
      { name: 'cycle_triage', chunks: 850, updated: '2026-06-26T09:00:00Z' },
    ],
    pluggedInto: ['Observe Agent'],
  },
  {
    id: 'mcp',
    name: 'MCP & tooling reference',
    status: 'syncing',
    scope: 'Shared',
    owner: 'Observer',
    color: '#20b486',
    description:
      'Schemas and usage notes for every registered MCP server and runtime '
      + 'tool. Currently re-embedding after the latest tool discovery pass.',
    embeddingModel: 'text-embedding-3-small',
    dimensions: 1536,
    indexType: 'IVFFlat',
    metric: 'cosine',
    host: '127.0.0.1:15433',
    database: 'progenitor',
    table: 'kb_mcp',
    documents: 263,
    chunks: 3120,
    sizeBytes: 41 * 1024 * 1024,
    lastSynced: '2026-06-27T08:02:00Z',
    tags: ['mcp', 'tools', 'schema'],
    collections: [
      { name: 'tool_schemas', chunks: 1640, updated: '2026-06-27T08:02:00Z' },
      { name: 'server_manifests', chunks: 720, updated: '2026-06-27T07:50:00Z' },
      { name: 'usage_examples', chunks: 760, updated: '2026-06-27T07:40:00Z' },
    ],
    pluggedInto: ['Progenitor'],
  },
  {
    id: 'insights',
    name: 'Customer insights',
    status: 'connected',
    scope: 'Mine',
    owner: 'Jina',
    color: '#ff7a30',
    description:
      'Interview transcripts and support threads embedded for semantic recall. '
      + 'Plugged into the planning subagent to keep proposals grounded in real '
      + 'user language.',
    embeddingModel: 'text-embedding-3-large',
    dimensions: 3072,
    indexType: 'HNSW',
    metric: 'inner product',
    host: '127.0.0.1:15433',
    database: 'progenitor',
    table: 'kb_insights',
    documents: 318,
    chunks: 4210,
    sizeBytes: 64 * 1024 * 1024,
    lastSynced: '2026-06-24T15:00:00Z',
    tags: ['research', 'voice-of-customer'],
    collections: [
      { name: 'interviews', chunks: 2400, updated: '2026-06-24T15:00:00Z' },
      { name: 'support_threads', chunks: 1810, updated: '2026-06-23T11:00:00Z' },
    ],
    pluggedInto: ['Planner subagent'],
  },
  {
    id: 'incidents',
    name: 'Postmortems & incidents',
    status: 'error',
    scope: 'Shared',
    owner: 'Builder',
    color: '#ff5f57',
    description:
      'Historic incident writeups. The last sync failed — the embedding '
      + 'endpoint returned 429s mid-batch, so a third of the corpus is stale.',
    embeddingModel: 'text-embedding-3-large',
    dimensions: 3072,
    indexType: 'HNSW',
    metric: 'cosine',
    host: '127.0.0.1:15433',
    database: 'progenitor',
    table: 'kb_incidents',
    documents: 96,
    chunks: 1240,
    sizeBytes: 19 * 1024 * 1024,
    lastSynced: '2026-06-21T03:20:00Z',
    lastError: 'Embedding batch failed: 429 Too Many Requests (8 of 24 batches incomplete)',
    tags: ['incident', 'reliability'],
    collections: [
      { name: 'postmortems', chunks: 840, updated: '2026-06-21T03:20:00Z' },
      { name: 'sev1_timeline', chunks: 400, updated: '2026-06-18T20:00:00Z' },
    ],
    pluggedInto: [],
  },
  {
    id: 'rfcs',
    name: 'RFCs & design docs',
    status: 'idle',
    scope: 'Mine',
    owner: 'Jina',
    color: '#58a6ff',
    description:
      'Long-form design proposals. Connected but not currently attached to any '
      + 'agent — available to plug in when planning larger changes.',
    embeddingModel: 'text-embedding-3-small',
    dimensions: 1536,
    indexType: 'IVFFlat',
    metric: 'cosine',
    host: '127.0.0.1:15433',
    database: 'progenitor',
    table: 'kb_rfcs',
    documents: 74,
    chunks: 2090,
    sizeBytes: 33 * 1024 * 1024,
    lastSynced: '2026-06-20T10:00:00Z',
    tags: ['design', 'rfc'],
    collections: [
      { name: 'accepted', chunks: 1180, updated: '2026-06-20T10:00:00Z' },
      { name: 'drafts', chunks: 910, updated: '2026-06-19T16:00:00Z' },
    ],
    pluggedInto: [],
  },
];

const TAG_TONES = ['green', 'blue', 'orange', 'red'];

function KnowledgeCard({ base, selected, onSelect }) {
  const meta = statusMeta(base.status);
  return (
    <article className={`task-card ${selected ? 'is-selected' : ''}`}>
      <button type="button" className="task-card__button" onClick={() => onSelect(base.id)}>
        <div className="task-card__title">
          <span className="kb-card__dot" style={{ background: base.color }} />
          {base.name}
        </div>
        <div className="task-card__meta">
          <span className={`task-pri-inline detail-status detail-status--${meta.tone}`}>{meta.label}</span>
          <span className="task-card__age">{formatRelativeTime(base.lastSynced)}</span>
        </div>
        <div className="task-card__foot">
          <span className="task-svc"><span className="task-svc-ic" />{base.table}</span>
          <span className="kb-card__count">{formatCount(base.documents)} docs · {formatCount(base.chunks)} vectors</span>
        </div>
      </button>
    </article>
  );
}

function StatGrid({ stats }) {
  return (
    <div className="kb-stat-grid">
      {stats.map((stat) => (
        <div key={stat.label} className="kb-stat">
          <span className="kb-stat__label">{stat.label}</span>
          <span className="kb-stat__value">{stat.value}</span>
        </div>
      ))}
    </div>
  );
}

function CodeBlock({ children }) {
  return <pre className="agents-prompt kb-code">{children}</pre>;
}

export default function KnowledgePage() {
  const [bases, setBases] = useState(KNOWLEDGE_BASES);
  const [activeFilter, setActiveFilter] = useState('All');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortMode, setSortMode] = useState('Recent');
  const [selectedBaseId, setSelectedBaseId] = useState(KNOWLEDGE_BASES[0].id);
  const [detailTab, setDetailTab] = useState('Overview');
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('tasks-list');

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

  const addBase = () => {
    const index = bases.filter((base) => base.id.startsWith('new-base')).length + 1;
    const draft = {
      id: `new-base-${index}`,
      name: `New knowledge base ${index}`,
      status: 'idle',
      scope: 'Mine',
      owner: 'You',
      color: '#58a6ff',
      description: 'Newly created pgvector store. Sync a corpus to start embedding documents.',
      embeddingModel: 'text-embedding-3-large',
      dimensions: 3072,
      indexType: 'HNSW',
      metric: 'cosine',
      host: '127.0.0.1:15433',
      database: 'progenitor',
      table: `kb_new_base_${index}`,
      documents: 0,
      chunks: 0,
      sizeBytes: 0,
      lastSynced: new Date().toISOString(),
      tags: [],
      collections: [],
      pluggedInto: [],
    };
    setBases((current) => [draft, ...current]);
    setSelectedBaseId(draft.id);
    setActiveFilter('All');
    setSearchQuery('');
  };

  const deleteSelected = () => {
    if (!selectedBase) {
      return;
    }
    setBases((current) => current.filter((base) => base.id !== selectedBase.id));
    setSelectedBaseId(null);
  };

  const setSelectedStatus = (status) => {
    if (!selectedBase) {
      return;
    }
    setBases((current) => current.map((base) => (
      base.id === selectedBase.id
        ? { ...base, status, lastSynced: new Date().toISOString(), lastError: undefined }
        : base
    )));
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
              {!visibleBases.length ? <div className="task-empty">No matching knowledge bases.</div> : null}
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
              title={<span className="chat-detail-header__title-text">{selectedBase.name}</span>}
              actions={(
                <DetailHeaderActions>
                  <DetailHeaderAction
                    icon={<PlayActionIcon />}
                    label="Sync"
                    tooltip="Re-embed and sync this store"
                    ariaLabel="Sync knowledge base"
                    variant="primary"
                    onClick={() => setSelectedStatus('syncing')}
                    disabled={selectedBase.status === 'syncing'}
                  />
                  <DetailHeaderAction
                    icon={<SaveActionIcon />}
                    label="Connect"
                    tooltip="Mark this store connected"
                    ariaLabel="Mark connected"
                    onClick={() => setSelectedStatus('connected')}
                    disabled={selectedBase.status === 'connected'}
                  />
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
                <span key="model" className="taskid">{selectedBase.embeddingModel}</span>,
                <span key="dims" className="taskid">{formatCount(selectedBase.dimensions)} dims</span>,
                <span key="index" className="taskid">{selectedBase.indexType} · {selectedBase.metric}</span>,
                <span key="table" className="taskid">{selectedBase.database}.{selectedBase.table}</span>,
              ]}
              tabs={['Overview', 'Collections', 'Connection', 'Usage']}
              activeTab={detailTab}
              onTabChange={setDetailTab}
              tabLabel="Knowledge base tabs"
            />

            <div className="task-detail__body">
              {detailTab === 'Overview' ? (
                <div className="task-overview">
                  {selectedBase.lastError ? (
                    <div className="task-alert">{selectedBase.lastError}</div>
                  ) : null}

                  <p className="task-description">{selectedBase.description}</p>

                  <section className="agents-section">
                    <header className="agents-section__head"><h2>Vector store</h2></header>
                    <StatGrid
                      stats={[
                        { label: 'Documents', value: formatCount(selectedBase.documents) },
                        { label: 'Vectors', value: formatCount(selectedBase.chunks) },
                        { label: 'Dimensions', value: formatCount(selectedBase.dimensions) },
                        { label: 'Embedding model', value: selectedBase.embeddingModel },
                        { label: 'Index', value: `${selectedBase.indexType} (${selectedBase.metric})` },
                        { label: 'On disk', value: formatBytes(selectedBase.sizeBytes) },
                        { label: 'Owner', value: selectedBase.owner },
                        { label: 'Last sync', value: formatRelativeTime(selectedBase.lastSynced) },
                      ]}
                    />
                  </section>

                  <section className="agents-section">
                    <header className="agents-section__head"><h2>Tags</h2></header>
                    <div className="task-labels">
                      {selectedBase.tags.length ? selectedBase.tags.map((tag, index) => (
                        <span key={tag} className={`task-lbl task-lbl--${TAG_TONES[index % TAG_TONES.length]}`}>{tag}</span>
                      )) : <span className="task-meta-sub">No tags</span>}
                    </div>
                  </section>
                </div>
              ) : null}

              {detailTab === 'Collections' ? (
                <section className="agents-section">
                  <header className="agents-section__head">
                    <h2>Collections <span>{selectedBase.collections.length} in {selectedBase.table}</span></h2>
                  </header>
                  {selectedBase.collections.length ? selectedBase.collections.map((collection) => (
                    <div key={collection.name} className="kb-collection">
                      <span className="kb-collection__mark" style={{ background: selectedBase.color }} />
                      <div className="kb-collection__body">
                        <div className="kb-collection__name">{collection.name}</div>
                        <div className="task-meta-sub">{formatCount(collection.chunks)} vectors · synced {formatRelativeTime(collection.updated)}</div>
                      </div>
                    </div>
                  )) : <div className="task-meta-sub">No collections embedded yet.</div>}
                </section>
              ) : null}

              {detailTab === 'Connection' ? (
                <div className="task-overview">
                  <section className="agents-section">
                    <header className="agents-section__head"><h2>Postgres + pgvector</h2></header>
                    <StatGrid
                      stats={[
                        { label: 'Host', value: selectedBase.host },
                        { label: 'Database', value: selectedBase.database },
                        { label: 'Table', value: selectedBase.table },
                        { label: 'Extension', value: 'pgvector' },
                      ]}
                    />
                  </section>
                  <section className="agents-section">
                    <header className="agents-section__head"><h2>Connection string</h2></header>
                    <CodeBlock>{`postgresql://progenitor@${selectedBase.host}/${selectedBase.database}`}</CodeBlock>
                  </section>
                  <section className="agents-section">
                    <header className="agents-section__head"><h2>Schema</h2></header>
                    <CodeBlock>{`CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE ${selectedBase.table} (
  id          bigserial PRIMARY KEY,
  collection  text NOT NULL,
  document_id text NOT NULL,
  content     text NOT NULL,
  embedding   vector(${selectedBase.dimensions})
);

CREATE INDEX ON ${selectedBase.table}
  USING ${selectedBase.indexType.toLowerCase()} (embedding vector_${selectedBase.metric === 'inner product' ? 'ip' : selectedBase.metric === 'cosine' ? 'cosine' : 'l2'}_ops);`}</CodeBlock>
                  </section>
                </div>
              ) : null}

              {detailTab === 'Usage' ? (
                <div className="task-overview">
                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Plugged into <span>{selectedBase.pluggedInto.length} agent{selectedBase.pluggedInto.length === 1 ? '' : 's'}</span></h2>
                    </header>
                    {selectedBase.pluggedInto.length ? (
                      <div className="task-labels">
                        {selectedBase.pluggedInto.map((agent) => (
                          <span key={agent} className="task-lbl task-lbl--blue">{agent}</span>
                        ))}
                      </div>
                    ) : <div className="task-meta-sub">Not attached to any agent yet.</div>}
                  </section>
                  <section className="agents-section">
                    <header className="agents-section__head"><h2>Attach to an agent</h2></header>
                    <p className="task-meta-sub">Register this store as a retrieval source in the agent&apos;s knowledge config:</p>
                    <CodeBlock>{`{
  "knowledge": [
    {
      "id": "${selectedBase.id}",
      "backend": "pgvector",
      "table": "${selectedBase.table}",
      "embedding_model": "${selectedBase.embeddingModel}",
      "top_k": 6
    }
  ]
}`}</CodeBlock>
                  </section>
                  <section className="agents-section">
                    <header className="agents-section__head"><h2>Retrieval query</h2></header>
                    <CodeBlock>{`SELECT collection, document_id, content
FROM ${selectedBase.table}
ORDER BY embedding ${selectedBase.metric === 'inner product' ? '<#>' : selectedBase.metric === 'cosine' ? '<=>' : '<->'} $1
LIMIT 6;`}</CodeBlock>
                  </section>
                </div>
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
