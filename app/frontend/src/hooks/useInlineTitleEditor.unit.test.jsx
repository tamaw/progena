// @vitest-environment jsdom
import React, { act, useEffect } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createRoot } from 'react-dom/client';
import useInlineTitleEditor from './useInlineTitleEditor';

let host;
let root;
let current;

function Probe({ scopeKey, title, onCommit, onEditor }) {
  const editor = useInlineTitleEditor({ scopeKey, title, onCommit });
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
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
const setCurrent = (editor) => {
  current = editor;
};

const render = (scopeKey, title, onCommit) => act(async () => {
  root.render(<Probe scopeKey={scopeKey} title={title} onCommit={onCommit} onEditor={setCurrent} />);
});

it('blocks same-tick duplicate title saves and retains the rejected draft', async () => {
  const pending = deferred();
  const onCommit = vi.fn(() => pending.promise);
  await render('schedule-a', 'Original title', onCommit);

  await act(async () => {
    current.start();
  });
  await act(async () => {
    current.setDraft('  Updated   title  ');
  });

  let firstSave;
  let duplicateResult;
  await act(async () => {
    firstSave = current.save();
    duplicateResult = await current.save();
  });
  expect(onCommit).toHaveBeenCalledTimes(1);
  expect(duplicateResult).toEqual({ ok: false, busy: true });

  await act(async () => {
    pending.reject(new Error('save failed'));
    await firstSave;
  });
  expect(current.isEditing).toBe(true);
  expect(current.draft).toBe('  Updated   title  ');
  expect(current.error?.message).toBe('save failed');
});

it('does not let an old title save alter the newly selected entity', async () => {
  const pending = deferred();
  const onCommit = vi.fn(() => pending.promise);
  await render('task-a', 'Task A', onCommit);

  await act(async () => {
    current.start();
  });
  await act(async () => {
    current.setDraft('Task A renamed');
  });
  let oldSave;
  await act(async () => {
    oldSave = current.save();
  });

  await render('task-b', 'Task B', onCommit);
  await act(async () => {
    pending.resolve({ ok: true });
    await oldSave;
  });

  expect(current.isEditing).toBe(false);
  expect(current.draft).toBe('');
  expect(current.error).toBeNull();
});
