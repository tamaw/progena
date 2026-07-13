import { useCallback, useEffect } from 'react';

// Acknowledgement only arms the save; the matching snapshot applies canonical content.
export function useSnapshotSave({
  items, snapshotRequestId, pendingSave, dispatch, thunk, buildArgs,
  path, content, savedContent, saving,
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

  return useCallback(async () => {
    if (!path || content === savedContent || saving) return false;
    const requestId = crypto.randomUUID();
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
      setPendingSave(null);
      setSaving(false);
      setError(saveError);
      return false;
    }
  }, [buildArgs, content, dispatch, path, savedContent, saving, setError, setPendingSave, setSaving, setStatusMessage, thunk]);
}
