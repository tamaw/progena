import { useCallback, useEffect, useMemo, useState } from 'react';
import DetailHeader, {
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  EditActionIcon,
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
} from '../components/layout/PaneList';
import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import { useApi } from '../hooks/useApi';

const priorityOrder = { high: 0, medium: 1, med: 1, low: 2 };
// Sort modes offered by the toolbar sort menu (mirrors the Agents page dropdown).
const SORT_MODES = ['Priority', 'Recent', 'Name'];
const TASK_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'mine', label: 'My tasks' },
  { value: 'watching', label: 'Watching' },
];
const agentColors = ['#2f6ff6', '#a78bfa', '#22a06b', '#ef6c2a', '#6f86c4'];

function formatTimestamp(value) {
  if (!value) {
    return 'Not set';
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString([], {
    month: 'numeric',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatRelativeTime(value) {
  if (!value) {
    return '--';
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return '--';
  }

  const diff = Math.max(0, Date.now() - parsed.getTime());
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'now';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return parsed.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function formatAgentName(value) {
  return String(value || 'unassigned')
    .replace(/[_:]/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function parseLabels(labelsText) {
  return labelsText
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);
}

function normalizePriority(card) {
  const labels = Array.isArray(card.labels) ? card.labels : [];
  const fromLabel = labels.find((label) => /^(high|medium|med|low)$/i.test(label));
  if (fromLabel) {
    return fromLabel.toLowerCase() === 'med' ? 'medium' : fromLabel.toLowerCase();
  }
  if (card.last_error || card.status === 'failed' || card.status === 'blocked') {
    return 'high';
  }
  if (card.status === 'running') {
    return 'medium';
  }
  return 'low';
}

function priorityLabel(priority) {
  return priority === 'high' ? 'High' : priority === 'medium' ? 'Medium' : 'Low';
}

function buildDraft(card) {
  if (!card) {
    return null;
  }

  return {
    ...card,
    labelsText: Array.isArray(card.labels) ? card.labels.join(', ') : '',
  };
}

function isArchivedCard(card) {
  return Boolean(card?.metadata?.archived_at);
}

function isFormField(element) {
  if (!(element instanceof HTMLElement)) {
    return false;
  }

  return Boolean(element.closest('input, textarea, select, button, a, [role="button"]'));
}

function getCardService(card) {
  if (card.spec_path) {
    return card.spec_path.split('/').slice(-2).join('/');
  }
  if (card.branch_name) {
    return card.branch_name;
  }
  return card.owner || 'Todo Service';
}

function getCardAssignee(card, index = 0) {
  const name = formatAgentName(card.agent_name || card.owner);
  const initial = name.trim().charAt(0).toUpperCase() || 'A';
  return { name, initial, color: agentColors[index % agentColors.length] };
}

function buildCardBadges(card) {
  return [
    card.spec_path ? { key: 'spec', label: 'Spec' } : null,
    card.branch_name ? { key: 'branch', label: card.branch_name } : null,
    card.pr_url ? { key: 'pr', label: 'PR' } : null,
    card.last_error ? { key: 'error', label: 'Blocked', tone: 'red' } : null,
  ].filter(Boolean);
}

function sortCards(sortMode) {
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

function groupVisibleCards(board, filter, query, sortMode) {
  if (!board) {
    return [];
  }

  const normalizedQuery = query.trim().toLowerCase();
  const comparator = sortCards(sortMode);
  return board.columns.map((column) => {
    const cards = board.cards
      .filter((card) => card.column_id === column.id && !isArchivedCard(card))
      .filter((card) => {
        if (filter === 'mine' && card.owner !== 'user') {
          return false;
        }
        if (filter === 'watching' && !(card.labels || []).some((label) => /watch|review|observe/i.test(label))) {
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
          ...(card.labels || []),
        ].filter(Boolean).join(' ').toLowerCase();
        return haystack.includes(normalizedQuery);
      })
      .sort(comparator);

    return { ...column, cards };
  });
}

function ChevronRightIcon() {
  return (
    <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
      <path d="M4 2l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function PriorityPill({ priority, inline = false }) {
  return (
    <span className={inline ? 'task-pri-inline' : `task-pri task-pri--${priority}`}>
      <span className={`task-pdot task-pri-${priority}`} />
      {inline ? `${priorityLabel(priority)} priority` : priorityLabel(priority)}
    </span>
  );
}

function TaskRow({ card, index, selected, compact, onSelect }) {
  const priority = normalizePriority(card);
  const assignee = getCardAssignee(card, index);

  return (
    <article className={`task-card ${selected ? 'is-selected' : ''} ${compact ? 'task-card--compact' : ''}`}>
      <button type="button" className="task-card__button" onClick={() => onSelect(card.id)}>
        <div className="task-card__title">{card.title}</div>
        {compact ? (
          <div className="task-card__foot">
            <PriorityPill priority={priority} />
            <span className="task-dot-sep">·</span>
            <span className="task-svc-plain">{getCardService(card)}</span>
            <span className="task-card__chev"><ChevronRightIcon /></span>
          </div>
        ) : (
          <>
            <div className="task-card__meta">
              <PriorityPill priority={priority} />
              <span className="task-card__age">{formatRelativeTime(card.updated_at)}</span>
            </div>
            <div className="task-card__foot">
              <span className="task-svc"><span className="task-svc-ic" />{getCardService(card)}</span>
              <span className="task-assignee"><span className="adot" style={{ background: assignee.color }} />{assignee.name}</span>
            </div>
          </>
        )}
      </button>
    </article>
  );
}

function DetailSection({ title, children, meta }) {
  return (
    <section className="task-sec">
      <h3 className="task-sec__h">{title}</h3>
      {meta ? <div className="task-sec__meta">{meta}</div> : null}
      {children}
    </section>
  );
}

function CardModal({
  board,
  draft,
  saving,
  onChange,
  onClose,
  onSave,
}) {
  if (!draft) {
    return null;
  }

  return (
    <div
      className="task-modal-backdrop"
      onClick={(event) => {
        if (!isFormField(event.target)) {
          onClose();
        }
      }}
    >
      <section className="task-modal" onClick={(event) => event.stopPropagation()}>
        <header className="task-modal__header">
          <div>
            <div className="task-modal__chips">
              <span className="task-badge">{draft.status || 'idle'}</span>
              <span className="task-badge">{draft.column_id || 'backlog'}</span>
            </div>
            <h3>{draft.id ? 'Edit task' : 'New task'}</h3>
          </div>
          <button type="button" className="task-modal__close" onClick={onClose}>×</button>
        </header>

        <div className="task-modal__body">
          <label>
            <span>Title</span>
            <input value={draft.title} onChange={(event) => onChange('title', event.target.value)} />
          </label>
          <label>
            <span>Summary</span>
            <textarea rows="3" value={draft.summary || ''} onChange={(event) => onChange('summary', event.target.value)} />
          </label>
          <label>
            <span>Description</span>
            <textarea rows="7" value={draft.description || ''} onChange={(event) => onChange('description', event.target.value)} />
          </label>

          <div className="task-modal__grid">
            <label>
              <span>Column</span>
              <select value={draft.column_id || 'backlog'} onChange={(event) => onChange('column_id', event.target.value)}>
                {board?.columns.map((column) => <option key={column.id} value={column.id}>{column.label}</option>)}
              </select>
            </label>
            <label>
              <span>Status</span>
              <select value={draft.status || 'idle'} onChange={(event) => onChange('status', event.target.value)}>
                {board?.card_schema?.statuses.map((status) => <option key={status.id} value={status.id}>{status.label}</option>)}
              </select>
            </label>
            <label>
              <span>Assigned agent</span>
              <input value={draft.agent_name || ''} onChange={(event) => onChange('agent_name', event.target.value)} />
            </label>
            <label>
              <span>Owner</span>
              <input value={draft.owner || 'user'} onChange={(event) => onChange('owner', event.target.value)} />
            </label>
          </div>

          <div className="task-modal__grid">
            <label>
              <span>Spec file</span>
              <input value={draft.spec_path || ''} onChange={(event) => onChange('spec_path', event.target.value)} />
            </label>
            <label>
              <span>Branch</span>
              <input value={draft.branch_name || ''} onChange={(event) => onChange('branch_name', event.target.value)} />
            </label>
            <label>
              <span>Pull request</span>
              <input value={draft.pr_url || ''} onChange={(event) => onChange('pr_url', event.target.value)} />
            </label>
            <label>
              <span>Labels</span>
              <input value={draft.labelsText || ''} onChange={(event) => onChange('labelsText', event.target.value)} placeholder="frontend, high, review" />
            </label>
          </div>

          <label>
            <span>Last error</span>
            <textarea rows="4" value={draft.last_error || ''} onChange={(event) => onChange('last_error', event.target.value)} />
          </label>
        </div>

        <footer className="task-modal__footer">
          <button type="button" className="task-btn task-btn--secondary" onClick={onClose}>Close</button>
          <button type="button" className="task-btn task-btn--primary" onClick={onSave} disabled={saving}>
            {saving ? 'Saving...' : 'Save task'}
          </button>
        </footer>
      </section>
    </div>
  );
}

export default function TasksPage({ context = null }) {
  const {
    getKanbanBoard,
    createKanbanCard,
    updateKanbanCard,
    deleteKanbanCard,
  } = useApi();
  const [board, setBoard] = useState(null);
  const [selectedCardId, setSelectedCardId] = useState(null);
  const [draft, setDraft] = useState(null);
  const [modalMode, setModalMode] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [taskFilter, setTaskFilter] = useState('all');
  const [detailTab, setDetailTab] = useState('Task');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortMode, setSortMode] = useState('Priority');
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('tasks-list');

  const loadBoard = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const nextBoard = await getKanbanBoard();
      setBoard(nextBoard);
      setSelectedCardId((current) => {
        if (current && nextBoard.cards.some((card) => card.id === current && !isArchivedCard(card))) {
          return current;
        }
        return nextBoard.cards.find((card) => !isArchivedCard(card))?.id || null;
      });
    } catch (loadError) {
      setError(loadError.message || 'Failed to load tasks');
    } finally {
      setLoading(false);
    }
  }, [getKanbanBoard]);

  useEffect(() => {
    loadBoard();
  }, [loadBoard]);

  const groupedColumns = useMemo(
    () => groupVisibleCards(board, taskFilter, searchQuery, sortMode),
    [board, searchQuery, taskFilter, sortMode],
  );

  const visibleCards = useMemo(
    () => groupedColumns.flatMap((column) => column.cards),
    [groupedColumns],
  );

  const selectedCard = useMemo(() => {
    if (!board) {
      return null;
    }
    return board.cards.find((card) => card.id === selectedCardId) || visibleCards[0] || null;
  }, [board, selectedCardId, visibleCards]);

  const selectedColumn = useMemo(
    () => board?.columns.find((column) => column.id === selectedCard?.column_id) || null,
    [board, selectedCard],
  );

  const selectedPriority = selectedCard ? normalizePriority(selectedCard) : 'low';
  const selectedAssignee = selectedCard ? getCardAssignee(selectedCard, visibleCards.findIndex((card) => card.id === selectedCard.id)) : null;
  const selectedBadges = selectedCard ? buildCardBadges(selectedCard) : [];
  const completedChecklist = selectedCard ? [
    Boolean(selectedCard.summary),
    Boolean(selectedCard.description),
    Boolean(selectedCard.spec_path),
    Boolean(selectedCard.branch_name),
    Boolean(selectedCard.pr_url),
    !selectedCard.last_error,
  ].filter(Boolean).length : 0;

  const handleDraftChange = useCallback((key, value) => {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  }, []);

  const openEditModal = useCallback(() => {
    setDraft(buildDraft(selectedCard));
    setModalMode('edit');
  }, [selectedCard]);

  const openCreateModal = useCallback(() => {
    const firstColumn = board?.columns[0]?.id || 'backlog';
    const firstStatus = board?.card_schema?.statuses[0]?.id || 'idle';
    setDraft({
      title: '',
      summary: '',
      description: '',
      column_id: firstColumn,
      status: firstStatus,
      agent_name: '',
      owner: 'user',
      labelsText: '',
      spec_path: '',
      branch_name: '',
      pr_url: '',
      last_error: '',
    });
    setModalMode('create');
  }, [board]);

  const closeModal = useCallback(() => {
    setModalMode(null);
    setDraft(null);
  }, []);

  const saveDraft = useCallback(async () => {
    if (!draft) {
      return;
    }

    const title = draft.title.trim();
    if (!title) {
      setError('Tasks need a title.');
      return;
    }

    setSaving(true);
    setError('');
    try {
      if (modalMode === 'create') {
        const created = await createKanbanCard({
          title,
          summary: draft.summary?.trim() || '',
          description: draft.description?.trim() || '',
          owner: draft.owner?.trim() || 'user',
          labels: parseLabels(draft.labelsText || ''),
        });
        const updated = await updateKanbanCard(created.id, {
          column_id: draft.column_id,
          status: draft.status,
          agent_name: draft.agent_name,
          spec_path: draft.spec_path,
          branch_name: draft.branch_name,
          pr_url: draft.pr_url,
          last_error: draft.last_error,
        });
        setSelectedCardId(updated.id);
      } else {
        await updateKanbanCard(draft.id, {
          title,
          summary: draft.summary,
          description: draft.description,
          column_id: draft.column_id,
          status: draft.status,
          agent_name: draft.agent_name,
          owner: draft.owner,
          labels: parseLabels(draft.labelsText || ''),
          spec_path: draft.spec_path,
          branch_name: draft.branch_name,
          pr_url: draft.pr_url,
          last_error: draft.last_error,
        });
      }
      const refreshedBoard = await getKanbanBoard();
      setBoard(refreshedBoard);
      closeModal();
    } catch (saveError) {
      setError(saveError.message || 'Failed to save task');
    } finally {
      setSaving(false);
    }
  }, [closeModal, createKanbanCard, draft, getKanbanBoard, modalMode, updateKanbanCard]);

  const handleDeleteSelected = useCallback(async () => {
    if (!selectedCard) {
      return;
    }
    setSaving(true);
    setError('');
    try {
      await deleteKanbanCard(selectedCard.id);
      const refreshedBoard = await getKanbanBoard();
      setBoard(refreshedBoard);
      setSelectedCardId(refreshedBoard.cards.find((card) => !isArchivedCard(card))?.id || null);
    } catch (deleteError) {
      setError(deleteError.message || 'Failed to delete task');
    } finally {
      setSaving(false);
    }
  }, [deleteKanbanCard, getKanbanBoard, selectedCard]);

  return (
    <PageShell className={`pane-page--rail tasks-page ${context ? 'tasks-page--project' : ''}`}>
      <section className={paneClassName} aria-label="Tasks">
        {collapsed ? (
          <PaneExpandRail label={context?.listTitle || 'Tasks'} onExpand={() => setCollapsed(false)} />
        ) : (
        <>
        <PaneCollapseSeam label={context?.listTitle || 'Tasks'} onCollapse={() => setCollapsed(true)} />
        <PaneListFilters
          filters={TASK_FILTERS}
          activeFilter={taskFilter}
          onFilterChange={setTaskFilter}
          actionLabel={context?.newCardLabel || '+ New'}
          onAction={openCreateModal}
        />
        <PaneListToolbar>
          <PaneListSearch value={searchQuery} placeholder="Search tasks" onChange={setSearchQuery} />
          <PaneListSortButton
            title="Sort tasks"
            options={SORT_MODES}
            value={sortMode}
            onSelect={setSortMode}
          />
        </PaneListToolbar>

        <PaneListScroll>
          {loading ? <div className="task-empty">Loading tasks...</div> : null}
          {error ? <div className="task-alert">{error}</div> : null}
          {!loading && !visibleCards.length ? <div className="task-empty">No matching tasks.</div> : null}
          {!loading && groupedColumns.map((column) => (
            column.cards.length ? (
              <PaneListGroup key={column.id} title={column.label} count={column.cards.length}>
                {column.cards.map((card, index) => (
                  <TaskRow
                    key={card.id}
                    card={card}
                    index={index}
                    selected={selectedCard?.id === card.id}
                    compact={column.cards.length > 4 && index > 2}
                    onSelect={setSelectedCardId}
                  />
                ))}
              </PaneListGroup>
            ) : null
          ))}
        </PaneListScroll>

        <PaneListFooter>
          <span><span className="num">{board?.cards.filter((card) => !isArchivedCard(card)).length ?? 0}</span> tasks</span>
        </PaneListFooter>
        </>
        )}
      </section>

      <DetailPane flush className="task-detail">
        {selectedCard ? (
          <>
            <DetailHeader
              title={selectedCard.title}
              actions={(
                <DetailHeaderActions>
                  <DetailHeaderAction
                    icon={<EditActionIcon />}
                    label="Edit"
                    tooltip="Edit task"
                    ariaLabel="Edit task"
                    onClick={openEditModal}
                  />
                  <DetailHeaderAction
                    icon={<DeleteActionIcon />}
                    label="Delete"
                    tooltip="Delete task"
                    ariaLabel="Delete task"
                    variant="danger"
                    onClick={handleDeleteSelected}
                    disabled={saving}
                  />
                </DetailHeaderActions>
              )}
              meta={[
                context?.projectName ? <span key="project" className="taskid">{context.solutionName} / {context.projectName}</span> : null,
                <span key="id" className="taskid">#{selectedCard.id}</span>,
                <span key="status" className="task-status"><span className="task-sdot" />{selectedCard.status || 'open'}</span>,
                <PriorityPill key="priority" priority={selectedPriority} inline />,
                <span key="updated" className="updated">Updated {formatTimestamp(selectedCard.updated_at)}</span>,
              ]}
              tabs={['Task', 'Activity', 'References']}
              activeTab={detailTab}
              onTabChange={setDetailTab}
              tabLabel="Task detail tabs"
            />

            <div className="task-detail__body">
              {detailTab === 'Task' ? (
                <>
                  <DetailSection title="Body" meta={selectedCard.summary || selectedColumn?.description}>
                    <p>{selectedCard.description || selectedCard.summary || 'No detailed task body has been added yet.'}</p>
                  </DetailSection>

                  <DetailSection title="Problem">
                    <ul>
                      <li>Task is currently tracked in the {selectedColumn?.label || selectedCard.column_id} workflow lane.</li>
                      <li>{selectedCard.last_error ? 'The latest run has a blocking error that needs attention.' : 'No blocking runtime error is currently attached.'}</li>
                      <li>Ownership is assigned to {formatAgentName(selectedCard.owner)} with {formatAgentName(selectedCard.agent_name)} available for execution.</li>
                    </ul>
                  </DetailSection>

                  <DetailSection title="Desired Behavior">
                    <ul>
                      <li>Complete the task and keep the board status, labels, and linked artifacts current.</li>
                      <li>Use the edit action to update fields that are already available through the frontend API.</li>
                    </ul>
                    {selectedCard.pr_url ? <pre className="task-code"><code>{selectedCard.pr_url}</code></pre> : null}
                  </DetailSection>

                  <DetailSection title="Scope">
                    <ul>
                      <li>{selectedCard.spec_path || 'No spec file linked yet.'}</li>
                      <li>{selectedCard.branch_name || 'No branch linked yet.'}</li>
                      <li>{selectedCard.labels?.length ? `Labels: ${selectedCard.labels.join(', ')}` : 'No labels yet.'}</li>
                    </ul>
                  </DetailSection>
                </>
              ) : null}

              {detailTab === 'Activity' ? (
                <DetailSection title="Activity">
                  <div className="task-comment-input">
                    <span className="task-comment-avatar" />
                    <input type="text" placeholder="Leave a comment..." />
                  </div>
                  <div className="task-activity">
                    <div className="task-act">
                      <span className="task-act-avatar" style={{ background: selectedAssignee?.color }}>{selectedAssignee?.initial}</span>
                      <div className="task-act-body"><strong>{selectedAssignee?.name}</strong> updated this task <span className="muted">{formatRelativeTime(selectedCard.updated_at)} ago</span></div>
                    </div>
                    <div className="task-act">
                      <span className="task-act-avatar" style={{ background: '#22a06b' }}>S</span>
                      <div className="task-act-body"><strong>System</strong> created the card <span className="muted">{formatRelativeTime(selectedCard.created_at)} ago</span></div>
                    </div>
                    {selectedCard.last_error ? (
                      <div className="task-act">
                        <span className="task-act-avatar" style={{ background: '#e0524a' }}>!</span>
                        <div className="task-act-body"><strong>Runtime</strong> attached a blocking error</div>
                      </div>
                    ) : null}
                  </div>
                </DetailSection>
              ) : null}

              {detailTab === 'References' ? (
                <DetailSection title="References">
                  <ul>
                    <li>{selectedCard.spec_path || 'No spec file attached'}</li>
                    <li>{selectedCard.branch_name || 'No branch attached'}</li>
                    <li>{selectedCard.pr_url || 'No pull request attached'}</li>
                  </ul>
                </DetailSection>
              ) : null}
            </div>
          </>
        ) : (
          <div className="task-detail__empty">Select a task to inspect details.</div>
        )}
      </DetailPane>

      <aside className="task-meta">
        {selectedCard ? (
          <>
            <section className="task-meta-sec">
              <h4 className="task-meta-sec__h">Dependencies</h4>
              <div className="task-meta-sub">Dependencies are not exposed by the current board API yet.</div>
              <div className="task-dep-card">
                <div className="task-dep-card__main">
                  <div className="task-dep-card__title">{selectedColumn?.description || 'Workflow lane'}</div>
                  <div className="task-dep-card__id">{selectedColumn?.id || selectedCard.column_id}</div>
                </div>
                <button type="button" className="task-dep-card__btn">Open</button>
              </div>
            </section>

            <section className="task-meta-sec">
              <div className="task-meta-sec__row">
                <h4 className="task-meta-sec__h">Checklist</h4>
                <span className="muted small">{completedChecklist}/6 completed</span>
              </div>
              <div className="task-progress"><span style={{ width: `${Math.round((completedChecklist / 6) * 100)}%` }} /></div>
              {[
                ['Summary added', Boolean(selectedCard.summary)],
                ['Description added', Boolean(selectedCard.description)],
                ['Spec file linked', Boolean(selectedCard.spec_path)],
                ['Branch linked', Boolean(selectedCard.branch_name)],
                ['Pull request linked', Boolean(selectedCard.pr_url)],
                ['No active error', !selectedCard.last_error],
              ].map(([label, checked]) => (
                <label key={label} className="task-ck"><input type="checkbox" checked={checked} readOnly /><span>{label}</span></label>
              ))}
              <button type="button" className="task-add-sub" onClick={openEditModal}>+ Add subtask</button>
            </section>

            <section className="task-meta-sec">
              <h4 className="task-meta-sec__h">Labels</h4>
              <div className="task-labels">
                {selectedCard.labels?.length ? selectedCard.labels.map((label, index) => (
                  <span key={label} className={`task-lbl task-lbl--${['green', 'red', 'orange', 'blue'][index % 4]}`}>{label}</span>
                )) : <span className="task-meta-sub">No labels</span>}
                <button type="button" className="task-lbl task-lbl--add" onClick={openEditModal}>+</button>
              </div>
            </section>

            <section className="task-meta-sec">
              <h4 className="task-meta-sec__h">Assignees</h4>
              <div className="task-assignee-row">
                <span className="task-av" style={{ background: selectedAssignee?.color }}>{selectedAssignee?.initial}</span>
                <div className="task-ar-body">
                  <div className="task-ar-name">{selectedAssignee?.name} <span className="muted small">(Primary)</span></div>
                  <div className="task-ar-handle">@{String(selectedCard.agent_name || selectedCard.owner || 'user').toLowerCase()}</div>
                </div>
                <span className="task-online" />
              </div>
            </section>

            <section className="task-meta-sec">
              <h4 className="task-meta-sec__h">Attachments</h4>
              {selectedBadges.length ? selectedBadges.map((badge) => (
                <div key={badge.key} className="task-attach">
                  <span className="task-attach-ic" />
                  <span className="task-attach-name">{badge.label}</span>
                  <span className="task-attach-ext">{badge.key}</span>
                </div>
              )) : <div className="task-meta-sub">No attachments linked.</div>}
            </section>
          </>
        ) : null}
      </aside>

      <CardModal
        board={board}
        draft={draft}
        saving={saving}
        onChange={handleDraftChange}
        onClose={closeModal}
        onSave={saveDraft}
      />
    </PageShell>
  );
}
