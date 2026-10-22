import { useCallback, useEffect, useMemo } from 'react';
import useEntityDraft from '../../../hooks/useEntityDraft';
import { entityDraftKey } from '../../../store/editorDraftsSlice';

// Structured config editors parse each keystroke, which intentionally drops an
// incomplete line. Keep the raw text in the shared editor registry instead of
// component state so switching nodes, closing an inspector, or leaving the
// page cannot discard a partially authored field. A field key includes the
// routine, node, and config name: identical config shapes must never share a
// buffer.
export function useBufferedFieldText(routineId, nodeId, field, value, toText, isComplete) {
  const key = useMemo(
    () => entityDraftKey('routine-field', JSON.stringify([routineId, nodeId]), field),
    [routineId, nodeId, field],
  );
  const canonicalText = toText(value);
  const initialValue = useMemo(() => ({ text: canonicalText, complete: true }), [canonicalText]);
  const [draft, setRetainedDraft, { clear, hasDraft }] = useEntityDraft(key, initialValue);
  const text = draft.text;

  // Completion metadata is serializable so graph saves can settle fields even
  // after their inspector or the entire editor has unmounted and reopened.
  // Already-canonical source cannot mask a future snapshot; clearing it does
  // not alter the textarea text because its current canonical default matches.
  useEffect(() => {
    if (hasDraft && text === canonicalText) clear();
  }, [canonicalText, clear, hasDraft, text]);

  const setText = useCallback((next) => {
    setRetainedDraft((current) => {
      const text = typeof next === 'function' ? next(current.text) : next;
      return { text, complete: isComplete(text) };
    });
  }, [isComplete, setRetainedDraft]);

  return [text, setText];
}
