import { UI_EVENT } from '../../realtime/eventVocabulary';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector, useStore } from 'react-redux';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../../lib/routes';
import { scheduleRunDetail, scheduleRunStatusTone } from './scheduleRunsModel';
import { createUuid } from '../../lib/uuid';
import Modal from '../../components/Modal';

import {
  PaneEmptyState,
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListRows,
} from '../../components/layout/PaneList';
import PageShell from '../../components/layout/PageShell';
import DetailPane from '../../components/layout/DetailPane';
import { MetricCard } from '../../components/DetailCards';
import DetailHeader, {
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  PauseActionIcon,
  PlayActionIcon,
} from '../../components/layout/DetailHeader';
import EditableDetailTitle from '../../components/EditableDetailTitle';
import InlineFailure from '../../components/InlineFailure';
import PopupModal from '../../components/PopupModal';
import { useSchedulesApi } from './useSchedulesApi';
import { useConfig } from '../../context/ConfigContext';
import { useNotifications } from '../../context/NotificationContext';
import { useWidgetsApi } from './useWidgetsApi';
import useConfirmAction from '../../hooks/useConfirmAction';
import useEntityDraft from '../../hooks/useEntityDraft';
import useInlineTitleEditor from '../../hooks/useInlineTitleEditor';
import useUiAction from '../../hooks/useUiAction';
import { reportFailure } from '../../lib/failures';
import { fetchSchedules, selectSchedulesState } from './state';
import { entityDraftKey } from '../../store/editorDraftsSlice';
import { selectRealtimeConnected } from '../../realtime/realtimeSlice';
import { selectAgentName, selectPreferences } from '../settings/state/preferences';
import { fetchChatSessions, selectChatSessions, selectChatSessionsState } from '../chat/state/chats';

const SCHEDULE_FILTERS = ['All', 'Active', 'Paused', 'Completed'];
const SCHEDULE_SORTS = ['Type', 'Next', 'Status'];

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

function pad2(value) {
  return String(value).padStart(2, '0');
}

function isOneShotCron(cron) {
  if (!cron) return false;
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return false;
  return parts[2] !== '*' && parts[3] !== '*';
}

// Best-effort human cadence from a cron expression. Falls back to the raw
// expression for shapes we do not special-case.
function cronToCadence(cron) {
  if (!cron) return '';
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [minute, hour, dom, month, dow] = parts;

  const everyN = (field) => {
    const match = /^\*\/(\d+)$/.exec(field);
    return match ? Number(match[1]) : null;
  };

  const minutesStep = everyN(minute);
  if (minutesStep && hour === '*' && dom === '*' && month === '*' && dow === '*') {
    return minutesStep === 1 ? 'Every minute' : `Every ${minutesStep} minutes`;
  }
  const hoursStep = everyN(hour);
  if (minute === '0' && hoursStep && dom === '*' && month === '*' && dow === '*') {
    return hoursStep === 1 ? 'Every hour' : `Every ${hoursStep} hours`;
  }
  const daysStep = everyN(dom);
  if (minute === '0' && hour === '0' && daysStep && month === '*' && dow === '*') {
    return daysStep === 1 ? 'Every day' : `Every ${daysStep} days`;
  }

  const isNum = (value) => /^\d+$/.test(value);
  if (isNum(minute) && isNum(hour)) {
    const time = `${pad2(hour)}:${pad2(minute)}`;
    if (dow !== '*' && isNum(dow)) {
      return `${WEEKDAYS[Number(dow) % 7]}s at ${time}`;
    }
    if (dom === '*' && month === '*' && dow === '*') {
      return `Every day at ${time}`;
    }
    if (isOneShotCron(cron)) {
      return `Once at ${time}`;
    }
  }
  return cron;
}

function effectiveScheduleTimezone(schedule, preferenceTimezone) {
  const entryTimezone = typeof schedule?.timezone === 'string' ? schedule.timezone.trim() : '';
  const preferredTimezone = typeof preferenceTimezone === 'string' ? preferenceTimezone.trim() : '';
  return entryTimezone || preferredTimezone || 'UTC';
}

function cadenceFor(schedule, timeZone) {
  if (schedule.scheduled_at) {
    return `Once · ${formatAbsolute(schedule.scheduled_at, timeZone)}`;
  }
  return cronToCadence(schedule.cron_expression || schedule.schedule);
}

function isOneOff(schedule) {
  return Boolean(schedule.scheduled_at) || isOneShotCron(schedule.cron_expression || schedule.schedule);
}

const KIND_ORDER = ['Scheduled', 'Recurring', 'One-off'];

// Human label for the trigger kind: Scheduled (cron), Recurring (interval),
// One-off (once). Derived from the entry's schedule_type, with a one-shot cron
// or an explicit datetime always counting as a one-off.
function scheduleKind(schedule) {
  if (isOneOff(schedule) || (schedule.schedule_type || '').toLowerCase() === 'once') {
    return 'One-off';
  }
  if ((schedule.schedule_type || '').toLowerCase() === 'interval') {
    return 'Recurring';
  }
  return 'Scheduled';
}

function isCompleted(schedule) {
  return (
    isOneOff(schedule)
    && !schedule.next_run
    && Boolean(schedule.last_run)
    && (
      schedule.last_run_status === 'completed'
      || schedule.paused
    )
  );
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

// Absolute date/time rendered in the schedule's effective timezone, so the
// displayed wall-clock matches when the schedule actually fires.
function formatAbsolute(value, timeZone) {
  const date = parseDate(value);
  if (!date) return '—';
  const opts = { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' };
  try {
    return date.toLocaleString(undefined, { ...opts, timeZone: timeZone || 'UTC', timeZoneName: 'short' });
  } catch {
    return date.toLocaleString(undefined, { ...opts, timeZone: 'UTC', timeZoneName: 'short' });
  }
}

// Compact relative label, e.g. "in 42m", "5h ago", or an absolute date (in the
// schedule's timezone) for anything beyond a few days out.
function formatRelative(value, timeZone) {
  const date = parseDate(value);
  if (!date) return '—';
  const diffMs = date.getTime() - Date.now();
  const future = diffMs >= 0;
  const absSec = Math.abs(diffMs) / 1000;

  let label;
  if (absSec < 60) {
    label = 'just now';
    return label;
  }
  if (absSec < 3600) {
    label = `${Math.round(absSec / 60)}m`;
  } else if (absSec < 86400) {
    label = `${Math.round(absSec / 3600)}h`;
  } else if (absSec < 86400 * 6) {
    label = `${Math.round(absSec / 86400)}d`;
  } else {
    return formatAbsolute(value, timeZone);
  }
  return future ? `in ${label}` : `${label} ago`;
}

function statusOf(schedule) {
  if (isCompleted(schedule)) return 'completed';
  return schedule.paused ? 'paused' : 'active';
}

function formatLogTime(value) {
  const date = parseDate(value);
  if (!date) return '';
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function matchesSchedule(schedule, filter, search, timeZone) {
  const status = statusOf(schedule);
  const matchesFilter =
    filter === 'All'
    || (filter === 'Active' && status === 'active')
    || (filter === 'Paused' && status === 'paused')
    || (filter === 'Completed' && status === 'completed');
  const haystack = [schedule.name, schedule.message, cadenceFor(schedule, timeZone)].join(' ').toLowerCase();
  return matchesFilter && (!search || haystack.includes(search.toLowerCase()));
}

function nextRunSortValue(schedule) {
  const nextRun = parseDate(schedule.next_run)?.getTime();
  if (Number.isFinite(nextRun)) return nextRun;
  const lastRun = parseDate(schedule.last_run)?.getTime();
  if (Number.isFinite(lastRun)) return lastRun + (365 * 24 * 60 * 60 * 1000);
  return Number.MAX_SAFE_INTEGER;
}

function statusRank(schedule) {
  const status = statusOf(schedule);
  if (status === 'active') return 0;
  if (status === 'paused') return 1;
  return 2;
}

function compareSchedules(a, b, sortMode) {
  if (sortMode === 'Next') {
    const nextDelta = nextRunSortValue(a) - nextRunSortValue(b);
    if (nextDelta !== 0) return nextDelta;
  } else if (sortMode === 'Status') {
    const rankDelta = statusRank(a) - statusRank(b);
    if (rankDelta !== 0) return rankDelta;
    const nextDelta = nextRunSortValue(a) - nextRunSortValue(b);
    if (nextDelta !== 0) return nextDelta;
  } else {
    const typeDelta = KIND_ORDER.indexOf(scheduleKind(a)) - KIND_ORDER.indexOf(scheduleKind(b));
    if (typeDelta !== 0) return typeDelta;
    const nextDelta = nextRunSortValue(a) - nextRunSortValue(b);
    if (nextDelta !== 0) return nextDelta;
  }
  return String(a.name || '').localeCompare(String(b.name || ''));
}

function conversationIdFor(schedule) {
  return typeof schedule?.conversation_id === 'string' && schedule.conversation_id
    ? schedule.conversation_id
    : null;
}

function destinationLabel(schedule, chatsById) {
  const conversationId = conversationIdFor(schedule);
  if (!conversationId) return 'Standalone';
  return chatsById.get(conversationId)?.title || 'Chat deleted';
}

function ScheduleRow({ schedule, selected, onSelect, timeZone, chatsById }) {
  const status = statusOf(schedule);
  return (
    <button
      type="button"
      className={`sess-row schedule-pane-row schedule-pane-row--${status} ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(schedule.id)}
    >
      <span className="sess-row__top">
        <span className={`sess-row__dot schedule-pane-row__status schedule-pane-row__status--${status}`} />
        <span className="sess-row__agent schedule-pane-row__name">{schedule.name || 'Untitled schedule'}</span>
      </span>
      <span className="sess-row__age">
        {status === 'completed' ? 'completed' : schedule.paused ? 'paused' : formatRelative(schedule.next_run, timeZone)}
      </span>
      <span className="sess-row__title">
        {[cadenceFor(schedule, timeZone), destinationLabel(schedule, chatsById)].filter(Boolean).join(' · ') || '—'}
      </span>
    </button>
  );
}

function SchedulesPage() {
  const navigate = useNavigate();
  const dispatch = useDispatch();
  const { show } = useNotifications();
  const { createSchedule, retriggerSchedule } = useSchedulesApi();
  const { getApiUrl } = useConfig();
  const { updateWidget, deleteWidget } = useWidgetsApi();
  const { scheduleId: urlScheduleId } = useParams();
  const selectedScheduleId = typeof urlScheduleId === 'string' && urlScheduleId.trim().length > 0
    ? urlScheduleId
    : null;
  // The list is owned by the Redux store and kept live by `schedule.snapshot`
  // events on /api/ui/stream (backend mutations + runtime fires/CRUD). We only
  // seed it once after realtime connects; every change after that arrives as an
  // event.
  const schedulesState = useSelector(selectSchedulesState);
  const {
    items: schedules,
    loading,
    loaded,
    error: listFailure,
    requestId: scheduleRequestId,
  } = schedulesState;
  const agentName = useSelector(selectAgentName);
  const preferenceTimezone = useSelector((state) => selectPreferences(state).timezone);
  const realtimeConnected = useSelector(selectRealtimeConnected);
  const chatSessions = useSelector(selectChatSessions);
  const chatSessionsState = useSelector(selectChatSessionsState);
  const availableChats = useMemo(
    () => chatSessions.filter((session) => !session.archived),
    [chatSessions],
  );
  const chatsById = useMemo(
    () => new Map(chatSessions.map((session) => [session.thread_id, session])),
    [chatSessions],
  );
  const [createOpen, setCreateOpen] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [createFailure, setCreateFailure] = useState(null);
  const [createDraft, setCreateDraft] = useState({
    name: '',
    instruction: '',
    expression: '',
    scheduleType: 'cron',
    timezone: '',
    conversationId: '',
  });
  const [activeFilter, setActiveFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [sortMode, setSortMode] = useState('Type');
  const [intentValidationFailure, setIntentValidationFailure] = useState(null);
  const [triggerValidationFailure, setTriggerValidationFailure] = useState(null);
  const intentInputRef = useRef(null);
  const triggerInputRef = useRef(null);
  const requestSchedules = useCallback(() => (
    dispatch(fetchSchedules({ schedulesUrl: getApiUrl('schedules') }))
  ), [dispatch, getApiUrl]);

  useEffect(() => {
    if (realtimeConnected) {
      void requestSchedules();
    }
  }, [realtimeConnected, requestSchedules]);

  useEffect(() => {
    if (realtimeConnected && !chatSessionsState.loaded && !chatSessionsState.loading) {
      dispatch(fetchChatSessions({ chatsUrl: getApiUrl('chats'), includeArchived: true }));
    }
  }, [chatSessionsState.loaded, chatSessionsState.loading, dispatch, getApiUrl, realtimeConnected]);


  const store = useStore();
  useEffect(() => {
    // Read the store, not this render: the mount refresh above dispatches in this
    // same commit, and a list cached from an earlier visit may lack a new schedule.
    const current = selectSchedulesState(store.getState());
    if (!current.loaded || current.loading || current.error || current.requestId !== null || !selectedScheduleId) {
      return;
    }
    if (!current.items.some((schedule) => schedule.id === selectedScheduleId)) {
      navigate('/schedules', { replace: true });
    }
  }, [listFailure, loaded, loading, navigate, scheduleRequestId, schedules, selectedScheduleId, store]);


  const visibleSchedules = useMemo(() => {
    const filtered = schedules.filter((schedule) => matchesSchedule(
      schedule,
      activeFilter,
      search,
      effectiveScheduleTimezone(schedule, preferenceTimezone),
    ));
    return [...filtered].sort((a, b) => compareSchedules(a, b, sortMode));
  }, [activeFilter, preferenceTimezone, schedules, search, sortMode]);

  const groupedSchedules = useMemo(() => {
    if (sortMode === 'Type') {
      return visibleSchedules.reduce(
        (groups, schedule) => {
          groups[scheduleKind(schedule)].push(schedule);
          return groups;
        },
        { Scheduled: [], Recurring: [], 'One-off': [] },
      );
    }
    if (sortMode === 'Status') {
      return visibleSchedules.reduce(
        (groups, schedule) => {
          const key = statusOf(schedule)[0].toUpperCase() + statusOf(schedule).slice(1);
          groups[key].push(schedule);
          return groups;
        },
        { Active: [], Paused: [], Completed: [] },
      );
    }
    return visibleSchedules.reduce(
      (groups, schedule) => {
        groups.Next.push(schedule);
        return groups;
      },
      { Next: [] },
    );
  }, [sortMode, visibleSchedules]);

  const selectedSchedule = useMemo(
    () => schedules.find((schedule) => schedule.id === selectedScheduleId)
      || (!selectedScheduleId ? visibleSchedules[0] || null : null),
    [schedules, selectedScheduleId, visibleSchedules],
  );
  const selectedScheduleTimezone = effectiveScheduleTimezone(selectedSchedule, preferenceTimezone);

  // Keep the URL in sync with whichever schedule is actually selected after
  // the initial snapshot has hydrated. Until then, preserve a deep-linked id
  // instead of replacing it with an empty/default selection.
  useEntityUrlSync(
    loading || scheduleRequestId !== null ? null : '/schedules',
    selectedSchedule?.id,
    selectedSchedule?.name,
  );

  const activeCount = schedules.filter((schedule) => !schedule.paused).length;
  const pausedCount = schedules.filter((schedule) => statusOf(schedule) === 'paused').length;
  const completedCount = schedules.filter((schedule) => statusOf(schedule) === 'completed').length;
  const order = sortMode === 'Type'
    ? KIND_ORDER
    : sortMode === 'Status'
      ? ['Active', 'Paused', 'Completed']
      : ['Next'];
  const recentScheduleRuns = Array.isArray(selectedSchedule?.runs) ? selectedSchedule.runs : [];
  const scheduleTitle = selectedSchedule?.name || 'Untitled schedule';

  const scheduleScopeKey = selectedSchedule?.id ?? null;
  const intentKey = entityDraftKey('schedule', scheduleScopeKey, 'intent');
  const triggerKey = entityDraftKey('schedule', scheduleScopeKey, 'trigger');
  const destinationKey = entityDraftKey('schedule', scheduleScopeKey, 'destination');
  const [intentDraft, setIntentDraft, intentDraftControls] = useEntityDraft(
    intentKey,
    { editing: false, text: selectedSchedule?.message || '' },
  );
  const [triggerDraft, setTriggerDraft, triggerDraftControls] = useEntityDraft(
    triggerKey,
    { editing: false, text: selectedSchedule ? cadenceFor(selectedSchedule, selectedScheduleTimezone) : '' },
  );
  const [destinationDraft, setDestinationDraft, destinationDraftControls] = useEntityDraft(
    destinationKey,
    { editing: false, conversationId: conversationIdFor(selectedSchedule) || '' },
  );
  const selectedDestinationChat = conversationIdFor(selectedSchedule)
    ? chatsById.get(conversationIdFor(selectedSchedule)) || null
    : null;
  const {
    beginSave: beginIntentSave,
    clear: clearIntentDraft,
    error: intentSaveError,
    failSave: failIntentSave,
    saving: intentSaving,
    trackSave: trackIntentSave,
  } = intentDraftControls;
  const {
    beginSave: beginTriggerSave,
    clear: clearTriggerDraft,
    error: triggerSaveError,
    failSave: failTriggerSave,
    saving: triggerSaving,
    trackSave: trackTriggerSave,
  } = triggerDraftControls;
  const {
    beginSave: beginDestinationSave,
    clear: clearDestinationDraft,
    error: destinationSaveError,
    failSave: failDestinationSave,
    saving: destinationSaving,
    trackSave: trackDestinationSave,
  } = destinationDraftControls;
  const pauseAction = useUiAction(scheduleScopeKey);
  const deleteConfirmation = useConfirmAction(scheduleScopeKey);
  const { run: runPauseAction } = pauseAction;
  const { request: requestScheduleDelete } = deleteConfirmation;
  const editingIntent = Boolean(intentDraft.editing);
  const editingTrigger = Boolean(triggerDraft.editing);
  const editingDestination = Boolean(destinationDraft.editing);

  const reportActionFailure = useCallback((operation, result) => {
    if (!result?.ok && result?.error) {
      reportFailure(show, operation, result.error);
    }
  }, [show]);

  const closeCreate = useCallback(() => {
    if (createBusy) return;
    setCreateOpen(false);
    setCreateFailure(null);
  }, [createBusy]);

  const submitCreate = useCallback(async (event) => {
    event.preventDefault();
    const name = createDraft.name.trim();
    const instruction = createDraft.instruction.trim();
    const expression = createDraft.expression.trim();
    if (!name || !instruction || !expression) {
      setCreateFailure('Enter a name, instruction, and schedule expression.');
      return;
    }
    setCreateBusy(true);
    setCreateFailure(null);
    try {
      await createSchedule({
        name,
        instruction,
        expression,
        schedule_type: createDraft.scheduleType,
        ...(createDraft.timezone.trim() ? { timezone: createDraft.timezone.trim() } : {}),
        conversation_id: createDraft.conversationId || null,
      }, { requestId: createUuid() });
      setCreateOpen(false);
      setCreateDraft({
        name: '',
        instruction: '',
        expression: '',
        scheduleType: 'cron',
        timezone: '',
        conversationId: '',
      });
    } catch (error) {
      setCreateFailure(error);
      reportFailure(show, 'Create schedule', error);
    } finally {
      setCreateBusy(false);
    }
  }, [createDraft, createSchedule, show]);

  const commitTitle = useCallback(async (nextTitle, { ticket, trackSave, failSave }) => {
    if (!selectedSchedule) return { ok: false, skipped: true };
    const requestId = createUuid();
    trackSave(ticket, {
      requestId,
      events: [UI_EVENT.SCHEDULE_SNAPSHOT],
    });
    try {
      await updateWidget(
        `schedule:${selectedSchedule.id}`,
        { name: nextTitle },
        { requestId },
      );
      return { ok: true };
    } catch (error) {
      failSave(ticket, error);
      throw error;
    }
  }, [selectedSchedule, updateWidget]);
  const titleEditor = useInlineTitleEditor({
    scopeKey: scheduleScopeKey,
    kind: 'schedule',
    title: scheduleTitle,
    onCommit: commitTitle,
  });

  const handleTogglePause = useCallback(async (schedule) => {
    const operation = schedule.paused ? 'Resume schedule' : 'Pause schedule';
    const result = await runPauseAction(operation, async () => {
      await updateWidget(`schedule:${schedule.id}`, { enabled: schedule.paused });
    });
    reportActionFailure(operation, result);
    return result;
  }, [reportActionFailure, runPauseAction, updateWidget]);

  const handleDelete = useCallback((schedule) => {
    const target = schedule;
    requestScheduleDelete('Delete schedule', async ({ isCurrent }) => {
      await deleteWidget(`schedule:${target.id}`);
      if (isCurrent() && selectedScheduleId === target.id) {
        navigate('/schedules', { replace: true });
      }
    });
  }, [deleteWidget, navigate, requestScheduleDelete, selectedScheduleId]);

  useEffect(() => {
    if (editingIntent) {
      intentInputRef.current?.focus();
    }
  }, [editingIntent]);

  const startIntentEdit = useCallback(() => {
    setIntentValidationFailure(null);
    setIntentDraft((current) => ({ ...current, editing: true }));
  }, [setIntentDraft]);

  const cancelIntentEdit = useCallback(() => {
    if (intentSaving) return;
    setIntentValidationFailure(null);
    clearIntentDraft();
  }, [clearIntentDraft, intentSaving]);

  const saveIntentEdit = useCallback(async () => {
    if (!selectedSchedule || intentSaving) return;
    const target = selectedSchedule;
    const normalizedIntent = intentDraft.text.trim();
    if (!normalizedIntent) {
      setIntentValidationFailure('Describe what the schedule should do.');
      return;
    }
    if (normalizedIntent === (target.message || '')) {
      cancelIntentEdit();
      return;
    }
    const ticket = beginIntentSave();
    if (!ticket) return;
    const requestId = createUuid();
    trackIntentSave(ticket, {
      requestId,
      events: [UI_EVENT.SCHEDULE_SNAPSHOT],
    });
    try {
      await updateWidget(
        `schedule:${target.id}`,
        { instruction: normalizedIntent },
        { requestId },
      );
    } catch (error) {
      failIntentSave(ticket, error);
    }
  }, [
    beginIntentSave,
    cancelIntentEdit,
    failIntentSave,
    intentDraft.text,
    intentSaving,
    selectedSchedule,
    trackIntentSave,
    updateWidget,
  ]);

  useEffect(() => {
    if (editingTrigger) {
      triggerInputRef.current?.focus();
      triggerInputRef.current?.select();
    }
  }, [editingTrigger]);

  const startTriggerEdit = useCallback(() => {
    if (!selectedSchedule) return;
    setTriggerValidationFailure(null);
    setTriggerDraft((current) => ({ ...current, editing: true }));
  }, [selectedSchedule, setTriggerDraft]);

  const cancelTriggerEdit = useCallback(() => {
    if (triggerSaving) return;
    setTriggerValidationFailure(null);
    clearTriggerDraft();
  }, [clearTriggerDraft, triggerSaving]);

  const saveTriggerEdit = useCallback(async () => {
    if (!selectedSchedule || triggerSaving) return;
    const target = selectedSchedule;
    const text = triggerDraft.text.trim();
    if (!text) {
      setTriggerValidationFailure('Describe when the schedule should run.');
      return;
    }
    const ticket = beginTriggerSave();
    if (!ticket) return;
    const requestId = createUuid();
    trackTriggerSave(ticket, {
      requestId,
      events: [UI_EVENT.SCHEDULE_SNAPSHOT],
    });
    try {
      await retriggerSchedule(target.id, text, { requestId });
    } catch (error) {
      failTriggerSave(ticket, error);
    }
  }, [
    beginTriggerSave,
    failTriggerSave,
    retriggerSchedule,
    selectedSchedule,
    trackTriggerSave,
    triggerDraft.text,
    triggerSaving,
  ]);

  const startDestinationEdit = useCallback(() => {
    if (!selectedSchedule) return;
    setDestinationDraft((current) => ({ ...current, editing: true }));
  }, [selectedSchedule, setDestinationDraft]);

  const cancelDestinationEdit = useCallback(() => {
    if (!destinationSaving) clearDestinationDraft();
  }, [clearDestinationDraft, destinationSaving]);

  const saveDestinationEdit = useCallback(async () => {
    if (!selectedSchedule || destinationSaving) return;
    const target = selectedSchedule;
    const conversationId = destinationDraft.conversationId || null;
    if (conversationId === conversationIdFor(target)) {
      cancelDestinationEdit();
      return;
    }
    const ticket = beginDestinationSave();
    if (!ticket) return;
    const requestId = createUuid();
    trackDestinationSave(ticket, {
      requestId,
      events: [UI_EVENT.SCHEDULE_SNAPSHOT],
    });
    try {
      await updateWidget(
        `schedule:${target.id}`,
        { conversation_id: conversationId },
        { requestId },
      );
    } catch (error) {
      failDestinationSave(ticket, error);
      reportFailure(show, 'Save schedule destination', error);
    }
  }, [
    beginDestinationSave,
    cancelDestinationEdit,
    destinationDraft.conversationId,
    destinationSaving,
    failDestinationSave,
    selectedSchedule,
    show,
    trackDestinationSave,
    updateWidget,
  ]);

  return (
    <PageShell className="schedules-page">
      <PaneList label="Schedules" className="schedules-pane">
        <PaneListFilters
          filters={SCHEDULE_FILTERS}
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
          actionLabel="+ New"
          onAction={() => {
            setCreateFailure(null);
            setCreateOpen(true);
          }}
        />
        <PaneListToolbar>
          <PaneListSearch value={search} placeholder="Search schedules..." onChange={setSearch} />
          <PaneListSortButton
            title="Sort schedules"
            options={SCHEDULE_SORTS}
            value={sortMode}
            onSelect={setSortMode}
          />
        </PaneListToolbar>
        {loading ? <PaneEmptyState>Loading schedules…</PaneEmptyState> : null}
        {!loading && listFailure ? (
          <InlineFailure
            failure={listFailure}
            operation="Load schedules"
            onRetry={requestSchedules}
          />
        ) : null}
        {!loading && visibleSchedules.length ? (
          <PaneListRows
            groups={order
              .filter((group) => groupedSchedules[group]?.length)
              .map((group) => ({
                key: group,
                title: group,
                items: groupedSchedules[group],
              }))}
            getItemKey={(schedule) => schedule.id}
            renderItem={(schedule) => (
              <ScheduleRow
                schedule={schedule}
                selected={selectedSchedule?.id === schedule.id}
                timeZone={effectiveScheduleTimezone(schedule, preferenceTimezone)}
                chatsById={chatsById}
                onSelect={() => navigate(buildEntityPath('/schedules', schedule.id, schedule.name), { replace: true })}
              />
            )}
          />
        ) : !loading && !listFailure ? (
          <PaneEmptyState>No schedules match this view.</PaneEmptyState>
        ) : null}
        <PaneListFooter>
          <span>
            <span className="num">{activeCount}</span> active
          </span>
          <span>
            <span className="num">{pausedCount}</span> paused
          </span>
          <span>
            <span className="num">{completedCount}</span> completed
          </span>
        </PaneListFooter>
      </PaneList>

      <DetailPane className="detail-pane--dense schedules-detail" label="Schedule detail">
        {loading ? (
          <div className="detail-pane__body">
            <PaneEmptyState>Loading schedules…</PaneEmptyState>
          </div>
        ) : !selectedSchedule && listFailure ? (
          <div className="detail-pane__body">
            <InlineFailure
              failure={listFailure}
              operation="Load schedules"
              onRetry={requestSchedules}
            />
          </div>
        ) : !selectedSchedule ? (
          <div className="detail-pane__body">
            <PaneEmptyState>Select a schedule, or use New to ask {agentName} to create one.</PaneEmptyState>
          </div>
        ) : (
          <>
            <DetailHeader
              titleClassName="chat-detail-header__title"
              title={(
                <EditableDetailTitle
                  title={scheduleTitle}
                  editor={titleEditor}
                  maxLength={120}
                  ariaLabel="Schedule name"
                  entityLabel="schedule"
                />
              )}
              meta={[
                <DetailStatus
                  key="status"
                  tone={statusOf(selectedSchedule) === 'active' ? 'ok' : statusOf(selectedSchedule) === 'paused' ? 'warning' : 'muted'}
                >
                  {statusOf(selectedSchedule)}
                </DetailStatus>,
                <span key="type" className="muted">{scheduleKind(selectedSchedule)}</span>,
                <span key="cadence" className="muted">{cadenceFor(selectedSchedule, selectedScheduleTimezone)}</span>,
                <span key="next" className="muted">
                  {statusOf(selectedSchedule) === 'completed'
                    ? 'completed'
                    : selectedSchedule.paused ? 'paused' : `Next ${formatRelative(selectedSchedule.next_run, selectedScheduleTimezone)}`}
                </span>,
                <span key="tz" className="muted">{selectedScheduleTimezone}</span>,
                <span key="id" className="muted">{selectedSchedule.id}</span>,
              ]}
              actions={
                <DetailHeaderActions>
                  {statusOf(selectedSchedule) !== 'completed' ? (
                    <DetailHeaderAction
                      icon={selectedSchedule.paused ? <PlayActionIcon /> : <PauseActionIcon />}
                      label={selectedSchedule.paused ? 'Resume' : 'Pause'}
                      tooltip={selectedSchedule.paused ? 'Resume schedule' : 'Pause schedule'}
                      ariaLabel={selectedSchedule.paused ? 'Resume schedule' : 'Pause schedule'}
                      variant="primary"
                      disabled={pauseAction.busy}
                      onClick={() => { void handleTogglePause(selectedSchedule); }}
                    />
                  ) : null}
                  <DetailHeaderAction
                    icon={<DeleteActionIcon />}
                    label="Delete"
                    tooltip="Delete schedule"
                    ariaLabel="Delete schedule"
                    disabled={deleteConfirmation.busy}
                    onClick={() => handleDelete(selectedSchedule)}
                  />
                </DetailHeaderActions>
              }
            />
            {titleEditor.error ? <InlineFailure failure={titleEditor.error} operation="Rename schedule" /> : null}

            <div className="detail-pane__body">
              <div className="agents-metrics">
                <MetricCard
                  label="Next run"
                  value={
                    statusOf(selectedSchedule) === 'completed'
                      ? 'completed'
                      : selectedSchedule.paused ? 'paused' : formatRelative(selectedSchedule.next_run, selectedScheduleTimezone)
                  }
                  detail={selectedSchedule.next_run ? formatAbsolute(selectedSchedule.next_run, selectedScheduleTimezone) : 'not scheduled'}
                />
                <MetricCard
                  label="Last run"
                  value={selectedSchedule.last_run ? formatRelative(selectedSchedule.last_run, selectedScheduleTimezone) : 'never'}
                  detail={selectedSchedule.last_run ? formatAbsolute(selectedSchedule.last_run, selectedScheduleTimezone) : 'no runs yet'}
                />
                <MetricCard label="Cadence" value={cadenceFor(selectedSchedule, selectedScheduleTimezone)} detail="trigger" />
                <MetricCard label="Status" value={statusOf(selectedSchedule)} detail="schedule state" />
              </div>

              <section className="agents-section schedules-summary">
                <header className="agents-section__head">
                  <h2>Schedule intent</h2>
                  <div>
                    {editingIntent ? (
                      <>
                        <button
                          type="button"
                          className="task-btn task-btn--secondary"
                          onClick={cancelIntentEdit}
                          disabled={intentSaving}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="task-btn task-btn--primary"
                          onClick={saveIntentEdit}
                          disabled={intentSaving}
                        >
                          Save
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className="task-btn task-btn--secondary"
                        onClick={startIntentEdit}
                      >
                        Edit
                      </button>
                    )}
                  </div>
                </header>
                {editingIntent ? (
                  <textarea
                    ref={intentInputRef}
                    className="schedules-summary__editor"
                    value={intentDraft.text}
                    rows={4}
                    aria-label="Schedule intent"
                    placeholder="What should the agent do when this runs?"
                    disabled={intentSaving}
                    onChange={(event) => {
                      setIntentValidationFailure(null);
                      setIntentDraft((current) => ({ ...current, text: event.target.value }));
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Escape') {
                        event.preventDefault();
                        cancelIntentEdit();
                      } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                        event.preventDefault();
                        saveIntentEdit();
                      }
                    }}
                  />
                ) : (
                  <p>{selectedSchedule.message || selectedSchedule.name || 'No instruction set.'}</p>
                )}
                {editingIntent && (intentValidationFailure || intentSaveError) ? (
                  <InlineFailure
                    failure={intentValidationFailure || intentSaveError}
                    operation="Save schedule intent"
                  />
                ) : null}
              </section>

              <section className="agents-section">
                <header className="agents-section__head">
                  <h2>Trigger</h2>
                  <div>
                    {editingTrigger ? (
                      <>
                        <button
                          type="button"
                          className="task-btn task-btn--secondary"
                          onClick={cancelTriggerEdit}
                          disabled={triggerSaving}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="task-btn task-btn--primary"
                          onClick={saveTriggerEdit}
                          disabled={triggerSaving}
                        >
                          Save
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className="task-btn task-btn--secondary"
                        onClick={startTriggerEdit}
                      >
                        Edit
                      </button>
                    )}
                  </div>
                </header>
                {editingTrigger ? (
                  <div className="schedules-trigger-edit">
                    <textarea
                      ref={triggerInputRef}
                      className="schedules-summary__editor"
                      value={triggerDraft.text}
                      rows={2}
                      aria-label="Schedule timing"
                      placeholder="e.g. every weekday at 9am, or Friday at 15:00"
                      disabled={triggerSaving}
                      onChange={(event) => {
                        setTriggerValidationFailure(null);
                        setTriggerDraft((current) => ({ ...current, text: event.target.value }));
                      }}
                      onKeyDown={(event) => {
                        if (event.key === 'Escape') {
                          event.preventDefault();
                          cancelTriggerEdit();
                        } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                          event.preventDefault();
                          saveTriggerEdit();
                        }
                      }}
                    />
                    <p className="schedules-trigger-edit__hint">
                      {triggerSaving
                        ? 'Processing…'
                        : `Describe the timing in plain language — ${agentName} converts it for you.`}
                    </p>
                  </div>
                ) : (
                  <div className="agents-connection-list">
                    <div className="agents-connection-row">
                      <span className="agents-connection-row__icon">cr</span>
                      <div className="agents-connection-row__body">
                        <strong>{cadenceFor(selectedSchedule, selectedScheduleTimezone)}</strong>
                        <p>{selectedSchedule.cron_expression || selectedSchedule.scheduled_at || selectedSchedule.schedule || '—'}</p>
                      </div>
                      <span className="agents-chip agents-chip--info">
                        {selectedSchedule.scheduled_at ? 'datetime' : 'cron'}
                      </span>
                    </div>
                  </div>
                )}
                {editingTrigger && (triggerValidationFailure || triggerSaveError) ? (
                  <InlineFailure
                    failure={triggerValidationFailure || triggerSaveError}
                    operation="Save schedule trigger"
                  />
                ) : null}
              </section>

              <section className="agents-section">
                <header className="agents-section__head">
                  <h2>Destination</h2>
                  <div>
                    {editingDestination ? (
                      <>
                        <button
                          type="button"
                          className="task-btn task-btn--secondary"
                          onClick={cancelDestinationEdit}
                          disabled={destinationSaving}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="task-btn task-btn--primary"
                          onClick={() => { void saveDestinationEdit(); }}
                          disabled={destinationSaving}
                        >
                          Save
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className="task-btn task-btn--secondary"
                        onClick={startDestinationEdit}
                      >
                        Edit
                      </button>
                    )}
                  </div>
                </header>
                {editingDestination ? (
                  <label className="sidebar-modal__field">
                    Chat destination
                    <select
                      aria-label="Schedule destination"
                      value={destinationDraft.conversationId}
                      disabled={destinationSaving}
                      onChange={(event) => setDestinationDraft((current) => ({
                        ...current,
                        conversationId: event.target.value,
                      }))}
                    >
                      <option value="">Standalone</option>
                      {availableChats.map((chat) => (
                        <option key={chat.thread_id} value={chat.thread_id}>{chat.title || 'Untitled chat'}</option>
                      ))}
                    </select>
                  </label>
                ) : (
                  <div className="agents-connection-list">
                    <div className="agents-connection-row">
                      <span className="agents-connection-row__icon">ch</span>
                      <div className="agents-connection-row__body">
                        {conversationIdFor(selectedSchedule) && selectedDestinationChat ? (
                          <Link to={buildEntityPath('/chat', selectedDestinationChat.thread_id, selectedDestinationChat.title)}>
                            {selectedDestinationChat.title || 'Untitled chat'}
                          </Link>
                        ) : (
                          <strong>{destinationLabel(selectedSchedule, chatsById)}</strong>
                        )}
                        <p>
                          {conversationIdFor(selectedSchedule)
                            ? selectedDestinationChat ? 'Deliver into this chat' : 'The destination chat was deleted'
                            : 'Runs outside a chat'}
                        </p>
                      </div>
                    </div>
                  </div>
                )}
                {editingDestination && destinationSaveError ? (
                  <InlineFailure failure={destinationSaveError} operation="Save schedule destination" />
                ) : null}
              </section>

              <section className="agents-section">
                <header className="agents-section__head">
                  <h2>Recent runs</h2>
                </header>
                {recentScheduleRuns.length ? (
                  <div className="agents-mini-list">
                    {recentScheduleRuns.map((run) => (
                      <div key={run.run_id} className="agents-mini-row">
                        <span className="agents-mini-row__icon">run</span>
                        <div>
                          <strong>{formatLogTime(run.started_at) || 'Scheduled run'}</strong>
                          <p>{scheduleRunDetail(run)}</p>
                        </div>
                        <DetailStatus tone={scheduleRunStatusTone(run.status)}>
                          {run.status}
                        </DetailStatus>
                      </div>
                    ))}
                  </div>
                ) : <p className="muted">No runs yet.</p>}
              </section>

              <section className="agents-section">
                <header className="agents-section__head">
                  <h2>Latest activity</h2>
                </header>
                <div className="agents-mini-list">
                  <div className="agents-mini-row">
                    <span className="agents-mini-row__icon">cd</span>
                    <div>
                      <strong>Created</strong>
                      <p>{formatAbsolute(selectedSchedule.created_at, selectedScheduleTimezone)}</p>
                    </div>
                    <span className="agents-chip agents-chip--info">{selectedSchedule.id}</span>
                  </div>
                </div>
              </section>
            </div>
          </>
        )}
      </DetailPane>
      {deleteConfirmation.open && selectedSchedule ? (
        <PopupModal
          title="Delete schedule?"
          message={`Delete “${selectedSchedule.name || 'Untitled schedule'}”? This cannot be undone.`}
          confirmLabel="Delete schedule"
          busy={deleteConfirmation.busy}
          error={deleteConfirmation.error}
          onClose={deleteConfirmation.close}
          onConfirm={() => { void deleteConfirmation.confirm(); }}
        />
      ) : null}
      {createOpen ? (
        <Modal
          className="sidebar-modal"
          backdropClassName="sidebar-modal-backdrop"
          ariaLabel="Create schedule"
          busy={createBusy}
          onClose={closeCreate}
        >
          <form onSubmit={submitCreate}>
            <header className="sidebar-modal__head">
              <div>
                <span className="sidebar-modal__eyebrow">Schedules</span>
                <h3>Create schedule</h3>
              </div>
              <button type="button" className="sidebar-modal__close" aria-label="Close" onClick={closeCreate} disabled={createBusy}>×</button>
            </header>
            <label className="sidebar-modal__field">
              Name
              <input
                aria-label="Schedule name"
                value={createDraft.name}
                disabled={createBusy}
                onChange={(event) => setCreateDraft((current) => ({ ...current, name: event.target.value }))}
              />
            </label>
            <label className="sidebar-modal__field">
              Instruction
              <textarea
                aria-label="Schedule instruction"
                value={createDraft.instruction}
                disabled={createBusy}
                onChange={(event) => setCreateDraft((current) => ({ ...current, instruction: event.target.value }))}
              />
            </label>
            <label className="sidebar-modal__field">
              Timing
              <input
                aria-label="Schedule expression"
                placeholder="e.g. 0 9 * * 1-5"
                value={createDraft.expression}
                disabled={createBusy}
                onChange={(event) => setCreateDraft((current) => ({ ...current, expression: event.target.value }))}
              />
            </label>
            <label className="sidebar-modal__field">
              Schedule type
              <select
                aria-label="Schedule type"
                value={createDraft.scheduleType}
                disabled={createBusy}
                onChange={(event) => setCreateDraft((current) => ({ ...current, scheduleType: event.target.value }))}
              >
                <option value="cron">Cron</option>
                <option value="once">One-off</option>
              </select>
            </label>
            <label className="sidebar-modal__field">
              Timezone (optional)
              <input
                aria-label="Schedule timezone"
                value={createDraft.timezone}
                disabled={createBusy}
                onChange={(event) => setCreateDraft((current) => ({ ...current, timezone: event.target.value }))}
              />
            </label>
            <label className="sidebar-modal__field">
              Chat destination
              <select
                aria-label="Schedule destination"
                value={createDraft.conversationId}
                disabled={createBusy}
                onChange={(event) => setCreateDraft((current) => ({ ...current, conversationId: event.target.value }))}
              >
                <option value="">Standalone</option>
                {availableChats.map((chat) => (
                  <option key={chat.thread_id} value={chat.thread_id}>{chat.title || 'Untitled chat'}</option>
                ))}
              </select>
            </label>
            {createFailure ? <InlineFailure failure={createFailure} operation="Create schedule" /> : null}
            <footer className="sidebar-modal__foot">
              <button type="button" className="task-btn task-btn--secondary" onClick={closeCreate} disabled={createBusy}>Cancel</button>
              <button type="submit" className="task-btn task-btn--primary" disabled={createBusy}>
                {createBusy ? 'Creating…' : 'Create schedule'}
              </button>
            </footer>
          </form>
        </Modal>
      ) : null}
    </PageShell>
  );
}

export default SchedulesPage;
