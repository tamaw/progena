import { useCallback } from 'react';
import { useDispatch } from 'react-redux';
import { useConfig } from '../../context/ConfigContext';
import { createSchedule as create, fetchSchedules, retriggerSchedule as triggerSchedule } from './state';

/** Bind schedule operations to the active workspace and shared request lifecycle. */
export function useSchedulesApi() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const getSchedules = useCallback(
    () => dispatch(fetchSchedules({ schedulesUrl: getApiUrl('schedules') })).unwrap(),
    [dispatch, getApiUrl],
  );
  const createSchedule = useCallback(
    (schedule, { requestId } = {}) => dispatch(create({
      schedulesUrl: getApiUrl('schedules'), schedule, requestId,
    })).unwrap(),
    [dispatch, getApiUrl],
  );
  const retriggerSchedule = useCallback(
    (scheduleId, text, { requestId } = {}) => dispatch(triggerSchedule({
      schedulesUrl: getApiUrl('schedules'), scheduleId, text, requestId,
    })).unwrap(),
    [dispatch, getApiUrl],
  );
  return { createSchedule, getSchedules, retriggerSchedule };
}
