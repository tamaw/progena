import { useEffect, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../../context/ConfigContext';
import {
  selectRealtimeConnected,
  selectRealtimeStreamReady,
} from '../../realtime/realtimeSlice';
import { fetchWidgets, selectUiWidgets } from '../schedules/widgetsState';
import useStreamTopic from '../../hooks/useStreamTopic';

/**
 * Shared data hook for the redesigned DashboardPage.
 *
 * Returns live Redux state for status, widgets, alerts, workflows, runs,
 * schedules, and inbox. Hydrates independent collections once per live stream
 * session (after `streamReady`) using the existing acknowledged-request
 * framework. Deduplicates StrictMode double-mounts and reconnect refetches.
 */
import { fetchSchedules, selectSchedules, selectSchedulesState } from '../schedules/state';

/**
 * Shared data hook for the redesigned DashboardPage.
 *
 * Returns live Redux state for status, widgets, alerts, workflows, runs,
 * schedules, and inbox. Hydrates independent collections once per live stream
 * session (after `streamReady`) using the existing acknowledged-request
 * framework. Deduplicates StrictMode double-mounts and reconnect refetches.
 */
import { selectInboxUnreadCount, selectInboxState } from '../inbox/state';

/**
 * Shared data hook for the redesigned DashboardPage.
 *
 * Returns live Redux state for status, widgets, alerts, workflows, runs,
 * schedules, and inbox. Hydrates independent collections once per live stream
 * session (after `streamReady`) using the existing acknowledged-request
 * framework. Deduplicates StrictMode double-mounts and reconnect refetches.
 */
import { fetchStatus, selectUiStatus } from '../monitoring/state';

/**
 * Shared data hook for the redesigned DashboardPage.
 *
 * Returns live Redux state for status, widgets, alerts, workflows, runs,
 * schedules, and inbox. Hydrates independent collections once per live stream
 * session (after `streamReady`) using the existing acknowledged-request
 * framework. Deduplicates StrictMode double-mounts and reconnect refetches.
 */
import { selectUiAlerts } from '../alerts/state';

/**
 * Shared data hook for the redesigned DashboardPage.
 *
 * Returns live Redux state for status, widgets, alerts, workflows, runs,
 * schedules, and inbox. Hydrates independent collections once per live stream
 * session (after `streamReady`) using the existing acknowledged-request
 * framework. Deduplicates StrictMode double-mounts and reconnect refetches.
 */
import { fetchWorkflows, fetchWorkflowRuns, selectWorkflows, selectWorkflowsState, selectWorkflowRuns } from '../workflows/state/workflows';

/**
 * Shared data hook for the redesigned DashboardPage.
 *
 * Returns live Redux state for status, widgets, alerts, workflows, runs,
 * schedules, and inbox. Hydrates independent collections once per live stream
 * session (after `streamReady`) using the existing acknowledged-request
 * framework. Deduplicates StrictMode double-mounts and reconnect refetches.
 */

/**
 * Shared data hook for the redesigned DashboardPage.
 *
 * Returns live Redux state for status, widgets, alerts, workflows, runs,
 * schedules, and inbox. Hydrates independent collections once per live stream
 * session (after `streamReady`) using the existing acknowledged-request
 * framework. Deduplicates StrictMode double-mounts and reconnect refetches.
 */
export function useDashboardData() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();

  useStreamTopic('dashboard');
  useStreamTopic('schedules');
  useStreamTopic('workflows');
  const connected = useSelector(selectRealtimeConnected);
  const streamReady = useSelector(selectRealtimeStreamReady);
  const status = useSelector(selectUiStatus) ?? null;
  const widgets = useSelector(selectUiWidgets) ?? [];
  const alerts = useSelector(selectUiAlerts) ?? [];
  const schedules = useSelector(selectSchedules) ?? [];
  const workflows = useSelector(selectWorkflows) ?? [];
  const runs = useSelector(selectWorkflowRuns) ?? [];
  const inboxUnreadCount = useSelector(selectInboxUnreadCount) ?? 0;
  const workflowsState = useSelector(selectWorkflowsState);
  const schedulesState = useSelector(selectSchedulesState);
  const inboxState = useSelector(selectInboxState);
  const inboxPendingApprovalCount = inboxState?.counts?.pendingApprovals ?? 0;

  const widgetsUrl = getApiUrl('widgets');
  const schedulesUrl = getApiUrl('schedules');
  const workflowsUrl = getApiUrl('workflows');
  const workflowRunsUrl = getApiUrl('workflowRuns');
  const statusUrl = getApiUrl('status');

  const requestedWidgetsUrlRef = useRef(null);
  const requestedSchedulesUrlRef = useRef(null);
  const requestedWorkflowsUrlRef = useRef(null);
  const requestedRunsUrlRef = useRef(null);
  const requestedStatusUrlRef = useRef(null);

  useEffect(() => {
    if (!streamReady) {
      requestedWidgetsUrlRef.current = null;
      requestedSchedulesUrlRef.current = null;
      requestedWorkflowsUrlRef.current = null;
      requestedRunsUrlRef.current = null;
      requestedStatusUrlRef.current = null;
      return;
    }

    if (requestedStatusUrlRef.current !== statusUrl) {
      requestedStatusUrlRef.current = statusUrl;
      dispatch(fetchStatus({ statusUrl }));
    }

    if (requestedWidgetsUrlRef.current !== widgetsUrl) {
      requestedWidgetsUrlRef.current = widgetsUrl;
      dispatch(fetchWidgets({ widgetsUrl }));
    }

    if (requestedSchedulesUrlRef.current !== schedulesUrl) {
      requestedSchedulesUrlRef.current = schedulesUrl;
      dispatch(fetchSchedules({ schedulesUrl }));
    }

    if (requestedWorkflowsUrlRef.current !== workflowsUrl) {
      requestedWorkflowsUrlRef.current = workflowsUrl;
      dispatch(fetchWorkflows({ workflowsUrl }));
    }

    if (requestedRunsUrlRef.current !== workflowRunsUrl) {
      requestedRunsUrlRef.current = workflowRunsUrl;
      dispatch(fetchWorkflowRuns({ workflowRunsUrl }));
    }

    // Finite requests belong to Redux, not this component. Aborting on effect
    // cleanup would cancel StrictMode's first request while the retained URL
    // refs prevent the replay from replacing it.
  }, [dispatch, streamReady, statusUrl, widgetsUrl, schedulesUrl, workflowsUrl, workflowRunsUrl]);

  return {
    status,
    widgets,
    alerts,
    schedules,
    workflows,
    runs,
    inboxUnreadCount,
    inboxPendingApprovalCount,
    connected,
    streamReady,
    retryRuns: () => dispatch(fetchWorkflowRuns({ workflowRunsUrl })),
    retrySchedules: () => dispatch(fetchSchedules({ schedulesUrl })),
    collectionState: {
      workflows: {
        loaded: workflowsState?.loaded ?? false,
        loading: workflowsState?.loading ?? false,
        error: workflowsState?.error ?? null,
      },
      runs: {
        loaded: workflowsState?.runsLoaded ?? false,
        loading: workflowsState?.runsLoading ?? false,
        error: workflowsState?.runsError ?? null,
      },
      schedules: {
        loaded: schedulesState?.loaded ?? false,
        loading: schedulesState?.loading ?? false,
        error: schedulesState?.error ?? null,
      },
      inbox: {
        loaded: inboxState.counts.unread !== null && inboxState.counts.pendingApprovals !== null,
        loading: inboxState.counts.loading,
        error: inboxState.counts.error,
      },
    },
  };
}
