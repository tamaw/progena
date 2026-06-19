import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import {
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListGroup,
  PaneListScroll,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
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
import { useApi } from '../hooks/useApi';
import { scheduleSnapshotReceived, selectSchedules } from '../store/uiDataSlice';

const SCHEDULE_FILTERS = ['All', 'Active', 'Paused', 'Completed'];
const SCHEDULE_SORTS = ['Type', 'Next', 'Status'];

const SCHEDULE_TYPES = [
  { value: 'cron', label: 'Cron', placeholder: '0 9 * * *' },
  { value: 'interval', label: 'Interval', placeholder: '30m' },
  { value: 'once', label: 'Once', placeholder: '2026-06-20T09:00' },
];

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
    return `Once · ${formatAbsolute(schedule.scheduled_at)}`;
  }
  return cronToCadence(schedule.cron_expression || schedule.schedule);
}

function isOneOff(schedule) {
  return Boolean(schedule.scheduled_at) || isOneShotCron(schedule.cron_expression || schedule.schedule);
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

function formatAbsolute(value) {
  const date = parseDate(value);
  if (!date) return '—';
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// Compact relative label, e.g. "in 42m", "5h ago", or an absolute date for
// anything beyond a few days out.
function formatRelative(value) {
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
    return formatAbsolute(value);
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
    const typeDelta = Number(isOneOff(a)) - Number(isOneOff(b));
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
      className={`sess-row schedule-row schedule-row--${status} ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(schedule.id)}
    >
      <span className="sess-row__top">
        <span className={`sess-row__dot schedule-row__status schedule-row__status--${status}`} />
        <span className="sess-row__agent schedule-row__name">{schedule.name || 'Untitled schedule'}</span>
        <span className="sess-row__project">{status}</span>
      </span>
      <span className="sess-row__age">
        {status === 'completed' ? 'completed' : schedule.paused ? 'paused' : formatRelative(schedule.next_run)}
      </span>
      <span className="sess-row__title">{cadenceFor(schedule)}</span>
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

function CreateScheduleForm({ onCancel, onCreate, busy }) {
  const [name, setName] = useState('');
  const [instruction, setInstruction] = useState('');
  const [scheduleType, setScheduleType] = useState('cron');
  const [expression, setExpression] = useState('');
  const [error, setError] = useState(null);

  const typeMeta = SCHEDULE_TYPES.find((entry) => entry.value === scheduleType) || SCHEDULE_TYPES[0];

  async function handleSubmit(event) {
    event.preventDefault();
    setError(null);
    if (!name.trim() || !instruction.trim() || !expression.trim()) {
      setError('Name, instruction, and trigger are all required.');
      return;
    }
    try {
      await onCreate({
        name: name.trim(),
        instruction: instruction.trim(),
        schedule_type: scheduleType,
        expression: expression.trim(),
        enabled: true,
      });
    } catch (err) {
      setError(err.message || 'Failed to create schedule.');
    }
  }

  return (
    <form className="schedule-form" onSubmit={handleSubmit}>
      <h2>New schedule</h2>
      <label className="schedule-form__field">
        <span>Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Morning brief" />
      </label>
      <label className="schedule-form__field">
        <span>Instruction</span>
        <textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder="What should the agent do when this runs?"
          rows={3}
        />
      </label>
      <div className="schedule-form__row">
        <label className="schedule-form__field">
          <span>Trigger type</span>
          <select value={scheduleType} onChange={(e) => setScheduleType(e.target.value)}>
            {SCHEDULE_TYPES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
        <label className="schedule-form__field">
          <span>Expression</span>
          <input
            value={expression}
            onChange={(e) => setExpression(e.target.value)}
            placeholder={typeMeta.placeholder}
          />
        </label>
      </div>
      {error ? <p className="schedule-form__error">{error}</p> : null}
      <div className="schedule-form__actions">
        <button type="button" className="repo-btn repo-btn--ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="submit" className="repo-btn" disabled={busy}>
          {busy ? 'Creating…' : 'Create schedule'}
        </button>
      </div>
    </form>
  );
}

function SchedulesPage() {
  const { getSchedules, getWidgets, createSchedule, updateWidget, deleteWidget } = useApi();
  const dispatch = useDispatch();
  // The list is owned by the Redux store and kept live by `schedule.snapshot`
  // events on /api/ui/stream (backend mutations + runtime fires/CRUD). We only
  // seed it once via REST on mount; every change after that arrives as an event.
  const schedules = useSelector(selectSchedules);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeFilter, setActiveFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [sortMode, setSortMode] = useState('Type');
  const [selectedScheduleId, setSelectedScheduleId] = useState(null);
  const [creating, setCreating] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [scheduleWidgets, setScheduleWidgets] = useState({});
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');
  const titleInputRef = useRef(null);
  const scheduleIdsKey = useMemo(() => schedules.map((schedule) => schedule.id).join('|'), [schedules]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getSchedules()
      .then((data) => {
        if (cancelled) return;
        const items = Array.isArray(data?.items) ? data.items : [];
        dispatch(scheduleSnapshotReceived(items));
        setError(null);
        setSelectedScheduleId((current) => current ?? items[0]?.id ?? null);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || 'Failed to load schedules.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [getSchedules, dispatch]);

  useEffect(() => {
    let cancelled = false;
    getWidgets()
      .then((data) => {
        if (cancelled) return;
        const items = Array.isArray(data?.items) ? data.items : [];
        const widgetMap = {};
        for (const item of items) {
          const scheduleId = item?.data?.schedule_id;
          if (item?.type !== 'schedule' || typeof scheduleId !== 'string') continue;
          widgetMap[scheduleId] = item;
        }
        setScheduleWidgets(widgetMap);
      })
      .catch(() => {
        if (!cancelled) setScheduleWidgets({});
      });
    return () => {
      cancelled = true;
    };
  }, [getWidgets, scheduleIdsKey]);

  const visibleSchedules = useMemo(() => {
    const filtered = schedules.filter((schedule) => matchesSchedule(schedule, activeFilter, search));
    return [...filtered].sort((a, b) => compareSchedules(a, b, sortMode));
  }, [activeFilter, schedules, search, sortMode]);

  const groupedSchedules = useMemo(() => {
    if (sortMode === 'Type') {
      return visibleSchedules.reduce(
        (groups, schedule) => {
          const key = isOneOff(schedule) ? 'One-off' : 'Recurring';
          groups[key].push(schedule);
          return groups;
        },
        { Recurring: [], 'One-off': [] },
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

  const selectedSchedule =
    schedules.find((schedule) => schedule.id === selectedScheduleId) || visibleSchedules[0] || null;
  const activeCount = schedules.filter((schedule) => !schedule.paused).length;
  const pausedCount = schedules.filter((schedule) => statusOf(schedule) === 'paused').length;
  const completedCount = schedules.filter((schedule) => statusOf(schedule) === 'completed').length;
  const order = sortMode === 'Type'
    ? ['Recurring', 'One-off']
    : sortMode === 'Status'
      ? ['Active', 'Paused', 'Completed']
      : ['Next'];
  const selectedScheduleWidget = selectedSchedule ? scheduleWidgets[selectedSchedule.id] || null : null;
  const selectedScheduleLogs = Array.isArray(selectedScheduleWidget?.logs) ? selectedScheduleWidget.logs : [];
  const scheduleTitle = selectedSchedule?.name || 'Untitled schedule';

  // Mutations don't refetch — the backend emits a `schedule.snapshot` event as
  // part of handling each request, which updates the store (and any other open
  // tab) live. We only manage local selection here.
  const handleCreate = useCallback(
    async (payload) => {
      setMutating(true);
      try {
        const widget = await createSchedule(payload);
        const newId = String(widget?.id || '').replace(/^schedule:/, '');
        if (newId) setSelectedScheduleId(newId);
        setActiveFilter('All');
        setCreating(false);
      } finally {
        setMutating(false);
      }
    },
    [createSchedule],
  );

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
      setMutating(true);
      setError(null);
      try {
        await deleteWidget(`schedule:${schedule.id}`);
        if (selectedScheduleId === schedule.id) setSelectedScheduleId(null);
      } catch (err) {
        setError(err.message || 'Failed to delete schedule.');
      } finally {
        setMutating(false);
      }
    },
    [deleteWidget, selectedScheduleId],
  );

  // Leave edit mode whenever the selected schedule changes so a stale draft
  // never leaks across selections.
  useEffect(() => {
    setIsEditingTitle(false);
    setTitleDraft('');
  }, [selectedScheduleId]);

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

  return (
    <PageShell className="schedules-page">
      <PaneList label="Schedules" className="schedules-pane">
        <PaneListFilters
          filters={SCHEDULE_FILTERS}
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
          actionLabel="+ New"
          onAction={() => {
            setCreating(true);
            setSelectedScheduleId(null);
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
        <PaneListScroll>
          {loading ? (
            <div className="agents-empty-state">Loading schedules…</div>
          ) : error && !schedules.length ? (
            <div className="agents-empty-state">{error}</div>
          ) : visibleSchedules.length ? (
            order.map((group) =>
              groupedSchedules[group]?.length ? (
                <PaneListGroup key={group} title={group} count={groupedSchedules[group].length}>
                  {groupedSchedules[group].map((schedule) => (
                    <ScheduleRow
                      key={schedule.id}
                      schedule={schedule}
                      selected={selectedSchedule?.id === schedule.id}
                      onSelect={(id) => {
                        setSelectedScheduleId(id);
                        setCreating(false);
                      }}
                    />
                  ))}
                </PaneListGroup>
              ) : null,
            )
          ) : (
            <div className="agents-empty-state">No schedules match this view.</div>
          )}
        </PaneListScroll>
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
        {creating ? (
          <div className="detail-pane__body">
            <CreateScheduleForm onCancel={() => setCreating(false)} onCreate={handleCreate} busy={mutating} />
          </div>
        ) : !selectedSchedule ? (
          <div className="detail-pane__body">
            <div className="agents-empty-state">Select a schedule, or create one to get started.</div>
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
                <span key="type" className="muted">{isOneOff(selectedSchedule) ? 'One-off' : 'Recurring'}</span>,
                <span key="cadence" className="muted">{cadenceFor(selectedSchedule)}</span>,
                <span key="next" className="muted">
                  {statusOf(selectedSchedule) === 'completed'
                    ? 'completed'
                    : selectedSchedule.paused ? 'paused' : `Next ${formatRelative(selectedSchedule.next_run)}`}
                </span>,
                selectedSchedule.timezone ? (
                  <span key="tz" className="muted">{selectedSchedule.timezone}</span>
                ) : null,
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
                      : selectedSchedule.paused ? 'paused' : formatRelative(selectedSchedule.next_run)
                  }
                  detail={selectedSchedule.next_run ? formatAbsolute(selectedSchedule.next_run) : 'not scheduled'}
                />
                <MetricCard
                  label="Last run"
                  value={selectedSchedule.last_run ? formatRelative(selectedSchedule.last_run) : 'never'}
                  detail={selectedSchedule.last_run ? formatAbsolute(selectedSchedule.last_run) : 'no runs yet'}
                />
                <MetricCard label="Cadence" value={cadenceFor(selectedSchedule)} detail="trigger" />
                <MetricCard label="Status" value={statusOf(selectedSchedule)} detail="schedule state" />
              </div>

              <section className="agents-section schedules-summary">
                <header className="agents-section__head">
                  <h2>
                    Schedule intent <span>{statusOf(selectedSchedule)}</span>
                  </h2>
                </header>
                <p>{selectedSchedule.message || selectedSchedule.name || 'No instruction set.'}</p>
              </section>

              <section className="agents-section">
                <header className="agents-section__head">
                  <h2>Trigger</h2>
                </header>
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
                  <div className="agents-connection-row">
                    <span className="agents-connection-row__icon">cd</span>
                    <div className="agents-connection-row__body">
                      <strong>Created</strong>
                      <p>{formatAbsolute(selectedSchedule.created_at)}</p>
                    </div>
                    <span className="agents-chip agents-chip--info">{selectedSchedule.id}</span>
                  </div>
                </div>
              </section>

              <section className="agents-section">
                <header className="agents-section__head">
                  <h2>Activity</h2>
                </header>
                <div className="agents-mini-list">
                  {selectedScheduleLogs.length ? selectedScheduleLogs.map((entry, index) => (
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
                  )) : <div className="agents-empty-state">No schedule activity yet.</div>}
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
