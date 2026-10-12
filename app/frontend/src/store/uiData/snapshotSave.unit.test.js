// @vitest-environment jsdom
import { createElement, act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SNAPSHOT_EVENT_TIMEOUT_MS, useSnapshotSave } from './snapshotSave';

let root;
let container;
const items = [{ path: 'profile', content: 'canonical' }];
const identity = (value) => value;
const defaultDispatch = () => ({ unwrap: async () => ({}) });

function Editor({ resolvedContent, resolveContent = false, pending, snapshotRequestId, dispatch = defaultDispatch }) {
  const [content, setContent] = useState('draft');
  const [saving, setSaving] = useState(Boolean(pending));
  const [pendingSave, setPendingSave] = useState(pending || null);
  const [status, setStatus] = useState('');
  const [error, setError] = useState(null);
  const save = useSnapshotSave({
    items, snapshotRequestId, pendingSave, dispatch, thunk: identity, buildArgs: identity,
    path: 'profile', content, savedContent: 'original', saving, realtimeConnected: true,
    setDraftContent: setContent, setSaving, setPendingSave,
    setStatusMessage: setStatus, setError,
    resolveSavedContent: resolveContent ? () => resolvedContent : undefined,
  });
  return createElement('div', null,
    createElement('input', { value: content, readOnly: true }),
    createElement('button', { disabled: saving, onClick: () => { void save(); } }, 'Save'),
    createElement('output', { role: 'status' }, status),
    error ? createElement('div', { role: 'alert' }, error.message) : null,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  delete globalThis.IS_REACT_ACT_ENVIRONMENT;
});

const render = async (props) => { await act(async () => root.render(createElement(Editor, props))); };
const completed = { requestId: 'save-1', path: 'profile', acknowledged: true };

describe('snapshot save observable state', () => {
  it('keeps success when the causal snapshot is already present as the acknowledgement arrives', async () => {
    await render({ pending: completed, snapshotRequestId: 'save-1' });
    expect(container.querySelector('[role="status"]').textContent).toBe('Saved profile.');
    expect(container.querySelector('input').value).toBe('canonical');
    expect(container.querySelector('button').disabled).toBe(false);
    await act(async () => vi.advanceTimersByTime(SNAPSHOT_EVENT_TIMEOUT_MS + 1));
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(container.querySelector('[role="status"]').textContent).toBe('Saved profile.');
  });

  it('waits for canonical content without discarding a draft, then cancels the timeout on completion', async () => {
    await render({ pending: completed, snapshotRequestId: 'save-1', resolveContent: true });
    expect(container.querySelector('input').value).toBe('draft');
    expect(container.querySelector('button').disabled).toBe(true);
    await render({ pending: completed, snapshotRequestId: 'save-1', resolveContent: true, resolvedContent: 'fresh' });
    expect(container.querySelector('input').value).toBe('fresh');
    expect(container.querySelector('button').disabled).toBe(false);
    await act(async () => vi.advanceTimersByTime(SNAPSHOT_EVENT_TIMEOUT_MS + 1));
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it('shows request failure and preserves an editable draft', async () => {
    const dispatch = () => ({ unwrap: async () => { throw new Error('network unavailable'); } });
    await render({ dispatch });
    await act(async () => container.querySelector('button').click());
    expect(container.querySelector('[role="alert"]').textContent).toBe('network unavailable');
    expect(container.querySelector('input').value).toBe('draft');
    expect(container.querySelector('button').disabled).toBe(false);
  });
});
