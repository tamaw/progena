import { useCallback, useMemo } from 'react';
import { useSelector, useStore } from 'react-redux';
import { createUuid } from '../lib/uuid';
import { registerPendingOp } from '../realtime/realtimeSlice';
import {
  draftChanged, draftDiscarded, draftSaveStarted, draftSaveTracked,
  draftSaveCompleted, draftSaveFailed,
} from '../store/editorDraftsSlice';

/** useState-shaped editor state, retained independently of the mounted page. */
export default function useEntityDraft(key, initialValue) {
  const store = useStore();
  const entry = useSelector((state) => key ? state.editorDrafts?.entries[key] : undefined);

  const setValue = useCallback((next) => {
    if (!key) return;
    const retained = store.getState().editorDrafts?.entries[key];
    const previous = retained ? retained.value : initialValue;
    const value = typeof next === 'function' ? next(previous) : next;
    if (!Object.is(previous, value)) store.dispatch(draftChanged({ key, value }));
  }, [store, key, initialValue]);

  const clear = useCallback(() => {
    if (key) store.dispatch(draftDiscarded(key));
  }, [store, key]);

  const trackSave = useCallback((ticket, { requestId, events }) => {
    if (!ticket || !requestId) return;
    store.dispatch(draftSaveTracked({ ticket, requestId, events }));
    const activeSave = store.getState().editorDrafts?.entries[ticket.key]?.save;
    if (activeSave?.token === ticket.token) {
      // Direct API callers need the same disconnect handling as RTK thunks.
      // An empty preliminary event list must not erase a thunk's pending op.
      store.dispatch(registerPendingOp({
        requestId, ...(events.length ? { eventTypes: events } : {}),
      }));
    }
    // A canonical event can beat its HTTP acknowledgement. Reconcile the
    // retained correlation index as well as events arriving after this call.
    const received = store.getState().realtime?.receivedRequestEvents?.[requestId] || [];
    if (received.some((event) => event.type === 'stream.error' || event.type.endsWith('.error'))) {
      store.dispatch(draftSaveFailed({ ticket, error: 'Save failed. Please try again.' }));
    } else if (received.some((event) => events.includes(event.type))) {
      store.dispatch(draftSaveCompleted(ticket));
    }
  }, [store]);

  const beginSave = useCallback(() => {
    if (!key) return null;
    let current = store.getState().editorDrafts?.entries[key];
    if (!current) {
      store.dispatch(draftChanged({ key, value: initialValue }));
      current = store.getState().editorDrafts?.entries[key];
    }
    if (!current || current.save) return null;
    const ticket = { key, revision: current.revision, token: createUuid() };
    store.dispatch(draftSaveStarted(ticket));
    return ticket;
  }, [store, key, initialValue]);

  const failSave = useCallback((ticket, error) => {
    if (ticket) store.dispatch(draftSaveFailed({ ticket, error: typeof error === 'string' ? error : error?.message }));
  }, [store]);
  const controls = useMemo(() => ({
    clear, beginSave, trackSave, failSave,
    hasDraft: Boolean(entry), revision: entry?.revision ?? null,
    saving: Boolean(entry?.save), error: entry?.error || null,
  }), [clear, beginSave, trackSave, failSave, entry]);

  return [entry ? entry.value : initialValue, setValue, controls];
}
