import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { Link } from 'react-router-dom';
import { useDispatch, useSelector } from 'react-redux';
import { selectRealtimeStreamReady } from '../../../realtime/realtimeSlice';
import { RunsNavIcon } from '../../../components/navIcons';
import { EntryLine, Trailing } from './ChatEntryLine';
import StickyChatEntry from './StickyChatEntry';
import { formatTimestamp } from '../models/messageRenderUtils';
import entryStyles from './StickyChatEntry.module.css';
import {
  fetchRuns,
  runsQueryKey,
  selectRunTrace,
  selectRunTraceRequest,
} from '../../runs/state';
import { formatDuration, runDurationMs } from '../../runs/runFormat';
import { backgroundRunIds, deriveBackgroundActivities } from '../models/backgroundActivityModel';
import { watchBackgroundTasks } from '../backgroundTaskSubscription';
import { watchBackgroundRunTrace } from '../backgroundRunTraceSubscription';
import { selectBackgroundTasks } from '../state/backgroundTasks';
import styles from './BackgroundActivity.module.css';

const EMPTY_SNAPSHOT = () => null;
const NOOP_SUBSCRIBE = () => () => {};

function useLiveBackgroundActivity(liveTurnStore) {
  return useSyncExternalStore(
    liveTurnStore?.subscribeBackgroundActivity || NOOP_SUBSCRIBE,
    liveTurnStore?.getBackgroundActivitySnapshot || EMPTY_SNAPSHOT,
    liveTurnStore?.getBackgroundActivitySnapshot || EMPTY_SNAPSHOT,
  );
}

function runIdsFromKey(runIdsKey) {
  return runIdsKey ? runIdsKey.split('\u0000') : [];
}

function useTrackedWorkflowRows({ messages, liveActivity, runsUrl, hydrate = true }) {
  const dispatch = useDispatch();
  const streamReady = useSelector(selectRealtimeStreamReady);
  const runIdsKey = useMemo(
    () => backgroundRunIds(messages, liveActivity).join('\u0000'),
    [messages, liveActivity],
  );
  const snapshots = useSelector((state) => state.uiData?.runs?.snapshots || {});
  const requests = useSelector((state) => state.uiData?.runs?.requests || {});
  const rows = useMemo(() => {
    const result = new Map();
    for (const runId of runIdsFromKey(runIdsKey)) {
      const snapshot = snapshots[runsQueryKey({ kind: 'workflow', q: runId, sort: 'newest' })];
      const row = snapshot?.runs?.find((candidate) => candidate?.run_id === runId);
      if (row) result.set(runId, row);
    }
    return result;
  }, [runIdsKey, snapshots]);
  const runRequests = useMemo(() => {
    const result = new Map();
    for (const runId of runIdsFromKey(runIdsKey)) {
      const key = runsQueryKey({ kind: 'workflow', q: runId, sort: 'newest' });
      const request = requests[key];
      const snapshot = snapshots[key];
      const notFound = Boolean(
        snapshot
        && !request?.loading
        && !request?.error
        && !(snapshot.runs || []).some((row) => row?.run_id === runId),
      );
      result.set(runId, { ...request, notFound });
    }
    return result;
  }, [requests, runIdsKey, snapshots]);
  const refresh = useCallback(() => {
    if (!streamReady || !runsUrl) return;
    for (const runId of runIdsFromKey(runIdsKey)) {
      dispatch(fetchRuns({ runsUrl, kind: 'workflow', q: runId, sort: 'newest', resync: true }));
    }
  }, [dispatch, runIdsKey, runsUrl, streamReady]);

  useEffect(() => {
    if (hydrate) refresh();
  }, [hydrate, refresh]);

  return { rows, runRequests, refresh };
}

function useActivities({
  messages, liveTurnStore, threadId, runsUrl, hydrateRuns, asyncTaskRecords, tasksUrl, subscribeTasks,
}) {
  const liveActivity = useLiveBackgroundActivity(liveTurnStore);
  const { rows, runRequests, refresh } = useTrackedWorkflowRows({
    messages,
    liveActivity,
    runsUrl,
    hydrate: hydrateRuns,
  });
  useEffect(() => {
    if (!subscribeTasks || !tasksUrl) return undefined;
    return watchBackgroundTasks(tasksUrl);
  }, [subscribeTasks, tasksUrl]);
  const activities = useMemo(
    () => deriveBackgroundActivities({
      messages,
      liveTurn: liveActivity,
      threadId,
      runRows: rows,
      runRequests,
      asyncTaskRecords,
    }),
    [asyncTaskRecords, liveActivity, messages, rows, runRequests, threadId],
  );
  return { ...activities, refresh };
}

function displayStatus(activity) {
  if (activity.status === 'checking') return 'Checking status';
  if (activity.status === 'paused') return 'Paused';
  if (activity.status === 'interrupted' || activity.status === 'waiting') return 'Waiting';
  if (activity.status === 'waiting_approval') return 'Waiting for approval';
  if (activity.status === 'unavailable') return 'Unable to refresh';
  if (activity.status === 'success') return 'Completed';
  if (activity.status === 'error') return 'Failed';
  return activity.status ? `${activity.status[0].toUpperCase()}${activity.status.slice(1)}` : 'Running';
}

function activityDuration(activity, now) {
  if (activity.status === 'checking') return '—';
  if (activity.type === 'workflow' && activity.row) {
    const duration = runDurationMs(activity.row, now);
    return duration === null ? '—' : formatDuration(activity.row.started_at, activity.row.completed_at, now);
  }
  if (!Number.isFinite(activity.startedAt)) return '—';
  const end = Number.isFinite(activity.finishedAt) ? activity.finishedAt : now;
  return formatDuration(new Date(activity.startedAt).toISOString(), new Date(end).toISOString(), now);
}

function WorkflowDetails({ activity, runsUrl }) {
  const trace = useSelector((state) => selectRunTrace(state, 'workflow', activity.runId));
  const request = useSelector((state) => selectRunTraceRequest(state, 'workflow', activity.runId));
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    if (!runsUrl || !activity.runId) return undefined;
    return watchBackgroundRunTrace(`${runsUrl}/subscription`, 'workflow', activity.runId);
  }, [activity.runId, runsUrl]);
  const spans = Array.isArray(trace?.spans) ? trace.spans.filter((span) => span?.kind !== 'run') : [];
  const visibleSpans = showAll ? spans : spans.slice(0, 8);
  return <div className={styles.details}>
    {request.error ? <p className="message-content" role="alert">Unable to load run details.</p> : null}
    {visibleSpans.length ? <>
      <div className={styles.list}>{visibleSpans.map((span) => (
        <StickyChatEntry key={span.id || `${span.name}:${span.start}`}
          badgeClassName={span.status === 'failed' || span.status === 'error' ? 'entry__badge--err' : 'entry__badge--tool'}
          active={span.status === 'running'}>
          <EntryLine title={span.name || span.operation || 'Activity'}
            text={<span className="tool-call-entry__summary">{span.status || span.kind || 'Recorded'}</span>}
            trailing={<Trailing time={formatTimestamp(span.start)} />}>
            {span.error ? <p className="message-content" role="alert">{span.error}</p> : null}
          </EntryLine>
        </StickyChatEntry>
      ))}</div>
      {spans.length > visibleSpans.length ? <button type="button" className="earlier-lines-toggle"
        onClick={() => setShowAll(true)}>Show all {spans.length} activities</button> : null}
    </> : <p className="muted">{request.loading ? 'Loading run details…' : 'No trace activity recorded.'}</p>}
  </div>;
}

function DetailsIcon() {
  return <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true">
    <rect x="3" y="2" width="10" height="12" rx="1.5" />
    <path d="M5.5 5h5M5.5 8h5M5.5 11h3" />
  </svg>;
}

function RetryIcon() {
  return <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M13.5 8a5.5 5.5 0 1 1-1.7-3.98M13.5 2.5v3h-3" />
  </svg>;
}

function ActivityRow({ activity, now, runsUrl, onRefresh }) {
  const [expanded, setExpanded] = useState(false);
  const runHref = activity.type === 'workflow' ? `/runs?run=workflow:${encodeURIComponent(activity.runId)}` : null;
  const failed = activity.status === 'failed' || activity.status === 'error';
  const waiting = ['paused', 'interrupted', 'waiting', 'waiting_approval', 'unavailable'].includes(activity.status);
  const badge = failed ? 'entry__badge--err' : waiting ? 'entry__badge--plan' : 'entry__badge--tool';
  const time = formatTimestamp(activity.startedAt);
  return <li>
    <StickyChatEntry className={styles.row} badgeClassName={badge} active={activity.status === 'running'}>
      <EntryLine title={activity.name}
        text={<span className="tool-call-entry__summary entry__meta">{displayStatus(activity)}</span>}
        summaryLabel={`${activity.name}, ${displayStatus(activity)}`}
        open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}
        trailing={<>
          <Trailing duration={activityDuration(activity, now)} />
          <span className="message-actions msg__actions">
            {activity.status === 'unavailable' && onRefresh ? <button type="button" className="msg-action-btn"
              aria-label={`Retry ${activity.name}`} onClick={(event) => { event.preventDefault(); onRefresh(); }}>
              <RetryIcon /><span className="msg-action-btn__label">Retry</span>
            </button> : null}
            <button type="button" className="msg-action-btn"
              aria-label={`${expanded ? 'Hide' : 'Show'} ${activity.name} details`} aria-expanded={expanded}
              onClick={(event) => { event.preventDefault(); setExpanded((value) => !value); }}>
              <DetailsIcon /><span className="msg-action-btn__label">Details</span>
            </button>
            {runHref ? <Link className="msg-action-btn" to={runHref} aria-label={`Open ${activity.name} in Runs`}
              onClick={(event) => event.stopPropagation()}>
              <RunsNavIcon /><span className="msg-action-btn__label">Runs</span>
            </Link> : null}
          </span>
          <Trailing time={time} />
        </>}>
        <div className="tool-call-entry__detail">
          {activity.error ? <p className="message-content" role="alert">{activity.error}</p> : null}
          {activity.summary ? <p className="message-content">{activity.summary}</p> : null}
          <dl className={styles.metadata}>
            <dt>Status</dt><dd>{displayStatus(activity)}</dd>
            <dt>{activity.type === 'workflow' ? 'Run' : 'Task'}</dt><dd>{activity.runId || activity.taskId}</dd>
            {time ? <><dt>Started</dt><dd>{new Date(activity.startedAt).toLocaleString()}</dd></> : null}
            {Number.isFinite(activity.finishedAt) ? <><dt>Finished</dt><dd>{new Date(activity.finishedAt).toLocaleString()}</dd></> : null}
          </dl>
          {expanded && activity.type === 'workflow' ? <WorkflowDetails activity={activity} runsUrl={runsUrl} /> : null}
        </div>
      </EntryLine>
    </StickyChatEntry>
  </li>;
}

function useElapsedClock(active) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

/** Sticky, chat-scoped work that survives a root reply ending. */
export function BackgroundActivity({ messages, liveTurnStore, threadId, runsUrl, chatsUrl }) {
  const asyncTaskRecords = useSelector((state) => selectBackgroundTasks(state, threadId));
  const tasksUrl = threadId && chatsUrl
    ? `${chatsUrl}/${encodeURIComponent(threadId)}/background-tasks`
    : null;
  const { active, refresh } = useActivities({
    messages, liveTurnStore, threadId, runsUrl, hydrateRuns: true,
    asyncTaskRecords, tasksUrl, subscribeTasks: true,
  });
  const now = useElapsedClock(active.length > 0);
  if (!active.length) return null;
  return <section className={`chat-transcript ${entryStyles.region} ${styles.sticky}`} aria-label="Background activity">
    <ul className={styles.list}>{active.map((activity) => <ActivityRow key={activity.key} activity={activity} now={now} runsUrl={runsUrl} onRefresh={refresh} />)}</ul>
  </section>;
}

/** Settled work is transcript history, never retained in the composer sticky area. */
export function BackgroundActivityHistory({ messages, liveTurnStore, threadId, runsUrl, chatsUrl }) {
  const asyncTaskRecords = useSelector((state) => selectBackgroundTasks(state, threadId));
  const tasksUrl = threadId && chatsUrl
    ? `${chatsUrl}/${encodeURIComponent(threadId)}/background-tasks`
    : null;
  const { history } = useActivities({
    messages, liveTurnStore, threadId, runsUrl, hydrateRuns: false,
    asyncTaskRecords, tasksUrl, subscribeTasks: false,
  });
  const now = useElapsedClock(false);
  if (!history.length) return null;
  return <section className={`chat-transcript ${styles.history}`} aria-label="Background activity history">
    <ul className={styles.list}>{history.map((activity) => <ActivityRow key={activity.key} activity={activity} now={now} runsUrl={runsUrl} />)}</ul>
  </section>;
}
