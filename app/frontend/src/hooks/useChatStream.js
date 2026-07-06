import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSelector } from 'react-redux';
import {
  clearChatErrored,
  clearChatGenerating,
  fetchChatSessions,
  markChatErrored,
  markChatGenerating,
  markChatUnread,
  selectErroredChatIds,
  selectGeneratingChatIds,
  selectUnreadChatIds,
  touchChatSession,
} from '../store/uiDataSlice';
import {
  appendReasoningText,
  appendStreamEvent,
  createStreamEvent,
  extractReasoningTokenCount,
  finalizeSupersededAssistant,
} from '../components/chat/streamEventsModel';
import { resolveBackgroundStreamOutcome, shouldPreserveStreamInBackground } from './chatStreamModel';

function resolveSelectedBackendChat(activeBackendChat, selectedBackendChatId) {
  if (!selectedBackendChatId) {
    return null;
  }
  if (activeBackendChat?.thread_id !== selectedBackendChatId) {
    return null;
  }
  return activeBackendChat;
}

// Owns the streaming-send lifecycle and the background generation/unread
// indicators. The `messages` state stays in the page (it is shared with the
// backend-load, scheduled-message, and archive flows); this hook receives it
// plus `setMessages` so the dependency arrays and stale-closure guards stay
// identical to the original inline implementation.
export function useChatStream({
  messages,
  setMessages,
  activeBackendChat,
  selectedBackendChatId,
  threadId,
  sendMessageStream,
  cancelStream,
  dispatch,
  getApiUrl,
  show,
  selectedBackendChatIdRef,
  reloadOpenChat,
}) {
  const [isGenerating, setIsGenerating] = useState(false);
  const unreadChatIdList = useSelector(selectUnreadChatIds);
  const generatingChatIdList = useSelector(selectGeneratingChatIds);
  const erroredChatIdList = useSelector(selectErroredChatIds);
  const unreadChatIds = useMemo(() => new Set(unreadChatIdList), [unreadChatIdList]);
  const backgroundGeneratingChatIds = useMemo(() => new Set(generatingChatIdList), [generatingChatIdList]);
  const backgroundErrorChatIds = useMemo(() => new Set(erroredChatIdList), [erroredChatIdList]);
  const activeAssistantIdRef = useRef(null);
  const backgroundOriginChatIdRef = useRef(null);

  // Marks the chat currently streaming (if any) as generating-in-background.
  // Called whenever the caller is about to stop treating that stream as the
  // foreground one — switching chats, starting a new chat, or unmounting.
  const preserveStreamInBackground = useCallback(() => {
    if (shouldPreserveStreamInBackground({
      streamingOriginId: backgroundOriginChatIdRef.current,
      activeAssistantId: activeAssistantIdRef.current,
    })) {
      dispatch(markChatGenerating(backgroundOriginChatIdRef.current));
    }
  }, [dispatch]);

  // If the page unmounts (navigating to a different nav item) while a stream
  // is in flight, treat it exactly like switching chats: preserve it as a
  // background stream instead of leaving it orphaned. Without this, nothing
  // ever marks activeAssistantIdRef stale, so the stream's completion takes
  // the foreground branch on a dead closure — no unread marker, no reload,
  // and the reply silently goes nowhere once it finishes.
  useEffect(() => {
    return () => {
      preserveStreamInBackground();
      activeAssistantIdRef.current = null;
    };
  }, [preserveStreamInBackground]);

  // Dispatches the redux side effects for a resolveBackgroundStreamOutcome()
  // result whose action isn't 'foreground' (that case is handled inline by
  // the caller, since it also needs to touch the local `messages` state).
  const applyBackgroundStreamOutcome = useCallback((outcome) => {
    if (outcome.action === 'clearGenerating') {
      dispatch(clearChatGenerating(outcome.threadId));
      return;
    }
    if (outcome.action === 'reload') {
      dispatch(clearChatGenerating(outcome.threadId));
      // The user navigated away and back to this chat while it was
      // streaming. Reload its detail so the finished reply shows, and do NOT
      // mark the chat the user is actively viewing as unread.
      reloadOpenChat?.(outcome.threadId);
      return;
    }
    if (outcome.action === 'markUnread') {
      dispatch(clearChatGenerating(outcome.threadId));
      dispatch(fetchChatSessions({ chatsUrl: getApiUrl('chats'), includeArchived: true }));
      dispatch(markChatUnread(outcome.threadId));
      dispatch(outcome.errored ? markChatErrored(outcome.threadId) : clearChatErrored(outcome.threadId));
    }
  }, [dispatch, getApiUrl, reloadOpenChat]);

  const handleSend = useCallback(async (content) => {
    const userMessage = {
      id: `msg_${Date.now()}`,
      role: 'user',
      content,
      timestamp: Date.now()
    };

    setMessages(prev => [...prev, userMessage]);
    setIsGenerating(true);

    let assistantContent = '';
    const assistantId = `msg_${Date.now()}_assistant`;
    const previousAssistantId = activeAssistantIdRef.current;
    activeAssistantIdRef.current = assistantId;

    // Add placeholder assistant message; clear isStreaming on any prior
    // placeholder that is being superseded so the spinner doesn't stick.
    setMessages(prev => {
      const next = previousAssistantId
        ? prev
          .map((m) => (
            m.id === previousAssistantId
              ? finalizeSupersededAssistant(m)
              : m
          ))
          .filter(Boolean)
        : prev;
      return [...next, {
        id: assistantId,
        role: 'assistant',
        content: '',
        timestamp: Date.now(),
        tokenCount: 0,
        reasoningTokenCount: 0,
        reasoningText: '',
        isStreaming: true,
        streamEvents: [],
      }];
    });

    const history = messages.slice(-10).map(m => ({
      role: m.role,
      content: m.content
    }));

    // When continuing a backend chat, route to its original LangGraph thread so
    // the full conversation history is preserved rather than starting a new thread.
    const selectedBackendChat = resolveSelectedBackendChat(
      activeBackendChat,
      selectedBackendChatId,
    );
    const threadMetadata = selectedBackendChat?.thread_id
      ? { thread_id: selectedBackendChat.thread_id }
      : null;

    // The chat this stream belongs to, captured as a closure-local so
    // background completion can update the right sidebar card later.
    const originChatId = selectedBackendChat?.thread_id ?? null;
    // Mirror the foreground stream's origin into the ref for selectSessionItem
    // (deciding which card shows the orange "generating in background" dot).
    backgroundOriginChatIdRef.current = originChatId;
    // Bump this chat's activity now so "Recent" sorts it to the top right
    // away, instead of waiting for the stream to finish and a fresh chat list
    // snapshot to arrive.
    if (originChatId) {
      dispatch(touchChatSession(originChatId));
    }

    // Identifies this stream's chat so a later send only aborts it when it
    // targets the same chat; sending in a different chat leaves this running.
    const streamKey = originChatId ?? threadId ?? null;

    try {
      const isStale = () => activeAssistantIdRef.current !== assistantId;
      await sendMessageStream(
        content,
        history,
        // onChunk
        (chunk) => {
          if (isStale()) return;
          assistantContent += chunk;
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? { ...m, content: assistantContent, tokenCount: (m.tokenCount || 0) + 1 }
              : m
          ));
        },
        // onComplete
        (status) => {
          const outcome = resolveBackgroundStreamOutcome({
            isStale: isStale(),
            originChatId,
            isViewingOrigin: selectedBackendChatIdRef?.current === originChatId,
            resultType: status === 'cancelled' ? 'cancelled' : 'completed',
          });
          if (outcome.action !== 'foreground') {
            applyBackgroundStreamOutcome(outcome);
            return;
          }
          // Foreground stream finished — clear the in-flight origin marker.
          backgroundOriginChatIdRef.current = null;
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? { ...m, isStreaming: false, content: assistantContent + (status === 'cancelled' ? ' [Cancelled]' : '') }
              : m
          ));
          dispatch(fetchChatSessions({
            chatsUrl: getApiUrl('chats'),
            includeArchived: true,
          }));
          setIsGenerating(false);
        },
        // onError
        (error) => {
          const outcome = resolveBackgroundStreamOutcome({
            isStale: isStale(),
            originChatId,
            isViewingOrigin: selectedBackendChatIdRef?.current === originChatId,
            resultType: 'error',
          });
          if (outcome.action !== 'foreground') {
            applyBackgroundStreamOutcome(outcome);
            return;
          }
          show('error', 'Chat Error', error.message);
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? { ...m, isStreaming: false, error: true, content: `Error: ${error.message}` }
              : m
          ));
          setIsGenerating(false);
        },
        // onStreamEvent
        (streamEvent) => {
          // Discard frames that arrive after this request was superseded
          // (cancel/retry) so they cannot attach to a newer message.
          if (isStale()) {
            return;
          }
          const nextEvent = createStreamEvent(streamEvent);
          if (!nextEvent) {
            return;
          }
          const reasoningTokenCount = extractReasoningTokenCount(nextEvent);
          const modelName = nextEvent.eventType === 'node_update'
            && typeof nextEvent.metadata?.model_name === 'string'
            ? nextEvent.metadata.model_name
            : null;
          setMessages((prev) => prev.map(m =>
            m.id === assistantId
              ? {
                ...m,
                streamEvents: appendStreamEvent(m.streamEvents, nextEvent),
                reasoningText: appendReasoningText(m.reasoningText, nextEvent),
                reasoningTokenCount: reasoningTokenCount ?? m.reasoningTokenCount ?? 0,
                ...(modelName && !m.model ? { model: modelName } : {}),
              }
              : m
          ));
        },
        threadMetadata,
        streamKey,
      );
    } catch (error) {
      show('error', 'Chat Error', error.message);
      setIsGenerating(false);
    }
  }, [
    activeBackendChat,
    applyBackgroundStreamOutcome,
    dispatch,
    getApiUrl,
    messages,
    selectedBackendChatId,
    selectedBackendChatIdRef,
    sendMessageStream,
    threadId,
    setMessages,
    show,
  ]);

  const handleStop = useCallback(() => {
    cancelStream();
  }, [cancelStream]);

  return {
    isGenerating,
    setIsGenerating,
    unreadChatIds,
    backgroundGeneratingChatIds,
    backgroundErrorChatIds,
    activeAssistantIdRef,
    backgroundOriginChatIdRef,
    preserveStreamInBackground,
    handleSend,
    handleStop,
  };
}
