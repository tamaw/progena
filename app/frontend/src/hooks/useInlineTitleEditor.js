import { useCallback, useEffect, useRef, useState } from 'react';
import { failureMessage, isCancellation } from '../lib/failures';

export function normalizeInlineTitle(value) {
  return String(value ?? '').replace(/\s+/g, ' ').trim();
}

/**
 * Owns the short-lived draft for a detail title. A scope change drops the draft
 * and makes completions for the previous entity invisible to the new one.
 */
export default function useInlineTitleEditor({ scopeKey, title, onCommit }) {
  const scopeRef = useRef(scopeKey);
  const titleRef = useRef(String(title ?? ''));
  const mountedRef = useRef(true);
  const activeSaveRef = useRef(null);
  const scopeIncarnationRef = useRef(0);
  const [state, setState] = useState({ scopeKey, editing: false, draft: '', saving: false, error: null });

  scopeRef.current = scopeKey;
  titleRef.current = String(title ?? '');

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    scopeIncarnationRef.current += 1;
    activeSaveRef.current = null;
    setState({ scopeKey, editing: false, draft: '', saving: false, error: null });
  }, [scopeKey]);

  const isCurrentScope = useCallback((savedScope, savedIncarnation) => (
    mountedRef.current
    && scopeRef.current === savedScope
    && scopeIncarnationRef.current === savedIncarnation
  ), []);

  const isCurrentSave = useCallback((savedScope, savedIncarnation, saveToken) => (
    isCurrentScope(savedScope, savedIncarnation)
    && activeSaveRef.current?.token === saveToken
  ), [isCurrentScope]);

  const start = useCallback(() => {
    const nextScopeKey = scopeRef.current;
    setState({ scopeKey: nextScopeKey, editing: true, draft: titleRef.current, saving: false, error: null });
  }, []);

  const cancel = useCallback(() => {
    if (
      activeSaveRef.current?.scopeKey === scopeRef.current
      && activeSaveRef.current.incarnation === scopeIncarnationRef.current
    ) {
      return;
    }
    setState({
      scopeKey: scopeRef.current,
      editing: false,
      draft: titleRef.current,
      saving: false,
      error: null,
    });
  }, []);

  const setDraft = useCallback((draft) => {
    setState((current) => ({ ...current, draft, error: null }));
  }, []);

  const save = useCallback(async () => {
    if (
      activeSaveRef.current?.scopeKey === scopeRef.current
      && activeSaveRef.current.incarnation === scopeIncarnationRef.current
    ) {
      return { ok: false, busy: true };
    }
    const savedScope = scopeRef.current;
    const savedIncarnation = scopeIncarnationRef.current;
    const nextTitle = normalizeInlineTitle(state.draft);
    const currentTitle = normalizeInlineTitle(titleRef.current);
    if (!nextTitle || nextTitle === currentTitle) {
      if (isCurrentScope(savedScope, savedIncarnation)) {
        setState({ scopeKey: savedScope, editing: false, draft: titleRef.current, saving: false, error: null });
      }
      return { ok: false, skipped: true };
    }
    const saveToken = Symbol('title-save');
    activeSaveRef.current = {
      scopeKey: savedScope,
      incarnation: savedIncarnation,
      token: saveToken,
    };
    const releaseSave = () => {
      if (
        activeSaveRef.current?.incarnation === savedIncarnation
        && activeSaveRef.current.token === saveToken
      ) {
        activeSaveRef.current = null;
      }
    };

    setState((current) => ({ ...current, saving: true, error: null }));
    try {
      const result = await onCommit(nextTitle);
      if (!isCurrentSave(savedScope, savedIncarnation, saveToken)) {
        return { ok: false, stale: true };
      }
      if (result?.ok === false) {
        if (result.stale || result.cancelled || result.busy) {
          setState((current) => ({ ...current, saving: false }));
          return result;
        }
        const error = failureMessage(result.error || new Error('Please try again.'), 'Save title');
        setState((current) => ({ ...current, saving: false, error }));
        return { ...result, error };
      }
      setState({ scopeKey: savedScope, editing: false, draft: nextTitle, saving: false, error: null });
      return { ok: true, value: result };
    } catch (error) {
      if (!isCurrentSave(savedScope, savedIncarnation, saveToken)) {
        return { ok: false, stale: true };
      }
      if (isCancellation(error)) {
        setState((current) => ({ ...current, saving: false }));
        return { ok: false, cancelled: true };
      }
      const failure = failureMessage(error, 'Save title');
      setState((current) => ({ ...current, saving: false, error: failure }));
      return { ok: false, error: failure };
    } finally {
      releaseSave();
    }
  }, [isCurrentSave, isCurrentScope, onCommit, state.draft]);

  const visible = state.scopeKey === scopeKey
    ? state
    : { editing: false, draft: '', saving: false, error: null };

  return {
    isEditing: visible.editing,
    draft: visible.draft,
    saving: visible.saving,
    error: visible.error,
    setDraft,
    start,
    cancel,
    save,
  };
}
