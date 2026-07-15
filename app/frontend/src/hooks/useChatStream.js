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
import { createLiveAssistantTurnStore } from '../components/chat/liveAssistantTurnStore';
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
  const [liveTurnStore] = useState(createLiveAssistantTurnStore);
  const unreadChatIdList = useSelector(selectUnreadChatIds);
  const generatingChatIdList = useSelector(selectGeneratingChatIds);
  const erroredChatIdList = useSelector(selectErroredChatIds);
  const unreadChatIds = useMemo(() => new Set(unreadChatIdList), [unreadChatIdList]);
  const backgroundGeneratingChatIds = useMemo(() => new Set(generatingChatIdList), [generatingChatIdList]);
  const backgroundErrorChatIds = useMemo(() => new Set(erroredChatIdList), [erroredChatIdList]);
  const activeStreamIdRef = useRef(null);
  const backgroundOriginChatIdRef = useRef(null);
  const liveTurnRef = useRef(null);
  const pendingLiveTurnUpdatesRef = useRef([]);
  const liveTurnFrameRef = useRef(null);

  const setLiveAssistantTurn = useCallback((nextTurn) => {
    const resolvedTurn = typeof nextTurn === 'function'
      ? nextTurn(liveTurnRef.current)
      : nextTurn;
    liveTurnRef.current = resolvedTurn;
    liveTurnStore.set(resolvedTurn);
  }, [liveTurnStore]);

  const flushLiveTurnUpdates = useCallback((streamId) => {
    const updates = pendingLiveTurnUpdatesRef.current;
    if (updates.length === 0) {
      return;
    }
    const currentUpdates = updates.filter((update) => update.streamId === streamId);
    const remainingUpdates = updates.filter((update) => update.streamId !== streamId);
    pendingLiveTurnUpdatesRef.current = remainingUpdates;
    if (remainingUpdates.length > 0 && liveTurnFrameRef.current === null) {
      const nextStreamId = remainingUpdates[0].streamId;
      liveTurnFrameRef.current = requestAnimationFrame(() => {
        liveTurnFrameRef.current = null;
        flushLiveTurnUpdates(nextStreamId);
      });
    }
    if (
      currentUpdates.length === 0
      || activeStreamIdRef.current !== streamId
      || liveTurnRef.current?.streamId !== streamId
    ) {
      return;
    }
    setLiveAssistantTurn((turn) => currentUpdates.reduce(
      (nextTurn, { updater }) => (nextTurn ? updater(nextTurn) : nextTurn),
      turn,
    ));
  }, [setLiveAssistantTurn]);

  const discardPendingLiveTurnUpdates = useCallback(() => {
    pendingLiveTurnUpdatesRef.current = [];
    if (liveTurnFrameRef.current !== null) {
      cancelAnimationFrame(liveTurnFrameRef.current);
      liveTurnFrameRef.current = null;
    }
  }, []);

  const queueLiveTurnUpdate = useCallback((streamId, updater) => {
    pendingLiveTurnUpdatesRef.current.push({ streamId, updater });
    if (liveTurnFrameRef.current !== null) {
      return;
    }
    liveTurnFrameRef.current = requestAnimationFrame(() => {
      liveTurnFrameRef.current = null;
      flushLiveTurnUpdates(streamId);
    });
  }, [flushLiveTurnUpdates]);
  // A stream may finish after a newer send has replaced the live turn. Only
  // the stream that still owns both identities may clear that turn.
  const isCurrentLiveTurn = useCallback((streamId) => (
    activeStreamIdRef.current === streamId
    && liveTurnRef.current?.streamId === streamId
  ), []);

  const clearLiveTurnForStream = useCallback((streamId) => {
    if (!isCurrentLiveTurn(streamId)) {
      return false;
    }
    setLiveAssistantTurn(null);
    return true;
  }, [isCurrentLiveTurn, setLiveAssistantTurn]);


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
      discardPendingLiveTurnUpdates();
      activeStreamIdRef.current = null;
    };
  }, [discardPendingLiveTurnUpdates, preserveStreamInBackground]);

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
    discardPendingLiveTurnUpdates();
    activeStreamIdRef.current = streamId;

    // Start the live turn — no assistant message created yet.
    const initialTurn = createLiveAssistantTurn({ streamId, startedAt: Date.now() });
    const directAgentName = extractDirectAgentName(content);
    const initialTurnWithMetadata = directAgentName
      ? applyAssistantMetadata(initialTurn, { display_agent_name: directAgentName })
      : initialTurn;
    setLiveAssistantTurn(initialTurnWithMetadata);

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

    // Render the first text delta immediately so an accepted response never
    // waits behind a frame boundary. Later text deltas share one visual update
    // per frame; every structural event flushes first and remains immediate.
    const updateTextTurn = (updater) => {
      if (!liveTurnRef.current?.content) {
        flushLiveTurnUpdates(streamId);
        setLiveAssistantTurn((turn) => (turn ? updater(turn) : turn));
        return;
      }
      queueLiveTurnUpdate(streamId, updater);
    };

    const updateStreamEventTurn = (updater) => {
      flushLiveTurnUpdates(streamId);
      setLiveAssistantTurn((turn) => (turn ? updater(turn) : turn));
    };

    const isStale = () => activeStreamIdRef.current !== streamId;
    try {
      await sendMessageStream(
        content,
        history,
        // onChunk
        (chunk) => {
          if (isStale()) return;
          updateTextTurn((prev) => applyAssistantChunk(prev, chunk, Date.now()));
        },
        // onComplete
        (status) => {
          const stale = isStale();
          if (!stale) {
            flushLiveTurnUpdates(streamId);
          }
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
            clearLiveTurnForStream(streamId);
            return;
          }
          if (!isCurrentLiveTurn(streamId)) {
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
          flushLiveTurnUpdates(streamId);
          clearLiveTurnForStream(streamId);
          if (activeStreamIdRef.current === streamId) {
            activeStreamIdRef.current = null;
            backgroundOriginChatIdRef.current = null;
          }
          if (originChatId) {
            reloadOpenChat?.(originChatId);
          }
          // Reconcile the completed turn with the persisted session. The
          // terminal SSE envelope has no response content, so this repairs a
          // missed final delta without trusting volatile live state.
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
            discardPendingLiveTurnUpdates();
            clearLiveTurnForStream(streamId);
            return;
          }
          if (!isCurrentLiveTurn(streamId)) {
            return;
          }
          flushLiveTurnUpdates(streamId);
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
          clearLiveTurnForStream(streamId);
          if (activeStreamIdRef.current === streamId) {
            activeStreamIdRef.current = null;
            backgroundOriginChatIdRef.current = null;
          }
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
            updateStreamEventTurn((prev) => applyAssistantMetadata(prev, streamEvent.metadata));
            return;
          }

          updateStreamEventTurn((prev) => applyAssistantStreamEvent(prev, streamEvent));
        },
        threadMetadata,
      );
    } catch (error) {
      if (isStale()) {
        return;
      }
      show('error', 'Chat Error', error.message);
      setIsGenerating(false);
      discardPendingLiveTurnUpdates();
      clearLiveTurnForStream(streamId);
    }
  }, [
    discardPendingLiveTurnUpdates,
    flushLiveTurnUpdates,
    queueLiveTurnUpdate,
    reloadOpenChat,
    clearLiveTurnForStream,
    isCurrentLiveTurn,
    activeBackendChat,
    applyBackgroundStreamOutcome,
    dispatch,
    messages,
    selectedBackendChatId,
    selectedBackendChatIdRef,
    sendMessageStream,
    threadId,
    setLiveAssistantTurn,
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
    liveTurnStore,
    setLiveAssistantTurn,
  };
}
