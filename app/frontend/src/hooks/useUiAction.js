import { useCallback, useEffect, useRef, useState } from 'react';
import { failureMessage, isCancellation } from '../lib/failures';

export default function useUiAction(scopeKey) {
  const active = useRef({ scopeKey, token: null, mounted: true });
  const [state, setState] = useState({ scopeKey, pending: null, error: null });
  useEffect(() => {
    active.current.scopeKey = scopeKey;
    active.current.token = null;
    setState({ scopeKey, pending: null, error: null });
  }, [scopeKey]);
  useEffect(() => {
    const lifecycle = active.current;
    lifecycle.mounted = true;
    return () => { lifecycle.mounted = false; lifecycle.token = null; };
  }, []);

  const clearError = useCallback(() => {
    setState((current) => ({ ...current, error: null }));
  }, []);
  const run = useCallback(async (operation, task) => {
    const current = active.current;
    if (!current.mounted || current.scopeKey !== scopeKey) return { ok: false, stale: true };
    if (current.token) return { ok: false, busy: true };
    const token = Symbol();
    current.token = token;
    const isCurrent = () => current.mounted && current.scopeKey === scopeKey && current.token === token;
    setState({ scopeKey, pending: operation, error: null });
    try {
      const value = await task({ isCurrent });
      if (!isCurrent()) return { ok: false, stale: true };
      setState({ scopeKey, pending: null, error: null });
      return { ok: true, value };
    } catch (error) {
      if (!isCurrent()) return { ok: false, stale: true };
      if (isCancellation(error)) {
        setState({ scopeKey, pending: null, error: null });
        return { ok: false, cancelled: true };
      }
      const failure = failureMessage(error, operation);
      setState({ scopeKey, pending: null, error: failure });
      return { ok: false, error: failure };
    } finally {
      if (isCurrent()) current.token = null;
    }
  }, [scopeKey]);

  const visible = state.scopeKey === scopeKey ? state : { pending: null, error: null };
  return { busy: visible.pending !== null, pending: visible.pending, error: visible.error, run, clearError };
}
