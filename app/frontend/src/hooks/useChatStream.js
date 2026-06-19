import { useCallback, useRef, useState } from 'react';
import { fetchChatSessions } from '../store/uiDataSlice';
import {
  appendReasoningText,
  appendStreamEvent,
  createStreamEvent,
  extractReasoningTokenCount,
  finalizeSupersededAssistant,
} from '../components/chat/streamEventsModel';

function resolveSelectedBackendChat(activeBackendChat, selectedBackendChatId) {
  if (!selectedBackendChatId) {
    return null;
  }
  if (activeBackendChat?.chat_session_id !== selectedBackendChatId) {
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
  sessionId,
  sendMessageStream,
  cancelStream,
  dispatch,
  getApiUrl,
  show,
  selectedBackendChatIdRef,
  reloadOpenChat,
}) {
  const [isGenerating, setIsGenerating] = useState(false);
  const [unreadChatIds, setUnreadChatIds] = useState(() => new Set());
  // Set of chat ids with a stream still running in the background — each shows
  // its own orange "generating" dot, so several at once are all indicated.
  const [backgroundGeneratingChatIds, setBackgroundGeneratingChatIds] = useState(() => new Set());
  // Background chats whose last turn ended in an error — their unread dot is
  // shown red instead of green.
  const [backgroundErrorChatIds, setBackgroundErrorChatIds] = useState(() => new Set());
  const activeAssistantIdRef = useRef(null);
  const backgroundOriginChatIdRef = useRef(null);

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
    const originChatId = selectedBackendChat?.chat_session_id ?? null;
    // Mirror the foreground stream's origin into the ref for selectSessionItem
    // (deciding which card shows the orange "generating in background" dot).
    backgroundOriginChatIdRef.current = originChatId;

    // Identifies this stream's chat so a later send only aborts it when it
    // targets the same chat; sending in a different chat leaves this running.
    const streamKey = originChatId ?? sessionId ?? null;

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
          if (isStale()) {
            // A background stream finished. Clear just this chat's orange dot,
            // leaving any other still-generating background chats indicated.
            setBackgroundGeneratingChatIds((prev) => {
              if (!prev.has(originChatId)) return prev;
              const next = new Set(prev);
              next.delete(originChatId);
              return next;
            });
            if (status !== 'cancelled' && originChatId) {
              if (selectedBackendChatIdRef?.current === originChatId) {
                // The user navigated away and back to this chat while it was
                // streaming. Reload its detail so the finished reply shows, and
                // do NOT mark the chat the user is actively viewing as unread.
                reloadOpenChat?.(originChatId);
              } else {
                // Finished OK in the background while the user is on another
                // chat: green unread dot, clearing any prior error flag.
                dispatch(fetchChatSessions({ chatsUrl: getApiUrl('chats'), includeArchived: true }));
                setUnreadChatIds((prev) => new Set([...prev, originChatId]));
                setBackgroundErrorChatIds((prev) => {
                  if (!prev.has(originChatId)) return prev;
                  const next = new Set(prev);
                  next.delete(originChatId);
                  return next;
                });
              }
            }
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
          if (isStale()) {
            // A background stream errored. Clear its orange dot; if the user is
            // viewing it, reload; otherwise flag it unread + errored so its card
            // shows a red dot in place of the green "done" dot.
            setBackgroundGeneratingChatIds((prev) => {
              if (!prev.has(originChatId)) return prev;
              const next = new Set(prev);
              next.delete(originChatId);
              return next;
            });
            if (originChatId) {
              if (selectedBackendChatIdRef?.current === originChatId) {
                reloadOpenChat?.(originChatId);
              } else {
                dispatch(fetchChatSessions({ chatsUrl: getApiUrl('chats'), includeArchived: true }));
                setUnreadChatIds((prev) => new Set([...prev, originChatId]));
                setBackgroundErrorChatIds((prev) => new Set([...prev, originChatId]));
              }
            }
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
    dispatch,
    getApiUrl,
    messages,
    reloadOpenChat,
    selectedBackendChatId,
    selectedBackendChatIdRef,
    sendMessageStream,
    sessionId,
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
    setUnreadChatIds,
    backgroundGeneratingChatIds,
    setBackgroundGeneratingChatIds,
    backgroundErrorChatIds,
    setBackgroundErrorChatIds,
    activeAssistantIdRef,
    backgroundOriginChatIdRef,
    handleSend,
    handleStop,
  };
}
