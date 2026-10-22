// @vitest-environment jsdom
import React, { act, useEffect } from 'react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createRoot } from 'react-dom/client';
import realtimeReducer from '@frontend/realtime/realtimeSlice';
import uiDataReducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';
import editorDraftsReducer from '@frontend/store/editorDraftsSlice';
import useInlineTitleEditor from '@frontend/hooks/useInlineTitleEditor';

let host;
let root;
let store;
let current;

function Probe({ kind = 'task', scopeKey, title, onCommit, onEditor }) {
  const editor = useInlineTitleEditor({ kind, scopeKey, title, onCommit });
  useEffect(() => {
    onEditor(editor);
  }, [editor, onEditor]);
  return null;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.stubGlobal('React', React);
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  store = configureStore({
    reducer: {
      editorDrafts: editorDraftsReducer,
      realtime: realtimeReducer,
      uiData: uiDataReducer,
    },
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

const setCurrent = (editor) => {
  current = editor;
};

const render = ({ kind = 'task', scopeKey, title, onCommit }) => act(async () => {
  root.render(
    <Provider store={store}>
      <Probe kind={kind} scopeKey={scopeKey} title={title} onCommit={onCommit} onEditor={setCurrent} />
    </Provider>,
  );
});

it('retains an active raw title draft through selection changes and page remount', async () => {
  const onCommit = vi.fn();
  await render({ scopeKey: 'task-a', title: 'Task A', onCommit });

  await act(async () => {
    current.start();
    current.setDraft('  Task A, raw title  ');
  });
  await render({ scopeKey: 'task-b', title: 'Task B', onCommit });
  expect(current.isEditing).toBe(false);
  expect(current.draft).toBe('Task B');

  await render({ scopeKey: 'task-a', title: 'Task A', onCommit });
  expect(current.isEditing).toBe(true);
  expect(current.draft).toBe('  Task A, raw title  ');

  await act(async () => root.unmount());
  root = createRoot(host);
  await render({ scopeKey: 'task-a', title: 'Task A', onCommit });
  expect(current.isEditing).toBe(true);
  expect(current.draft).toBe('  Task A, raw title  ');
});

it('discards only on explicit cancel and otherwise follows a live canonical title', async () => {
  const onCommit = vi.fn();
  await render({ scopeKey: 'task-a', title: 'Task A', onCommit });

  await act(async () => {
    current.start();
    current.setDraft('Unsaved task title');
    current.cancel();
  });
  await render({ scopeKey: 'task-a', title: 'Canonical title', onCommit });

  expect(current.isEditing).toBe(false);
  expect(current.draft).toBe('Canonical title');
});

it('keeps a title save pending until its correlated canonical event arrives', async () => {
  const onCommit = vi.fn((_title, { ticket, trackSave }) => {
    trackSave(ticket, {
      requestId: 'task-title-request',
      events: ['kanban.snapshot'],
    });
    return Promise.resolve({ request_id: 'task-title-request' });
  });
  await render({ scopeKey: 'task-a', title: 'Task A', onCommit });

  await act(async () => {
    current.start();
    current.setDraft('Renamed task');
  });
  await act(async () => {
    await current.save();
  });
  expect(current.isEditing).toBe(true);
  expect(current.saving).toBe(true);
  expect(current.draft).toBe('Renamed task');

  await act(async () => store.dispatch(applyUiEvent({
    type: 'kanban.snapshot',
    payload: { request_id: 'task-title-request' },
  })));
  await render({ scopeKey: 'task-a', title: 'Renamed task', onCommit });

  expect(current.isEditing).toBe(false);
  expect(current.saving).toBe(false);
  expect(current.draft).toBe('Renamed task');
});

it('settles a failed save on its original task without surfacing it under a new selection', async () => {
  const pending = deferred();
  const onCommit = vi.fn(() => pending.promise);
  await render({ scopeKey: 'task-a', title: 'Task A', onCommit });

  await act(async () => {
    current.start();
    current.setDraft('Task A renamed');
  });
  let oldSave;
  await act(async () => {
    oldSave = current.save();
  });

  await render({ scopeKey: 'task-b', title: 'Task B', onCommit });
  await act(async () => {
    pending.reject(new Error('save failed'));
    await oldSave;
  });
  expect(current.isEditing).toBe(false);
  expect(current.error).toBeNull();

  await render({ scopeKey: 'task-a', title: 'Task A', onCommit });
  expect(current.isEditing).toBe(true);
  expect(current.saving).toBe(false);
  expect(current.draft).toBe('Task A renamed');
  expect(current.error).toBe('save failed');
});

it('does not let a late task confirmation clear a newer draft for that task', async () => {
  const onCommit = vi.fn((_title, { ticket, trackSave }) => {
    trackSave(ticket, {
      requestId: 'old-title-request',
      events: ['kanban.snapshot'],
    });
    return Promise.resolve({ request_id: 'old-title-request' });
  });
  await render({ scopeKey: 'task-a', title: 'Task A', onCommit });

  await act(async () => {
    current.start();
    current.setDraft('Old title');
  });
  await act(async () => {
    await current.save();
  });
  await act(async () => {
    current.cancel();
  });
  await act(async () => {
    current.start();
    current.setDraft('Newer raw title');
  });
  await act(async () => store.dispatch(applyUiEvent({
    type: 'kanban.snapshot',
    payload: { request_id: 'old-title-request' },
  })));

  expect(current.isEditing).toBe(true);
  expect(current.draft).toBe('Newer raw title');
  expect(current.saving).toBe(false);
});
