import { describe, expect, it } from 'vitest';
import { resolveBackgroundStreamOutcome, shouldPreserveStreamInBackground } from './chatStreamModel';

describe('shouldPreserveStreamInBackground', () => {
  it('preserves when a stream is active for a known origin chat', () => {
    expect(shouldPreserveStreamInBackground({
      streamingOriginId: 'thread_a',
      activeAssistantId: 'msg_1_assistant',
    })).toBe(true);
  });

  it('does not preserve when there is no origin chat (e.g. a never-materialized draft)', () => {
    expect(shouldPreserveStreamInBackground({
      streamingOriginId: null,
      activeAssistantId: 'msg_1_assistant',
    })).toBe(false);
  });

  it('does not preserve when nothing is actively streaming', () => {
    expect(shouldPreserveStreamInBackground({
      streamingOriginId: 'thread_a',
      activeAssistantId: null,
    })).toBe(false);
  });
});

describe('resolveBackgroundStreamOutcome', () => {
  it('stays foreground when the stream is not stale', () => {
    expect(resolveBackgroundStreamOutcome({
      isStale: false,
      originChatId: 'thread_a',
      isViewingOrigin: true,
      resultType: 'completed',
    })).toEqual({ action: 'foreground' });
  });

  it('stays foreground when there is no origin chat, even if stale', () => {
    // Regression: a message sent in a chat that was never materialized into a
    // backend card has no thread_id to track, so it must fall back to the
    // foreground path rather than silently vanishing into a no-op.
    expect(resolveBackgroundStreamOutcome({
      isStale: true,
      originChatId: null,
      isViewingOrigin: false,
      resultType: 'completed',
    })).toEqual({ action: 'foreground' });
  });

  it('only clears the generating dot for a cancelled background stream', () => {
    expect(resolveBackgroundStreamOutcome({
      isStale: true,
      originChatId: 'thread_a',
      isViewingOrigin: false,
      resultType: 'cancelled',
    })).toEqual({ action: 'clearGenerating', threadId: 'thread_a' });
  });

  it('reloads instead of marking unread when the user is back on the origin chat', () => {
    // Regression: navigating away mid-stream (chat switch, new chat, or a
    // full page unmount via another nav item) and then returning to the same
    // chat must live-refresh it, not silently do nothing.
    expect(resolveBackgroundStreamOutcome({
      isStale: true,
      originChatId: 'thread_a',
      isViewingOrigin: true,
      resultType: 'completed',
    })).toEqual({ action: 'reload', threadId: 'thread_a' });
  });

  it('marks unread (not errored) when a completed stream finishes elsewhere', () => {
    expect(resolveBackgroundStreamOutcome({
      isStale: true,
      originChatId: 'thread_a',
      isViewingOrigin: false,
      resultType: 'completed',
    })).toEqual({ action: 'markUnread', threadId: 'thread_a', errored: false });
  });

  it('marks unread and errored when an errored stream finishes elsewhere', () => {
    expect(resolveBackgroundStreamOutcome({
      isStale: true,
      originChatId: 'thread_a',
      isViewingOrigin: false,
      resultType: 'error',
    })).toEqual({ action: 'markUnread', threadId: 'thread_a', errored: true });
  });
});
