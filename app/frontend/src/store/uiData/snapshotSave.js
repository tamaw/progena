import { useCallback } from 'react';

function saveRealtimeUnavailableError(path) {
  return `Reconnect the realtime stream before saving ${path}.`;
}

/**
 * Dispatch an acknowledged request while retaining completion ownership in an
 * entity draft. The HTTP acknowledgement deliberately does not complete a
 * save: the draft slice settles the ticket when the expected canonical event
 * is applied (including an event that arrived before the acknowledgement).
 */
export function useSnapshotSave({
  dispatch, thunk, buildArgs, path, content, savedContent, saving,
  realtimeConnected, beginSave, trackSave, failSave, events,
}) {
  return useCallback(async () => {
    if (!path || content === savedContent || saving) return false;

    const ticket = beginSave();
    if (!ticket) return false;
    if (!realtimeConnected) {
      failSave(ticket, saveRealtimeUnavailableError(path));
      return false;
    }

    try {
      const operation = dispatch(thunk(buildArgs({ path, content })));
      trackSave(ticket, { requestId: operation.requestId, events });
      await operation.unwrap();
      return true;
    } catch (saveError) {
      failSave(ticket, saveError);
      return false;
    }
  }, [beginSave, buildArgs, content, dispatch, events, failSave, path,
    realtimeConnected, savedContent, saving, thunk, trackSave]);
}
