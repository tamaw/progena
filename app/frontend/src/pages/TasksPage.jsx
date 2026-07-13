import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useParams } from 'react-router-dom';
import { useEntityUrlSync } from '../lib/routes';
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
} from '../components/layout/PaneList';
import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import { PaneSection } from '../components/layout/PaneList';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import { useConfig } from '../context/ConfigContext';
import { useNotifications } from '../context/NotificationContext';
import { useTasksApi } from '../hooks/api/useTasksApi';
import {
  fetchKanbanBoard,
  fetchTaskActivity,
  selectKanbanBoard,
  selectTaskActivityEntry,
  selectTasksState,
} from '../store/uiDataSlice';
import { selectRealtime } from '../store/realtimeSlice';
import {
  agentColors,
  buildCardBadges,
  cardTags,
  formatAgentName,
  formatRelativeTime,
  getCardAssignee,
  getCardService,
  groupVisibleCards,
  isArchivedCard,
  normalizePriority,
  priorityLabel,
  statusMeta,
} from './tasksPageModel';

// Sort modes offered by the toolbar sort menu (mirrors the Agents page dropdown).
const SORT_MODES = ['Priority', 'Recent', 'Name'];
const TASK_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'mine', label: 'My tasks' },
  { value: 'watching', label: 'Watching' },
];
// agentColors, formatRelativeTime, formatAgentName, normalizePriority,
// priorityLabel, isArchivedCard, cardTags, getCardService, getCardAssignee,
// buildCardBadges, sortCards, and groupVisibleCards were extracted to
// tasksPageModel.js in the frontend-refactor branch (imported above);
// groupVisibleCards there also gained the projectId filter this branch added.

function ChevronRightIcon() {
  return (
    <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
      <path d="M4 2l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function PencilIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="M2 14h3l8-8-3-3-8 8z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="M3.25 8.25 6.5 11.5l6.25-6.75" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// "Jump to the blocking task" — a small launch arrow leaping out of its corner.
function DependencyOpenIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M5.5 10.5 11 5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" />
      <path d="M6.6 5H11v4.4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" />
    </svg>
  );
}

// Tiny priority glyph sized to the status dot (~7px): up = high, flag = medium,
// down = low. Colour matches the kanban priority palette via currentColor.
function PriorityIcon({ priority }) {
  const className = `task-pri-ico task-pri-ico--${priority}`;
  if (priority === 'high') {
    return (
      <svg className={className} viewBox="0 0 10 10" width="10" height="10" aria-hidden="true">
        <path d="M5 1.5 9 7.5H1z" fill="currentColor" />
      </svg>
    );
  }
  if (priority === 'low') {
    return (
      <svg className={className} viewBox="0 0 10 10" width="10" height="10" aria-hidden="true">
        <path d="M5 8.5 1 2.5h8z" fill="currentColor" />
      </svg>
    );
  }
  return (
    <svg className={className} viewBox="0 0 10 10" width="10" height="10" aria-hidden="true">
      <path d="M2.4 1v8.2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <path d="M2.9 1.5h4.5l-1.2 1.8 1.2 1.8H2.9z" fill="currentColor" />
    </svg>
  );
}

function PriorityPill({ priority, inline = false }) {
  return (
    <span className={inline ? 'task-pri-inline' : `task-pri task-pri--${priority}`}>
      {inline ? <PriorityIcon priority={priority} /> : <span className={`task-pdot task-pri-${priority}`} />}
      {inline ? `${priorityLabel(priority)} priority` : priorityLabel(priority)}
    </span>
  );
}

function TaskRow({ card, index, selected, compact, onSelect }) {
  const priority = normalizePriority(card);
  const assignee = getCardAssignee(card, index);
  const cardStatus = statusMeta(card.status);

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
              <span className={`task-pri-inline detail-status detail-status--${cardStatus.tone}`}>{cardStatus.label}</span>
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

// Minimal add affordance for the meta pane: a small "+ Add" link (or "+" pill for
// tags) that reveals a single inline input only when clicked. Restores the
// original lightweight look without losing the dynamic add capability.
function InlineAdd({ openLabel = '+ Add', placeholder, disabled = false, pill = false, pillLabel = '', onSubmit }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  const inputRef = useRef(null);

  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
    }
  }, [open]);

  const close = () => {
    setValue('');
    setOpen(false);
  };

  const submit = async () => {
    const trimmed = value.trim();
    if (!trimmed) {
      close();
      return;
    }
    await onSubmit(trimmed);
    close();
  };

  if (!open) {
    return (
      <button
        type="button"
        className={pill ? 'task-lbl task-lbl--add' : 'task-add-sub'}
        data-tooltip={pill ? openLabel : undefined}
        aria-label={pill ? openLabel : undefined}
        onClick={() => setOpen(true)}
        disabled={disabled}
      >
        {pill ? (pillLabel ? `+ ${pillLabel}` : '+') : openLabel}
      </button>
    );
  }

  // Close when focus leaves the whole control (click-away). relatedTarget is the
  // element gaining focus; if it's outside this row — including the Add button's
  // own click, which keeps focus inside until submit() runs — we dismiss.
  const handleBlur = (event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) {
      close();
    }
  };

  return (
    <div className="task-comment-input" style={{ marginTop: 8 }} onBlur={handleBlur}>
      <input
        ref={inputRef}
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            submit();
          } else if (event.key === 'Escape') {
            event.preventDefault();
            close();
          }
        }}
        disabled={disabled}
      />
      <button
        type="button"
        className="task-add-sub"
        onMouseDown={(event) => event.preventDefault()}
        onClick={submit}
        disabled={disabled}
      >
        Add
      </button>
    </div>
  );
}

// Attachments need two fields, so they get their own toggled variant.
function InlineAddAttachment({ disabled = false, onSubmit }) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState('');
  const [url, setUrl] = useState('');
  const labelRef = useRef(null);

  useEffect(() => {
    if (open) {
      labelRef.current?.focus();
    }
  }, [open]);

  const close = () => {
    setLabel('');
    setUrl('');
    setOpen(false);
  };

  const submit = async () => {
    if (!label.trim() || !url.trim()) {
      return;
    }
    await onSubmit(label.trim(), url.trim());
    close();
  };

  if (!open) {
    return (
      <div style={{ marginTop: 8 }}>
        <button
          type="button"
          className="task-lbl task-lbl--add"
          data-tooltip="Add attachment"
          aria-label="Add attachment"
          onClick={() => setOpen(true)}
          disabled={disabled}
        >
          +
        </button>
      </div>
    );
  }

  // Close on click-away — only when focus leaves the whole control, so moving
  // between the two fields (or onto the button) keeps it open.
  const handleBlur = (event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) {
      close();
    }
  };

  return (
    <div
      className="task-comment-input"
      style={{ marginTop: 8, flexDirection: 'column', alignItems: 'stretch' }}
      onBlur={handleBlur}
    >
      <input
        ref={labelRef}
        type="text"
        value={label}
        placeholder="Attachment label..."
        onChange={(event) => setLabel(event.target.value)}
      />
      <input
        type="text"
        value={url}
        placeholder="Attachment URL..."
        onChange={(event) => setUrl(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault();
            close();
          }
        }}
      />
      <button
        type="button"
        className="task-add-sub"
        onMouseDown={(event) => event.preventDefault()}
        onClick={submit}
        disabled={disabled || !label.trim() || !url.trim()}
      >
        Add attachment
      </button>
    </div>
  );
}

function normalizeChecklistItems(items) {
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .filter((item) => item && typeof item === 'object')
    .map((item, index) => ({
      id: String(item.id || `item_${index}`),
      title: String(item.title || '').trim(),
      done: Boolean(item.done),
    }))
    .filter((item) => item.title);
}

// Checklists are fully dynamic: the agent (via `/state/tasks/`) or the user can
// add any number of named checklists. A legacy single `subtasks` list is
// surfaced as one default checklist for backward compatibility.
function normalizeChecklists(metadata) {
  const lists = metadata?.checklists;
  if (Array.isArray(lists) && lists.length) {
    return lists
      .filter((list) => list && typeof list === 'object')
      .map((list, index) => ({
        id: String(list.id || `checklist_${index}`),
        title: String(list.title || 'Checklist').trim() || 'Checklist',
        items: normalizeChecklistItems(list.items),
      }));
  }
  const legacy = normalizeChecklistItems(metadata?.subtasks);
  return legacy.length ? [{ id: 'default', title: 'Checklist', items: legacy }] : [];
}

function serializeChecklists(checklists) {
  return checklists.map((list) => ({
    id: list.id,
    title: list.title,
    items: list.items.map((item) => ({ id: item.id, title: item.title, done: item.done })),
  }));
}

// Dependencies are blocking-task references rendered as cards (title + a short
// #ref + an Open action). Legacy plain-string dependencies are upgraded to the
// {id,title,ref} shape on read so old cards keep working.
function normalizeDependencies(metadata) {
  const dependencies = metadata?.dependencies;
  if (!Array.isArray(dependencies)) {
    return [];
  }
  return dependencies
    .map((item, index) => {
      if (typeof item === 'string') {
        const title = item.trim();
        return title ? { id: `dep_${index}`, title, ref: '' } : null;
      }
      if (item && typeof item === 'object') {
        const title = String(item.title || item.text || '').trim();
        return title
          ? { id: String(item.id || `dep_${index}`), title, ref: String(item.ref || '').trim() }
          : null;
      }
      return null;
    })
    .filter(Boolean);
}

function serializeDependencies(dependencies) {
  return dependencies.map((dep) => ({ id: dep.id, title: dep.title, ref: dep.ref }));
}

function normalizeCollaborators(metadata) {
  const collaborators = metadata?.collaborators;
  if (!Array.isArray(collaborators)) {
    return [];
  }
  return collaborators.map((item) => String(item || '').trim()).filter(Boolean);
}

function normalizeAttachments(metadata) {
  const attachments = metadata?.attachments;
  if (!Array.isArray(attachments)) {
    return [];
  }
  return attachments
    .filter((item) => item && typeof item === 'object')
    .map((item, index) => ({
      id: String(item.id || `attachment_${index}`),
      label: String(item.label || item.url || '').trim(),
      url: String(item.url || '').trim(),
      kind: String(item.kind || 'link').trim() || 'link',
    }))
    .filter((item) => item.label && item.url);
}

function buildDependencyDraft(value, board) {
  const trimmed = String(value || '').trim();
  if (!trimmed) {
    return null;
  }
  const normalized = trimmed.toLowerCase();
  const match = board?.cards.find((card) => (
    card.id === trimmed || String(card.title || '').trim().toLowerCase() === normalized
  ));
  if (match) {
    return {
      title: String(match.title || trimmed).trim(),
      ref: match.id,
    };
  }
  return {
    title: trimmed,
    ref: '',
  };
}

export default function TasksPage({ context = null }) {
  const { updateKanbanCard, deleteKanbanCard, createTaskActivity } = useTasksApi();
  const { getApiUrl } = useConfig();
  const { show } = useNotifications();
  const dispatch = useDispatch();
  const realtime = useSelector(selectRealtime);
  // `titleSlug` is display context only (e.g. /tasks/card_42/fix-the-thing) --
  // selection is driven entirely by the id, never by the slug. Only the
  // unscoped /tasks page (no project `context`) owns the URL -- Tasks
  // embedded in a project's kanban board (KanbanPage/ProjectPage) keeps its
  // own route untouched.
  const { cardId: urlCardId } = useParams();
  const isStandaloneTasksPage = context === null;
  const board = useSelector(selectKanbanBoard);
  const tasksState = useSelector(selectTasksState);
  const [selectedCardId, setSelectedCardId] = useState(() => (
    isStandaloneTasksPage && urlCardId ? urlCardId : null
  ));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [taskFilter, setTaskFilter] = useState('all');
  const [detailTab, setDetailTab] = useState('Task');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortMode, setSortMode] = useState('Priority');
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');
  const titleInputRef = useRef(null);
  const [taskActivity, setTaskActivity] = useState([]);
  const [taskActivityLoading, setTaskActivityLoading] = useState(false);
  const [statusSaving, setStatusSaving] = useState(false);
  const [metadataSaving, setMetadataSaving] = useState(false);
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('tasks-list');

  const loadBoard = useCallback(async () => {
    setError('');
    try {
      await dispatch(fetchKanbanBoard({
        kanbanUrl: getApiUrl('kanban'),
      })).unwrap();
    } catch (loadError) {
      setError(loadError.message || 'Failed to load tasks.');
    }
  }, [dispatch, getApiUrl]);

  useEffect(() => {
    if (!realtime.connected) {
      return;
    }
    void loadBoard();
  }, [loadBoard, realtime.connected]);

  const groupedColumns = useMemo(
    () => groupVisibleCards(board, taskFilter, searchQuery, sortMode, context?.projectId),
    [board, searchQuery, taskFilter, sortMode, context?.projectId],
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

  // Keep the URL in sync with whichever task is actually selected (a
  // deep-linked or stale id falls back to the top visible card the same way
  // `selectedCard` already does above). The title slug is cosmetic; only the
  // id segment is ever read back. Disabled when embedded in a project's
  // kanban board, which doesn't own the /tasks URL.
  useEntityUrlSync(isStandaloneTasksPage ? '/tasks' : null, selectedCard?.id, selectedCard?.title);

  const selectedColumn = useMemo(
    () => board?.columns.find((column) => column.id === selectedCard?.column_id) || null,
    [board, selectedCard],
  );
  const activeStatusMeta = statusMeta(selectedCard?.status);

  const selectedPriority = selectedCard ? normalizePriority(selectedCard) : 'low';
  const selectedAssignee = selectedCard ? getCardAssignee(selectedCard, visibleCards.findIndex((card) => card.id === selectedCard.id)) : null;
  const selectedBadges = selectedCard ? buildCardBadges(selectedCard) : [];
  const selectedTags = useMemo(() => (selectedCard ? cardTags(selectedCard) : []), [selectedCard]);
  const selectedChecklists = useMemo(
    () => (selectedCard ? normalizeChecklists(selectedCard.metadata) : []),
    [selectedCard],
  );
  const selectedDependencies = useMemo(
    () => (selectedCard ? normalizeDependencies(selectedCard.metadata) : []),
    [selectedCard],
  );
  const selectedCollaborators = useMemo(
    () => (selectedCard ? normalizeCollaborators(selectedCard.metadata) : []),
    [selectedCard],
  );
  const selectedAttachments = useMemo(
    () => (selectedCard ? normalizeAttachments(selectedCard.metadata) : []),
    [selectedCard],
  );
  const selectedTaskActivityEntry = useSelector((state) => selectTaskActivityEntry(state, selectedCard?.id));
  const loading = tasksState.loading && !board;

  // A task has exactly one checklist (1:1), so flatten any persisted lists into a
  // single item list for the UI.
  const checklistItems = useMemo(
    () => selectedChecklists.flatMap((list) => list.items),
    [selectedChecklists],
  );

  const checklistProgress = useMemo(() => {
    const done = checklistItems.filter((item) => item.done).length;
    return { done, total: checklistItems.length };
  }, [checklistItems]);

  const blockedLabel = selectedDependencies.length
    ? `Blocked by ${selectedDependencies.length} task${selectedDependencies.length === 1 ? '' : 's'}`
    : 'Not blocked';

  // Creation is delegated to the Progenitor agent (same pattern as Tools and
  // Schedules): there is no local create form. The agent writes `/state/tasks/`
  // entries and the new card arrives here live via the kanban.snapshot event.
  const openCreateTask = useCallback(() => {
    window.dispatchEvent(new CustomEvent('progenitor:open-quick-chat', {
      detail: {
        mode: 'task-create',
        projectId: context?.projectId,
        projectName: context?.projectName,
      },
    }));
  }, [context?.projectId, context?.projectName]);

  useEffect(() => {
    if (!selectedCard?.id) {
      setTaskActivity([]);
      return undefined;
    }

    setTaskActivityLoading(true);
    dispatch(fetchTaskActivity({
      taskActivityUrl: getApiUrl('/api/ui/tasks'),
      cardId: selectedCard.id,
    }))
      .unwrap()
      .catch(() => {
        setTaskActivity([]);
      })
      .finally(() => {
        setTaskActivityLoading(false);
      });
    return undefined;
  }, [dispatch, getApiUrl, selectedCard?.id]);

  useEffect(() => {
    if (!selectedTaskActivityEntry || selectedTaskActivityEntry.card_id !== selectedCard?.id) {
      return;
    }
    setTaskActivity(selectedTaskActivityEntry.items || []);
  }, [selectedCard?.id, selectedTaskActivityEntry]);

  // Leave title-edit mode whenever the selected task changes so a stale draft
  // never leaks across selections.
  useEffect(() => {
    setIsEditingTitle(false);
    setTitleDraft('');
  }, [selectedCard?.id]);

  useEffect(() => {
    if (!isEditingTitle) return undefined;
    titleInputRef.current?.focus();
    titleInputRef.current?.select();
    return undefined;
  }, [isEditingTitle]);

  const startTitleEdit = useCallback(() => {
    setTitleDraft(selectedCard?.title || '');
    setIsEditingTitle(true);
  }, [selectedCard?.title]);

  const cancelTitleEdit = useCallback(() => {
    setIsEditingTitle(false);
    setTitleDraft('');
  }, []);

  const saveTitleEdit = useCallback(async () => {
    if (!selectedCard?.id) return;
    const nextTitle = titleDraft.replace(/\s+/g, ' ').trim();
    if (!nextTitle || nextTitle === (selectedCard.title || '')) {
      cancelTitleEdit();
      return;
    }
    setIsEditingTitle(false);
    setSaving(true);
    try {
      // The board refreshes via the kanban.snapshot event; no refetch needed.
      await updateKanbanCard({ cardId: selectedCard.id, payload: { title: nextTitle } }, { dispatch, getApiUrl });
    } catch (saveError) {
      show('error', 'Rename failed', saveError.message || 'Failed to rename task');
    } finally {
      setSaving(false);
    }
  }, [cancelTitleEdit, dispatch, getApiUrl, selectedCard?.id, selectedCard?.title, show, titleDraft, updateKanbanCard]);

  const handleDeleteSelected = useCallback(async () => {
    if (!selectedCard) {
      return;
    }
    const deletedTitle = selectedCard.title;
    setSaving(true);
    try {
      await deleteKanbanCard({ cardId: selectedCard.id }, { dispatch, getApiUrl });
      // Trust the kanban.snapshot event to remove the card from the store; clear
      // the selection so selectedCard falls back to the first visible card.
      setSelectedCardId(null);
      show('success', 'Task deleted', deletedTitle);
    } catch (deleteError) {
      show('error', 'Delete failed', deleteError.message || 'Failed to delete task');
    } finally {
      setSaving(false);
    }
  }, [deleteKanbanCard, dispatch, getApiUrl, selectedCard, show]);

  const handleQuickStatusUpdate = useCallback(async (nextStatus) => {
    if (!selectedCard?.id || selectedCard.status === nextStatus) {
      return;
    }
    setStatusSaving(true);
    try {
      await updateKanbanCard({ cardId: selectedCard.id, payload: { status: nextStatus } }, { dispatch, getApiUrl });
      // The board refreshes via the kanban.snapshot event; no refetch needed.
    } catch (updateError) {
      show('error', 'Status update failed', updateError.message || 'Failed to update task status');
    } finally {
      setStatusSaving(false);
    }
  }, [dispatch, getApiUrl, selectedCard?.id, selectedCard?.status, show, updateKanbanCard]);

  const handleMetadataUpdate = useCallback(async (nextMetadata, activityMessage = '') => {
    if (!selectedCard?.id) {
      return;
    }
    setMetadataSaving(true);
    try {
      await updateKanbanCard({ cardId: selectedCard.id, payload: { metadata: nextMetadata } }, { dispatch, getApiUrl });
      // The board refreshes via the kanban.snapshot event; no refetch needed.
      if (activityMessage) {
        await createTaskActivity({ cardId: selectedCard.id, payload: {
          message: activityMessage,
          actor: 'user',
          kind: 'meta',
        } }, { dispatch, getApiUrl });
        // `task.activity.updated` appends the canonical activity item in Redux;
        // the acknowledgement carries only correlation metadata.
      }
    } catch (updateError) {
      show('error', 'Update failed', updateError.message || 'Failed to update task metadata');
    } finally {
      setMetadataSaving(false);
    }
  }, [createTaskActivity, dispatch, getApiUrl, selectedCard?.id, show, updateKanbanCard]);

  // ---- tags (free-form, searchable; stored on card.labels) -----------------
  const handleAddTag = useCallback(async (value) => {
    if (!selectedCard?.id || !value || selectedTags.includes(value)) {
      return;
    }
    setSaving(true);
    try {
      await updateKanbanCard({ cardId: selectedCard.id, payload: { labels: [...selectedTags, value] } }, { dispatch, getApiUrl });
    } catch (updateError) {
      show('error', 'Tag failed', updateError.message || 'Failed to add tag');
    } finally {
      setSaving(false);
    }
  }, [dispatch, getApiUrl, selectedCard?.id, selectedTags, show, updateKanbanCard]);

  const handleRemoveTag = useCallback(async (value) => {
    if (!selectedCard?.id) {
      return;
    }
    setSaving(true);
    try {
      await updateKanbanCard({ cardId: selectedCard.id, payload: { labels: selectedTags.filter((tag) => tag !== value) } }, { dispatch, getApiUrl });
    } catch (updateError) {
      show('error', 'Tag failed', updateError.message || 'Failed to remove tag');
    } finally {
      setSaving(false);
    }
  }, [dispatch, getApiUrl, selectedCard?.id, selectedTags, show, updateKanbanCard]);

  // ---- checklist (exactly one per task) ------------------------------------
  // Persist the flat item list back as the single checklist document.
  const commitChecklistItems = useCallback((items, activityMessage) => {
    const meta = { ...(selectedCard?.metadata || {}) };
    delete meta.subtasks; // migrate legacy single-list metadata onto `checklists`
    meta.checklists = serializeChecklists([{ id: 'default', title: 'Checklist', items }]);
    return handleMetadataUpdate(meta, activityMessage);
  }, [handleMetadataUpdate, selectedCard?.metadata]);

  const handleAddChecklistItem = useCallback(async (title) => {
    if (!selectedCard || !title) {
      return;
    }
    const nextItems = [...checklistItems, { id: `item_${Date.now().toString(16)}`, title, done: false }];
    await commitChecklistItems(nextItems, `Added checklist item: ${title}`);
  }, [checklistItems, commitChecklistItems, selectedCard]);

  const handleToggleChecklistItem = useCallback(async (itemId) => {
    if (!selectedCard) {
      return;
    }
    let toggled = null;
    const nextItems = checklistItems.map((item) => {
      if (item.id !== itemId) {
        return item;
      }
      toggled = { ...item, done: !item.done };
      return toggled;
    });
    await commitChecklistItems(
      nextItems,
      toggled ? `${toggled.done ? 'Completed' : 'Reopened'} item: ${toggled.title}` : '',
    );
  }, [checklistItems, commitChecklistItems, selectedCard]);

  const handleRemoveChecklistItem = useCallback(async (itemId) => {
    if (!selectedCard) {
      return;
    }
    const removed = checklistItems.find((item) => item.id === itemId);
    const nextItems = checklistItems.filter((item) => item.id !== itemId);
    await commitChecklistItems(nextItems, removed ? `Removed item: ${removed.title}` : '');
  }, [checklistItems, commitChecklistItems, selectedCard]);

  // ---- dependencies / collaborators / attachments --------------------------
  const commitDependencies = useCallback((nextDependencies, activityMessage) => (
    handleMetadataUpdate(
      { ...(selectedCard?.metadata || {}), dependencies: serializeDependencies(nextDependencies) },
      activityMessage,
    )
  ), [handleMetadataUpdate, selectedCard?.metadata]);

  const handleAddDependency = useCallback(async (title) => {
    if (!selectedCard || !title) {
      return;
    }
    const draft = buildDependencyDraft(title, board);
    if (!draft) {
      return;
    }
    const nextDependencies = [
      ...selectedDependencies,
      { id: `dep_${Date.now().toString(16)}`, title: draft.title, ref: draft.ref },
    ];
    await commitDependencies(nextDependencies, `Added dependency: ${draft.title}`);
  }, [board, commitDependencies, selectedCard, selectedDependencies]);

  const handleRemoveDependency = useCallback(async (id) => {
    if (!selectedCard) {
      return;
    }
    const removed = selectedDependencies.find((dep) => dep.id === id);
    const nextDependencies = selectedDependencies.filter((dep) => dep.id !== id);
    await commitDependencies(nextDependencies, removed ? `Removed dependency: ${removed.title}` : '');
  }, [commitDependencies, selectedCard, selectedDependencies]);

  // "Open" jumps to the blocking task when the dependency resolves to a board
  // card (by ref id or matching title); otherwise it's a no-op.
  const handleOpenDependency = useCallback((dep) => {
    const match = board?.cards.find(
      (card) => card.id === dep.ref || (card.title || '').toLowerCase() === dep.title.toLowerCase(),
    );
    if (match) {
      setSelectedCardId(match.id);
    }
  }, [board]);

  const handleAddCollaborator = useCallback(async (value) => {
    if (!selectedCard || !value) {
      return;
    }
    const nextCollaborators = [...selectedCollaborators, value];
    await handleMetadataUpdate(
      { ...(selectedCard.metadata || {}), collaborators: nextCollaborators },
      `Added collaborator: ${value}`,
    );
  }, [handleMetadataUpdate, selectedCard, selectedCollaborators]);

  const handleRemoveCollaborator = useCallback(async (value) => {
    if (!selectedCard) {
      return;
    }
    const nextCollaborators = selectedCollaborators.filter((item) => item !== value);
    await handleMetadataUpdate(
      { ...(selectedCard.metadata || {}), collaborators: nextCollaborators },
      `Removed collaborator: ${value}`,
    );
  }, [handleMetadataUpdate, selectedCard, selectedCollaborators]);

  const handleAddAttachment = useCallback(async (label, url) => {
    if (!selectedCard || !label || !url) {
      return;
    }
    const nextAttachments = [
      ...selectedAttachments,
      {
        id: `attachment_${Date.now().toString(16)}`,
        label,
        url,
        kind: url.includes('/pull/') ? 'pr' : 'link',
      },
    ];
    await handleMetadataUpdate(
      { ...(selectedCard.metadata || {}), attachments: nextAttachments },
      `Added attachment: ${label}`,
    );
  }, [handleMetadataUpdate, selectedAttachments, selectedCard]);

  const handleRemoveAttachment = useCallback(async (attachmentId) => {
    if (!selectedCard) {
      return;
    }
    const removed = selectedAttachments.find((item) => item.id === attachmentId);
    const nextAttachments = selectedAttachments.filter((item) => item.id !== attachmentId);
    await handleMetadataUpdate(
      { ...(selectedCard.metadata || {}), attachments: nextAttachments },
      removed ? `Removed attachment: ${removed.label}` : '',
    );
  }, [handleMetadataUpdate, selectedAttachments, selectedCard]);

  return (
    <PageShell className={`tasks-page ${context ? 'tasks-page--project' : ''}`}>
      <PaneSection className={paneClassName} aria-label="Tasks" onItemSelect={() => setCollapsed(true)}>
        {collapsed ? (
          <PaneExpandRail label={context?.listTitle || 'Tasks'} onExpand={() => setCollapsed(false)} />
        ) : (
        <>
        <PaneCollapseSeam label={context?.listTitle || 'Tasks'} onCollapse={() => setCollapsed(true)} />
        <button type="button" className="pane-mobile-scrim" aria-label="Close Tasks list" onClick={() => setCollapsed(true)} />
        <PaneListFilters
          filters={TASK_FILTERS}
          activeFilter={taskFilter}
          onFilterChange={setTaskFilter}
          actionLabel={context?.newCardLabel || '+ New'}
          onAction={openCreateTask}
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
      </PaneSection>

      <DetailPane flush className="task-detail">
        {selectedCard ? (
          <>
            <DetailHeader
              titleClassName="chat-detail-header__title"
              title={(
                <>
                  {isEditingTitle ? (
                    <input
                      ref={titleInputRef}
                      type="text"
                      className="chat-page-header__title-input"
                      value={titleDraft}
                      maxLength={160}
                      aria-label="Task name"
                      onChange={(event) => setTitleDraft(event.target.value)}
                      onBlur={cancelTitleEdit}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          event.preventDefault();
                          saveTitleEdit();
                        } else if (event.key === 'Escape') {
                          event.preventDefault();
                          cancelTitleEdit();
                        }
                      }}
                    />
                  ) : (
                    <span className="chat-detail-header__title-text">{selectedCard.title}</span>
                  )}
                  <button
                    type="button"
                    className="chat-icon-button chat-detail-header__rename"
                    data-tooltip={isEditingTitle ? 'Save task name' : 'Rename task'}
                    aria-label={isEditingTitle ? 'Save task name' : 'Rename task'}
                    onMouseDown={isEditingTitle ? (event) => event.preventDefault() : undefined}
                    onClick={isEditingTitle ? saveTitleEdit : startTitleEdit}
                  >
                    {isEditingTitle ? <CheckIcon /> : <PencilIcon />}
                  </button>
                </>
              )}
              actions={(
                <DetailHeaderActions>
                  <DetailHeaderAction
                    icon={<PlayActionIcon />}
                    label="Start"
                    tooltip="Mark task running"
                    ariaLabel="Mark task running"
                    variant="primary"
                    onClick={() => handleQuickStatusUpdate('running')}
                    disabled={statusSaving || selectedCard.status === 'running'}
                  />
                  <DetailHeaderAction
                    icon={<SaveActionIcon />}
                    label="Complete"
                    tooltip="Mark task complete"
                    ariaLabel="Mark task complete"
                    onClick={() => handleQuickStatusUpdate('complete')}
                    disabled={statusSaving || selectedCard.status === 'complete'}
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
                <DetailStatus key="status" tone={activeStatusMeta.tone}>{activeStatusMeta.label}</DetailStatus>,
                context?.projectName ? <span key="project" className="taskid">{context.solutionName} / {context.projectName}</span> : null,
                selectedColumn ? <span key="column" className="taskid">{selectedColumn.label}</span> : null,
                <PriorityPill key="priority" priority={selectedPriority} inline />,
                // Id last: its length is variable, so trailing it keeps the
                // leading meta aligned across tasks.
                <span key="id" className="taskid">#{selectedCard.id}</span>,
              ]}
              tabs={['Task', 'Description', 'Activity', 'References']}
              activeTab={detailTab}
              onTabChange={setDetailTab}
              tabLabel="Task detail tabs"
            />

            <div className="task-detail__body">
              {detailTab === 'Task' ? (
                <div className="task-overview">
                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Checklist <span>{checklistProgress.done}/{checklistProgress.total} done</span></h2>
                      <InlineAdd
                        pill
                        openLabel="Add item"
                        placeholder="Add an item..."
                        disabled={metadataSaving}
                        onSubmit={handleAddChecklistItem}
                      />
                    </header>
                    {checklistItems.length ? (
                      <div className="task-progress">
                        <span style={{ width: `${checklistProgress.total ? Math.round((checklistProgress.done / checklistProgress.total) * 100) : 0}%` }} />
                      </div>
                    ) : null}
                    {checklistItems.map((item) => (
                      <div key={item.id} className="task-assignee-row" style={{ marginTop: 8 }}>
                        <label className="task-ck" style={{ flex: 1 }}>
                          <input
                            type="checkbox"
                            checked={item.done}
                            onChange={() => handleToggleChecklistItem(item.id)}
                            disabled={metadataSaving}
                          />
                          <span>{item.title}</span>
                        </label>
                        <button
                          type="button"
                          className="task-lbl task-lbl--add"
                          aria-label={`Remove item ${item.title}`}
                          onClick={() => handleRemoveChecklistItem(item.id)}
                          disabled={metadataSaving}
                        >
                          ×
                        </button>
                      </div>
                    ))}
                    {!checklistItems.length ? (
                      <div className="task-meta-sub" style={{ marginTop: 8 }}>No checklist items yet.</div>
                    ) : null}
                  </section>

                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Dependencies <span>{blockedLabel}</span></h2>
                      <InlineAdd
                        pill
                        openLabel="Add dependency"
                        placeholder="Add dependency reference..."
                        disabled={metadataSaving}
                        onSubmit={handleAddDependency}
                      />
                    </header>
                    {selectedDependencies.length ? selectedDependencies.map((dependency) => (
                      <div key={dependency.id} className="task-dep-card" style={{ marginTop: 8 }}>
                        <div className="task-dep-card__main">
                          <div className="task-dep-card__title">{dependency.title}</div>
                          {dependency.ref ? <div className="task-dep-card__id">#{dependency.ref}</div> : null}
                        </div>
                        <button
                          type="button"
                          className="task-dep-card__open"
                          data-tooltip="Open blocking task"
                          aria-label={`Open blocking task ${dependency.title}`}
                          onClick={() => handleOpenDependency(dependency)}
                        >
                          <DependencyOpenIcon />
                        </button>
                        <button
                          type="button"
                          className="task-dep-card__remove"
                          aria-label={`Remove dependency ${dependency.title}`}
                          onClick={() => handleRemoveDependency(dependency.id)}
                          disabled={metadataSaving}
                        >
                          ×
                        </button>
                      </div>
                    )) : <div className="task-meta-sub" style={{ marginTop: 8 }}>Not blocked by any tasks.</div>}
                  </section>

                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Tags</h2>
                      <InlineAdd
                        pill
                        openLabel="Add tag"
                        placeholder="Add a tag..."
                        disabled={saving}
                        onSubmit={handleAddTag}
                      />
                    </header>
                    <div className="task-labels">
                      {selectedTags.length ? selectedTags.map((tag, index) => (
                        <span key={tag} className={`task-lbl task-lbl--${['green', 'red', 'orange', 'blue'][index % 4]}`}>
                          {tag}
                          <button
                            type="button"
                            className="task-tag-remove"
                            aria-label={`Remove tag ${tag}`}
                            onClick={() => handleRemoveTag(tag)}
                            disabled={saving}
                          >
                            ×
                          </button>
                        </span>
                      )) : <span className="task-meta-sub">No tags</span>}
                    </div>
                  </section>

                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Assignees</h2>
                      <InlineAdd
                        pill
                        openLabel="Add collaborator"
                        placeholder="Add collaborator handle..."
                        disabled={metadataSaving}
                        onSubmit={handleAddCollaborator}
                      />
                    </header>
                    <div className="task-assignee-row">
                      <span className="task-av" style={{ background: selectedAssignee?.color }}>{selectedAssignee?.initial}</span>
                      <div className="task-ar-body">
                        <div className="task-ar-name">{selectedAssignee?.name} <span className="muted small">(Primary)</span></div>
                        <div className="task-ar-handle">@{String(selectedCard.agent_name || selectedCard.owner || 'user').toLowerCase()}</div>
                      </div>
                      <span className="task-online" />
                    </div>
                    {selectedCollaborators.length ? selectedCollaborators.map((collaborator, index) => {
                      const color = agentColors[(index + 1) % agentColors.length];
                      const initial = collaborator.charAt(0).toUpperCase() || 'C';
                      return (
                        <div key={collaborator} className="task-assignee-row" style={{ marginTop: 8 }}>
                          <span className="task-av" style={{ background: color }}>{initial}</span>
                          <div className="task-ar-body">
                            <div className="task-ar-name">{formatAgentName(collaborator)}</div>
                            <div className="task-ar-handle">@{collaborator.toLowerCase()}</div>
                          </div>
                          <button
                            type="button"
                            className="task-lbl task-lbl--add"
                            onClick={() => handleRemoveCollaborator(collaborator)}
                            disabled={metadataSaving}
                          >
                            ×
                          </button>
                        </div>
                      );
                    }) : null}
                  </section>

                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Attachments</h2>
                      <InlineAddAttachment disabled={metadataSaving} onSubmit={handleAddAttachment} />
                    </header>
                    {selectedAttachments.length ? selectedAttachments.map((attachment) => (
                      <div key={attachment.id} className="task-attach">
                        <span className="task-attach-ic" />
                        <a className="task-attach-name" href={attachment.url} target="_blank" rel="noreferrer">{attachment.label}</a>
                        <span className="task-attach-ext">{attachment.kind}</span>
                        <button
                          type="button"
                          className="task-lbl task-lbl--add"
                          onClick={() => handleRemoveAttachment(attachment.id)}
                          disabled={metadataSaving}
                        >
                          ×
                        </button>
                      </div>
                    )) : selectedBadges.length ? selectedBadges.map((badge) => (
                      <div key={badge.key} className="task-attach">
                        <span className="task-attach-ic" />
                        <span className="task-attach-name">{badge.label}</span>
                        <span className="task-attach-ext">{badge.key}</span>
                      </div>
                    )) : <div className="task-meta-sub">No attachments linked.</div>}
                  </section>
                </div>
              ) : null}

              {detailTab === 'Description' ? (
                <p className="task-description">
                  {selectedCard.description || selectedCard.summary || 'No detailed task body has been added yet.'}
                </p>
              ) : null}

              {detailTab === 'Activity' ? (
                <DetailSection title="Activity">
                  <div className="task-activity">
                    <div className="task-act">
                      <span className="task-act-avatar" style={{ background: '#22a06b' }}>S</span>
                      <div className="task-act-body"><strong>System</strong> created the card <span className="muted">{formatRelativeTime(selectedCard.created_at)} ago</span></div>
                    </div>
                    {taskActivityLoading ? <div className="task-meta-sub">Loading activity…</div> : null}
                    {!taskActivityLoading && taskActivity.map((entry) => {
                      const actorName = formatAgentName(entry.actor || 'system');
                      const initial = actorName.charAt(0).toUpperCase() || 'S';
                      const avatarColor = entry.actor === 'user' ? '#2f6ff6' : entry.kind === 'status' ? '#ef6c2a' : '#22a06b';
                      return (
                        <div key={entry.id} className="task-act">
                          <span className="task-act-avatar" style={{ background: avatarColor }}>{initial}</span>
                          <div className="task-act-body"><strong>{actorName}</strong> {entry.message} <span className="muted">{formatRelativeTime(entry.created_at)} ago</span></div>
                        </div>
                      );
                    })}
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

    </PageShell>
  );
}
