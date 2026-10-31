import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSelector } from 'react-redux';
import { reportFailure } from '../../../lib/failures';
import { subscribeUiEvent } from '../../../realtime/realtimeEvents';
import { UI_EVENT } from '../../../realtime/eventVocabulary';
import { createUuid } from '../../../lib/uuid';
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
  queueMessage,
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
  const queuedExecutionsRef = useRef(new Map());
  const queuedOwnerRequestIdRef = useRef(null);
  const claimedQueuedMessageIdsRef = useRef(new Set());
  const queuedAdmissionCandidatesRef = useRef(new Map());
  const spectatorCleanupRef = useRef(null);
  const spectatedRequestIdRef = useRef(null);

  const stopSpectating = useCallback(() => {
    spectatorCleanupRef.current?.();
    spectatorCleanupRef.current = null;
    spectatedRequestIdRef.current = null;
  }, []);

  // A queued execution may supersede a response this pane was only observing.
  // Freeze that response into its own transcript row before replacing the live
  // owner; otherwise the streamed text disappears until canonical reload.
  const freezeSpectatedTurn = useCallback(() => {
    const requestId = spectatedRequestIdRef.current;
    if (!requestId) return false;
    const streamId = `spectate_${requestId}`;
    const turn = liveTurnRef.current?.streamId === streamId ? liveTurnRef.current : null;
    stopSpectating();
    const committed = turn
      ? finalizeLiveAssistantTurn(turn, { status: 'unknown', now: Date.now() })
      : null;
    if (!committed) return false;
    setMessages((current) => (
      current.some((message) => message.id === committed.id) ? current : current.concat(committed)
    ));
    return true;
  }, [setMessages, stopSpectating]);

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

  const isQueuedExecutionOwner = useCallback((execution) => (
    queuedOwnerRequestIdRef.current === execution.requestId
    && activeStreamIdRef.current === execution.streamId
  ), []);

  const publishQueuedExecution = useCallback((execution, { status = 'unknown', errorMessage } = {}) => {
    const message = finalizeLiveAssistantTurn(execution.turn, {
      status,
      now: Date.now(),
      errorMessage,
      interruptionReason: status === 'cancelled' ? 'user_cancelled' : undefined,
    });
    if (!message) {
      return null;
    }
    execution.entryId = message.id;
    setMessages((current) => {
      const entryIndex = current.findIndex((item) => item.id === message.id);
      if (entryIndex === -1) {
        return current.concat(message);
      }
      const next = current.slice();
      next[entryIndex] = message;
      return next;
    });
    return message;
  }, [setMessages]);

  const renderQueuedExecution = useCallback((execution) => {
    if (isQueuedExecutionOwner(execution)) {
      setLiveAssistantTurn(execution.turn);
      return;
    }
    publishQueuedExecution(execution);
  }, [isQueuedExecutionOwner, publishQueuedExecution, setLiveAssistantTurn]);

  const flushQueuedText = useCallback((execution) => {
    if (!execution.pendingText) {
      return;
    }
    if (execution.frameId !== null) {
      cancelAnimationFrame(execution.frameId);
      execution.frameId = null;
    }
    const text = execution.pendingText;
    execution.pendingText = '';
    execution.turn = applyAssistantChunk(execution.turn, text, Date.now());
    renderQueuedExecution(execution);
  }, [renderQueuedExecution]);

  const queueQueuedText = useCallback((execution, chunk) => {
    execution.pendingText += chunk;
    if (execution.frozen || !execution.turn.content) {
      flushQueuedText(execution);
      return;
    }
    if (execution.frameId !== null) {
      return;
    }
    execution.frameId = requestAnimationFrame(() => {
      execution.frameId = null;
      flushQueuedText(execution);
    });
  }, [flushQueuedText]);

  const releaseQueuedExecutions = useCallback(() => {
    for (const execution of queuedExecutionsRef.current.values()) {
      if (execution.frameId !== null) {
        cancelAnimationFrame(execution.frameId);
        execution.frameId = null;
      }
      execution.cleanup?.();
    }
    queuedExecutionsRef.current.clear();
    queuedOwnerRequestIdRef.current = null;
  }, []);
  const queuedExecutionThreadIdRef = useRef(null);
  useEffect(() => {
    const previousThreadId = queuedExecutionThreadIdRef.current;
    if (previousThreadId !== null && previousThreadId !== threadId) {
      releaseQueuedExecutions();
      if (backgroundOriginChatIdRef.current === previousThreadId) {
        activeStreamIdRef.current = null;
        backgroundOriginChatIdRef.current = null;
      }
    }
    queuedExecutionThreadIdRef.current = threadId;
  }, [releaseQueuedExecutions, threadId]);


  // Deferred executions have independent response transports. Starting C
  // freezes B into history, but B remains observed until its content stream
  // drains so late text, tool and subagent frames retain their original row.
  useEffect(() => {
    const unsubscribe = subscribeUiEvent((event) => {
      const payload = event?.payload;
      const requestId = payload?.request_id;
      const candidate = requestId ? queuedAdmissionCandidatesRef.current.get(requestId) : null;
      const conversationId = payload?.thread_id || candidate?.threadId || null;
      if (conversationId !== threadId || (payload?.queue_execution !== true && !candidate)) return;
      const contentRequestId = payload.content_request_id || payload.request_id;
      if (!contentRequestId) return;

      if (event.type === UI_EVENT.CHAT_RESPONSE_STARTED) {
        if (payload?.queue_execution !== true && payload?.queued === true) return;
        if (queuedExecutionsRef.current.has(contentRequestId)) return;
        freezeSpectatedTurn();


        const previousRequestId = queuedOwnerRequestIdRef.current;
        const previousExecution = previousRequestId
          ? queuedExecutionsRef.current.get(previousRequestId)
          : null;
        if (previousExecution) {
          flushQueuedText(previousExecution);
          previousExecution.frozen = true;
          publishQueuedExecution(previousExecution);
        } else {
          const previousStreamId = activeStreamIdRef.current;
          if (previousStreamId && liveTurnRef.current?.streamId === previousStreamId) {
            flushPendingText(previousStreamId);
            const previous = finalizeLiveAssistantTurn(liveTurnRef.current, { status: 'unknown', now: Date.now() });
            if (previous) {
              setMessages((current) => {
                const lastAssistant = current.findLast((message) => message.role === 'assistant');
                return current.some((message) => message.id === previous.id)
                  || lastAssistant?.content === previous.content
                  ? current
                  : current.concat(previous);
              });
            }
          }
          discardPendingText();
        }
        cancelTerminalHandoff();

        const queued = payload.queued_message || (candidate && {
          id: `user_${requestId}`,
          text: candidate.text,
          attachments: candidate.attachments,
        });
        const streamId = `queued_${contentRequestId}`;
        const execution = {
          requestId: contentRequestId,
          threadId: conversationId,
          streamId,
          turn: createLiveAssistantTurn({ streamId, startedAt: Date.now(), assistantIdPrefix: contentRequestId }),
          pendingText: '',
          frameId: null,
          cleanup: null,
          terminalStatus: null,
          frozen: false,
          entryId: null,
          adoptedHumanMessageId: null,
        };
        queuedExecutionsRef.current.set(contentRequestId, execution);
        queuedOwnerRequestIdRef.current = contentRequestId;
        activeStreamIdRef.current = streamId;
        backgroundOriginChatIdRef.current = conversationId;
        onForegroundStreamStarted();
        setIsGenerating(true);
        if (candidate) queuedAdmissionCandidatesRef.current.delete(requestId);
        setLiveAssistantTurn(execution.turn);
        execution.cleanup = observeResponseStream(contentRequestId, {
          onChunk: (chunk) => {
            if (queuedExecutionsRef.current.get(execution.requestId) !== execution) return;
            queueQueuedText(execution, chunk);
          },
          onStreamEvent: (streamEvent) => {
            if (queuedExecutionsRef.current.get(execution.requestId) !== execution) return;
            flushQueuedText(execution);
            execution.turn = streamEvent.event_type === 'assistant_metadata' && streamEvent.metadata
              ? applyAssistantMetadata(execution.turn, streamEvent.metadata)
              : applyAssistantStreamEvent(execution.turn, streamEvent);
            renderQueuedExecution(execution);
          },
          onComplete: (status) => {
            if (queuedExecutionsRef.current.get(execution.requestId) !== execution) return;
            flushQueuedText(execution);
            const finalStatus = execution.terminalStatus || status;
            const completed = publishQueuedExecution(execution, { status: finalStatus });
            queuedExecutionsRef.current.delete(execution.requestId);
            execution.cleanup = null;
            if (!isQueuedExecutionOwner(execution)) return;
            queuedOwnerRequestIdRef.current = null;
            if (completed) setLiveAssistantTurn(completed); else clearLiveTurnForStream(execution.streamId);
            activeStreamIdRef.current = null;
            backgroundOriginChatIdRef.current = null;
            setIsGenerating(false);
            requestForegroundReconciliation({ threadId: execution.threadId, streamId: execution.streamId, forceCanonical: true });
          },
          onError: (error) => {
            if (queuedExecutionsRef.current.get(execution.requestId) !== execution) return;
            flushQueuedText(execution);
            const completed = publishQueuedExecution(execution, { status: 'failed', errorMessage: error?.message });
            queuedExecutionsRef.current.delete(execution.requestId);
            execution.cleanup = null;
            if (!isQueuedExecutionOwner(execution)) return;
            queuedOwnerRequestIdRef.current = null;
            if (completed) setLiveAssistantTurn(completed); else clearLiveTurnForStream(execution.streamId);
            activeStreamIdRef.current = null;
            backgroundOriginChatIdRef.current = null;
            setIsGenerating(false);
            reportFailure(show, 'Generate response', error);
            requestForegroundReconciliation({ threadId: execution.threadId, streamId: execution.streamId, forceCanonical: true });
          },
          onAdopt: (owner) => {
            execution.adoptedHumanMessageId = owner?.humanMessageId || null;
          },
        });
        if (queued?.id && !claimedQueuedMessageIdsRef.current.has(queued.id)) {
          claimedQueuedMessageIdsRef.current.add(queued.id);
          setMessages((current) => {
            const adoptedIndex = execution.adoptedHumanMessageId
              ? current.findIndex((message) => message.id === execution.adoptedHumanMessageId)
              : -1;
            if (adoptedIndex !== -1) {
              const next = current.slice();
              next[adoptedIndex] = {
                ...next[adoptedIndex],
                id: queued.id,
                content: queued.text || next[adoptedIndex].content,
                attachments: queued.attachments || next[adoptedIndex].attachments,
              };
              return next;
            }
            return current.some((message) => message.id === queued.id) ? current : current.concat({
              id: queued.id, role: 'user', content: queued.text || '', attachments: queued.attachments || [], timestamp: Date.now(),
            });
          });
        }
        return;
      }

      const execution = queuedExecutionsRef.current.get(contentRequestId);
      if (
        !execution
        || ![UI_EVENT.CHAT_RESPONSE_COMPLETED, UI_EVENT.CHAT_RESPONSE_CANCELLED, UI_EVENT.CHAT_RESPONSE_FAILED].includes(event.type)
      ) return;
      execution.terminalStatus = event.type === UI_EVENT.CHAT_RESPONSE_CANCELLED
        ? 'cancelled'
        : event.type === UI_EVENT.CHAT_RESPONSE_FAILED ? 'failed' : 'completed';
    });
    return unsubscribe;
  }, [cancelTerminalHandoff, clearLiveTurnForStream, discardPendingText, flushPendingText, flushQueuedText, freezeSpectatedTurn, isQueuedExecutionOwner, observeResponseStream, onForegroundStreamStarted, publishQueuedExecution, queueQueuedText, releaseQueuedExecutions, renderQueuedExecution, requestForegroundReconciliation, setLiveAssistantTurn, setMessages, show, stopSpectating, threadId]);

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
    releaseQueuedExecutions();
    activeStreamIdRef.current = null;
    backgroundOriginChatIdRef.current = null;
    setLiveAssistantTurn(null);
    setIsGenerating(false);
  }, [cancelTerminalHandoff, discardPendingText, releaseQueuedExecutions, setLiveAssistantTurn]);

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
      releaseQueuedExecutions();
      activeStreamIdRef.current = null;
      backgroundOriginChatIdRef.current = null;
    };
  }, [cancelTerminalHandoff, discardPendingText, preserveStreamInBackground, releaseQueuedExecutions]);

  // Spectator support: render live deltas for a generation this hook did not
  // itself dispatch (another surface, or another tab, sending into the
  // thread currently on screen). `activeStreamIdRef`/`backgroundOriginChatIdRef`
  // already cover "my own foreground/backgrounded stream" — this only
  // attaches when neither of those own the currently-viewed thread.

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
        // eslint-disable-next-line react-hooks/set-state-in-effect -- the canonical lifecycle ended the spectated request.
        setIsGenerating(false);
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
    setIsGenerating(true);
    setLiveAssistantTurn(createLiveAssistantTurn({ streamId, startedAt: Date.now() }));

    spectatorCleanupRef.current = observeResponseStream(requestId, {
      onChunk: (chunk) => {
        if (spectatedRequestIdRef.current !== requestId) return;
        setLiveAssistantTurn((turn) => (turn ? applyAssistantChunk(turn, chunk, Date.now()) : turn));
      },
      onStreamEvent: (streamEvent) => {
        if (spectatedRequestIdRef.current !== requestId) return;
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
      __frontendResponse: { allowDesyncedCompletion: true },
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
        { humanMessageId: userMessage.id },
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
    const selectedBackendChat = resolveSelectedBackendChat(activeBackendChat, selectedBackendChatId);
    const attachmentIds = attachments.map((attachment) => attachment.id).filter(Boolean);
    const metadata = {
      ...(selectedBackendChat?.thread_id ? { thread_id: selectedBackendChat.thread_id } : threadId ? { thread_id: threadId } : {}),
      ...(attachmentIds.length > 0 ? { attachments: attachmentIds } : {}),
    };
    const requestId = createUuid();
    queuedAdmissionCandidatesRef.current.set(requestId, { text: content, attachments, threadId });
    try {
      await queueMessage(content, messages.slice(-10).map((message) => ({ role: message.role, content: message.content })), metadata, requestId);
    } catch (error) {
      queuedAdmissionCandidatesRef.current.delete(requestId);
      reportFailure(show, 'Queue message', error);
    }
  }, [activeBackendChat, messages, queueMessage, selectedBackendChatId, show, threadId]);

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
