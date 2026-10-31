import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSelector } from 'react-redux';
import { reportFailure } from '../../../lib/failures';
import { clearChatErrored, clearChatGenerating, markChatErrored, markChatGenerating, markChatUnread, touchChatSession } from '../state/actions';
import { fetchChatSessions, selectChatActivityByThreadId, selectErroredChatIds, selectGeneratingChatIds, selectRequestIdByThreadId, selectUnreadChatIds } from '../state/chats';
import {
  createLiveAssistantTurn,
  applyAssistantMetadata,
  applyAssistantStreamEvent,
  applyAssistantChunk,
  finalizeLiveAssistantTurn,
  resolveLiveTurnResponseStatus,
} from '../models/liveAssistantTurnModel';
import { createLiveAssistantTurnStore } from '../models/liveAssistantTurnStore';
import { resolveBackgroundStreamOutcome, shouldPreserveStreamInBackground } from './chatStreamModel';
const FRONTEND_RESPONSE_OPTIONS_KEY = '__frontendResponse';
const noop = () => {};

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
  observeResponseStream,
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
  const backgroundChatActivityByThreadId = useSelector(selectChatActivityByThreadId);
  const requestIdByThreadId = useSelector(selectRequestIdByThreadId);
  const unreadChatIds = useMemo(() => new Set(unreadChatIdList), [unreadChatIdList]);
  const backgroundGeneratingChatIds = useMemo(() => new Set(generatingChatIdList), [generatingChatIdList]);
  const backgroundErrorChatIds = useMemo(() => new Set(erroredChatIdList), [erroredChatIdList]);
  const activeStreamIdRef = useRef(null);
  const backgroundOriginChatIdRef = useRef(null);
  const liveTurnRef = useRef(null);
  const pendingTextRef = useRef({ streamId: null, content: '' });
  const liveTurnFrameRef = useRef(null);
  // Completion must not replace the live turn in the same task that flushes
  // its final text. Two animation frames guarantee the browser paints that
  // final live snapshot before the committed Markdown/history tree takes over.
  const terminalHandoffFrameRef = useRef(null);
  const queueBoundaryMessageIdsRef = useRef(new Set());

  const setLiveAssistantTurn = useCallback((nextTurn) => {
    const previousTurn = liveTurnRef.current;
    const resolvedTurn = typeof nextTurn === 'function'
      ? nextTurn(previousTurn)
      : nextTurn;
    liveTurnRef.current = resolvedTurn;
    liveTurnStore.set(resolvedTurn);
  }, [liveTurnStore]);

  const flushPendingText = useCallback((streamId) => {
    const pending = pendingTextRef.current;
    if (!pending.content || pending.streamId !== streamId) {
      return;
    }
    pendingTextRef.current = { streamId: null, content: '' };
    if (
      activeStreamIdRef.current !== streamId
      || liveTurnRef.current?.streamId !== streamId
    ) {
      return;
    }
    setLiveAssistantTurn((turn) => (
      turn ? applyAssistantChunk(turn, pending.content, Date.now()) : turn
    ));
  }, [setLiveAssistantTurn]);

  const discardPendingText = useCallback(() => {
    pendingTextRef.current = { streamId: null, content: '' };
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
    pendingTextRef.current = {
      streamId,
      content: pending.content + chunk,
    };
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

  // A queue boundary is a new model turn inside the same response transport.
  // Freeze the preceding assistant before inserting the runtime-owned human
  // message so text from adjacent turns can never concatenate in one row.
  const applyQueueBoundary = useCallback((streamId, streamEvent) => {
    const metadata = streamEvent?.metadata;
    const messageId = typeof metadata?.message_id === 'string' ? metadata.message_id : '';
    const requestId = typeof metadata?.request_id === 'string' ? metadata.request_id : '';
    if (
      !messageId
      || !requestId
      || liveTurnRef.current?.streamId !== streamId
      || queueBoundaryMessageIdsRef.current.has(messageId)
    ) {
      return false;
    }
    queueBoundaryMessageIdsRef.current.add(messageId);
    flushPendingText(streamId);
    const previousTurn = liveTurnRef.current;
    const committedAssistant = finalizeLiveAssistantTurn(previousTurn, {
      status: 'completed',
      now: Date.now(),
      interruptionReason: 'new_message',
    });
    const queuedHuman = {
      id: messageId,
      role: 'user',
      content: typeof metadata.text === 'string' ? metadata.text : '',
      timestamp: Date.now(),
      attachments: Array.isArray(metadata.attachments) ? metadata.attachments : [],
    };
    setMessages((current) => (
      committedAssistant ? [...current, committedAssistant, queuedHuman] : [...current, queuedHuman]
    ));
    setLiveAssistantTurn(createLiveAssistantTurn({
      streamId,
      startedAt: Date.now(),
      assistantIdPrefix: requestId,
    }));
    return true;
  }, [flushPendingText, setLiveAssistantTurn, setMessages]);

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

  // The caller can leave a foreground stream running in the background. Once
  // preserved, release only this surface's ownership; do not cancel the
  // transport or erase its background origin.
  const retireForegroundStream = useCallback(() => {
    discardPendingText();
    cancelTerminalHandoff();
    activeStreamIdRef.current = null;
    setLiveAssistantTurn(null);
    setIsGenerating(false);
  }, [cancelTerminalHandoff, discardPendingText, setLiveAssistantTurn]);

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

  // Spectator support: render live deltas for a generation this hook did not
  // itself dispatch (another surface, or another tab, sending into the
  // thread currently on screen). `activeStreamIdRef`/`backgroundOriginChatIdRef`
  // already cover "my own foreground/backgrounded stream" — this only
  // attaches when neither of those own the currently-viewed thread.
  const spectatorCleanupRef = useRef(null);
  const spectatedRequestIdRef = useRef(null);

  const stopSpectating = useCallback(() => {
    spectatorCleanupRef.current?.();
    spectatorCleanupRef.current = null;
    spectatedRequestIdRef.current = null;
  }, []);

  useEffect(() => {
    const isOwnForegroundStream = activeStreamIdRef.current !== null;
    const isOwnBackgroundedStream = backgroundOriginChatIdRef.current === threadId;
    const shouldSpectate = Boolean(threadId)
      && backgroundGeneratingChatIds.has(threadId)
      && !isOwnForegroundStream
      && !isOwnBackgroundedStream;
    const requestId = shouldSpectate ? (requestIdByThreadId[threadId] || null) : null;

    if (!requestId) {
      if (spectatedRequestIdRef.current) {
        const finishedStreamId = `spectate_${spectatedRequestIdRef.current}`;
        stopSpectating();
        // Finalize locally first (same shape a self-initiated foreground send
        // ends with) and hand off to the existing canonical-reconciliation
        // flow (mergeCanonicalMessages) rather than a full messages reload —
        // that flow preserves ordering/scroll position; wholesale replacing
        // `messages` mid-conversation visibly jumped the pane to the top.
        const finalTurn = liveTurnRef.current?.streamId === finishedStreamId ? liveTurnRef.current : null;
        const committed = finalTurn
          ? finalizeLiveAssistantTurn(finalTurn, { status: 'unknown', now: Date.now() })
          : null;
        if (committed) {
          setMessages((prevMessages) => prevMessages.concat(committed));
          setLiveAssistantTurn(committed);
        } else {
          setLiveAssistantTurn(null);
        }
        if (threadId) {
          requestForegroundReconciliation({ threadId, streamId: finishedStreamId });
        }
      }
      return;
    }
    if (spectatedRequestIdRef.current === requestId) {
      return;
    }

    stopSpectating();
    const streamId = `spectate_${requestId}`;
    spectatedRequestIdRef.current = requestId;
    setLiveAssistantTurn(createLiveAssistantTurn({ streamId, startedAt: Date.now() }));

    spectatorCleanupRef.current = observeResponseStream(requestId, {
      onChunk: (chunk) => {
        if (spectatedRequestIdRef.current !== requestId) return;
        setLiveAssistantTurn((turn) => (turn ? applyAssistantChunk(turn, chunk, Date.now()) : turn));
      },
      onStreamEvent: (streamEvent) => {
        if (spectatedRequestIdRef.current !== requestId) return;
        if (streamEvent.event_type === 'chat_queue_boundary') {
          applyQueueBoundary(streamId, streamEvent);
          return;
        }
        if (streamEvent.event_type === 'assistant_metadata' && streamEvent.metadata) {
          setLiveAssistantTurn((turn) => (turn ? applyAssistantMetadata(turn, streamEvent.metadata) : turn));
          return;
        }
        setLiveAssistantTurn((turn) => (turn ? applyAssistantStreamEvent(turn, streamEvent) : turn));
      },
    });
  }, [
    threadId,
    backgroundGeneratingChatIds,
    requestIdByThreadId,
    observeResponseStream,
    requestForegroundReconciliation,
    setMessages,
    setLiveAssistantTurn,
    applyQueueBoundary,
    stopSpectating,
  ]);

  useEffect(() => stopSpectating, [stopSpectating]);

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

  const handleSend = useCallback(async (content, attachments = []) => {
    preserveStreamInBackground();
    const userMessage = {
      id: `msg_${Date.now()}`,
      role: 'user',
      content,
      timestamp: Date.now(),
      attachments: attachments.map(({ id, name, mime_type: mimeType, size_bytes: sizeBytes }) => (
        { id, name, mime_type: mimeType, size_bytes: sizeBytes }
      )),
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
    const attachmentIds = attachments.map((attachment) => attachment.id).filter(Boolean);
    const threadMetadata = {
      [FRONTEND_RESPONSE_OPTIONS_KEY]: { allowDesyncedCompletion: true },
      ...(selectedBackendChat?.thread_id
        ? { thread_id: selectedBackendChat.thread_id }
        : threadId
          ? { thread_id: threadId }
          : {}),
      ...(attachmentIds.length > 0 ? { attachments: attachmentIds } : {}),
    };

    // The chat this stream belongs to, captured as a closure-local so
    // background completion can update the right sidebar card later. A local
    // draft has no backend card until Postgres creates its session, so fall
    // back to the draft's threadId (the session's id once it exists); without
    // it resolveBackgroundStreamOutcome takes its `!originChatId` foreground
    // branch and the stale-foreground guard discards the finished reply.
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
        setLiveAssistantTurn((turn) => (
          turn ? applyAssistantChunk(turn, chunk, Date.now()) : turn
        ));
        return;
      }
      queueTextChunk(streamId, chunk);
    };

    const updateStreamEventTurn = (updater) => {
      flushPendingText(streamId);
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
          updateTextTurn(chunk);
        },
        // onComplete
        (status) => {
          const stale = isStale();
          if (!stale) {
            flushPendingText(streamId);
          }
          // A stale stream must not resolve its outcome from the replacement
          // turn: provider terminal metadata belongs to the turn's stream.
          const completedTurn = liveTurnRef.current?.streamId === streamId
            ? liveTurnRef.current
            : null;
          const responseStatus = resolveLiveTurnResponseStatus(completedTurn, status);
          const outcome = resolveBackgroundStreamOutcome({
            isStale: stale,
            originChatId,
            isViewingOrigin: selectedBackendChatIdRef?.current === originChatId,
            resultType: responseStatus === 'cancelled'
              ? 'cancelled'
              : (responseStatus === 'failed' || responseStatus === 'interrupted' ? 'error' : 'completed'),
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
          terminalHandoffFrameRef.current = requestAnimationFrame(() => {
            // The first frame gives React and the browser a chance to render
            // the synchronously flushed final text. In the following frame,
            // freeze that same live owner in place so its cursor can stop
            // without replacing the visible transcript row.
            terminalHandoffFrameRef.current = requestAnimationFrame(() => {
              terminalHandoffFrameRef.current = null;
              if (!isCurrentLiveTurn(streamId)) {
                return;
              }
              flushPendingText(streamId);
              const finalTurn = liveTurnRef.current;
              const committed = finalTurn
                ? finalizeLiveAssistantTurn(finalTurn, {
                  status,
                  now: Date.now(),
                  interruptionReason: status === 'cancelled' ? 'user_cancelled' : undefined,
                })
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
          reportFailure(show, 'Generate response', error);
          const turn = liveTurnRef.current;
          if (turn) {
            const committed = finalizeLiveAssistantTurn(turn, {
              status: 'failed',
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

          if (streamEvent.event_type === 'chat_queue_boundary') {
            applyQueueBoundary(streamId, streamEvent);
            return;
          }

          if (streamEvent.event_type === 'assistant_metadata' && streamEvent.metadata) {
            updateStreamEventTurn(
              (prev) => applyAssistantMetadata(prev, streamEvent.metadata),
            );
            return;
          }

          updateStreamEventTurn(
            (prev) => applyAssistantStreamEvent(prev, streamEvent),
          );
        },
        threadMetadata,
      );
    } catch (error) {
      if (isStale()) {
        return;
      }
      reportFailure(show, 'Generate response', error);
      setIsGenerating(false);
      discardPendingText();
      clearLiveTurnForStream(streamId);
    }
  }, [
    applyQueueBoundary,
    discardPendingText,
    flushPendingText,
    queueTextChunk,
    preserveStreamInBackground,
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

  // A second response request while this conversation is generating becomes a
  // durable runtime queue item. It deliberately does not claim the foreground
  // stream or add a speculative transcript message: the queue snapshot is the
  // sole browser representation until runtime consumes the item.
  const handleQueue = useCallback(async (content, attachments = []) => {
    const selectedBackendChat = resolveSelectedBackendChat(
      activeBackendChat,
      selectedBackendChatId,
    );
    const attachmentIds = attachments.map((attachment) => attachment.id).filter(Boolean);
    const metadata = {
      [FRONTEND_RESPONSE_OPTIONS_KEY]: { allowDesyncedCompletion: true },
      ...(selectedBackendChat?.thread_id
        ? { thread_id: selectedBackendChat.thread_id }
        : threadId
          ? { thread_id: threadId }
          : {}),
      ...(attachmentIds.length > 0 ? { attachments: attachmentIds } : {}),
    };

    await sendMessageStream(
      content,
      messages.slice(-10).map((message) => ({ role: message.role, content: message.content })),
      undefined,
      undefined,
      (error) => reportFailure(show, 'Queue message', error),
      undefined,
      metadata,
      false,
    );
  }, [activeBackendChat, messages, selectedBackendChatId, sendMessageStream, show, threadId]);

  const handleStop = useCallback(() => cancelStream(), [cancelStream]);

  return {
    isGenerating,
    setIsGenerating,
    unreadChatIds,
    backgroundGeneratingChatIds,
    backgroundChatActivityByThreadId,
    backgroundErrorChatIds,
    activeStreamIdRef,
    backgroundOriginChatIdRef,
    preserveStreamInBackground,
    retireForegroundStream,
    handleSend,
    handleQueue,
    handleStop,
    liveTurnStore,
    setLiveAssistantTurn,
  };
}
