import { useMemo, useState } from 'react';
import {
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListGroup,
  PaneListScroll,
  PaneListSortButton,
  PaneListToolbar,
} from '../components/layout/PaneList';

const KNOWLEDGE_BASES = [
  {
    id: 'architecture',
    title: 'Progenitor architecture',
    owner: 'Builder',
    updated: '2h',
    filter: 'Mine',
    color: '#2f7dff',
    badge: 'BASE',
    notes: 128,
    links: 214,
    tags: 31,
    clusters: 4,
  },
  {
    id: 'runbooks',
    title: 'Agent runbooks',
    owner: 'Builder Pro',
    updated: '1d',
    filter: 'Shared',
    color: '#9b7cff',
    notes: 42,
    links: 88,
    tags: 18,
    clusters: 3,
  },
  {
    id: 'mcp',
    title: 'MCP & tooling reference',
    owner: 'Observer',
    updated: '3d',
    filter: 'Shared',
    color: '#20b486',
    notes: 67,
    links: 154,
    tags: 24,
    clusters: 5,
  },
  {
    id: 'insights',
    title: 'Customer Insights',
    owner: 'Jina',
    updated: '5d',
    filter: 'Mine',
    color: '#ff7a30',
    notes: 31,
    links: 62,
    tags: 12,
    clusters: 2,
  },
  { id: 'incidents', title: 'Postmortems & incidents', owner: 'Builder', updated: '6d', filter: 'Shared', color: '#ff5f57', notes: 19, links: 38, tags: 8, clusters: 2 },
  { id: 'rfcs', title: 'RFCs & design docs', owner: 'Jina', updated: '1w', filter: 'Mine', color: '#58a6ff', notes: 24, links: 51, tags: 14, clusters: 3 },
  { id: 'onboarding', title: 'Onboarding playbook', owner: 'Observer', updated: '1w', filter: 'Shared', color: '#3fb950', notes: 14, links: 22, tags: 7, clusters: 1 },
  { id: 'scratchpad', title: 'Scratchpad', owner: 'Builder', updated: '2w', filter: 'Mine', color: '#8b949e', notes: 9, links: 3, tags: 2, clusters: 1 },
];

const SAVED_VIEWS = [
  { label: 'Unlinked notes', count: 12, color: '#f1c40f' },
  { label: 'Orphans', count: 4, color: '#ff5f57' },
  { label: 'Recently edited', count: 8, color: '#9b7cff' },
];

const GRAPH_NODES = [
  { id: 'runtime', label: 'Progenitor Runtime', type: 'runtime', x: 32, y: 52, size: 22, color: '#2f7dff' },
  { id: 'agents', label: 'Agents', type: 'agents', x: 72, y: 42, size: 22, color: '#9b7cff' },
  { id: 'tools', label: 'MCP & Tools', type: 'tools', x: 66, y: 76, size: 22, color: '#20b486' },
  { id: 'core_loop', label: 'core_loop.ts', type: 'runtime', x: 23, y: 39, size: 7, color: '#2f7dff' },
  { id: 'scheduler', label: 'cycle scheduler', type: 'runtime', x: 21, y: 46, size: 7, color: '#2f7dff' },
  { id: 'state_machine', label: 'state machine', type: 'runtime', x: 25, y: 66, size: 7, color: '#2f7dff' },
  { id: 'cold_start', label: 'cold start', type: 'runtime', x: 30, y: 36, size: 7, color: '#2f7dff' },
  { id: 'supervisor', label: 'supervisor tree', type: 'runtime', x: 39, y: 35, size: 7, color: '#2f7dff' },
  { id: 'message_bus', label: 'message bus', type: 'runtime', x: 43, y: 41, size: 7, color: '#2f7dff' },
  { id: 'snapshot', label: 'snapshot.json', type: 'runtime', x: 42, y: 60, size: 7, color: '#2f7dff' },
  { id: 'restart', label: 'restart policy', type: 'runtime', x: 37, y: 69, size: 7, color: '#2f7dff' },
  { id: 'shutdown', label: 'graceful shutdown', type: 'runtime', x: 27, y: 72, size: 7, color: '#2f7dff' },
  { id: 'builder_agent', label: 'builder_agent', type: 'agents', x: 63, y: 35, size: 7, color: '#9b7cff' },
  { id: 'observer', label: 'observe_agent', type: 'agents', x: 68, y: 30, size: 7, color: '#9b7cff' },
  { id: 'builder_progenitor', label: 'builder_progenitor', type: 'agents', x: 77, y: 29, size: 7, color: '#9b7cff' },
  { id: 'dispatch', label: 'tool dispatcher', type: 'agents', x: 83, y: 34, size: 7, color: '#9b7cff' },
  { id: 'prompt_cache', label: 'prompt cache', type: 'agents', x: 86, y: 41, size: 7, color: '#9b7cff' },
  { id: 'backoff', label: 'retry & backoff', type: 'agents', x: 85, y: 54, size: 7, color: '#9b7cff' },
  { id: 'guardrails', label: 'guardrails', type: 'agents', x: 77, y: 58, size: 7, color: '#9b7cff' },
  { id: 'memory', label: 'memory store', type: 'agents', x: 67, y: 55, size: 7, color: '#9b7cff' },
  { id: 'spawn', label: 'spawn_agent()', type: 'agents', x: 57, y: 47, size: 7, color: '#9b7cff' },
  { id: 'todos_list', label: 'todos_list', type: 'tools', x: 70, y: 65, size: 7, color: '#20b486' },
  { id: 'todos_get', label: 'todos_get', type: 'tools', x: 76, y: 69, size: 7, color: '#20b486' },
  { id: 'notify', label: 'notify_user', type: 'tools', x: 78, y: 80, size: 7, color: '#20b486' },
  { id: 'registry', label: 'tool registry', type: 'tools', x: 72, y: 85, size: 7, color: '#20b486' },
  { id: 'schema', label: 'schema diff', type: 'tools', x: 63, y: 86, size: 7, color: '#20b486' },
  { id: 'discover', label: 'tools.discover', type: 'tools', x: 58, y: 80, size: 7, color: '#20b486' },
  { id: 'mcp', label: 'mcp', type: 'tools', x: 57, y: 70, size: 7, color: '#20b486' },
  { id: 'interview', label: 'interview prep', type: 'note', x: 12, y: 30, size: 7, color: '#f1c40f' },
  { id: 'migration', label: 'migration plan', type: 'note', x: 94, y: 36, size: 7, color: '#f1c40f' },
  { id: 'scratch', label: 'scratch_03.md', type: 'note', x: 92, y: 65, size: 6, color: '#697386' },
  { id: 'release', label: 'draft_release', type: 'note', x: 87, y: 71, size: 6, color: '#697386' },
  { id: 'meeting', label: 'tmp_meeting', type: 'note', x: 15, y: 76, size: 6, color: '#697386' },
  { id: 'notes', label: 'notes_2026', type: 'note', x: 21, y: 80, size: 6, color: '#697386' },
];

const GRAPH_LINKS = [
  ['runtime', 'core_loop'], ['runtime', 'scheduler'], ['runtime', 'state_machine'], ['runtime', 'cold_start'],
  ['runtime', 'supervisor'], ['runtime', 'message_bus'], ['runtime', 'snapshot'], ['runtime', 'restart'],
  ['runtime', 'shutdown'], ['runtime', 'agents'], ['agents', 'builder_agent'], ['agents', 'observer'],
  ['agents', 'builder_progenitor'], ['agents', 'dispatch'], ['agents', 'prompt_cache'], ['agents', 'backoff'],
  ['agents', 'guardrails'], ['agents', 'memory'], ['agents', 'spawn'], ['agents', 'tools'], ['tools', 'todos_list'],
  ['tools', 'todos_get'], ['tools', 'notify'], ['tools', 'registry'], ['tools', 'schema'], ['tools', 'discover'],
  ['tools', 'mcp'], ['meeting', 'notes'], ['scratch', 'release'], ['mcp', 'tools'],
];

const NODE_BY_ID = Object.fromEntries(GRAPH_NODES.map((node) => [node.id, node]));

function KnowledgeCard({ base, selected, onSelect }) {
  return (
    <button
      type="button"
      className={`knowledge-source-row ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(base.id)}
    >
      <span className="knowledge-source-row__mark" style={{ background: base.color }} />
      <span className="knowledge-source-row__top">
        <span className="knowledge-source-row__source">{base.owner}</span>
        <span className="knowledge-source-row__scope">{base.filter}</span>
      </span>
      <span className="knowledge-source-row__age">{base.updated}</span>
      <span className="knowledge-source-row__title">{base.title}</span>
      <span className="knowledge-source-row__meta">
        {base.notes} notes · {base.links} links · {base.tags} tags · {base.clusters} clusters
      </span>
    </button>
  );
}

export default function KnowledgePage() {
  const [activeFilter, setActiveFilter] = useState('All');
  const [bases, setBases] = useState(KNOWLEDGE_BASES);
  const [selectedBaseId, setSelectedBaseId] = useState(KNOWLEDGE_BASES[0].id);
  const [selectedNodeId, setSelectedNodeId] = useState('runtime');
  const [zoom, setZoom] = useState(100);

  const visibleBases = useMemo(() => (
    activeFilter === 'All'
      ? bases
      : bases.filter((base) => base.filter === activeFilter)
  ), [activeFilter, bases]);
  const selectedBase = bases.find((base) => base.id === selectedBaseId) || bases[0];
  const selectedNode = NODE_BY_ID[selectedNodeId] || NODE_BY_ID.runtime;

  const addBase = () => {
    const nextIndex = bases.filter((base) => base.id.startsWith('new-base')).length + 1;
    const nextBase = {
      id: `new-base-${nextIndex}`,
      title: `New knowledge base ${nextIndex}`,
      owner: 'Builder',
      updated: 'now',
      filter: 'Mine',
      color: '#58a6ff',
      badge: 'DRAFT',
      notes: 0,
      links: 0,
      tags: 0,
      clusters: 1,
    };
    setBases((current) => [nextBase, ...current]);
    setSelectedBaseId(nextBase.id);
    setActiveFilter('All');
  };

  return (
    <div className="knowledge-page">
      <PaneList className="knowledge-pane" label="Knowledge bases">
        <PaneListFilters filters={['All', 'Mine', 'Shared']} activeFilter={activeFilter} onFilterChange={setActiveFilter} />
        <PaneListToolbar actionLabel="+ Base" onAction={addBase}>
          <span className="knowledge-pane__hint">Graph sources</span>
          <PaneListSortButton title="Sort knowledge bases">Updated</PaneListSortButton>
        </PaneListToolbar>

        <PaneListScroll>
          <PaneListGroup title="Bases" count={visibleBases.length}>
            {visibleBases.map((base) => (
              <KnowledgeCard
                key={base.id}
                base={base}
                selected={base.id === selectedBaseId}
                onSelect={setSelectedBaseId}
              />
            ))}
          </PaneListGroup>

          <PaneListGroup title="Saved views" count={SAVED_VIEWS.length}>
            <div className="knowledge-saved">
              {SAVED_VIEWS.map((view) => (
                <button key={view.label} type="button" className="knowledge-saved-row">
                  <i className="knowledge-saved-row__mark" style={{ background: view.color }} />
                  <span className="knowledge-saved-row__label">{view.label}</span>
                  <em>({view.count})</em>
                </button>
              ))}
            </div>
          </PaneListGroup>
        </PaneListScroll>

        <PaneListFooter>
          <span>{selectedBase.owner}</span>
          <span>{selectedBase.notes} notes</span>
        </PaneListFooter>
      </PaneList>

      <main className="knowledge-workspace">

        <section className="knowledge-graph" aria-label="Knowledge graph">
          <svg className="knowledge-graph__links" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            {GRAPH_LINKS.map(([from, to]) => {
              const source = NODE_BY_ID[from];
              const target = NODE_BY_ID[to];
              if (!source || !target) {
                return null;
              }
              const isActive = selectedNodeId === from || selectedNodeId === to;
              return (
                <line
                  key={`${from}-${to}`}
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  className={isActive ? 'is-active' : ''}
                />
              );
            })}
          </svg>

          {GRAPH_NODES.map((node) => (
            <button
              key={node.id}
              type="button"
              className={`knowledge-node knowledge-node--${node.type} ${selectedNodeId === node.id ? 'is-selected' : ''}`}
              style={{
                left: `${node.x}%`,
                top: `${node.y}%`,
                '--node-color': node.color,
                '--node-size': `${node.size}px`,
              }}
              onClick={() => setSelectedNodeId(node.id)}
            >
              <i />
              <span>{node.label}</span>
            </button>
          ))}

          <aside className="knowledge-node-card">
            <span style={{ color: selectedNode.color }}>{selectedNode.type}</span>
            <strong>{selectedNode.label}</strong>
            <p>{selectedNode.type === 'note' ? 'Loose note connected by recent edits.' : 'Cluster node with linked notes, tasks, and runtime references.'}</p>
          </aside>

          <div className="knowledge-zoom">
            <button type="button" onClick={() => setZoom((current) => Math.min(current + 10, 140))}>+</button>
            <span>{zoom}%</span>
            <button type="button" onClick={() => setZoom((current) => Math.max(current - 10, 70))}>-</button>
            <button type="button" onClick={() => setZoom(100)}>[]</button>
          </div>
        </section>
      </main>
    </div>
  );
}
