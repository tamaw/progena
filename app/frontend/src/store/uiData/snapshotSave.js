import { useCallback, useEffect, useRef } from 'react';
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
  resolveSavedContent,
}) {
  const latestSaveRequestId = useRef(null);
  useEffect(() => {
    if (!pendingSave) return undefined;
    if (pendingSave.acknowledged && snapshotRequestId === pendingSave.requestId) {
      const updatedItem = items.find((item) => item.path === pendingSave.path);
      const resolvedContent = updatedItem && (resolveSavedContent
        ? resolveSavedContent(pendingSave.path) : updatedItem.content);
      if (updatedItem && resolvedContent !== undefined) {
        setDraftContent(resolvedContent || '');
        setSaving(false);
        setPendingSave(null);
        setStatusMessage(`Saved ${updatedItem.path}.`);
        return undefined;
      }
    }
    if (!realtimeConnected) {
      setSaving(false);
      setPendingSave(null);
      setStatusMessage('');
      setError(pendingSave.acknowledged
        ? saveSnapshotDisconnectedError(pendingSave.path)
        : saveInterruptedError(pendingSave.path));
      return undefined;
    }
    if (!pendingSave.acknowledged) return undefined;
    setStatusMessage(`Save acknowledged. Waiting for ${pendingSave.path} to refresh.`);
    const timeoutId = setTimeout(() => {
      setSaving(false);
      setPendingSave(null);
      setStatusMessage('');
      setError(saveSnapshotTimeoutError(pendingSave.path));
    }, SNAPSHOT_EVENT_TIMEOUT_MS);
    return () => clearTimeout(timeoutId);
  }, [items, pendingSave, realtimeConnected, resolveSavedContent, setDraftContent,
    setError, setPendingSave, setSaving, setStatusMessage, snapshotRequestId]);

  return useCallback(async () => {
    if (!path || content === savedContent || saving) return false;
    if (!realtimeConnected) {
      setError(saveRealtimeUnavailableError(path));
      setStatusMessage('');
      return false;
    }
    const requestId = createUuid();
    latestSaveRequestId.current = requestId;
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
      if (latestSaveRequestId.current !== requestId) return false;
      setPendingSave((current) => current?.requestId === requestId ? null : current);
      setSaving(false);
      setError(saveError);
      return false;
    }
  }, [buildArgs, content, dispatch, path, realtimeConnected, savedContent, saving, setError, setPendingSave, setSaving, setStatusMessage, thunk]);
}
