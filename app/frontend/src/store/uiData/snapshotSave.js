import { useCallback, useEffect } from 'react';
import { createUuid } from '../../lib/uuid';

export const SNAPSHOT_EVENT_TIMEOUT_MS = 15000;

function saveRealtimeUnavailableError(path) {
  return new Error(`Reconnect the realtime stream before saving ${path}.`);
}

function saveInterruptedError(path) {
  return new Error(`Save for ${path} could not be confirmed because the realtime stream disconnected. Reconnect and refresh to verify the current content before saving again.`);
}

function saveSnapshotDisconnectedError(path) {
  return new Error(`Save for ${path} was acknowledged, but the realtime stream disconnected before the updated snapshot arrived. Reconnect and refresh to verify the current content before saving again.`);
}

function saveSnapshotTimeoutError(path) {
  return new Error(`Save for ${path} was acknowledged, but the updated snapshot did not arrive. Reconnect and refresh to verify the current content before saving again.`);
}

// Acknowledgement only arms the save; the matching snapshot applies canonical content.
export function useSnapshotSave({
  items, snapshotRequestId, pendingSave, dispatch, thunk, buildArgs,
  path, content, savedContent, saving, realtimeConnected,
  setDraftContent, setSaving, setPendingSave, setStatusMessage, setError,
}) {
  useEffect(() => {
    if (!pendingSave?.acknowledged || snapshotRequestId !== pendingSave.requestId) return;
    const updatedItem = items.find((item) => item.path === pendingSave.path);
    if (!updatedItem) return;
    setDraftContent(updatedItem.content || '');
    setSaving(false);
    setPendingSave(null);
    setStatusMessage(`Saved ${updatedItem.path}.`);
  }, [items, pendingSave, setDraftContent, setPendingSave, setSaving, setStatusMessage, snapshotRequestId]);

  useEffect(() => {
    if (!pendingSave || realtimeConnected) {
      return;
    }
    setSaving(false);
    setPendingSave(null);
    setStatusMessage('');
    setError(
      pendingSave.acknowledged
        ? saveSnapshotDisconnectedError(pendingSave.path)
        : saveInterruptedError(pendingSave.path),
    );
  }, [pendingSave, realtimeConnected, setError, setPendingSave, setSaving, setStatusMessage]);

  useEffect(() => {
    if (!pendingSave?.acknowledged || !realtimeConnected) {
      return undefined;
    }
    setStatusMessage(`Save acknowledged. Waiting for ${pendingSave.path} to refresh.`);
    const timeoutId = window.setTimeout(() => {
      setSaving(false);
      setPendingSave(null);
      setStatusMessage('');
      setError(saveSnapshotTimeoutError(pendingSave.path));
    }, SNAPSHOT_EVENT_TIMEOUT_MS);
    return () => window.clearTimeout(timeoutId);
  }, [pendingSave, realtimeConnected, setError, setPendingSave, setSaving, setStatusMessage]);

  return useCallback(async () => {
    if (!path || content === savedContent || saving) return false;
    if (!realtimeConnected) {
      setError(saveRealtimeUnavailableError(path));
      setStatusMessage('');
      return false;
    }
    const requestId = createUuid();
    setSaving(true);
    setPendingSave({ requestId, path, acknowledged: false });
    setError(null);
    setStatusMessage('');
    try {
      await dispatch(thunk(buildArgs({ path, content, requestId }))).unwrap();
      setPendingSave((current) => current?.requestId === requestId
        ? { ...current, acknowledged: true } : current);
      return true;
    } catch (saveError) {
      let failedCurrentSave = false;
      setPendingSave((current) => {
        failedCurrentSave = current?.requestId === requestId;
        return failedCurrentSave ? null : current;
      });
      setSaving(false);
      if (failedCurrentSave) {
        setError(saveError);
      }
      return false;
    }
  }, [buildArgs, content, dispatch, path, realtimeConnected, savedContent, saving, setError, setPendingSave, setSaving, setStatusMessage, thunk]);
}
