/**
 * Pure decision logic for useChatStream's background-stream bookkeeping.
 *
 * Extracted so the branching that decides whether a chat's stream should be
 * treated as foreground vs. background (and what to do when it finishes) can
 * be unit tested without rendering the hook — this is exactly the logic that
 * caused messages to go missing when the user navigated away mid-stream.
 */

// Whether leaving the chat that owns `activeStreamId` (switching chats,
// starting a new chat, or unmounting the page entirely) should preserve its
// stream as a background one, rather than silently cancelling/orphaning it.
export function shouldPreserveStreamInBackground({ streamingOriginId, activeStreamId }) {
  return Boolean(streamingOriginId) && activeStreamId !== null;
}

// Decide what a completed/cancelled/errored stream should do once its origin
// chat may no longer be the one in the foreground.
//
// - `isStale`: true once the caller has navigated away from the chat that
//   owns this stream (a newer send, a chat switch, or an unmount).
// - `originChatId`: the backend thread_id the stream belongs to, or null for
//   a chat that was never materialized into a backend chat card.
// - `isViewingOrigin`: true if the user is (still/again) looking at that
//   exact chat right now.
// - `resultType`: 'completed' | 'cancelled' | 'error'.
export function resolveBackgroundStreamOutcome({ isStale, originChatId, isViewingOrigin, resultType }) {
  if (!isStale || !originChatId) {
    return { action: 'foreground' };
  }
  if (resultType === 'cancelled') {
    return { action: 'clearGenerating', threadId: originChatId };
  }
  if (isViewingOrigin) {
    return { action: 'reload', threadId: originChatId };
  }
  return { action: 'markUnread', threadId: originChatId, errored: resultType === 'error' };
}
