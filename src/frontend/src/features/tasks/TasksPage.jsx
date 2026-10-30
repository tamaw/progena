import { UI_EVENT } from '../../realtime/eventVocabulary';
import { usePaneCollapse } from '../../hooks/usePaneCollapse';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../../lib/routes';
import { formatCompactRelativeAge } from '../../lib/displayTime';
import { createUuid } from '../../lib/uuid';
import { reportFailure } from '../../lib/failures';
import { dispatchQuickChatOpen, QUICK_CHAT_MODE } from '../../lib/browserContracts';

import DetailHeader, { DeleteActionIcon, DetailHeaderAction, DetailHeaderActions, DetailStatus, PlayActionIcon, SaveActionIcon } from '../../components/layout/DetailHeader';
import { PaneCollapseSeam, PaneEmptyState, PaneExpandRail, PaneListFilters, PaneListFooter, PaneListSearch, PaneListSortButton, PaneListToolbar, PaneListRows, PaneSection } from '../../components/layout/PaneList';
import PageShell from '../../components/layout/PageShell';
import DetailPane from '../../components/layout/DetailPane';
import EditableDetailTitle from '../../components/EditableDetailTitle';
import InlineFailure from '../../components/InlineFailure';
import PopupModal from '../../components/PopupModal';
import { useConfig } from '../../context/ConfigContext';
import { useNotifications } from '../../context/NotificationContext';
import { useTasksApi } from './useTasksApi';
import useConfirmAction from '../../hooks/useConfirmAction';
import useEntityDraft from '../../hooks/useEntityDraft';
import useInlineTitleEditor from '../../hooks/useInlineTitleEditor';
import useMissingEntityRoute from '../../hooks/useMissingEntityRoute';
import useUiAction from '../../hooks/useUiAction';
import { entityDraftKey } from '../../store/editorDraftsSlice';
import { fetchKanbanBoard, fetchTaskActivity, selectKanbanBoard, selectTaskActivityEntry, selectTaskActivityRequest, selectTasksState } from './state';
import { selectRealtime } from '../../realtime/realtimeSlice';
import { agentColors, buildCardBadges, cardTags, formatAgentName, getCardAssignee, groupVisibleCards, isArchivedCard, normalizePriority, statusMeta } from './tasksPageModel';
import { DependencyOpenIcon } from './components/TaskIcons';
import { PriorityPill, TaskRow } from './components/TaskRow';
import { DetailSection } from './components/DetailSection';
import { InlineAdd, InlineAddAttachment } from './components/TaskInlineEditors';
import { normalizeChecklists, serializeChecklists, normalizeDependencies, serializeDependencies, normalizeCollaborators, normalizeAttachments, buildDependencyDraft } from './taskMetadata';

// Sort modes offered by the toolbar sort menu (mirrors the Agents page dropdown).
const SORT_MODES = ['Priority', 'Recent', 'Name'];

const TASK_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'mine', label: 'My tasks' },
  { value: 'watching', label: 'Watching' },
];

const ACTIVITY_AVATAR_COLORS = {
  user: agentColors[0],
  status: agentColors[3],
  system: agentColors[2],
};

export default function TasksPage({ context = null }) {
  const { updateKanbanCard, deleteKanbanCard, createTaskActivity } = useTasksApi();
  const navigate = useNavigate();
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
  const routedCardId = isStandaloneTasksPage && typeof urlCardId === 'string' && urlCardId.trim().length > 0
    ? urlCardId
    : null;
  const board = useSelector(selectKanbanBoard);
  const tasksState = useSelector(selectTasksState);
  const embeddedSelectionKey = context?.projectId
    ? entityDraftKey('task-board', context.projectId, 'selection')
    : null;
  const [selectedCardId, setSelectedCardId, { clear: clearSelectedCardId }] = useEntityDraft(
    embeddedSelectionKey,
    null,
  );
  const [taskFilter, setTaskFilter] = useState('all');
  const [detailTab, setDetailTab] = useState('Task');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortMode, setSortMode] = useState('Priority');
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('tasks-list');

  const listFailure = tasksState.error;
  const loadBoard = useCallback(() => (
    dispatch(fetchKanbanBoard({
      kanbanUrl: getApiUrl('kanban'),
    }))
  ), [dispatch, getApiUrl]);

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

  const effectiveSelectedCardId = isStandaloneTasksPage ? routedCardId : selectedCardId;
  const selectedCard = useMemo(() => {
    if (!board) {
      return null;
    }
    return board.cards.find((card) => card.id === effectiveSelectedCardId)
      || ((!isStandaloneTasksPage || !routedCardId) ? visibleCards[0] || null : null);
  }, [board, effectiveSelectedCardId, isStandaloneTasksPage, routedCardId, visibleCards]);

  // Keep the URL in sync with whichever task is actually selected. A routed id
  // stays authoritative until task hydration confirms it is absent. Disabled
  // when embedded in a project's kanban board, which doesn't own the /tasks URL.
  useEntityUrlSync(isStandaloneTasksPage ? '/tasks' : null, selectedCard?.id, selectedCard?.title);
  useMissingEntityRoute({
    hydrated: isStandaloneTasksPage && tasksState.loaded,
    entityId: routedCardId,
    entities: board?.cards ?? [],
    getEntityId: (card) => card.id,
    fallbackPath: '/tasks',
  });

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
  const taskScopeKey = selectedCard?.id ?? null;
  const titleAction = useUiAction(taskScopeKey);
  const statusAction = useUiAction(taskScopeKey);
  const metadataAction = useUiAction(taskScopeKey);
  const tagsAction = useUiAction(taskScopeKey);
  const deleteConfirmation = useConfirmAction(taskScopeKey);
  const { run: runTitleAction } = titleAction;
  const { run: runStatusAction } = statusAction;
  const { run: runMetadataAction } = metadataAction;
  const { run: runTagsAction } = tagsAction;
  const { request: requestTaskDelete } = deleteConfirmation;
  const selectedTaskActivityEntry = useSelector((state) => selectTaskActivityEntry(state, taskScopeKey));
  const selectedTaskActivityRequest = useSelector((state) => selectTaskActivityRequest(state, taskScopeKey));
  const taskActivity = selectedTaskActivityEntry?.items || [];
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

  // Creation is delegated to the Progena agent (same pattern as Tools and
  // Schedules): there is no local create form. The agent writes `/tasks/`
  // entries and the new card arrives here live via the kanban.snapshot event.
  const openCreateTask = useCallback(() => {
    dispatchQuickChatOpen({ mode: QUICK_CHAT_MODE.TASK_CREATE });
  }, []);
  const selectTaskCard = useCallback((cardId, title = cardId) => {
    if (!cardId) {
      return;
    }
    if (isStandaloneTasksPage) {
      navigate(buildEntityPath('/tasks', cardId, title), { replace: true });
      return;
    }
    setSelectedCardId(cardId);
  }, [isStandaloneTasksPage, navigate, setSelectedCardId]);

  const requestTaskActivity = useCallback((cardId) => {
    if (!cardId) {
      return;
    }
    void dispatch(fetchTaskActivity({
      taskActivityUrl: getApiUrl('/api/ui/tasks'),
      cardId,
    }));
  }, [dispatch, getApiUrl]);

  useEffect(() => {
    requestTaskActivity(taskScopeKey);
  }, [requestTaskActivity, taskScopeKey]);

  const commitTitle = useCallback((nextTitle, { ticket, trackSave, failSave }) => {
    const target = selectedCard;
    if (!target) {
      failSave(ticket, 'Task is no longer selected. Please try again.');
      return Promise.resolve({ ok: false, stale: true });
    }

    const requestId = createUuid();
    trackSave(ticket, {
      requestId,
      events: [UI_EVENT.KANBAN_SNAPSHOT],
    });
    let dispatched = false;
    return runTitleAction('Rename task', async () => {
      dispatched = true;
      try {
        const acknowledgement = await updateKanbanCard({
          cardId: target.id,
          payload: { title: nextTitle },
          requestId,
        });
        return acknowledgement;
      } catch (commitError) {
        // useUiAction fences stale UI state, not the retained record that
        // originated this request. Settle that original record directly.
        failSave(ticket, commitError);
        throw commitError;
      }
    }).then((result) => {
      if (!dispatched && !result.ok) {
        failSave(ticket, result.error || 'Task is no longer selected. Please try again.');
      }
      return result;
    });
  }, [runTitleAction, selectedCard, updateKanbanCard]);
  const titleEditor = useInlineTitleEditor({
    kind: 'task',
    scopeKey: taskScopeKey,
    title: selectedCard?.title || '',
    onCommit: commitTitle,
  });

  const handleDeleteSelected = useCallback(() => {
    const target = selectedCard;
    if (!target) {
      return;
    }
    requestTaskDelete('Delete task', async ({ isCurrent }) => {
      await deleteKanbanCard({ cardId: target.id }, { dispatch, getApiUrl });
      if (!isCurrent()) {
        return;
      }
      // The canonical removal arrives through kanban.snapshot. Clear only this
      // detail's local selection after its request is accepted.
      if (isStandaloneTasksPage && routedCardId === target.id) {
        navigate('/tasks', { replace: true });
      } else {
        clearSelectedCardId();
      }
    });
  }, [
    clearSelectedCardId,
    requestTaskDelete,
    deleteKanbanCard,
    dispatch,
    getApiUrl,
    isStandaloneTasksPage,
    navigate,
    routedCardId,
    selectedCard,
  ]);

  const reportActionFailure = useCallback((operation, result) => {
    if (!result?.ok && result?.error) {
      reportFailure(show, operation, result.error);
    }
  }, [show]);

  const handleQuickStatusUpdate = useCallback(async (nextStatus) => {
    if (!selectedCard?.id || selectedCard.status === nextStatus) {
      return;
    }
    const result = await runStatusAction('Update task status', async () => {
      await updateKanbanCard(
        { cardId: selectedCard.id, payload: { status: nextStatus } },
        { dispatch, getApiUrl },
      );
    });
    reportActionFailure('Update task status', result);
    return result;
  }, [
    dispatch,
    getApiUrl,
    reportActionFailure,
    runStatusAction,
    selectedCard,
    updateKanbanCard,
  ]);

  const handleMetadataUpdate = useCallback((nextMetadata, activityMessage = '', draftSave = null) => {
    const target = selectedCard;
    if (!target?.id) {
      draftSave?.failSave(draftSave.ticket, 'Task is no longer selected. Please try again.');
      return Promise.resolve({ ok: false, stale: true });
    }

    const requestId = draftSave ? createUuid() : undefined;
    if (draftSave) {
      draftSave.trackSave(draftSave.ticket, {
        requestId,
        events: [UI_EVENT.KANBAN_SNAPSHOT],
      });
    }
    let dispatched = false;
    return runMetadataAction('Update task metadata', async () => {
      dispatched = true;
      try {
        const acknowledgement = await updateKanbanCard(
          { cardId: target.id, payload: { metadata: nextMetadata }, requestId },
          { dispatch, getApiUrl },
        );
        if (activityMessage) {
          try {
            await createTaskActivity({ cardId: target.id, payload: {
              message: activityMessage,
              actor: 'user',
              kind: 'meta',
            } }, { dispatch, getApiUrl });
          } catch (activityError) {
            reportFailure(show, 'Log task activity', activityError);
          }
        }
        return acknowledgement;
      } catch (commitError) {
        draftSave?.failSave(draftSave.ticket, commitError);
        throw commitError;
      }
    }).then((result) => {
      if (!dispatched && !result.ok) {
        draftSave?.failSave(
          draftSave.ticket,
          result.error || 'Task is no longer selected. Please try again.',
        );
      }
      reportActionFailure('Update task metadata', result);
      return result;
    });
  }, [
    createTaskActivity,
    dispatch,
    getApiUrl,
    reportActionFailure,
    runMetadataAction,
    selectedCard,
    show,
    updateKanbanCard,
  ]);

  // ---- tags (free-form, searchable; stored on card.labels) -----------------
  const handleAddTag = useCallback((value, draftSave) => {
    const target = selectedCard;
    if (!target?.id || !value || selectedTags.includes(value)) {
      draftSave?.failSave(draftSave.ticket, 'Tag cannot be added. Please try again.');
      return Promise.resolve({ ok: false, skipped: true });
    }

    const requestId = draftSave ? createUuid() : undefined;
    if (draftSave) {
      draftSave.trackSave(draftSave.ticket, {
        requestId,
        events: [UI_EVENT.KANBAN_SNAPSHOT],
      });
    }
    let dispatched = false;
    return runTagsAction('Add task tag', async () => {
      dispatched = true;
      try {
        const acknowledgement = await updateKanbanCard(
          { cardId: target.id, payload: { labels: [...selectedTags, value] }, requestId },
          { dispatch, getApiUrl },
        );
        return acknowledgement;
      } catch (commitError) {
        draftSave?.failSave(draftSave.ticket, commitError);
        throw commitError;
      }
    }).then((result) => {
      if (!dispatched && !result.ok) {
        draftSave?.failSave(draftSave.ticket, result.error || 'Tag cannot be added. Please try again.');
      }
      reportActionFailure('Add task tag', result);
      return result;
    });
  }, [
    dispatch,
    getApiUrl,
    reportActionFailure,
    runTagsAction,
    selectedCard,
    selectedTags,
    updateKanbanCard,
  ]);

  const handleRemoveTag = useCallback(async (value) => {
    if (!selectedCard?.id) {
      return;
    }
    const result = await runTagsAction('Remove task tag', async () => {
      await updateKanbanCard(
        { cardId: selectedCard.id, payload: { labels: selectedTags.filter((tag) => tag !== value) } },
        { dispatch, getApiUrl },
      );
    });
    reportActionFailure('Remove task tag', result);
    return result;
  }, [
    dispatch,
    getApiUrl,
    reportActionFailure,
    runTagsAction,
    selectedCard,
    selectedTags,
    updateKanbanCard,
  ]);

  // ---- checklist (exactly one per task) ------------------------------------
  // Persist the flat item list back as the single checklist document.
  const commitChecklistItems = useCallback((items, activityMessage, draftSave) => {
    const meta = { ...(selectedCard?.metadata || {}) };
    delete meta.subtasks; // migrate legacy single-list metadata onto `checklists`
    meta.checklists = serializeChecklists([{ id: 'default', title: 'Checklist', items }]);
    return handleMetadataUpdate(meta, activityMessage, draftSave);
  }, [handleMetadataUpdate, selectedCard?.metadata]);

  const handleAddChecklistItem = useCallback(async (title, draftSave) => {
    if (!selectedCard || !title) {
      draftSave?.failSave(draftSave.ticket, 'Task is no longer selected. Please try again.');
      return { ok: false, stale: true };
    }
    const nextItems = [...checklistItems, { id: `item_${Date.now().toString(16)}`, title, done: false }];
    return commitChecklistItems(nextItems, `Added checklist item: ${title}`, draftSave);
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
  const commitDependencies = useCallback((nextDependencies, activityMessage, draftSave) => (
    handleMetadataUpdate(
      { ...(selectedCard?.metadata || {}), dependencies: serializeDependencies(nextDependencies) },
      activityMessage,
      draftSave,
    )
  ), [handleMetadataUpdate, selectedCard?.metadata]);

  const handleAddDependency = useCallback(async (title, draftSave) => {
    if (!selectedCard || !title) {
      draftSave?.failSave(draftSave.ticket, 'Task is no longer selected. Please try again.');
      return { ok: false, stale: true };
    }
    const draft = buildDependencyDraft(title, board);
    if (!draft) {
      draftSave?.failSave(draftSave.ticket, 'Dependency cannot be added. Please try again.');
      return { ok: false, skipped: true };
    }
    const nextDependencies = [
      ...selectedDependencies,
      { id: `dep_${Date.now().toString(16)}`, title: draft.title, ref: draft.ref },
    ];
    return commitDependencies(nextDependencies, `Added dependency: ${draft.title}`, draftSave);
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
      selectTaskCard(match.id, match.title);
    }
  }, [board, selectTaskCard]);

  const handleAddCollaborator = useCallback(async (value, draftSave) => {
    if (!selectedCard || !value) {
      draftSave?.failSave(draftSave.ticket, 'Task is no longer selected. Please try again.');
      return { ok: false, stale: true };
    }
    const nextCollaborators = [...selectedCollaborators, value];
    return handleMetadataUpdate(
      { ...(selectedCard.metadata || {}), collaborators: nextCollaborators },
      `Added collaborator: ${value}`,
      draftSave,
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

  const handleAddAttachment = useCallback(async (label, url, draftSave) => {
    if (!selectedCard || !label || !url) {
      draftSave?.failSave(draftSave.ticket, 'Task is no longer selected. Please try again.');
      return { ok: false, stale: true };
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
    return handleMetadataUpdate(
      { ...(selectedCard.metadata || {}), attachments: nextAttachments },
      `Added attachment: ${label}`,
      draftSave,
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

        {loading ? <PaneEmptyState>Loading tasks...</PaneEmptyState> : null}
        {listFailure ? <InlineFailure failure={listFailure} operation="Load tasks" onRetry={() => { void loadBoard(); }} /> : null}
        {!loading && !listFailure && !visibleCards.length ? <PaneEmptyState>No matching tasks.</PaneEmptyState> : null}
        {!loading && groupedColumns.some((column) => column.cards.length) ? (
          <PaneListRows
            groups={groupedColumns
              .filter((column) => column.cards.length)
              .map((column) => ({ ...column, items: column.cards }))}
            getItemKey={(card) => card.id}
            renderItem={(card, index) => {
              const column = groupedColumns.find((entry) => entry.cards.some((item) => item.id === card.id));
              return (
                <TaskRow
                  card={card}
                  index={index}
                  selected={selectedCard?.id === card.id}
                  compact={column?.cards.length > 4 && index > 2}
                  onSelect={() => selectTaskCard(card.id, card.title)}
                />
              );
            }}
          />
        ) : null}

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
                <EditableDetailTitle
                  title={selectedCard.title}
                  editor={titleEditor}
                  maxLength={160}
                  ariaLabel="Task name"
                  entityLabel="task"
                />
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
                    disabled={statusAction.busy || selectedCard.status === 'running'}
                  />
                  <DetailHeaderAction
                    icon={<SaveActionIcon />}
                    label="Complete"
                    tooltip="Mark task complete"
                    ariaLabel="Mark task complete"
                    onClick={() => handleQuickStatusUpdate('complete')}
                    disabled={statusAction.busy || selectedCard.status === 'complete'}
                  />
                  <DetailHeaderAction
                    icon={<DeleteActionIcon />}
                    label="Delete"
                    tooltip="Delete task"
                    ariaLabel="Delete task"
                    variant="danger"
                    onClick={handleDeleteSelected}
                    disabled={deleteConfirmation.busy}
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
            {titleEditor.error ? <InlineFailure failure={titleEditor.error} operation="Rename task" /> : null}

            <div className="task-detail__body">
              {detailTab === 'Task' ? (
                <div className="task-overview">
                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Checklist <span>{checklistProgress.done}/{checklistProgress.total} done</span></h2>
                      <InlineAdd
                        key={taskScopeKey}
                        draftKey={entityDraftKey('task', taskScopeKey, 'checklist-add')}
                        pill
                        openLabel="Add item"
                        operation="Add checklist item"
                        placeholder="Add an item..."
                        disabled={metadataAction.busy}
                        onSubmit={handleAddChecklistItem}
                      />
                    </header>
                    <div className="agents-section__body">
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
                            disabled={metadataAction.busy}
                          />
                          <span>{item.title}</span>
                        </label>
                        <button
                          type="button"
                          className="task-lbl task-lbl--add"
                          aria-label={`Remove item ${item.title}`}
                          onClick={() => handleRemoveChecklistItem(item.id)}
                          disabled={metadataAction.busy}
                        >
                          ×
                        </button>
                      </div>
                    ))}
                    {!checklistItems.length ? (
                      <div className="task-meta-sub" style={{ marginTop: 8 }}>No checklist items yet.</div>
                    ) : null}
                    </div>
                  </section>

                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Dependencies <span>{blockedLabel}</span></h2>
                      <InlineAdd
                        key={taskScopeKey}
                        draftKey={entityDraftKey('task', taskScopeKey, 'dependency-add')}
                        pill
                        openLabel="Add dependency"
                        operation="Add dependency"
                        placeholder="Add dependency reference..."
                        disabled={metadataAction.busy}
                        onSubmit={handleAddDependency}
                      />
                    </header>
                    <div className="agents-section__body">
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
                          disabled={metadataAction.busy}
                        >
                          ×
                        </button>
                      </div>
                    )) : <div className="task-meta-sub" style={{ marginTop: 8 }}>Not blocked by any tasks.</div>}
                    </div>
                  </section>

                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Tags</h2>
                      <InlineAdd
                        key={taskScopeKey}
                        draftKey={entityDraftKey('task', taskScopeKey, 'tag-add')}
                        pill
                        openLabel="Add tag"
                        operation="Add tag"
                        placeholder="Add a tag..."
                        disabled={tagsAction.busy}
                        onSubmit={handleAddTag}
                      />
                    </header>
                    <div className="agents-section__body">
                    <div className="task-labels">
                      {selectedTags.length ? selectedTags.map((tag, index) => (
                        <span key={tag} className={`task-lbl task-lbl--${['green', 'red', 'orange', 'blue'][index % 4]}`}>
                          {tag}
                          <button
                            type="button"
                            className="task-tag-remove"
                            aria-label={`Remove tag ${tag}`}
                            onClick={() => handleRemoveTag(tag)}
                            disabled={tagsAction.busy}
                          >
                            ×
                          </button>
                        </span>
                      )) : <span className="task-meta-sub">No tags</span>}
                    </div>
                    </div>
                  </section>

                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Assignees</h2>
                      <InlineAdd
                        key={taskScopeKey}
                        draftKey={entityDraftKey('task', taskScopeKey, 'collaborator-add')}
                        pill
                        openLabel="Add collaborator"
                        operation="Add collaborator"
                        placeholder="Add collaborator handle..."
                        disabled={metadataAction.busy}
                        onSubmit={handleAddCollaborator}
                      />
                    </header>
                    <div className="agents-section__body">
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
                            disabled={metadataAction.busy}
                          >
                            ×
                          </button>
                        </div>
                      );
                    }) : null}
                    </div>
                  </section>

                  <section className="agents-section">
                    <header className="agents-section__head">
                      <h2>Attachments</h2>
                      <InlineAddAttachment
                        key={taskScopeKey}
                        draftKey={entityDraftKey('task', taskScopeKey, 'attachment-add')}
                        disabled={metadataAction.busy}
                        onSubmit={handleAddAttachment}
                      />
                    </header>
                    <div className="agents-section__body">
                    {selectedAttachments.length ? selectedAttachments.map((attachment) => (
                      <div key={attachment.id} className="task-attach">
                        <span className="task-attach-ic" />
                        <a className="task-attach-name" href={attachment.url} target="_blank" rel="noreferrer">{attachment.label}</a>
                        <span className="task-attach-ext">{attachment.kind}</span>
                        <button
                          type="button"
                          className="task-lbl task-lbl--add"
                          onClick={() => handleRemoveAttachment(attachment.id)}
                          disabled={metadataAction.busy}
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
                    </div>
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
                      <span className="task-act-avatar" style={{ background: ACTIVITY_AVATAR_COLORS.system }}>S</span>
                      <div className="task-act-body"><strong>System</strong> created the card <span className="muted">{formatCompactRelativeAge(selectedCard.created_at)}</span></div>
                    </div>
                    {selectedTaskActivityRequest.loading ? <div className="task-meta-sub">Loading activity…</div> : null}
                    {selectedTaskActivityRequest.error ? (
                      <InlineFailure
                        failure={selectedTaskActivityRequest.error}
                        operation="Load task activity"
                        onRetry={() => requestTaskActivity(taskScopeKey)}
                      />
                    ) : null}
                    {!selectedTaskActivityRequest.loading && taskActivity.map((entry) => {
                      const actorName = formatAgentName(entry.actor || 'system');
                      const initial = actorName.charAt(0).toUpperCase() || 'S';
                      const avatarColor = entry.actor === 'user'
                        ? ACTIVITY_AVATAR_COLORS.user
                        : entry.kind === 'status' ? ACTIVITY_AVATAR_COLORS.status : ACTIVITY_AVATAR_COLORS.system;
                      return (
                        <div key={entry.id} className="task-act">
                          <span className="task-act-avatar" style={{ background: avatarColor }}>{initial}</span>
                          <div className="task-act-body"><strong>{actorName}</strong> {entry.message} <span className="muted">{formatCompactRelativeAge(entry.created_at)}</span></div>
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
        ) : listFailure ? (
          <InlineFailure failure={listFailure} operation="Load tasks" onRetry={() => { void loadBoard(); }} />
        ) : (
          <PaneEmptyState detail>Select a task to inspect details.</PaneEmptyState>
        )}
      </DetailPane>

      {deleteConfirmation.open && selectedCard ? (
        <PopupModal
          title="Delete task?"
          message={`Delete “${selectedCard.title}”? This cannot be undone.`}
          confirmLabel="Delete task"
          busy={deleteConfirmation.busy}
          error={deleteConfirmation.error}
          onClose={deleteConfirmation.close}
          onConfirm={() => { void deleteConfirmation.confirm(); }}
        />
      ) : null}

    </PageShell>
  );
}
