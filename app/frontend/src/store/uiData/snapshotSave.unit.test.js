import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const reactHarness = vi.hoisted(() => {
  let effects = [];
  return {
    beginRender() {
      effects = [];
    },
    flushEffects() {
      effects.forEach((effect) => effect());
    },
    useEffect(effect) {
      effects.push(effect);
    },
    useCallback(fn) {
      return fn;
    },
  };
});

vi.mock('react', () => ({
  useEffect: reactHarness.useEffect,
  useCallback: reactHarness.useCallback,
}));

import { useSnapshotSave } from './snapshotSave';

function useHookUnderTest(args) {
  reactHarness.beginRender();
  const save = useSnapshotSave(args);
  reactHarness.flushEffects();
  return save;
}

function acknowledgedSaveArgs(overrides) {
  return {
    items: [{ path: 'a.md', content: 'list-content' }],
    snapshotRequestId: 'REQ1',
    pendingSave: { requestId: 'REQ1', path: 'a.md', acknowledged: true },
    dispatch: vi.fn(),
    thunk: vi.fn(),
    buildArgs: (value) => value,
    path: 'a.md',
    content: 'edited',
    savedContent: 'old',
    saving: true,
    realtimeConnected: true,
    setDraftContent: vi.fn(),
    setSaving: vi.fn(),
    setPendingSave: vi.fn(),
    setStatusMessage: vi.fn(),
    setError: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe('useSnapshotSave resolveSavedContent', () => {
  it('does not clear pendingSave while the content cache has not caught up yet', () => {
    const args = acknowledgedSaveArgs({ resolveSavedContent: () => undefined });
    useHookUnderTest(args);

    expect(args.setDraftContent).not.toHaveBeenCalled();
    expect(args.setSaving).not.toHaveBeenCalled();
    expect(args.setPendingSave).not.toHaveBeenCalled();
  });

  it('applies the resolved content and clears pendingSave once the cache catches up', () => {
    const args = acknowledgedSaveArgs({ resolveSavedContent: () => 'fresh-content' });
    useHookUnderTest(args);

    expect(args.setDraftContent).toHaveBeenCalledWith('fresh-content');
    expect(args.setSaving).toHaveBeenCalledWith(false);
    expect(args.setPendingSave).toHaveBeenCalledWith(null);
  });

  it('falls back to the list item content when resolveSavedContent is not provided', () => {
    const args = acknowledgedSaveArgs();
    useHookUnderTest(args);

    expect(args.setDraftContent).toHaveBeenCalledWith('list-content');
    expect(args.setPendingSave).toHaveBeenCalledWith(null);
  });
});
