import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../lib/routes';
import { createUuid } from '../lib/uuid';
import {
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListVirtualized,
} from '../components/layout/PaneList';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import DetailHeader, {
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  PauseActionIcon,
  PlayActionIcon,
} from '../components/layout/DetailHeader';
import { useSchedulesApi } from '../hooks/api/useSchedulesApi';
import { useConfig } from '../context/ConfigContext';
import { useWidgetsApi } from '../hooks/api/useWidgetsApi';
import { selectSchedules, selectUiWidgets } from '../store/uiDataSlice';
import { requestAcknowledged } from '../store/http';
import { selectRealtimeConnected, selectUiEventQueue } from '../store/realtimeSlice';

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

function ScheduleStatusPill({ status }) {
  return (
    <span className={`agents-status-pill agents-status-pill--${status}`}>
      <span className="adot" />
      {status}
    </span>
  );
}

function MetricCard({ label, value, detail }) {
  return (
    <div className="agents-metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function SchedulesPage() {
  const navigate = useNavigate();
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
  const schedules = useSelector(selectSchedules);
  const widgets = useSelector(selectUiWidgets);
  const realtimeConnected = useSelector(selectRealtimeConnected);
  const [loading, setLoading] = useState(true);
  const [pendingScheduleRequestId, setPendingScheduleRequestId] = useState(null);
  const hasPendingScheduleSnapshot = useSelector((state) => (
    pendingScheduleRequestId !== null
    && selectUiEventQueue(state).some(({ event }) => (
      event?.type === 'schedule.snapshot'
      && event?.payload?.request_id === pendingScheduleRequestId
    ))
  ));
  const [error, setError] = useState(null);
  const [activeFilter, setActiveFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [sortMode, setSortMode] = useState('Type');
  const [mutating, setMutating] = useState(false);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');
  const titleInputRef = useRef(null);
  const [isEditingIntent, setIsEditingIntent] = useState(false);
  const [intentDraft, setIntentDraft] = useState('');
  const intentInputRef = useRef(null);
  const [isEditingTrigger, setIsEditingTrigger] = useState(false);
  const [triggerText, setTriggerText] = useState('');
  const triggerInputRef = useRef(null);
  useEffect(() => {
    if (!realtimeConnected) {
      return undefined;
    }
    let cancelled = false;
    const requestId = createUuid();
    setLoading(true);
    setError(null);
    setPendingScheduleRequestId(requestId);
    requestAcknowledged(getApiUrl('schedules'), {
      requestId,
      errorLabel: 'Failed to load schedules',
    }).catch((err) => {
      if (!cancelled) {
        setPendingScheduleRequestId(null);
        setLoading(false);
        setError(err.message || 'Failed to load schedules.');
      }
    });
    return () => {
      cancelled = true;
    };
  }, [getApiUrl, realtimeConnected]);

  useEffect(() => {
    if (!pendingScheduleRequestId || !hasPendingScheduleSnapshot) {
      return;
    }
    setPendingScheduleRequestId(null);
    setLoading(false);
    setError(null);
  }, [hasPendingScheduleSnapshot, pendingScheduleRequestId]);

  useEffect(() => {
    let cancelled = false;
    getWidgets().catch((err) => {
      if (!cancelled) setError(err.message || 'Failed to load schedule logs.');
    });
    return () => {
      cancelled = true;
    };
  }, [getWidgets]);

  useEffect(() => {
    if (loading || pendingScheduleRequestId !== null || !selectedScheduleId) {
      return;
    }
    if (!schedules.some((schedule) => schedule.id === selectedScheduleId)) {
      navigate('/schedules', { replace: true });
    }
  }, [loading, navigate, pendingScheduleRequestId, schedules, selectedScheduleId]);

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
    loading || pendingScheduleRequestId !== null ? null : '/schedules',
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

  // Mutations don't refetch — the backend emits a `schedule.snapshot` event as
  // part of handling each request, which updates the store (and any other open
  // tab) live. We only manage local selection here.
  const handleTogglePause = useCallback(
    async (schedule) => {
      setMutating(true);
      setError(null);
      try {
        await updateWidget(`schedule:${schedule.id}`, { enabled: schedule.paused });
      } catch (err) {
        setError(err.message || 'Failed to update schedule.');
      } finally {
        setMutating(false);
      }
    },
    [updateWidget],
  );

  const handleDelete = useCallback(
    async (schedule) => {
      const confirmed = window.confirm(
        `Delete schedule "${schedule.name || 'Untitled schedule'}"? This can't be undone.`,
      );
      if (!confirmed) return;
      setMutating(true);
      setError(null);
      try {
        await deleteWidget(`schedule:${schedule.id}`);
        if (selectedScheduleId === schedule.id) {
          navigate('/schedules', { replace: true });
        }
      } catch (err) {
        setError(err.message || 'Failed to delete schedule.');
      } finally {
        setMutating(false);
      }
    },
    [deleteWidget, navigate, selectedScheduleId],
  );

  // Leave edit mode whenever the selected schedule changes so a stale draft
  // never leaks across selections.
  useEffect(() => {
    setIsEditingTitle(false);
    setTitleDraft('');
    setIsEditingIntent(false);
    setIntentDraft('');
    setIsEditingTrigger(false);
  }, [selectedSchedule?.id]);

  useEffect(() => {
    if (!isEditingTitle) return undefined;
    titleInputRef.current?.focus();
    titleInputRef.current?.select();
    return undefined;
  }, [isEditingTitle]);

  const cancelTitleEdit = useCallback(() => {
    setTitleDraft(scheduleTitle);
    setIsEditingTitle(false);
  }, [scheduleTitle]);

  const startTitleEdit = useCallback(() => {
    setTitleDraft(selectedSchedule?.name || '');
    setIsEditingTitle(true);
  }, [selectedSchedule]);

  const saveTitleEdit = useCallback(async () => {
    if (!selectedSchedule) return;
    const normalizedTitle = titleDraft.replace(/\s+/g, ' ').trim();
    if (!normalizedTitle || normalizedTitle === (selectedSchedule.name || '')) {
      cancelTitleEdit();
      return;
    }
    setIsEditingTitle(false);
    setMutating(true);
    setError(null);
    try {
      // The backend emits a `schedule.snapshot` event, so the store (and any
      // other open tab) picks up the new name live — no local mutation needed.
      await updateWidget(`schedule:${selectedSchedule.id}`, { name: normalizedTitle });
    } catch (err) {
      setError(err.message || 'Failed to rename schedule.');
    } finally {
      setMutating(false);
    }
  }, [cancelTitleEdit, selectedSchedule, titleDraft, updateWidget]);

  useEffect(() => {
    if (!isEditingIntent) return undefined;
    intentInputRef.current?.focus();
    return undefined;
  }, [isEditingIntent]);

  const startIntentEdit = useCallback(() => {
    setIntentDraft(selectedSchedule?.message || '');
    setIsEditingIntent(true);
  }, [selectedSchedule]);

  const cancelIntentEdit = useCallback(() => {
    setIsEditingIntent(false);
    setIntentDraft('');
  }, []);

  const saveIntentEdit = useCallback(async () => {
    if (!selectedSchedule) return;
    const normalizedIntent = intentDraft.trim();
    if (!normalizedIntent || normalizedIntent === (selectedSchedule.message || '')) {
      cancelIntentEdit();
      return;
    }
    setIsEditingIntent(false);
    setMutating(true);
    setError(null);
    try {
      // The backend mirrors `instruction` into the schedule's message and emits
      // a `schedule.snapshot` event, so the store updates live.
      await updateWidget(`schedule:${selectedSchedule.id}`, { instruction: normalizedIntent });
    } catch (err) {
      setError(err.message || 'Failed to update schedule intent.');
    } finally {
      setMutating(false);
    }
  }, [cancelIntentEdit, intentDraft, selectedSchedule, updateWidget]);

  useEffect(() => {
    if (!isEditingTrigger) return undefined;
    triggerInputRef.current?.focus();
    triggerInputRef.current?.select();
    return undefined;
  }, [isEditingTrigger]);

  const startTriggerEdit = useCallback(() => {
    if (!selectedSchedule) return;
    setTriggerText(cadenceFor(selectedSchedule));
    setIsEditingTrigger(true);
  }, [selectedSchedule]);

  const cancelTriggerEdit = useCallback(() => {
    setIsEditingTrigger(false);
  }, []);

  const saveTriggerEdit = useCallback(async () => {
    if (!selectedSchedule) return;
    const text = triggerText.trim();
    if (!text) {
      setError('Describe when the schedule should run.');
      return;
    }
    setMutating(true);
    setError(null);
    try {
      // The backend hands this phrase to the Progenitor agent, which converts
      // it to cron/datetime and updates the schedule; the resulting
      // `schedule.snapshot` refreshes the trigger, cadence, and next-run live.
      await retriggerSchedule(selectedSchedule.id, text);
      setIsEditingTrigger(false);
    } catch (err) {
      setError(err.message || 'Failed to update trigger.');
    } finally {
      setMutating(false);
    }
  }, [retriggerSchedule, selectedSchedule, triggerText]);

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
        {loading ? (
          <div className="agents-empty-state">Loading schedules…</div>
        ) : error && !schedules.length ? (
          <div className="agents-empty-state">{error}</div>
        ) : visibleSchedules.length ? (
          <PaneListVirtualized
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
        ) : (
          <div className="agents-empty-state">No schedules match this view.</div>
        )}
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
            <div className="agents-empty-state">Loading schedules…</div>
          </div>
        ) : !selectedSchedule ? (
          <div className="detail-pane__body">
            <div className="agents-empty-state">Select a schedule, or use New to ask Progenitor to create one.</div>
          </div>
        ) : (
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
                      maxLength={120}
                      aria-label="Schedule name"
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
                    <span className="chat-detail-header__title-text">{scheduleTitle}</span>
                  )}
                  <button
                    type="button"
                    className="chat-icon-button chat-detail-header__rename"
                    data-tooltip={isEditingTitle ? 'Save schedule name' : 'Rename schedule'}
                    aria-label={isEditingTitle ? 'Save schedule name' : 'Rename schedule'}
                    onMouseDown={isEditingTitle ? (event) => event.preventDefault() : undefined}
                    onClick={isEditingTitle ? saveTitleEdit : startTitleEdit}
                  >
                    {isEditingTitle ? (
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
              meta={[
                <ScheduleStatusPill key="status" status={statusOf(selectedSchedule)} />,
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
                      disabled={mutating}
                      onClick={() => handleTogglePause(selectedSchedule)}
                    />
                  ) : null}
                  <DetailHeaderAction
                    icon={<DeleteActionIcon />}
                    label="Delete"
                    tooltip="Delete schedule"
                    ariaLabel="Delete schedule"
                    disabled={mutating}
                    onClick={() => handleDelete(selectedSchedule)}
                  />
                </DetailHeaderActions>
              }
            />

            <div className="detail-pane__body">
              {error ? <div className="agents-empty-state">{error}</div> : null}
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
                    {isEditingIntent ? (
                      <>
                        <button
                          type="button"
                          className="repo-btn repo-btn--ghost"
                          onClick={cancelIntentEdit}
                          disabled={mutating}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="repo-btn"
                          onClick={saveIntentEdit}
                          disabled={mutating}
                        >
                          Save
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className="repo-btn repo-btn--ghost"
                        onClick={startIntentEdit}
                      >
                        Edit
                      </button>
                    )}
                  </div>
                </header>
                {isEditingIntent ? (
                  <textarea
                    ref={intentInputRef}
                    className="schedules-summary__editor"
                    value={intentDraft}
                    rows={4}
                    aria-label="Schedule intent"
                    placeholder="What should the agent do when this runs?"
                    onChange={(event) => setIntentDraft(event.target.value)}
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
              </section>

              <section className="agents-section">
                <header className="agents-section__head">
                  <h2>Trigger</h2>
                  <div>
                    {isEditingTrigger ? (
                      <>
                        <button
                          type="button"
                          className="repo-btn repo-btn--ghost"
                          onClick={cancelTriggerEdit}
                          disabled={mutating}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="repo-btn"
                          onClick={saveTriggerEdit}
                          disabled={mutating}
                        >
                          Save
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className="repo-btn repo-btn--ghost"
                        onClick={startTriggerEdit}
                      >
                        Edit
                      </button>
                    )}
                  </div>
                </header>
                {isEditingTrigger ? (
                  <div className="schedules-trigger-edit">
                    <textarea
                      ref={triggerInputRef}
                      className="schedules-summary__editor"
                      value={triggerText}
                      rows={2}
                      aria-label="Schedule timing"
                      placeholder="e.g. every weekday at 9am, or Friday at 15:00"
                      disabled={mutating}
                      onChange={(event) => setTriggerText(event.target.value)}
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
                      {mutating
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
              </section>

              <section className="agents-section">
                <header className="agents-section__head">
                  <h2>Latest activity</h2>
                </header>
                <div className="agents-mini-list">
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
    </PageShell>
  );
}

export default SchedulesPage;
