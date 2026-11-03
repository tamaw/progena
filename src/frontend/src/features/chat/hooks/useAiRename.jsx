import { useCallback, useRef, useState } from 'react';

// Sent as an ordinary user message so the assistant renames the session with
// its existing session-title capability; there is no separate rename API.
export const AI_RENAME_PROMPT = "Give this conversation a short, descriptive title based on what we've discussed.";

/**
 * Why "AI Rename" cannot send right now, or null when it can. Mirrors the
 * composer's own restrictions and requires a persisted, selected conversation.
 */
export function aiRenameBlockedReason({
  threadId,
  isGenerating,
  isClearingSession,
  connectionState,
  isContentLoading,
  isEmptyConversationPane,
}) {
  if (!threadId || isContentLoading || isEmptyConversationPane) return 'no-conversation';
  if (isGenerating) return 'generating';
  if (isClearingSession) return 'clearing';
  if (connectionState === 'error') return 'disconnected';
  return null;
}

/**
 * Sends the rename request through the page's normal send pipeline for the
 * selected thread. A synchronous guard drops repeat clicks before React
 * re-renders with the generating state, and the request is skipped if the
 * selection changed since the action was rendered.
 */
export default function useAiRename({ threadId, blockedReason, send, getSelectedThreadId }) {
  const pendingRef = useRef(false);
  const [pending, setPending] = useState(false);

  const request = useCallback(() => {
    if (pendingRef.current || blockedReason || !threadId) return false;
    if (getSelectedThreadId && getSelectedThreadId() !== threadId) return false;
    pendingRef.current = true;
    setPending(true);
    const settle = () => {
      pendingRef.current = false;
      setPending(false);
    };
    let result;
    try {
      result = send(AI_RENAME_PROMPT);
    } catch (error) {
      settle();
      throw error;
    }
    Promise.resolve(result).then(settle, settle);
    return true;
  }, [blockedReason, getSelectedThreadId, send, threadId]);

  return { request, pending };
}

/** Chat header action; DetailHeaderActions renders it inline and in the overflow menu. */
export function createAiRenameAction({ pending, blockedReason, onRequest }) {
  return {
    key: 'ai-rename',
    label: 'AI Rename',
    role: 'menuitem',
    disabled: Boolean(pending || blockedReason),
    onClick: onRequest,
    icon: (
      <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
        <path d="M6.5 2.5 7.6 5.4l2.9 1.1-2.9 1.1-1.1 2.9-1.1-2.9-2.9-1.1 2.9-1.1z" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
        <path d="M11.5 9.5l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6z" fill="none" stroke="currentColor" strokeWidth="1.1" strokeLinejoin="round" />
      </svg>
    ),
  };
}
