// @vitest-environment jsdom
import { act, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import useAiRename, {
  AI_RENAME_PROMPT,
  aiRenameBlockedReason,
  createAiRenameAction,
} from '@frontend/features/chat/hooks/useAiRename';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const seenRef = { current: null };
let root;

function Probe(props) {
  const value = useAiRename(props);
  useEffect(() => {
    seenRef.current = value;
  });
  return null;
}

const render = (props) => act(() => root.render(<Probe {...props} />));

beforeEach(() => {
  root = createRoot(document.createElement('div'));
});
afterEach(() => act(() => root.unmount()));

const ready = {
  threadId: 'thread-a',
  isGenerating: false,
  isClearingSession: false,
  connectionState: 'connected',
  isContentLoading: false,
  isEmptyConversationPane: false,
};

describe('aiRenameBlockedReason', () => {
  it('allows a persisted, selected, idle conversation', () => {
    expect(aiRenameBlockedReason(ready)).toBeNull();
  });

  it('blocks missing selection, loading, active runs and normal send restrictions', () => {
    expect(aiRenameBlockedReason({ ...ready, threadId: null })).toBe('no-conversation');
    expect(aiRenameBlockedReason({ ...ready, isContentLoading: true })).toBe('no-conversation');
    expect(aiRenameBlockedReason({ ...ready, isEmptyConversationPane: true })).toBe('no-conversation');
    expect(aiRenameBlockedReason({ ...ready, isGenerating: true })).toBe('generating');
    expect(aiRenameBlockedReason({ ...ready, isClearingSession: true })).toBe('clearing');
    expect(aiRenameBlockedReason({ ...ready, connectionState: 'error' })).toBe('disconnected');
  });
});

describe('useAiRename', () => {
  it('sends exactly the rename request as a normal message for the selected thread', () => {
    const send = vi.fn(() => new Promise(() => {}));
    render({ threadId: 'thread-a', blockedReason: null, send, getSelectedThreadId: () => 'thread-a' });
    let sent;
    act(() => { sent = seenRef.current.request(); });
    expect(sent).toBe(true);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith("Give this conversation a short, descriptive title based on what we've discussed.");
    expect(AI_RENAME_PROMPT).toBe("Give this conversation a short, descriptive title based on what we've discussed.");
    expect(seenRef.current.pending).toBe(true);
  });

  it('drops repeat clicks until the send settles, then allows another request', async () => {
    let finish;
    const send = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
    render({ threadId: 'thread-a', blockedReason: null, send });
    const { request } = seenRef.current;
    act(() => { request(); request(); });
    act(() => { seenRef.current.request(); });
    expect(send).toHaveBeenCalledTimes(1);

    await act(async () => { finish(); });
    expect(seenRef.current.pending).toBe(false);
    act(() => { seenRef.current.request(); });
    expect(send).toHaveBeenCalledTimes(2);
  });

  it('releases the guard when the send fails', async () => {
    const send = vi.fn().mockRejectedValueOnce(new Error('offline')).mockReturnValue(new Promise(() => {}));
    render({ threadId: 'thread-a', blockedReason: null, send });
    await act(async () => { seenRef.current.request(); });
    expect(seenRef.current.pending).toBe(false);
    act(() => { seenRef.current.request(); });
    expect(send).toHaveBeenCalledTimes(2);
  });

  it('does not send while blocked, without a thread, or after the selection moved', () => {
    const send = vi.fn();
    render({ threadId: 'thread-a', blockedReason: 'generating', send });
    act(() => { seenRef.current.request(); });
    render({ threadId: null, blockedReason: null, send });
    act(() => { seenRef.current.request(); });
    render({ threadId: 'thread-a', blockedReason: null, send, getSelectedThreadId: () => 'thread-b' });
    act(() => { seenRef.current.request(); });
    expect(send).not.toHaveBeenCalled();
  });
});

describe('createAiRenameAction', () => {
  it('describes one header action that is disabled while pending or blocked', () => {
    const onRequest = vi.fn();
    const action = createAiRenameAction({ pending: false, blockedReason: null, onRequest });
    expect(action).toMatchObject({ key: 'ai-rename', label: 'AI Rename', role: 'menuitem', disabled: false, onClick: onRequest });
    expect(action.icon.props['aria-hidden']).toBe('true');
    expect(createAiRenameAction({ pending: true, blockedReason: null, onRequest }).disabled).toBe(true);
    expect(createAiRenameAction({ pending: false, blockedReason: 'no-conversation', onRequest }).disabled).toBe(true);
  });
});
