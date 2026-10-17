import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../lib/routes';
import {
  PaneEmptyState,
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListRows,
} from '../components/layout/PaneList';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import { MetricCard } from '../components/common/DetailCards';
import DetailHeader, {
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  PauseActionIcon,
  PlayActionIcon,
} from '../components/layout/DetailHeader';
import EditableDetailTitle from '../components/common/EditableDetailTitle';
import InlineFailure from '../components/common/InlineFailure';
import PopupModal from '../components/common/PopupModal';
import { useSchedulesApi } from '../hooks/api/useSchedulesApi';
import { useConfig } from '../context/ConfigContext';
import { useNotifications } from '../context/NotificationContext';
import { useWidgetsApi } from '../hooks/api/useWidgetsApi';
import useConfirmAction from '../hooks/useConfirmAction';
import useInlineTitleEditor from '../hooks/useInlineTitleEditor';
import useUiAction from '../hooks/useUiAction';
import { reportFailure } from '../lib/failures';
import { fetchSchedules, selectSchedulesState, selectUiWidgets } from '../store/uiDataSlice';
import { selectRealtimeConnected } from '../store/realtimeSlice';

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

function cadenceFor(schedule) {
  if (schedule.scheduled_at) {
    return `Once · ${formatAbsolute(schedule.scheduled_at, schedule.timezone)}`;
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

// Absolute date/time rendered in the schedule's own timezone when given, so the
// displayed wall-clock matches when the schedule actually fires. Falls back to
// the browser's local zone if the timezone is missing or invalid.
function formatAbsolute(value, timeZone) {
  const date = parseDate(value);
  if (!date) return '—';
  const opts = { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' };
  if (timeZone) {
    try {
      return date.toLocaleString(undefined, { ...opts, timeZone, timeZoneName: 'short' });
    } catch {
      // Invalid IANA zone — fall through to local time.
    }
  }
  return date.toLocaleString(undefined, opts);
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

function matchesSchedule(schedule, filter, search) {
  const status = statusOf(schedule);
  const matchesFilter =
    filter === 'All'
    || (filter === 'Active' && status === 'active')
    || (filter === 'Paused' && status === 'paused')
    || (filter === 'Completed' && status === 'completed');
  const haystack = [schedule.name, schedule.message, cadenceFor(schedule)].join(' ').toLowerCase();
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

function ScheduleRow({ schedule, selected, onSelect }) {
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
        {status === 'completed' ? 'completed' : schedule.paused ? 'paused' : formatRelative(schedule.next_run, schedule.timezone)}
      </span>
      <span className="sess-row__title">{cadenceFor(schedule) || '—'}</span>
    </button>
  );
}


function SchedulesPage() {
  const navigate = useNavigate();
  const dispatch = useDispatch();
  const { show } = useNotifications();
  const { retriggerSchedule } = useSchedulesApi();
  const { getApiUrl } = useConfig();
  const { getWidgets, updateWidget, deleteWidget } = useWidgetsApi();
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
  const widgets = useSelector(selectUiWidgets);
  const realtimeConnected = useSelector(selectRealtimeConnected);
  const [logsFailure, setLogsFailure] = useState(null);
  const [activeFilter, setActiveFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [sortMode, setSortMode] = useState('Type');
  const [isEditingIntent, setIsEditingIntent] = useState(false);
  const [intentDraft, setIntentDraft] = useState('');
  const intentInputRef = useRef(null);
  const [isEditingTrigger, setIsEditingTrigger] = useState(false);
  const [triggerText, setTriggerText] = useState('');
  const triggerInputRef = useRef(null);
  const [intentValidationFailure, setIntentValidationFailure] = useState(null);
  const [triggerValidationFailure, setTriggerValidationFailure] = useState(null);
  const [intentEditScopeKey, setIntentEditScopeKey] = useState(null);
  const [triggerEditScopeKey, setTriggerEditScopeKey] = useState(null);
  const logsRequestRef = useRef(0);
  const requestSchedules = useCallback(() => (
    dispatch(fetchSchedules({ schedulesUrl: getApiUrl('schedules') }))
  ), [dispatch, getApiUrl]);

  useEffect(() => {
    if (realtimeConnected) {
      void requestSchedules();
    }
  }, [realtimeConnected, requestSchedules]);

  const loadScheduleLogs = useCallback(() => {
    const requestId = ++logsRequestRef.current;
    return getWidgets().catch((error) => {
      if (logsRequestRef.current === requestId) setLogsFailure(error);
    });
  }, [getWidgets]);

  const retryScheduleLogs = useCallback(() => {
    setLogsFailure(null);
    void loadScheduleLogs();
  }, [loadScheduleLogs]);

  useEffect(() => {
    void loadScheduleLogs();
    return () => { logsRequestRef.current += 1; };
  }, [loadScheduleLogs]);

  useEffect(() => {
    if (!loaded || loading || listFailure || scheduleRequestId !== null || !selectedScheduleId) {
      return;
    }
    if (!schedules.some((schedule) => schedule.id === selectedScheduleId)) {
      navigate('/schedules', { replace: true });
    }
  }, [listFailure, loaded, loading, navigate, scheduleRequestId, schedules, selectedScheduleId]);

  const scheduleWidgets = useMemo(() => widgets.reduce((byScheduleId, widget) => {
    const scheduleId = widget?.data?.schedule_id;
    if (widget?.type === 'schedule' && typeof scheduleId === 'string') {
      byScheduleId[scheduleId] = widget;
    }
    return byScheduleId;
  }, {}), [widgets]);

  const visibleSchedules = useMemo(() => {
    const filtered = schedules.filter((schedule) => matchesSchedule(schedule, activeFilter, search));
    return [...filtered].sort((a, b) => compareSchedules(a, b, sortMode));
  }, [activeFilter, schedules, search, sortMode]);

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
  const selectedScheduleWidget = selectedSchedule ? scheduleWidgets[selectedSchedule.id] || null : null;
  const selectedScheduleLogs = Array.isArray(selectedScheduleWidget?.logs) ? selectedScheduleWidget.logs : [];
  // Logs arrive newest-first; cap to the latest few so the list stays bounded.
  const recentScheduleLogs = selectedScheduleLogs.slice(0, 5);
  const scheduleTitle = selectedSchedule?.name || 'Untitled schedule';

  const scheduleScopeKey = selectedSchedule?.id ?? null;
  const titleAction = useUiAction(scheduleScopeKey);
  const pauseAction = useUiAction(scheduleScopeKey);
  const intentAction = useUiAction(scheduleScopeKey);
  const triggerAction = useUiAction(scheduleScopeKey);
  const deleteConfirmation = useConfirmAction(scheduleScopeKey);
  const { run: runTitleAction } = titleAction;
  const { run: runPauseAction } = pauseAction;
  const {
    run: runIntentAction,
    clearError: clearIntentActionError,
    busy: intentActionBusy,
    error: intentActionError,
  } = intentAction;
  const {
    run: runTriggerAction,
    clearError: clearTriggerActionError,
    busy: triggerActionBusy,
    error: triggerActionError,
  } = triggerAction;
  const { request: requestScheduleDelete } = deleteConfirmation;
  const editingIntent = isEditingIntent && intentEditScopeKey === scheduleScopeKey;
  const editingTrigger = isEditingTrigger && triggerEditScopeKey === scheduleScopeKey;

  const reportActionFailure = useCallback((operation, result) => {
    if (!result?.ok && result?.error) {
      reportFailure(show, operation, result.error);
    }
  }, [show]);

  const commitTitle = useCallback((nextTitle) => {
    if (!selectedSchedule) {
      return Promise.resolve({ ok: false, skipped: true });
    }
    return runTitleAction('Rename schedule', async () => {
      // The backend emits schedule.snapshot; the canonical title remains store-owned.
      await updateWidget(`schedule:${selectedSchedule.id}`, { name: nextTitle });
    });
  }, [runTitleAction, selectedSchedule, updateWidget]);
  const titleEditor = useInlineTitleEditor({
    scopeKey: scheduleScopeKey,
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
    setIntentEditScopeKey(scheduleScopeKey);
    setIntentValidationFailure(null);
    clearIntentActionError();
    setIntentDraft(selectedSchedule?.message || '');
    setIsEditingIntent(true);
  }, [clearIntentActionError, scheduleScopeKey, selectedSchedule]);

  const cancelIntentEdit = useCallback(() => {
    if (intentActionBusy) {
      return;
    }
    setIntentValidationFailure(null);
    setIsEditingIntent(false);
    setIntentDraft('');
  }, [intentActionBusy]);

  const saveIntentEdit = useCallback(async () => {
    if (!selectedSchedule) return;
    const normalizedIntent = intentDraft.trim();
    if (!normalizedIntent) {
      setIntentValidationFailure('Describe what the schedule should do.');
      return;
    }
    if (normalizedIntent === (selectedSchedule.message || '')) {
      cancelIntentEdit();
      return;
    }
    const result = await runIntentAction('Save schedule intent', async ({ isCurrent }) => {
      // schedule.snapshot is the canonical update for the instruction.
      await updateWidget(`schedule:${selectedSchedule.id}`, { instruction: normalizedIntent });
      if (isCurrent()) {
        setIsEditingIntent(false);
      }
    });
    return result;
  }, [cancelIntentEdit, intentDraft, runIntentAction, selectedSchedule, updateWidget]);

  useEffect(() => {
    if (editingTrigger) {
      triggerInputRef.current?.focus();
      triggerInputRef.current?.select();
    }
  }, [editingTrigger]);

  const startTriggerEdit = useCallback(() => {
    if (!selectedSchedule) return;
    setTriggerEditScopeKey(scheduleScopeKey);
    setTriggerValidationFailure(null);
    clearTriggerActionError();
    setTriggerText(cadenceFor(selectedSchedule));
    setIsEditingTrigger(true);
  }, [clearTriggerActionError, scheduleScopeKey, selectedSchedule]);

  const cancelTriggerEdit = useCallback(() => {
    if (triggerActionBusy) {
      return;
    }
    setTriggerValidationFailure(null);
    setIsEditingTrigger(false);
  }, [triggerActionBusy]);

  const saveTriggerEdit = useCallback(async () => {
    if (!selectedSchedule) return;
    const text = triggerText.trim();
    if (!text) {
      setTriggerValidationFailure('Describe when the schedule should run.');
      return;
    }
    const result = await runTriggerAction('Save schedule trigger', async ({ isCurrent }) => {
      // The backend agent emits schedule.snapshot after interpreting this phrase.
      await retriggerSchedule(selectedSchedule.id, text);
      if (isCurrent()) {
        setIsEditingTrigger(false);
      }
    });
    return result;
  }, [retriggerSchedule, runTriggerAction, selectedSchedule, triggerText]);

  return (
    <PageShell className="schedules-page">
      <PaneList label="Schedules" className="schedules-pane">
        <PaneListFilters
          filters={SCHEDULE_FILTERS}
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
          actionLabel="+ New"
          onAction={() => {
            window.dispatchEvent(new CustomEvent('progenitor:open-quick-chat', {
              detail: { mode: 'schedule-create' },
            }));
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
            <PaneEmptyState>Select a schedule, or use New to ask Progenitor to create one.</PaneEmptyState>
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
                <span key="cadence" className="muted">{cadenceFor(selectedSchedule)}</span>,
                <span key="next" className="muted">
                  {statusOf(selectedSchedule) === 'completed'
                    ? 'completed'
                    : selectedSchedule.paused ? 'paused' : `Next ${formatRelative(selectedSchedule.next_run, selectedSchedule.timezone)}`}
                </span>,
                selectedSchedule.timezone ? (
                  <span key="tz" className="muted">{selectedSchedule.timezone}</span>
                ) : null,
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
                      : selectedSchedule.paused ? 'paused' : formatRelative(selectedSchedule.next_run, selectedSchedule.timezone)
                  }
                  detail={selectedSchedule.next_run ? formatAbsolute(selectedSchedule.next_run, selectedSchedule.timezone) : 'not scheduled'}
                />
                <MetricCard
                  label="Last run"
                  value={selectedSchedule.last_run ? formatRelative(selectedSchedule.last_run, selectedSchedule.timezone) : 'never'}
                  detail={selectedSchedule.last_run ? formatAbsolute(selectedSchedule.last_run, selectedSchedule.timezone) : 'no runs yet'}
                />
                <MetricCard label="Cadence" value={cadenceFor(selectedSchedule)} detail="trigger" />
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
                          disabled={intentActionBusy}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="task-btn task-btn--primary"
                          onClick={saveIntentEdit}
                          disabled={intentActionBusy}
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
                    value={intentDraft}
                    rows={4}
                    aria-label="Schedule intent"
                    placeholder="What should the agent do when this runs?"
                    disabled={intentActionBusy}
                    onChange={(event) => {
                      clearIntentActionError();
                      setIntentValidationFailure(null);
                      setIntentDraft(event.target.value);
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
                {editingIntent && (intentValidationFailure || intentActionError) ? (
                  <InlineFailure
                    failure={intentValidationFailure || intentActionError}
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
                          disabled={triggerActionBusy}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="task-btn task-btn--primary"
                          onClick={saveTriggerEdit}
                          disabled={triggerActionBusy}
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
                      value={triggerText}
                      rows={2}
                      aria-label="Schedule timing"
                      placeholder="e.g. every weekday at 9am, or Friday at 15:00"
                      disabled={triggerActionBusy}
                      onChange={(event) => {
                        clearTriggerActionError();
                        setTriggerValidationFailure(null);
                        setTriggerText(event.target.value);
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
                      {triggerActionBusy
                        ? 'Processing…'
                        : 'Describe the timing in plain language — Progenitor converts it for you.'}
                    </p>
                  </div>
                ) : (
                  <div className="agents-connection-list">
                    <div className="agents-connection-row">
                      <span className="agents-connection-row__icon">cr</span>
                      <div className="agents-connection-row__body">
                        <strong>{cadenceFor(selectedSchedule)}</strong>
                        <p>{selectedSchedule.cron_expression || selectedSchedule.scheduled_at || selectedSchedule.schedule || '—'}</p>
                      </div>
                      <span className="agents-chip agents-chip--info">
                        {selectedSchedule.scheduled_at ? 'datetime' : 'cron'}
                      </span>
                    </div>
                  </div>
                )}
                {editingTrigger && (triggerValidationFailure || triggerActionError) ? (
                  <InlineFailure
                    failure={triggerValidationFailure || triggerActionError}
                    operation="Save schedule trigger"
                  />
                ) : null}
              </section>

              <section className="agents-section">
                <header className="agents-section__head">
                  <h2>Latest activity</h2>
                </header>
                <div className="agents-mini-list">
                  {logsFailure ? (
                    <InlineFailure
                      failure={logsFailure}
                      operation="Load schedule logs"
                      onRetry={retryScheduleLogs}
                    />
                  ) : null}
                  {recentScheduleLogs.map((entry, index) => (
                    <div
                      key={`${entry.id || entry.timestamp || entry.time || index}`}
                      className="agents-mini-row"
                    >
                      <span className="agents-mini-row__icon">log</span>
                      <div>
                        <strong>{entry.message || entry.event || entry.level || 'Schedule activity'}</strong>
                        <p>{formatLogTime(entry.timestamp || entry.time || entry.updated_at) || 'recent'}</p>
                      </div>
                      <span className="agents-chip agents-chip--info">
                        {String(entry.level || entry.source || 'info').toLowerCase()}
                      </span>
                    </div>
                  ))}
                  <div className="agents-mini-row">
                    <span className="agents-mini-row__icon">cd</span>
                    <div>
                      <strong>Created</strong>
                      <p>{formatAbsolute(selectedSchedule.created_at, selectedSchedule.timezone)}</p>
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
    </PageShell>
  );
}

export default SchedulesPage;
