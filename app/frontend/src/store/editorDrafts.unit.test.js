import { beforeEach, describe, expect, it } from 'vitest';
import reducer, {
  draftChanged, draftDiscarded, draftSaveStarted, draftSaveFailed,
  editorLocationRemembered, entityDraftKey,
} from './editorDraftsSlice';
import { applyUiEvent } from './uiEvents';
import { failPendingOp } from '../realtime/realtimeSlice';
import { store, workspaceSwitched } from './store';

const A = entityDraftKey('workflow', 'a');
const B = entityDraftKey('workflow', 'b');
const snapshot = (requestId) => applyUiEvent({
  id: `snapshot-${requestId}`, type: 'workflows.snapshot', payload: { request_id: requestId, items: [] },
});
const begin = (state, key, token, requestId) => reducer(state, draftSaveStarted({
  key, token, requestId, revision: state.entries[key].revision, events: ['workflows.snapshot'],
}));

beforeEach(() => store.dispatch(workspaceSwitched()));

describe('retained editor lifecycle', () => {
  it('keeps newer edits and another item when an earlier submitted revision completes', () => {
    let state = reducer(undefined, draftChanged({ key: A, value: '{ unfinished A' }));
    state = begin(state, A, 'save-a', 'request-a');
    state = reducer(state, draftChanged({ key: B, value: '{ unfinished B' }));
    state = reducer(state, draftChanged({ key: A, value: '{ newer unfinished A' }));
    state = reducer(state, snapshot('request-a'));
    expect(state.entries[A].value).toBe('{ newer unfinished A');
    expect(state.entries[A].save).toBeNull();
    expect(state.entries[B].value).toBe('{ unfinished B');
    state = begin(state, A, 'save-new-a', 'request-new-a');
    state = reducer(state, snapshot('request-new-a'));
    expect(state.entries[A]).toBeUndefined();
    expect(state.entries[B].value).toBe('{ unfinished B');
  });

  it('does not let a discarded save clear a newly opened draft for the same item', () => {
    let state = reducer(undefined, draftChanged({ key: A, value: 'old' }));
    state = begin(state, A, 'old-save', 'old-request');
    const oldRevision = state.entries[A].revision;
    state = reducer(state, draftDiscarded(A));
    state = reducer(state, draftChanged({ key: A, value: 'replacement' }));
    expect(state.entries[A].revision).toBeGreaterThan(oldRevision);
    state = begin(state, A, 'new-save', 'new-request');
    state = reducer(state, snapshot('old-request'));
    expect(state.entries[A].value).toBe('replacement');
    expect(state.entries[A].save).not.toBeNull();
    state = reducer(state, snapshot('new-request'));
    expect(state.entries[A]).toBeUndefined();
  });

  it('requires the matching canonical event rather than an acknowledgement or unrelated snapshot', () => {
    let state = reducer(undefined, draftChanged({ key: A, value: 'edit' }));
    state = begin(state, A, 'save', 'request');
    state = reducer(state, { type: 'uiData/saveWorkflow/fulfilled', meta: { requestId: 'request' }, payload: { request_id: 'request' } });
    state = reducer(state, snapshot('unrelated'));
    state = reducer(state, applyUiEvent({ id: 'progress', type: 'operation.progress', payload: { request_id: 'request' } }));
    expect(state.entries[A].value).toBe('edit');
    expect(state.entries[A].save).not.toBeNull();
    state = reducer(state, snapshot('request'));
    expect(state.entries[A]).toBeUndefined();
  });

  it('preserves raw text on stream failure and permits editing and retry', () => {
    let state = reducer(undefined, draftChanged({ key: A, value: '{ "incomplete": ' }));
    state = begin(state, A, 'save', 'request');
    state = reducer(state, failPendingOp({ requestId: 'request', error: 'Connection interrupted' }));
    expect(state.entries[A].value).toBe('{ "incomplete": ');
    expect(state.entries[A].save).toBeNull();
    expect(state.entries[A].error).toBe('Connection interrupted');
    state = reducer(state, draftChanged({ key: A, value: '{ "incomplete": 1 }' }));
    expect(state.entries[A].error).toBeNull();
    state = begin(state, A, 'retry', 'retry-request');
    state = reducer(state, snapshot('retry-request'));
    expect(state.entries[A]).toBeUndefined();
  });

  it('ignores an obsolete failure after a new save has taken ownership', () => {
    let state = reducer(undefined, draftChanged({ key: A, value: 'first' }));
    state = begin(state, A, 'first-save', 'first-request');
    const oldTicket = { ...state.entries[A].save };
    state = reducer(state, draftDiscarded(A));
    state = reducer(state, draftChanged({ key: A, value: 'second' }));
    state = begin(state, A, 'second-save', 'second-request');
    state = reducer(state, draftSaveFailed({ ticket: oldTicket, error: 'Old request failed' }));
    expect(state.entries[A].error).toBeNull();
    state = reducer(state, snapshot('second-request'));
    expect(state.entries[A]).toBeUndefined();
  });

  it('clears drafts and remembered selection at the actual workspace boundary', () => {
    store.dispatch(draftChanged({ key: A, value: 'workspace A secret' }));
    store.dispatch(editorLocationRemembered({ page: 'workflows', location: '/workflows/a' }));
    store.dispatch(workspaceSwitched());
    expect(store.getState().editorDrafts.entries).toEqual({});
    expect(store.getState().editorDrafts.locations).toEqual({});
  });
});
