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
import {
  chatPerformanceNow,
  isChatPerformanceTracingEnabled,
  traceChatPerformance,
} from '../components/chat/chatPerformanceTrace';

const noop = () => {};

function streamEventTraceDetails(streamEvent) {
  const metadata = streamEvent?.metadata && typeof streamEvent.metadata === 'object'
    ? streamEvent.metadata
    : {};
  return {
    eventType: typeof streamEvent?.event_type === 'string' ? streamEvent.event_type : 'unknown',
    contentLength: typeof streamEvent?.content === 'string' ? streamEvent.content.length : 0,
    toolName: typeof metadata.tool_name === 'string' ? metadata.tool_name : '',
    toolCallId: typeof metadata.tool_call_id === 'string' ? metadata.tool_call_id : '',
    subagentName: typeof metadata.subagent_name === 'string' ? metadata.subagent_name : '',
    nestedToolName: typeof metadata.nested_tool_name === 'string' ? metadata.nested_tool_name : '',
    durationMs: Number.isFinite(metadata.duration_ms) ? metadata.duration_ms : null,
  };
}

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
  onForegroundStreamStarted = noop,
  requestForegroundReconciliation = noop,
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
  const pendingTextRef = useRef({ streamId: null, content: '' });
  const liveTurnFrameRef = useRef(null);
  const tracePendingTextRef = useRef({ streamId: null, queuedAt: null, deltaCount: 0 });
  // Completion must not replace the live turn in the same task that flushes
  // its final text. Two animation frames guarantee the browser paints that
  // final live snapshot before the committed Markdown/history tree takes over.
  const terminalHandoffFrameRef = useRef(null);

  const setLiveAssistantTurn = useCallback((nextTurn) => {
    const tracing = isChatPerformanceTracingEnabled();
    const startedAt = tracing ? chatPerformanceNow() : 0;
    const previousTurn = liveTurnRef.current;
    const resolvedTurn = typeof nextTurn === 'function'
      ? nextTurn(previousTurn)
      : nextTurn;
    liveTurnRef.current = resolvedTurn;
    liveTurnStore.set(resolvedTurn);
    if (tracing) {
      const isTextOnlyUpdate = previousTurn?.timelineItems?.length === resolvedTurn?.timelineItems?.length
        && previousTurn?.isStreaming === resolvedTurn?.isStreaming
        && previousTurn?.content !== resolvedTurn?.content;
      traceChatPerformance('live_turn.store_published', {
        streamId: resolvedTurn?.streamId || previousTurn?.streamId || '',
        contentLength: resolvedTurn?.content?.length || 0,
        timelineItemCount: resolvedTurn?.timelineItems?.length || 0,
        isStreaming: Boolean(resolvedTurn?.isStreaming),
        publishDurationMs: chatPerformanceNow() - startedAt,
      }, { consoleOutput: !isTextOnlyUpdate });
    }
  }, [liveTurnStore]);

  const flushPendingText = useCallback((streamId) => {
    const pending = pendingTextRef.current;
    if (!pending.content || pending.streamId !== streamId) {
      return;
    }
    const tracePending = tracePendingTextRef.current;
    pendingTextRef.current = { streamId: null, content: '' };
    tracePendingTextRef.current = { streamId: null, queuedAt: null, deltaCount: 0 };
    if (
      activeStreamIdRef.current !== streamId
      || liveTurnRef.current?.streamId !== streamId
    ) {
      return;
    }
    const tracing = isChatPerformanceTracingEnabled();
    const reducerStartedAt = tracing ? chatPerformanceNow() : 0;
    setLiveAssistantTurn((turn) => (
      turn ? applyAssistantChunk(turn, pending.content, Date.now()) : turn
    ));
    if (tracing) {
      const flushedAt = chatPerformanceNow();
      traceChatPerformance('stream.delta_flushed', {
        streamId,
        pendingCharacterCount: pending.content.length,
        pendingDeltaCount: tracePending.deltaCount,
        queueWaitMs: tracePending.queuedAt === null ? null : flushedAt - tracePending.queuedAt,
        reducerAndPublishMs: flushedAt - reducerStartedAt,
      });
    }
  }, [setLiveAssistantTurn]);

  const discardPendingText = useCallback(() => {
    pendingTextRef.current = { streamId: null, content: '' };
    tracePendingTextRef.current = { streamId: null, queuedAt: null, deltaCount: 0 };
    if (liveTurnFrameRef.current !== null) {
      cancelAnimationFrame(liveTurnFrameRef.current);
      liveTurnFrameRef.current = null;
    }
  }, []);

  const cancelTerminalHandoff = useCallback(() => {
    if (terminalHandoffFrameRef.current !== null) {
      cancelAnimationFrame(terminalHandoffFrameRef.current);
      terminalHandoffFrameRef.current = null;
    }
  }, []);

  const queueTextChunk = useCallback((streamId, chunk) => {
    const pending = pendingTextRef.current;
    if (pending.streamId && pending.streamId !== streamId) {
      return;
    }
    const tracing = isChatPerformanceTracingEnabled();
    const receivedAt = tracing ? chatPerformanceNow() : null;
    const tracePending = tracePendingTextRef.current;
    pendingTextRef.current = {
      streamId,
      content: pending.content + chunk,
    };
    tracePendingTextRef.current = {
      streamId,
      queuedAt: tracePending.streamId === streamId ? tracePending.queuedAt : receivedAt,
      deltaCount: tracePending.streamId === streamId ? tracePending.deltaCount + 1 : 1,
    };
    if (tracing) {
      traceChatPerformance('stream.delta_queued', {
        streamId,
        deltaLength: chunk.length,
        pendingCharacterCount: pending.content.length + chunk.length,
        pendingDeltaCount: tracePendingTextRef.current.deltaCount,
      }, { consoleOutput: false });
    }
    if (liveTurnFrameRef.current !== null) {
      return;
    }
    liveTurnFrameRef.current = requestAnimationFrame(() => {
      liveTurnFrameRef.current = null;
      flushPendingText(streamId);
    });
  }, [flushPendingText]);
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
    // The terminal event already reached this browser. If the user leaves in
    // the two-frame visual handoff, the persisted reply will load normally;
    // do not resurrect a completed stream as background generation.
    if (terminalHandoffFrameRef.current !== null) {
      return;
    }
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
      discardPendingText();
      cancelTerminalHandoff();
      activeStreamIdRef.current = null;
    };
  }, [cancelTerminalHandoff, discardPendingText, preserveStreamInBackground]);

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
    discardPendingText();
    activeStreamIdRef.current = streamId;
    onForegroundStreamStarted();

    // Start the live turn — no assistant message created yet.
    const initialTurn = createLiveAssistantTurn({ streamId, startedAt: Date.now() });
    const directAgentName = extractDirectAgentName(content);
    const initialTurnWithMetadata = directAgentName
      ? applyAssistantMetadata(initialTurn, { display_agent_name: directAgentName })
      : initialTurn;
    setLiveAssistantTurn(initialTurnWithMetadata);
    if (isChatPerformanceTracingEnabled()) {
      traceChatPerformance('stream.foreground_started', {
        streamId,
        directAgent: directAgentName || '',
        historyMessageCount: Math.min(messages.length, 10),
        promptCharacterCount: content.length,
      });
    }

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
    const updateTextTurn = (chunk) => {
      if (!liveTurnRef.current?.content) {
        flushPendingText(streamId);
        const tracing = isChatPerformanceTracingEnabled();
        const reducerStartedAt = tracing ? chatPerformanceNow() : 0;
        setLiveAssistantTurn((turn) => (
          turn ? applyAssistantChunk(turn, chunk, Date.now()) : turn
        ));
        if (tracing) {
          traceChatPerformance('stream.delta_first_immediate', {
            streamId,
            deltaLength: chunk.length,
            reducerAndPublishMs: chatPerformanceNow() - reducerStartedAt,
          });
        }
        return;
      }
      queueTextChunk(streamId, chunk);
    };

    const updateStreamEventTurn = (streamEvent, updater) => {
      const tracing = isChatPerformanceTracingEnabled();
      const reducerStartedAt = tracing ? chatPerformanceNow() : 0;
      flushPendingText(streamId);
      setLiveAssistantTurn((turn) => (turn ? updater(turn) : turn));
      if (tracing) {
        traceChatPerformance('stream.structural_event_applied', {
          streamId,
          ...streamEventTraceDetails(streamEvent),
          reducerAndPublishMs: chatPerformanceNow() - reducerStartedAt,
        });
      }
    };

    const isStale = () => activeStreamIdRef.current !== streamId;
    try {
      await sendMessageStream(
        content,
        history,
        // onChunk
        (chunk) => {
          if (isStale()) return;
          updateTextTurn(chunk);
        },
        // onComplete
        (status) => {
          const stale = isStale();
          const tracing = isChatPerformanceTracingEnabled();
          const terminalReceivedAt = tracing ? chatPerformanceNow() : 0;
          if (!stale) {
            flushPendingText(streamId);
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
          // Foreground completion has to hand off ownership without erasing
          // the final streamed aggregate before it can paint. In particular,
          // do not reload the selected detail here: its pending reducer swaps
          // the transcript for a loading screen and defeats the live handoff.
          const terminalTurn = liveTurnRef.current;
          if (tracing) {
            traceChatPerformance('stream.terminal_received', {
              streamId,
              status: status || 'completed',
              contentLength: terminalTurn?.content?.length || 0,
              timelineItemCount: terminalTurn?.timelineItems?.length || 0,
              handoffSetupMs: chatPerformanceNow() - terminalReceivedAt,
            });
          }
          terminalHandoffFrameRef.current = requestAnimationFrame(() => {
            if (tracing) {
              traceChatPerformance('stream.terminal_first_frame', {
                streamId,
                waitMs: chatPerformanceNow() - terminalReceivedAt,
              });
            }
            // The first frame gives React and the browser a chance to render
            // the synchronously flushed final text. In the following frame,
            // freeze that same live owner in place so its cursor can stop
            // without replacing the visible transcript row.
            terminalHandoffFrameRef.current = requestAnimationFrame(() => {
              terminalHandoffFrameRef.current = null;
              if (tracing) {
                traceChatPerformance('stream.terminal_second_frame', {
                  streamId,
                  waitMs: chatPerformanceNow() - terminalReceivedAt,
                });
              }
              if (!isCurrentLiveTurn(streamId)) {
                return;
              }
              flushPendingText(streamId);
              const finalTurn = liveTurnRef.current;
              const committed = finalTurn
                ? finalizeLiveAssistantTurn(finalTurn, { status, now: Date.now() })
                : null;
              if (committed) {
                setMessages((prevMessages) => [...prevMessages, committed]);
                setLiveAssistantTurn(committed);
              } else {
                clearLiveTurnForStream(streamId);
              }
              if (activeStreamIdRef.current === streamId) {
                activeStreamIdRef.current = null;
                backgroundOriginChatIdRef.current = null;
              }
              setIsGenerating(false);
              requestForegroundReconciliation({ threadId: originChatId, streamId });
              if (tracing) {
                traceChatPerformance('stream.terminal_frozen', {
                  streamId,
                  totalHandoffMs: chatPerformanceNow() - terminalReceivedAt,
                  committed: Boolean(committed),
                });
              }
            });
          });
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
            discardPendingText();
            clearLiveTurnForStream(streamId);
            return;
          }
          if (!isCurrentLiveTurn(streamId)) {
            return;
          }
          flushPendingText(streamId);
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
              setLiveAssistantTurn(committed);
            } else {
              clearLiveTurnForStream(streamId);
            }
          }
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
            updateStreamEventTurn(
              streamEvent,
              (prev) => applyAssistantMetadata(prev, streamEvent.metadata),
            );
            return;
          }

          updateStreamEventTurn(
            streamEvent,
            (prev) => applyAssistantStreamEvent(prev, streamEvent),
          );
        },
        threadMetadata,
      );
    } catch (error) {
      if (isStale()) {
        return;
      }
      show('error', 'Chat Error', error.message);
      setIsGenerating(false);
      discardPendingText();
      clearLiveTurnForStream(streamId);
    }
  }, [
    discardPendingText,
    flushPendingText,
    queueTextChunk,
    reloadOpenChat,
    onForegroundStreamStarted,
    requestForegroundReconciliation,
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
