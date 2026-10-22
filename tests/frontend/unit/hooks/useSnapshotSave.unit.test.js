// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider, useDispatch } from 'react-redux';
import { configureStore, createAsyncThunk } from '@reduxjs/toolkit';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import useEntityDraft from '@frontend/hooks/useEntityDraft';
import editorDraftsReducer, { draftChanged, entityDraftKey } from '@frontend/store/editorDraftsSlice';
import realtimeReducer, { failPendingOp } from '@frontend/realtime/realtimeSlice';
import uiDataReducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';
import { useSnapshotSave } from '@frontend/hooks/useSnapshotSave';

let root;
let container;
let store;
let thunkNumber = 0;

function createStore() {
  return configureStore({
    reducer: {
      editorDrafts: editorDraftsReducer,
      realtime: realtimeReducer,
      uiData: uiDataReducer,
    },
  });
}

function deferredSave() {
  let resolve;
  let reject;
  const acknowledgement = new Promise((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return {
    thunk: createAsyncThunk(`test/snapshot-save/${++thunkNumber}`, async () => acknowledgement),
    resolve,
    reject,
  };
}

function ProfileEditor({ profileId, canonical, thunk, connected = true }) {
  const dispatch = useDispatch();
  const key = entityDraftKey('profile', profileId);
  const [editor, setEditor, controls] = useEntityDraft(key, {
    draft: null,
    baseline: null,
  });
  const draft = editor.draft;
  const save = useSnapshotSave({
    dispatch,
    thunk,
    buildArgs: ({ path, content }) => ({ path, content }),
    path: profileId,
    content: draft?.raw ?? '',
    savedContent: editor.baseline ?? '',
    saving: controls.saving,
    realtimeConnected: connected,
    beginSave: controls.beginSave,
    trackSave: controls.trackSave,
    failSave: controls.failSave,
    events: ['profiles.snapshot'],
  });
  const startEdit = () => {
    setEditor({
      draft: { raw: canonical },
      baseline: canonical,
    });
  };
  const updateDraft = (event) => {
    const raw = event.target.value;
    setEditor((current) => ({
      ...current,
      draft: { ...current.draft, raw },
    }));
  };

  return createElement('section', { 'data-profile': profileId },
    createElement('button', { type: 'button', onClick: startEdit }, 'Edit'),
    createElement('input', {
      'aria-label': `Profile draft ${profileId}`,
      value: draft?.raw ?? canonical,
      disabled: !draft || controls.saving,
      onChange: updateDraft,
    }),
    createElement('button', {
      type: 'button',
      disabled: !draft || controls.saving,
      onClick: () => { void save(); },
    }, 'Save'),
    createElement('output', { 'data-testid': `draft-${profileId}` }, controls.hasDraft ? 'draft' : 'clean'),
    createElement('output', { 'data-testid': `saving-${profileId}` }, controls.saving ? 'saving' : 'idle'),
    createElement('output', { 'data-testid': `error-${profileId}` }, controls.error || ''),
  );
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  store = createStore();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  delete globalThis.IS_REACT_ACT_ENVIRONMENT;
});

async function render(props) {
  await act(async () => root.render(createElement(
    Provider,
    { store },
    createElement(ProfileEditor, props),
  )));
}

async function click(label) {
  await act(async () => {
    [...container.querySelectorAll('button')].find((button) => button.textContent === label).click();
  });
}

async function typeDraft(profileId, value) {
  const input = container.querySelector(`[aria-label="Profile draft ${profileId}"]`);
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function entry(profileId) {
  return store.getState().editorDrafts.entries[entityDraftKey('profile', profileId)];
}

async function acknowledge(save) {
  await act(async () => {
    save.resolve({ accepted: true });
    await Promise.resolve();
  });
}

async function applySnapshot(requestId) {
  await act(async () => {
    store.dispatch(applyUiEvent({
      type: 'profiles.snapshot',
      payload: { request_id: requestId, profiles: [] },
    }));
  });
}

describe('snapshot save draft correlation', () => {
  it('keeps a 202-acknowledged edit pending until its canonical snapshot clears the submitted revision', async () => {
    const save = deferredSave();
    await render({ profileId: 'A', canonical: 'baseline A', thunk: save.thunk });
    await click('Edit');
    await typeDraft('A', 'submitted A');
    await click('Save');
    const requestId = entry('A').save.requestId;

    await acknowledge(save);
    expect(container.querySelector('[aria-label="Profile draft A"]').value).toBe('submitted A');
    expect(container.querySelector('[aria-label="Profile draft A"]').disabled).toBe(true);
    expect(container.querySelector('[data-testid="draft-A"]').textContent).toBe('draft');
    expect(container.querySelector('[data-testid="saving-A"]').textContent).toBe('saving');

    await render({ profileId: 'A', canonical: 'canonical A', thunk: save.thunk });
    await applySnapshot(requestId);
    expect(container.querySelector('[aria-label="Profile draft A"]').value).toBe('canonical A');
    expect(container.querySelector('[data-testid="draft-A"]').textContent).toBe('clean');
    expect(container.querySelector('[data-testid="saving-A"]').textContent).toBe('idle');
  });

  it('unlocks a failed save while retaining the exact raw edit', async () => {
    const save = deferredSave();
    await render({ profileId: 'A', canonical: 'baseline A', thunk: save.thunk });
    await click('Edit');
    await typeDraft('A', '  exact raw value  ');
    await click('Save');

    await act(async () => {
      save.reject(new Error('network unavailable'));
      await Promise.resolve();
    });

    expect(container.querySelector('[aria-label="Profile draft A"]').value).toBe('  exact raw value  ');
    expect(container.querySelector('[aria-label="Profile draft A"]').disabled).toBe(false);
    expect(container.querySelector('[data-testid="saving-A"]').textContent).toBe('idle');
    expect(container.querySelector('[data-testid="error-A"]').textContent).toContain('network unavailable');
  });

  it('leaves an offline save editable with its retained raw value', async () => {
    const save = deferredSave();
    await render({ profileId: 'A', canonical: 'baseline A', thunk: save.thunk, connected: false });
    await click('Edit');
    await typeDraft('A', 'offline raw');
    await click('Save');

    expect(container.querySelector('[aria-label="Profile draft A"]').value).toBe('offline raw');
    expect(container.querySelector('[aria-label="Profile draft A"]').disabled).toBe(false);
    expect(container.querySelector('[data-testid="saving-A"]').textContent).toBe('idle');
    expect(container.querySelector('[data-testid="error-A"]').textContent).not.toBe('');
  });

  it('settles an unmounted profile save without changing the active profile or a newer retained revision', async () => {
    const save = deferredSave();
    await render({ profileId: 'A', canonical: 'baseline A', thunk: save.thunk });
    await click('Edit');
    await typeDraft('A', 'submitted A');
    await click('Save');
    const requestId = entry('A').save.requestId;

    await render({ profileId: 'B', canonical: 'baseline B', thunk: save.thunk });
    await click('Edit');
    await typeDraft('B', 'draft B');
    await act(async () => {
      store.dispatch(draftChanged({
        key: entityDraftKey('profile', 'A'),
        value: {
          draft: { raw: 'newer A' },
          baseline: 'baseline A',
        },
      }));
    });

    await acknowledge(save);
    await applySnapshot(requestId);

    expect(container.querySelector('[aria-label="Profile draft B"]').value).toBe('draft B');
    expect(entry('B').value.draft.raw).toBe('draft B');
    expect(entry('A').value.draft.raw).toBe('newer A');
    expect(entry('A').save).toBeNull();
  });

  it('unlocks an acknowledged retained save when its connection is replaced', async () => {
    const save = deferredSave();
    await render({ profileId: 'A', canonical: 'baseline A', thunk: save.thunk });
    await click('Edit');
    await typeDraft('A', 'unsaved across reconnect');
    await click('Save');
    await acknowledge(save);
    await act(async () => {
      for (const requestId of Object.keys(store.getState().realtime.pendingOps)) {
        store.dispatch(failPendingOp({ requestId, error: 'Connection replaced' }));
      }
    });
    expect(container.querySelector('[aria-label="Profile draft A"]').value).toBe('unsaved across reconnect');
    expect(container.querySelector('[aria-label="Profile draft A"]').disabled).toBe(false);
    expect(container.querySelector('[data-testid="error-A"]').textContent).toContain('Connection replaced');
  });

  it('settles a canonical event that arrives before the HTTP acknowledgement', async () => {
    const save = deferredSave();
    await render({ profileId: 'A', canonical: 'baseline A', thunk: save.thunk });
    await click('Edit');
    await typeDraft('A', 'submitted A');
    await click('Save');
    const requestId = entry('A').save.requestId;

    await applySnapshot(requestId);
    expect(container.querySelector('[data-testid="draft-A"]').textContent).toBe('clean');

    await acknowledge(save);
    expect(container.querySelector('[data-testid="draft-A"]').textContent).toBe('clean');
  });
});
