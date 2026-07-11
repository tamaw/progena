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
  createLiveAssistantTurn,
  applyAssistantMetadata,
  applyAssistantStreamEvent,
  applyAssistantChunk,
  finalizeLiveAssistantTurn,
} from '../components/chat/liveAssistantTurnModel';
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

function extractDirectAgentName(content) {
  if (typeof content !== 'string') {
    return '';
  }
  const match = content.trim().match(/^@([A-Za-z0-9_][A-Za-z0-9_.-]*)\s+/);
  return match ? match[1] : '';
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
  const [liveAssistantTurn, setLiveAssistantTurn] = useState(null);
  const unreadChatIdList = useSelector(selectUnreadChatIds);
  const generatingChatIdList = useSelector(selectGeneratingChatIds);
  const erroredChatIdList = useSelector(selectErroredChatIds);
  const unreadChatIds = useMemo(() => new Set(unreadChatIdList), [unreadChatIdList]);
  const backgroundGeneratingChatIds = useMemo(() => new Set(generatingChatIdList), [generatingChatIdList]);
  const backgroundErrorChatIds = useMemo(() => new Set(erroredChatIdList), [erroredChatIdList]);
  const activeStreamIdRef = useRef(null);
  const backgroundOriginChatIdRef = useRef(null);
  const liveTurnRef = useRef(null);

  // Keep the ref in sync with state so callbacks (onComplete, onError)
  // created inside handleSend can read the latest turn without a stale
  // closure — the ref is never passed as a useCallback dependency.
  useEffect(() => {
    liveTurnRef.current = liveAssistantTurn;
  }, [liveAssistantTurn]);

  // Marks the chat currently streaming (if any) as generating-in-background.
  // Called whenever the caller is about to stop treating that stream as the
  // foreground one — switching chats, starting a new chat, or unmounting.
  const preserveStreamInBackground = useCallback(() => {
    if (shouldPreserveStreamInBackground({
      streamingOriginId: backgroundOriginChatIdRef.current,
      activeStreamId: activeStreamIdRef.current,
    })) {
      dispatch(markChatGenerating(backgroundOriginChatIdRef.current));
    }
  }, [dispatch]);

  // If the page unmounts (navigating to a different nav item) while a stream
  // is in flight, treat it exactly like switching chats: preserve it as a
  // background stream instead of leaving it orphaned. Without this, nothing
  // ever marks activeStreamIdRef stale, so the stream's completion takes
  // the foreground branch on a dead closure — no unread marker, no reload,
  // and the reply silently goes nowhere once it finishes.
  useEffect(() => {
    return () => {
      preserveStreamInBackground();
      activeStreamIdRef.current = null;
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

    const streamId = `stream_${Date.now()}`;
    const previousStreamId = activeStreamIdRef.current;
    activeStreamIdRef.current = streamId;

    // Start the live turn — no assistant message created yet.
    const initialTurn = createLiveAssistantTurn({ streamId, startedAt: Date.now() });
    const directAgentName = extractDirectAgentName(content);
    const initialTurnWithMetadata = directAgentName
      ? applyAssistantMetadata(initialTurn, { display_agent_name: directAgentName })
      : initialTurn;
    setLiveAssistantTurn(initialTurnWithMetadata);
    liveTurnRef.current = initialTurnWithMetadata;

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
    // background completion can update the right sidebar card later. Falls
    // back to the local draft threadId when this chat hasn't been sent to
    // the backend yet (selectedBackendChat is only set for chats already
    // materialized as a backend card) -- switching away from a brand-new
    // chat materializes it under this same threadId (see
    // materializeCurrentChatCard), so without this fallback a background
    // stream from an unmaterialized chat can never be matched back up:
    // resolveBackgroundStreamOutcome always takes its `!originChatId`
    // foreground branch, and the stale-foreground guard below then silently
    // discards the finished reply instead of finalizing or backgrounding it.
    const originChatId = selectedBackendChat?.thread_id ?? threadId ?? null;
    // Mirror the foreground stream's origin into the ref for selectSessionItem
    // (deciding which card shows the orange "generating in background" dot).
    backgroundOriginChatIdRef.current = originChatId;
    // Bump this chat's activity now so "Recent" sorts it to the top right
    // away, instead of waiting for the stream to finish and a fresh chat list
    // snapshot to arrive. A no-op if no backend card exists yet for this id.
    if (originChatId) {
      dispatch(touchChatSession(originChatId));
    }

    // Identifies this stream's chat so a later send only aborts it when it
    // targets the same chat; sending in a different chat leaves this running.
    const streamKey = originChatId;

    // Helper to update the live turn state without stale closures.
    const updateLiveTurn = (updater) => {
      setLiveAssistantTurn(prev => prev ? updater(prev) : prev);
    };

    try {
      const isStale = () => activeStreamIdRef.current !== streamId;
      await sendMessageStream(
        content,
        history,
        // onChunk
        (chunk) => {
          if (isStale()) return;
          updateLiveTurn((prev) => applyAssistantChunk(prev, chunk, Date.now()));
        },
        // onComplete
        (status) => {
          const stale = isStale();
          const outcome = resolveBackgroundStreamOutcome({
            isStale: stale,
            originChatId,
            isViewingOrigin: selectedBackendChatIdRef?.current === originChatId,
            resultType: status === 'cancelled' ? 'cancelled' : 'completed',
          });
          if (stale && outcome.action === 'foreground') {
            return;
          }
          if (outcome.action !== 'foreground') {
            applyBackgroundStreamOutcome(outcome);
            setLiveAssistantTurn(null);
            return;
          }
          // Foreground stream finished — finalise the live turn.
          backgroundOriginChatIdRef.current = null;
          const turn = liveTurnRef.current;
          if (turn) {
            const committed = finalizeLiveAssistantTurn(turn, {
              status,
              now: Date.now(),
            });
            if (committed) {
              setMessages(prevMessages => [...prevMessages, committed]);
            }
          }
          setLiveAssistantTurn(null);
          dispatch(fetchChatSessions({
            chatsUrl: getApiUrl('chats'),
            includeArchived: true,
          }));
          setIsGenerating(false);
        },
        // onError
        (error) => {
          const stale = isStale();
          const outcome = resolveBackgroundStreamOutcome({
            isStale: stale,
            originChatId,
            isViewingOrigin: selectedBackendChatIdRef?.current === originChatId,
            resultType: 'error',
          });
          if (stale && outcome.action === 'foreground') {
            return;
          }
          if (outcome.action !== 'foreground') {
            applyBackgroundStreamOutcome(outcome);
            setLiveAssistantTurn(null);
            return;
          }
          show('error', 'Chat Error', error.message);
          const turn = liveTurnRef.current;
          if (turn) {
            const committed = finalizeLiveAssistantTurn(turn, {
              status: 'error',
              now: Date.now(),
              errorMessage: error.message,
            });
            if (committed) {
              setMessages(prevMessages => [...prevMessages, committed]);
            }
          }
          setLiveAssistantTurn(null);
          setIsGenerating(false);
        },
        // onStreamEvent
        (streamEvent) => {
          // Discard frames that arrive after this request was superseded
          // (cancel/retry) so they cannot attach to a newer message.
          if (isStale()) {
            return;
          }

          if (streamEvent.event_type === 'assistant_metadata' && streamEvent.metadata) {
            updateLiveTurn((prev) => applyAssistantMetadata(prev, streamEvent.metadata));
            return;
          }

          updateLiveTurn((prev) => applyAssistantStreamEvent(prev, streamEvent, Date.now()));
        },
        threadMetadata,
        streamKey,
      );
    } catch (error) {
      show('error', 'Chat Error', error.message);
      setIsGenerating(false);
      setLiveAssistantTurn(null);
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
    activeStreamIdRef,
    backgroundOriginChatIdRef,
    preserveStreamInBackground,
    handleSend,
    handleStop,
    liveAssistantTurn,
    setLiveAssistantTurn,
  };
}
