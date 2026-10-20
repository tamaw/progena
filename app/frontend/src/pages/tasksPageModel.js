export const STATUS_META = {
  draft: { label: 'Draft', tone: 'muted' },
  idle: { label: 'Idle', tone: 'muted' },
  running: { label: 'Running', tone: 'info' },
  complete: { label: 'Complete', tone: 'ok' },
  failed: { label: 'Failed', tone: 'error' },
  blocked: { label: 'Blocked', tone: 'error' },
};

export function formatTaskLabel(value) {
  return String(value || 'unassigned')
    .replace(/[_:]/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

export function statusMeta(status) {
  return STATUS_META[status] || { label: formatTaskLabel(status || 'draft'), tone: 'muted' };
}

const priorityOrder = { high: 0, medium: 1, low: 2 };
export const agentColors = ['#2f6ff6', '#a78bfa', '#22a06b', '#ef6c2a', '#6f86c4'];


export function formatAgentName(value) {
  return formatTaskLabel(value);
}

// Priority is a derived signal for sorting/triage only — it follows the kanban
// status, not the free-form tags. Tags are user/agent owned and searchable.
export function normalizePriority(card) {
  if (card.last_error || card.status === 'failed' || card.status === 'blocked') {
    return 'high';
  }
  if (card.status === 'running') {
    return 'medium';
  }
  return 'low';
}

export function priorityLabel(priority) {
  return priority === 'high' ? 'High' : priority === 'medium' ? 'Medium' : 'Low';
}

export function isArchivedCard(card) {
  return Boolean(card?.metadata?.archived_at);
}

export function cardTags(card) {
  return Array.isArray(card?.labels) ? card.labels.filter(Boolean).map(String) : [];
}

export function getCardService(card) {
  if (card.spec_path) {
    return card.spec_path.split('/').slice(-2).join('/');
  }
  if (card.branch_name) {
    return card.branch_name;
  }
  return card.owner || 'Todo Service';
}

export function getCardAssignee(card, index = 0) {
  const name = formatAgentName(card.agent_name || card.owner);
  const initial = name.trim().charAt(0).toUpperCase() || 'A';
  return { name, initial, color: agentColors[index % agentColors.length] };
}

export function buildCardBadges(card) {
  return [
    card.spec_path ? { key: 'spec', label: 'Spec' } : null,
    card.branch_name ? { key: 'branch', label: card.branch_name } : null,
    card.pr_url ? { key: 'pr', label: 'PR' } : null,
    card.last_error ? { key: 'error', label: 'Blocked', tone: 'red' } : null,
  ].filter(Boolean);
}

export function sortCards(sortMode) {
  const byRecent = (a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
  const byName = (a, b) => String(a.title || '').localeCompare(String(b.title || ''));
  if (sortMode === 'Recent') {
    return (a, b) => byRecent(a, b) || byName(a, b);
  }
  if (sortMode === 'Name') {
    return byName;
  }
  // Priority (default): high → low, then most-recently updated.
  return (a, b) => {
    const rank = (priorityOrder[normalizePriority(a)] ?? 3) - (priorityOrder[normalizePriority(b)] ?? 3);
    return rank || byRecent(a, b);
  };
}

export function groupVisibleCards(board, filter, query, sortMode, projectId) {
  if (!board) {
    return [];
  }

  const normalizedQuery = query.trim().toLowerCase();
  const comparator = sortCards(sortMode);
  return board.columns.map((column) => {
    const cards = board.cards
      .filter((card) => card.column_id === column.id && !isArchivedCard(card))
      .filter((card) => !projectId || card.project_id === projectId)
      .filter((card) => {
        if (filter === 'mine' && card.owner !== 'user') {
          return false;
        }
        if (filter === 'watching' && !cardTags(card).some((tag) => /watch|review|observe/i.test(tag))) {
          return false;
        }
        if (!normalizedQuery) {
          return true;
        }
        const haystack = [
          card.title,
          card.summary,
          card.description,
          card.owner,
          card.agent_name,
          card.status,
          ...cardTags(card),
        ].filter(Boolean).join(' ').toLowerCase();
        return haystack.includes(normalizedQuery);
      })
      .sort(comparator);

    return { ...column, cards };
  });
}

