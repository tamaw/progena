import { useCallback } from 'react';
import { failureMessage, isCancellation } from '../lib/failures';
import useEntityDraft from './useEntityDraft';
import { entityDraftKey } from '../store/editorDraftsSlice';

export function normalizeInlineTitle(value) {
  return String(value ?? '').replace(/\s+/g, ' ').trim();
}

/**
 * Retains a detail-title edit independently of the mounted detail pane.
 *
 * `kind` is required to prevent one entity type's title draft from surfacing
 * under another. `onCommit` receives the saved ticket controls so the caller
 * can bind a 202 acknowledgement to its canonical stream confirmation:
 * `onCommit(title, { ticket, trackSave, failSave })`.
 */
export default function useInlineTitleEditor({ kind, scopeKey, title, onCommit }) {
  if (!kind) {
    throw new Error('useInlineTitleEditor requires a draft kind');
  }

  const key = entityDraftKey(kind, scopeKey, 'title');
  const initialValue = { editing: false, draft: String(title ?? '') };
  const [value, setValue, controls] = useEntityDraft(key, initialValue);
  const draft = typeof value?.draft === 'string' ? value.draft : initialValue.draft;
  const isEditing = Boolean(value?.editing);
  const {
    clear,
    beginSave,
    trackSave,
    failSave,
    saving,
    error,
  } = controls;

  const start = useCallback(() => {
    if (!key) return;
    setValue((current) => ({
      editing: true,
      draft: typeof current?.draft === 'string' ? current.draft : String(title ?? ''),
    }));
  }, [key, setValue, title]);

  const cancel = useCallback(() => {
    clear();
  }, [clear]);

  const setDraft = useCallback((nextDraft) => {
    if (!key) return;
    setValue((current) => {
      const previousDraft = typeof current?.draft === 'string'
        ? current.draft
        : String(title ?? '');
      const draftValue = typeof nextDraft === 'function'
        ? nextDraft(previousDraft)
        : nextDraft;
      return { editing: true, draft: String(draftValue ?? '') };
    });
  }, [key, setValue, title]);

  const save = useCallback(async () => {
    if (!key) return { ok: false, skipped: true };

    const nextTitle = normalizeInlineTitle(draft);
    if (!nextTitle || nextTitle === normalizeInlineTitle(title)) {
      clear();
      return { ok: false, skipped: true };
    }

    const ticket = beginSave();
    if (!ticket) return { ok: false, busy: true };

    try {
      const result = await onCommit(nextTitle, {
        ticket,
        trackSave,
        failSave,
      });
      if (result?.ok === false) {
        // A stale UI-action result can still have a tracked request in flight.
        // The page wrapper settles pre-dispatch stale calls itself.
        if (!result.stale && !result.cancelled && !result.busy) {
          failSave(ticket, result.error || 'Save title failed. Please try again.');
        }
        return result;
      }
      // An HTTP acknowledgement only means the request was accepted. The
      // retained entry is cleared by its correlated canonical stream event.
      return { ok: true, value: result };
    } catch (commitError) {
      if (isCancellation(commitError)) {
        failSave(ticket, null);
        return { ok: false, cancelled: true };
      }
      const failure = failureMessage(commitError, 'Save title');
      failSave(ticket, failure.message);
      return { ok: false, error: failure };
    }
  }, [
    beginSave,
    clear,
    draft,
    failSave,
    key,
    onCommit,
    title,
    trackSave,
  ]);

  return {
    isEditing,
    draft,
    saving,
    error,
    setDraft,
    start,
    cancel,
    save,
  };
}
