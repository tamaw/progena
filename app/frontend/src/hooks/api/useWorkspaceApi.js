import { useCallback } from 'react';
import { useApiClient } from './useApiClient';

/** Workspace graph + action run/generate + event emit (Workspace context). */
export function useWorkspaceApi() {
  const { request } = useApiClient();

  const getWorkspace = useCallback(() => request('workspace'), [request]);

  const runAction = useCallback(
    (payload) => request('actionRun', { method: 'POST', body: JSON.stringify(payload) }),
    [request],
  );

  const generateAction = useCallback(
    (payload) => request('actionGenerate', { method: 'POST', body: JSON.stringify(payload) }),
    [request],
  );

  const createEvent = useCallback(
    (payload) => request('events', { method: 'POST', body: JSON.stringify(payload) }),
    [request],
  );

  return { getWorkspace, runAction, generateAction, createEvent };
}
