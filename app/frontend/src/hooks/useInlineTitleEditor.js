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
  const saveTokenRef = useRef(0);
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
    setState({ scopeKey, editing: false, draft: '', saving: false, error: null });
  }, [scopeKey]);


  const isCurrentScope = useCallback((savedScope) => (
    mountedRef.current && scopeRef.current === savedScope
  ), []);

  const start = useCallback(() => {
    const nextScopeKey = scopeRef.current;
    setState({ scopeKey: nextScopeKey, editing: true, draft: titleRef.current, saving: false, error: null });
  }, []);

  const cancel = useCallback(() => {
    if (activeSaveRef.current?.scopeKey === scopeRef.current) {
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
    if (activeSaveRef.current?.scopeKey === scopeRef.current) {
      return { ok: false, busy: true };
    }

    const savedScope = scopeRef.current;
    const nextTitle = normalizeInlineTitle(state.draft);
    const currentTitle = normalizeInlineTitle(titleRef.current);
    if (!nextTitle || nextTitle === currentTitle) {
      if (isCurrentScope(savedScope)) {
        setState({ scopeKey: savedScope, editing: false, draft: titleRef.current, saving: false, error: null });
      }
      return { ok: false, skipped: true };
    }
    const saveToken = ++saveTokenRef.current;
    activeSaveRef.current = { scopeKey: savedScope, token: saveToken };
    const releaseSave = () => {
      if (activeSaveRef.current?.token === saveToken) {
        activeSaveRef.current = null;
      }
    };

    setState((current) => ({ ...current, saving: true, error: null }));
    try {
      const result = await onCommit(nextTitle);
      if (!isCurrentScope(savedScope)) {
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
      if (!isCurrentScope(savedScope)) {
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
  }, [isCurrentScope, onCommit, state.draft]);

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
