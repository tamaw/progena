import { getTimeBucket } from '../components/chat/conversationModel';

const RECENCY_BUCKETS = [
  ['today', 'Today'],
  ['yesterday', 'Yesterday'],
  ['this-week', 'This Week'],
  ['last-week', 'Last Week'],
  ['later', 'Later'],
];

const AGENT_TYPE_RANK = { object: 0 };

function byName(left, right) {
  return (left.name || '').localeCompare(right.name || '');
}

function updatedTime(item) {
  const timestamp = new Date(item.updated || 0).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function byRecent(left, right) {
  return updatedTime(right) - updatedTime(left) || byName(left, right);
}

function matchesSearch(item, search, fields) {
  if (!search) return true;
  return fields
    .map((field) => item[field])
    .concat(item.skills || [], item.tools || [])
    .map((value) => typeof value === 'object' ? value?.name : value)
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
    .includes(search.toLowerCase());
}

function recentSections(items) {
  const buckets = new Map(RECENCY_BUCKETS.map(([key]) => [key, []]));
  for (const item of items) {
    const bucket = getTimeBucket(item.updated);
    (buckets.get(bucket) || buckets.get('later')).push(item);
  }
  return RECENCY_BUCKETS
    .map(([key, title]) => ({ key, title, items: buckets.get(key) }))
    .filter((section) => section.items.length);
}

/**
 * Applies the Agents page's domain rules: object manifests have no run state,
 * so status filters only apply to protocol-backed agents.
 */
export function catalogueAgents(agents, { filter, search, sortMode }) {
  const visible = agents
    .filter((agent) => {
      const status = agent.enabled === false
        ? 'Disabled'
        : (!agent.status || agent.status === 'draft'
          ? 'Drafts'
          : agent.status.replace(/^./, (char) => char.toUpperCase()));
      return (filter === 'All' || (agent.kind !== 'object' && status === filter))
        && matchesSearch(agent, search, ['name', 'handle', 'model', 'role', 'summary']);
    })
    .sort(sortMode === 'Name' ? byName : sortMode === 'Recent' ? byRecent : (left, right) => (
      (AGENT_TYPE_RANK[left.kind] ?? 9) - (AGENT_TYPE_RANK[right.kind] ?? 9) || byName(left, right)
    ));

  if (sortMode === 'Name') return { visible, sections: [{ key: 'all', title: null, items: visible }] };
  if (sortMode === 'Recent') return { visible, sections: recentSections(visible) };

  const grouped = new Map();
  for (const agent of visible) {
    const group = agent.group || 'Agents';
    if (!grouped.has(group)) grouped.set(group, []);
    grouped.get(group).push(agent);
  }
  return {
    visible,
    sections: [...grouped].map(([key, items]) => ({ key, title: key, items })),
  };
}

/** Uses the latest correlated routine lifecycle for filters and roster order. */
export function catalogueRoutines(routines, { filter, search, sortMode, runStatusById }) {
  const visible = routines
    .filter((routine) => {
      const status = runStatusById[routine.id];
      return (filter === 'All' || status === filter)
        && matchesSearch(routine, search, ['name', 'handle', 'model', 'role', 'summary']);
    })
    .sort(sortMode === 'Recent' ? byRecent : byName);

  return {
    visible,
    sections: sortMode === 'Recent'
      ? recentSections(visible)
      : [{ key: 'all', title: null, items: visible }],
  };
}
