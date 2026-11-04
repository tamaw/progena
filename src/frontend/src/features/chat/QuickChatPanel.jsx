import { UI_EVENT } from '../../realtime/eventVocabulary';
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useLocation, useNavigate } from 'react-router-dom';
import { fetchChatMessages, selectGeneratingChatIds, selectRequestIdByThreadId } from './state/chats';
import { savePreferences, selectPreferences, selectPreferencesSaveRequest } from '../settings/state/preferences';
import { selectModelProfiles } from '../settings/state/modelProfiles';
import { selectUiEventQueue } from '../../realtime/realtimeSlice';
import { consumeUiEventQueue } from '../../realtime/eventQueue';
import { useConfig } from '../../context/ConfigContext';
import { useChatApi } from './hooks/useChatApi';
import { createUuid } from '../../lib/uuid';
import ChatInput from './components/ChatInput';
import ChatMessages from './components/ChatMessages';
import { BackgroundActivity, BackgroundActivityHistory } from './components/BackgroundActivity';
import StickyTodoProgress from './components/StickyTodoProgress';
import { OpenChatIcon, QuickChatIcon } from '../../components/navIcons';
import { defaultQuickChatConfig, normalizeQuickChatConfig } from './models/quickChatConfig';
import { derivePageContext, formatPageContextBlock } from '../../lib/pageContext';
import { PanelIconButton, TopNavPane } from '../../components/panels/PanelFrame';
import { quickChatMemory } from './models/quickChatMemory';
import { createLiveAssistantTurnStore } from './models/liveAssistantTurnStore';
import { applyAssistantChunk, applyAssistantMetadata, applyAssistantStreamEvent, createLiveAssistantTurn, finalizeLiveAssistantTurn, resolveLiveTurnResponseStatus } from './models/liveAssistantTurnModel';
import { loadQuickChatHistory, toQuickChatInputHistory } from './models/quickChatStreamModel';
import { createQuickChatPromotion, persistQuickChatHandoff } from './models/quickChatPromotionModel';
import { resolveQuickChatThreadAdoption } from './models/quickChatThreadTarget';
import { latestContextUsage, resolveComposerCacheExpiry, resolveComposerContextUsage } from './models/contextWindowModel';
import { followTranscript, noteTranscriptScroll, noteTranscriptUserInput, pinTranscriptToTail } from './models/transcriptScroll';
import { mergeConversationSummaryMarkers } from './models/conversationSummaryModel';
import { dispatchChatHistoryLoad } from '../../lib/browserContracts';


export function QuickChatPanel({
  onClose,
  panelConfig = defaultQuickChatConfig,
  isPinned,
  onTogglePin,
  isOpen,
  isBackground = false,
  pendingSubmission,
  onSubmissionConsumed,
  pendingComposerDraft,
  onComposerDraftConsumed,
  onInlineSubmissionSettled,
}) {
  const { sendMessageStream, cancelStream, observeResponseStream } = useChatApi();
  const { config, getApiUrl } = useConfig();
  const dispatch = useDispatch();
  const uiEventQueue = useSelector(selectUiEventQueue);
  const preferences = useSelector(selectPreferences);
  const preferencesSaveRequest = useSelector(selectPreferencesSaveRequest);
  const modelProfiles = useSelector(selectModelProfiles);
  const generatingChatIds = useSelector(selectGeneratingChatIds);
  const requestIdByThreadId = useSelector(selectRequestIdByThreadId);
  const navigate = useNavigate();
  const location = useLocation();
  const isOnChatPage = location.pathname.startsWith('/chat');
  const normalizedConfig = useMemo(
    () => normalizeQuickChatConfig(panelConfig),
    [panelConfig],
  );
  if (quickChatMemory.mode !== normalizedConfig.mode) {
    quickChatMemory.history = [];
    quickChatMemory.threadId = null;
    quickChatMemory.mode = normalizedConfig.mode;
  }
  const [history, setHistory] = useState(quickChatMemory.history);
  const [summaryMarkers, setSummaryMarkers] = useState([]);
  const [scratchThreadId, setScratchThreadId] = useState(quickChatMemory.threadId);
  const scratchThreadIdRef = useRef(quickChatMemory.threadId);
  const [isSending, setIsSending] = useState(false);
  // A reply this panel did not send (joined as a spectator) still owns the
  // thread. Quick chat has no queue controls, so it waits instead of sending
  // into that reply; it never cancels it.
  const isSpectatingReply = Boolean(scratchThreadId) && !isSending && generatingChatIds.includes(scratchThreadId);
  const isReplyRunning = isSending || isSpectatingReply;
  const [error, setError] = useState('');
  // Thread requested by Chat's "Open in Quick Chat" while its history loads.
  const [adoptingThreadId, setAdoptingThreadId] = useState(null);
  const [composerSeed, setComposerSeed] = useState({ key: 0, message: '' });
  const [liveTurnStore] = useState(createLiveAssistantTurnStore);
  const liveTurnRef = useRef(null);
  const activeStreamIdRef = useRef(null);
  const ownStreamIdRef = useRef(null);
  const quickChatEpochRef = useRef(0);
  const messagesContainerRef = useRef(null);
  const isPinnedToBottomRef = useRef(true);
  const messagesScrollFrameRef = useRef(null);
  const sessionPromiseRef = useRef(null);
  // History page waiters keyed by request ID. Only the newest load is
  // current; starting a load or leaving the panel settles older waiters so a
  // late or missing page event can never strand them.
  const pendingRehydratesRef = useRef(new Map());
  const historyLoadSeqRef = useRef(0);
  // Identifies the "Open in Quick Chat" load that owns the loading state.
  const adoptionLoadRef = useRef(0);
  const lastPersistedThreadIdRef = useRef(null);
  const hasInitializedThreadRef = useRef(false);
  const lastSentPageContextRef = useRef(null);
  const eventSequenceRef = useRef(0);
  const isQuickChatMountedRef = useRef(false);
  const consumedSubmissionIdRef = useRef(null);

  const setLiveAssistantTurn = useCallback((nextTurn) => {
    const resolvedTurn = typeof nextTurn === 'function'
      ? nextTurn(liveTurnRef.current)
      : nextTurn;
    liveTurnRef.current = resolvedTurn;
    liveTurnStore.set(resolvedTurn);
  }, [liveTurnStore]);

  const liveContextUsage = useSyncExternalStore(
    liveTurnStore.subscribeContextUsage,
    liveTurnStore.getContextUsageSnapshot,
    liveTurnStore.getContextUsageSnapshot,
  );
  const getHasLiveTurn = useCallback(() => Boolean(liveTurnStore.getSnapshot()), [liveTurnStore]);
  const hasLiveTurn = useSyncExternalStore(liveTurnStore.subscribe, getHasLiveTurn, getHasLiveTurn);
  const composerContextUsage = useMemo(() => resolveComposerContextUsage({
    contextUsage: liveContextUsage ?? latestContextUsage(history),
    profiles: modelProfiles,
    isFreshConversation: Boolean(scratchThreadId) && history.length === 0 && !liveContextUsage,
  }), [history, liveContextUsage, modelProfiles, scratchThreadId]);
  const composerCacheExpiry = useMemo(
    () => resolveComposerCacheExpiry({ messages: history, profiles: modelProfiles }),
    [history, modelProfiles],
  );

  useEffect(() => {
    if (messagesScrollFrameRef.current !== null) return;
    messagesScrollFrameRef.current = requestAnimationFrame(() => {
      messagesScrollFrameRef.current = null;
      followTranscript(messagesContainerRef.current, isPinnedToBottomRef);
    });
  }, [history, isSending]);

  useEffect(() => () => {
    if (messagesScrollFrameRef.current !== null) {
      cancelAnimationFrame(messagesScrollFrameRef.current);
      messagesScrollFrameRef.current = null;
    }
  }, []);

  // A persisted quick-chat thread's messages arrive as a causal stream event
  // with the rehydrate request's ID.
  useEffect(() => {
    eventSequenceRef.current = consumeUiEventQueue(
      uiEventQueue,
      eventSequenceRef.current,
      ({ event }) => {

        // A persisted quick-chat thread's messages, fetched directly (not
        // through the shared `chats` slice, which would clobber the main
        // Chat page's active-session state) to resume it after a reload.
        const pendingRehydrate = event?.type === UI_EVENT.CHAT_MESSAGES_PAGE_LOADED
          ? pendingRehydratesRef.current.get(event?.payload?.request_id)
          : null;
        if (pendingRehydrate) {
          pendingRehydratesRef.current.delete(event.payload.request_id);
          pendingRehydrate.resolve(event.payload);
        }

        return undefined;
      },
    );
  }, [uiEventQueue]);

  const settlePendingRehydrates = useCallback(() => {
    const waiters = [...pendingRehydratesRef.current.values()];
    pendingRehydratesRef.current.clear();
    waiters.forEach((waiter) => waiter.reject(new Error('Quick chat history load superseded')));
  }, []);

  // Local-only draft: the session document is created by Postgres when the
  // first user message is checkpointed.
  const startFreshQuickThread = useCallback(() => {
    const threadId = createUuid();
    sessionPromiseRef.current = Promise.resolve(threadId);
    scratchThreadIdRef.current = threadId;
    if (isQuickChatMountedRef.current) {
      setScratchThreadId(threadId);
    }
    return threadId;
  }, []);

  // Resume a thread persisted in preferences (see the mount effect below).
  // Returns true once resumed (even with zero messages, e.g. a thread that
  // was created but never sent to), or false if it couldn't be resumed
  // (deleted/archived elsewhere) so the caller falls back to a fresh thread.
  // `preserveLiveTurn` loads the committed transcript beneath a reply that is
  // still streaming (joined as a spectator) without discarding its live turn.
  const rehydrateFromPersistedThread = useCallback(async (persistedThreadId, { preserveLiveTurn = false } = {}) => {
    settlePendingRehydrates();
    historyLoadSeqRef.current += 1;
    const loadSeq = historyLoadSeqRef.current;
    const epoch = quickChatEpochRef.current;
    const expectedActiveStreamId = activeStreamIdRef.current;
    const expectedThreadId = scratchThreadIdRef.current;
    const isCurrent = () => isQuickChatMountedRef.current
      && quickChatEpochRef.current === epoch
      && historyLoadSeqRef.current === loadSeq
      && (preserveLiveTurn || activeStreamIdRef.current === expectedActiveStreamId)
      && scratchThreadIdRef.current === expectedThreadId;
    let normalizedMessages;
    let loadedSummaryMarkers = [];
    try {
      normalizedMessages = await loadQuickChatHistory(async (beforeMessageId) => {
        const requestId = createUuid();
        const pagePromise = new Promise((resolve, reject) => {
          pendingRehydratesRef.current.set(requestId, { resolve, reject });
        });
        try {
          await dispatch(fetchChatMessages({
            chatsUrl: getApiUrl('chats'),
            threadId: persistedThreadId,
            beforeMessageId,
            limit: 50,
            requestId,
          })).unwrap();
          return await pagePromise;
        } finally {
          pendingRehydratesRef.current.delete(requestId);
        }
      }, isCurrent, (markers) => {
        loadedSummaryMarkers = mergeConversationSummaryMarkers(loadedSummaryMarkers, markers);
      });
    } catch {
      return !isCurrent();
    }
    if (!normalizedMessages || !isCurrent()) return true;
    if (!preserveLiveTurn) setLiveAssistantTurn(null);
    setHistory(normalizedMessages);
    setSummaryMarkers((current) => mergeConversationSummaryMarkers(current, loadedSummaryMarkers));
    scratchThreadIdRef.current = persistedThreadId;
    setScratchThreadId(persistedThreadId);
    sessionPromiseRef.current = Promise.resolve(persistedThreadId);
    lastPersistedThreadIdRef.current = persistedThreadId;
    return true;
  }, [dispatch, getApiUrl, setLiveAssistantTurn, settlePendingRehydrates]);

  // Spectator support: render every rich stream frame for a generation this
  // panel did not dispatch. Completion first freezes the same rich local
  // representation, then rehydrates the checkpointed canonical transcript.
  const spectatorCleanupRef = useRef(null);
  const spectatedRequestIdRef = useRef(null);

  useEffect(() => {
    const shouldSpectate = Boolean(scratchThreadId)
      && !isSending
      && generatingChatIds.includes(scratchThreadId);
    const requestId = shouldSpectate ? (requestIdByThreadId[scratchThreadId] || null) : null;

    if (!requestId) {
      const completedRequestId = spectatedRequestIdRef.current;
      if (completedRequestId) {
        spectatorCleanupRef.current?.();
        spectatorCleanupRef.current = null;
        spectatedRequestIdRef.current = null;
        const streamId = `spectate_${completedRequestId}`;
        const turn = activeStreamIdRef.current === streamId ? liveTurnRef.current : null;
        const committed = turn
          ? finalizeLiveAssistantTurn(turn, { status: 'unknown', now: Date.now() })
          : null;
        if (committed) {
          setHistory((current) => current.concat(committed));
        }
        if (activeStreamIdRef.current === streamId) {
          activeStreamIdRef.current = null;
          setLiveAssistantTurn(null);
        }
        void rehydrateFromPersistedThread(scratchThreadId);
      }
      return;
    }
    if (spectatedRequestIdRef.current === requestId) {
      return;
    }

    // The thread moved on to its next request (for example a queued message):
    // keep the partial reply already shown as its own row until the canonical
    // reload replaces it.
    const previousStreamId = spectatedRequestIdRef.current ? `spectate_${spectatedRequestIdRef.current}` : null;
    const previousTurn = previousStreamId && liveTurnRef.current?.streamId === previousStreamId ? liveTurnRef.current : null;
    const frozen = previousTurn
      ? finalizeLiveAssistantTurn(previousTurn, { status: 'unknown', now: Date.now() })
      : null;
    if (frozen) {
      setHistory((current) => (current.some((message) => message.id === frozen.id) ? current : current.concat(frozen)));
    }
    spectatorCleanupRef.current?.();
    const streamId = `spectate_${requestId}`;
    spectatedRequestIdRef.current = requestId;
    activeStreamIdRef.current = streamId;
    setLiveAssistantTurn(createLiveAssistantTurn({ streamId, startedAt: Date.now() }));

    spectatorCleanupRef.current = observeResponseStream(requestId, {
      onChunk: (chunk) => {
        if (spectatedRequestIdRef.current !== requestId || activeStreamIdRef.current !== streamId) return;
        setLiveAssistantTurn((turn) => (turn ? applyAssistantChunk(turn, chunk, Date.now()) : turn));
      },
      onStreamEvent: (streamEvent) => {
        if (spectatedRequestIdRef.current !== requestId || activeStreamIdRef.current !== streamId) return;
        if (streamEvent.event_type === 'assistant_metadata' && streamEvent.metadata) {
          setLiveAssistantTurn((turn) => (turn ? applyAssistantMetadata(turn, streamEvent.metadata) : turn));
          return;
        }
        setLiveAssistantTurn((turn) => (turn ? applyAssistantStreamEvent(turn, streamEvent) : turn));
      },
    });
  }, [
    scratchThreadId,
    isSending,
    generatingChatIds,
    requestIdByThreadId,
    observeResponseStream,
    rehydrateFromPersistedThread,
    setLiveAssistantTurn,
  ]);

  useEffect(() => () => {
    spectatorCleanupRef.current?.();
  }, []);

  const ensureQuickThread = useCallback(async () => {
    if (scratchThreadId) {
      return scratchThreadId;
    }
    const threadId = sessionPromiseRef.current
      ? await sessionPromiseRef.current
      : startFreshQuickThread();
    if (!threadId) {
      throw new Error('Unable to create a quick chat thread.');
    }
    return threadId;
  }, [scratchThreadId, startFreshQuickThread]);

  // Keep committed rich messages across panel open/close. The live response
  // itself is recovered as a spectator stream after reopening.
  useEffect(() => {
    quickChatMemory.history = history;
  }, [history]);
  useEffect(() => {
    scratchThreadIdRef.current = scratchThreadId;
    quickChatMemory.threadId = scratchThreadId;
  }, [scratchThreadId]);

  // Persist the default-mode quick chat's thread id in preferences (the same
  // backend-persisted document `pinnedPanel` already lives in) so a pinned
  // quick chat can rehydrate after a full page reload, not just a
  // popover close/reopen. Other modes (schedule-create, mcp-create, ...) are
  // one-off flows whose own config never survives a reload either (it's
  // plain useState seeded from defaultQuickChatConfig), so persisting their
  // thread would just orphan it.
  useEffect(() => {
    if (
      normalizedConfig.mode !== 'default'
      || !scratchThreadId
      || scratchThreadId === lastPersistedThreadIdRef.current
      || !preferences.loaded
    ) {
      return;
    }
    lastPersistedThreadIdRef.current = scratchThreadId;
    if (scratchThreadId === preferences.quickChatThreadId) {
      return;
    }
    dispatch(savePreferences({
      preferencesUrl: getApiUrl('preferences'),
      ...preferencesSaveRequest,
      quickChatThreadId: scratchThreadId,
    }));
  }, [normalizedConfig.mode, scratchThreadId, preferences.loaded, preferences.quickChatThreadId, dispatch, getApiUrl, preferencesSaveRequest]);

  useEffect(() => {
    isQuickChatMountedRef.current = true;
    const pendingRehydrates = pendingRehydratesRef.current;
    return () => {
      quickChatEpochRef.current += 1;
      activeStreamIdRef.current = null;
      ownStreamIdRef.current = null;
      setLiveAssistantTurn(null);
      // If a rehydrate was still in flight, this abort leaves
      // sessionPromiseRef resolved to null with nothing to retry it -- reset
      // the "already decided" guard below so a remount (a real popover
      // reopen, or React StrictMode's dev-only double-invoke of mount
      // effects) starts over instead of treating the aborted attempt as done.
      if (pendingRehydrates.size > 0) {
        hasInitializedThreadRef.current = false;
      }
      settlePendingRehydrates();
      isQuickChatMountedRef.current = false;
    };
  }, [setLiveAssistantTurn, settlePendingRehydrates]);

  // Continue a Chat conversation here: switch to its canonical thread and load
  // its checkpointed history, beneath its running reply when one is joined as
  // a spectator. Never sends, cancels, or replaces the panel's own reply.
  const threadTarget = normalizedConfig.threadTarget;
  useEffect(() => {
    const decision = resolveQuickChatThreadAdoption({
      target: threadTarget,
      adoptedTargetId: quickChatMemory.adoptedTargetId,
      currentThreadId: scratchThreadId,
      ownStreamActive: Boolean(ownStreamIdRef.current),
    });
    if (decision === 'ignore') return;
    quickChatMemory.adoptedTargetId = threadTarget.id;
    // The requested thread replaces the mount-time resume decision below.
    hasInitializedThreadRef.current = true;
    if (decision === 'busy') {
      setError('Quick chat is still replying. Stop it, then open the conversation again.');
      return;
    }
    const { threadId } = threadTarget;
    // A newer request owns the loading state; an earlier load still settling
    // must neither clear nor keep it.
    adoptionLoadRef.current += 1;
    const adoptionLoad = adoptionLoadRef.current;
    setAdoptingThreadId(null);
    if (decision === 'adopt') {
      quickChatEpochRef.current += 1;
      spectatorCleanupRef.current?.();
      spectatorCleanupRef.current = null;
      spectatedRequestIdRef.current = null;
      activeStreamIdRef.current = null;
      setLiveAssistantTurn(null);
      quickChatMemory.history = [];
      quickChatMemory.threadId = threadId;
      lastSentPageContextRef.current = null;
      setHistory([]);
      setSummaryMarkers([]);
      // Set before the load below so its thread guard treats it as current.
      scratchThreadIdRef.current = threadId;
      setScratchThreadId(threadId);
      sessionPromiseRef.current = Promise.resolve(threadId);
    }
    setError('');
    // The panel's own reply on this thread keeps its live transcript and
    // reconciles when it ends. Otherwise load the committed transcript now; a
    // reply joined by the spectator effect stays live above it and reloads
    // the canonical history again on completion.
    if (ownStreamIdRef.current) return;
    setAdoptingThreadId(threadId);
    void rehydrateFromPersistedThread(threadId, { preserveLiveTurn: true }).then((resumed) => {
      if (!isQuickChatMountedRef.current || adoptionLoadRef.current !== adoptionLoad) return;
      setAdoptingThreadId(null);
      if (!resumed) {
        setError('Could not load that conversation. Open it from Chat and try again.');
      }
    });
  }, [rehydrateFromPersistedThread, scratchThreadId, setLiveAssistantTurn, threadTarget]);

  // Decide the thread once per mount. A pinned quick chat's QuickChatPanel
  // mounts as soon as the shell adopts the pinned panel from its fast
  // localStorage cache (see PINNED_PANEL_STORAGE_KEY below) -- well before
  // the backend `preferences.snapshot` round-trip resolves -- so this can't
  // just run once on mount like `startFreshQuickThread` used to: it has to
  // wait for `preferences.loaded` before deciding there's nothing to resume.
  useEffect(() => {
    if (hasInitializedThreadRef.current) {
      return;
    }
    if (quickChatMemory.threadId) {
      hasInitializedThreadRef.current = true;
      const threadId = quickChatMemory.threadId;
      sessionPromiseRef.current = Promise.resolve(threadId);
      // A running thread is joined by the spectator effect. Otherwise reload
      // its checkpointed rich history so reopen cannot retain a stale local
      // transcript after the panel was closed.
      if (!generatingChatIds.includes(threadId)) {
        void rehydrateFromPersistedThread(threadId);
      }
      return;
    }
    if (normalizedConfig.mode === 'default' && !preferences.loaded) {
      return;
    }
    hasInitializedThreadRef.current = true;
    if (normalizedConfig.mode === 'default' && preferences.quickChatThreadId) {
      void rehydrateFromPersistedThread(preferences.quickChatThreadId).then((resumed) => {
        if (!resumed && isQuickChatMountedRef.current) {
          startFreshQuickThread();
        }
      });
    } else {
      startFreshQuickThread();
    }
  }, [generatingChatIds, normalizedConfig.mode, preferences.loaded, preferences.quickChatThreadId, rehydrateFromPersistedThread, startFreshQuickThread]);

  const submit = useCallback(async (message, attachments = [], onSettled) => {
    const text = message.trim();
    if ((!text && attachments.length === 0) || isReplyRunning) return;
    quickChatEpochRef.current += 1;

    const now = Date.now();
    const streamId = `qchat_stream_${now}`;
    const attachmentIds = attachments.map((attachment) => attachment.id).filter(Boolean);
    activeStreamIdRef.current = streamId;
    ownStreamIdRef.current = streamId;
    pinTranscriptToTail(messagesContainerRef.current, isPinnedToBottomRef);
    setHistory((current) => current.concat({
      id: `qchat_user_${now}`,
      role: 'user',
      content: text,
      inputContent: text,
      timestamp: now,
      attachments: attachments.map(({ id, name, mime_type: mimeType, size_bytes: sizeBytes }) => (
        { id, name, mime_type: mimeType, size_bytes: sizeBytes }
      )),
    }));
    setLiveAssistantTurn(createLiveAssistantTurn({ streamId, startedAt: now }));
    setIsSending(true);
    setError('');
    let terminalStatus = 'failed';
    let terminalError = '';
    let terminalReceived = false;
    let terminalThreadId = null;
    let terminalReconciliationRequested = false;
    const isStale = () => activeStreamIdRef.current !== streamId;

    const commitLiveTurn = (status, errorMessage = '', contentComplete = true) => {
      if (isStale()) return;
      const turn = liveTurnRef.current;
      const committed = turn
        ? finalizeLiveAssistantTurn(turn, { status, now: Date.now(), errorMessage, contentComplete })
        : null;
      if (committed) {
        setHistory((current) => current.concat(committed));
      }
      setLiveAssistantTurn(null);
      activeStreamIdRef.current = null;
      if (terminalThreadId && !terminalReconciliationRequested) {
        terminalReconciliationRequested = true;
        // Release stream ownership before this non-blocking canonical read.
        // Its epoch/thread guard discards a response superseded by a new turn,
        // reset, unmount, or thread switch.
        void rehydrateFromPersistedThread(terminalThreadId);
      }
    };

    try {
      const threadId = await ensureQuickThread();
      terminalThreadId = threadId;
      if (isStale()) return;

      // The page context stays in the agent's input but never becomes visible
      // transcript content. Keep it on the local user message for any future
      // outgoing history, including the next turn before backend rehydration.
      const pageContext = derivePageContext(location.pathname);
      const previousPageContext = lastSentPageContextRef.current;
      const pageContextChanged = !previousPageContext
        || previousPageContext.threadId !== threadId
        || previousPageContext.page !== pageContext?.page
        || previousPageContext.id !== pageContext?.id;
      let outgoingText = text;
      if (pageContext && pageContextChanged) {
        outgoingText = `${formatPageContextBlock(pageContext)}\n\n${text}`;
        lastSentPageContextRef.current = { threadId, ...pageContext };
      }
      if (outgoingText !== text) {
        setHistory((current) => current.map((turn) => (
          turn.id === `qchat_user_${now}` ? { ...turn, inputContent: outgoingText } : turn
        )));
      }

      await sendMessageStream(
        outgoingText,
        toQuickChatInputHistory(history),
        (chunk) => {
          if (isStale()) return;
          setLiveAssistantTurn((turn) => (turn ? applyAssistantChunk(turn, chunk, Date.now()) : turn));
        },
        (status, { contentComplete = true } = {}) => {
          terminalReceived = true;
          terminalStatus = resolveLiveTurnResponseStatus(liveTurnRef.current, status);
          commitLiveTurn(status, '', contentComplete);
        },
        (streamError) => {
          terminalReceived = true;
          terminalStatus = 'failed';
          terminalError = streamError.message;
          if (isStale()) return;
          setError(streamError.message);
          commitLiveTurn('failed', streamError.message);
        },
        (streamEvent) => {
          if (isStale()) return;
          if (streamEvent.event_type === 'assistant_metadata' && streamEvent.metadata) {
            setLiveAssistantTurn((turn) => (turn ? applyAssistantMetadata(turn, streamEvent.metadata) : turn));
            return;
          }
          setLiveAssistantTurn((turn) => (turn ? applyAssistantStreamEvent(turn, streamEvent) : turn));
        },
        {
          thread_id: threadId,
          ...(attachmentIds.length > 0 ? { attachments: attachmentIds } : {}),
        },
      );

      if (!terminalReceived && !isStale()) {
        terminalStatus = 'unknown';
        commitLiveTurn('unknown');
      }
    } catch (sendError) {
      terminalStatus = 'failed';
      terminalError = sendError.message;
      if (sendError.name !== 'AbortError' && !isStale()) {
        setError(sendError.message);
        commitLiveTurn('failed', sendError.message);
      }
    } finally {
      if (ownStreamIdRef.current === streamId) {
        ownStreamIdRef.current = null;
        activeStreamIdRef.current = null;
        setIsSending(false);
      }
      onSettled?.(terminalStatus, terminalError);
    }
  }, [ensureQuickThread, history, isReplyRunning, location.pathname, rehydrateFromPersistedThread, sendMessageStream, setLiveAssistantTurn]);
  useEffect(() => {
    if (!isReplyRunning || !pendingSubmission?.inline || consumedSubmissionIdRef.current === pendingSubmission.id) return;
    consumedSubmissionIdRef.current = pendingSubmission.id;
    onSubmissionConsumed?.(pendingSubmission.id);
    onInlineSubmissionSettled?.(pendingSubmission.clientId, 'failed', 'Quick chat is busy. Try again when it finishes.');
  }, [isReplyRunning, pendingSubmission, onSubmissionConsumed, onInlineSubmissionSettled]);
  useEffect(() => {
    if (!pendingSubmission?.message || isReplyRunning || consumedSubmissionIdRef.current === pendingSubmission.id) {
      return;
    }

    consumedSubmissionIdRef.current = pendingSubmission.id;
    onSubmissionConsumed?.(pendingSubmission.id);
    setComposerSeed((current) => ({ key: current.key + 1, message: '' }));
    void submit(pendingSubmission.message, [], pendingSubmission.inline
      ? (status, error) => onInlineSubmissionSettled?.(pendingSubmission.clientId, status, error)
      : undefined);
  }, [isReplyRunning, onInlineSubmissionSettled, onSubmissionConsumed, pendingSubmission, submit]);
  useEffect(() => {
    if (!pendingComposerDraft?.message) return;
    onComposerDraftConsumed?.(pendingComposerDraft.id);
    setComposerSeed((current) => ({ key: current.key + 1, message: pendingComposerDraft.message }));
  }, [pendingComposerDraft, onComposerDraftConsumed]);

  const openInChat = useCallback(() => {
    const hasQuickChatHandoff = history.length > 0;
    let handoff = null;
    if (hasQuickChatHandoff) {
      const promotion = createQuickChatPromotion(history, scratchThreadId, summaryMarkers);
      handoff = promotion.handoff;
      quickChatMemory.handoff = handoff;
      // The live destination receives the complete renderer payload. Storage is
      // only the reload fallback, so it uses the bounded persisted form.
      persistQuickChatHandoff(config.storage.quickChatHandoff, promotion.persistedHandoff);
      dispatchChatHistoryLoad(handoff);
    }
    onClose();
    if (!isOnChatPage) {
      navigate('/chat', hasQuickChatHandoff ? { state: { quickChatHandoff: true } } : undefined);
    }
  }, [config.storage.quickChatHandoff, history, isOnChatPage, navigate, onClose, scratchThreadId, summaryMarkers]);

  const resetQuickChat = useCallback(() => {
    quickChatEpochRef.current += 1;
    if (ownStreamIdRef.current) {
      void cancelStream();
    }
    spectatorCleanupRef.current?.();
    spectatorCleanupRef.current = null;
    spectatedRequestIdRef.current = null;
    activeStreamIdRef.current = null;
    ownStreamIdRef.current = null;
    setLiveAssistantTurn(null);
    quickChatMemory.history = [];
    quickChatMemory.threadId = null;
    quickChatMemory.handoff = null;
    settlePendingRehydrates();
    adoptionLoadRef.current += 1;
    setAdoptingThreadId(null);
    setHistory([]);
    setSummaryMarkers([]);
    sessionPromiseRef.current = null;
    setIsSending(false);
    setError('');
    setComposerSeed((current) => ({ key: current.key + 1, message: '' }));
    startFreshQuickThread();
  }, [cancelStream, setLiveAssistantTurn, settlePendingRehydrates, startFreshQuickThread]);

  const selectPrompt = useCallback((prompt) => {
    setComposerSeed((current) => ({ key: current.key + 1, message: prompt }));
  }, []);

  const stopQuickChat = useCallback(() => {
    if (ownStreamIdRef.current) {
      void cancelStream();
    }
  }, [cancelStream]);

  return (
    <TopNavPane
      title={normalizedConfig.title}
      icon={<QuickChatIcon />}
      subtitle={normalizedConfig.subtitle || undefined}
      className="topnav-pane--quick"
      bodyClassName="qchat-pane__body"
      onClose={onClose}
      isPinned={isPinned}
      onTogglePin={onTogglePin}
      isBackground={isBackground}
      isOpen={isOpen}
      actions={(
        <>
          <PanelIconButton label="New chat" onClick={resetQuickChat} disabled={isSending}>
            <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" aria-hidden="true">
              <path d="M8 3v10M3 8h10" />
            </svg>
          </PanelIconButton>
          <PanelIconButton label="Open in chat" onClick={openInChat} disabled={isSending}>
            <OpenChatIcon />
          </PanelIconButton>
        </>
      )}
    >
      <div
        ref={messagesContainerRef}
        className={`qchat-pane__scroll chat-detail-body chat-transcript${history.length === 0 && !hasLiveTurn && !isSending && !error ? ' is-empty' : ''}`}
        onWheel={() => noteTranscriptUserInput(messagesContainerRef.current)}
        onTouchStart={() => noteTranscriptUserInput(messagesContainerRef.current)}
        onPointerDown={() => noteTranscriptUserInput(messagesContainerRef.current)}
        onKeyDown={() => noteTranscriptUserInput(messagesContainerRef.current)}
        onScroll={() => noteTranscriptScroll(messagesContainerRef.current, isPinnedToBottomRef)}
      >
        <ChatMessages
          messages={history}
          summaryMarkers={summaryMarkers}
          liveTurnStore={liveTurnStore}
          messagesContainerRef={messagesContainerRef}
          isPinnedToBottomRef={isPinnedToBottomRef}
          afterMessages={<BackgroundActivityHistory
            key={scratchThreadId ?? 'scratch'}
            messages={history}
            liveTurnStore={liveTurnStore}
            threadId={scratchThreadId}
            runsUrl={getApiUrl('runs')}
            chatsUrl={getApiUrl('chats')}
          />}
        />
        {adoptingThreadId && history.length === 0 ? (
          <div className="qchat-loading muted" role="status">Loading conversation…</div>
        ) : null}
        {error ? <div className="qchat-error" role="alert">{error}</div> : null}
      </div>
      {history.length === 0 && !hasLiveTurn && !isSending && !error && !adoptingThreadId ? (
        <div className="qchat-pane__hints" aria-label="Quick chat suggestions">
          {normalizedConfig.prompts.map((prompt) => (
            <button key={prompt} type="button" className="qchat-chip" onClick={() => selectPrompt(prompt)}>
              {prompt}
            </button>
          ))}
        </div>
      ) : null}
      <BackgroundActivity
        key={scratchThreadId ?? 'scratch'}
        messages={history}
        liveTurnStore={liveTurnStore}
        threadId={scratchThreadId}
        runsUrl={getApiUrl('runs')}
        chatsUrl={getApiUrl('chats')}
      />
      <StickyTodoProgress
        liveTurnStore={liveTurnStore}
        isActive={isSending}
        messages={history}
      />
      <ChatInput
        key={composerSeed.key}
        className="qchat-input"
        placeholder={normalizedConfig.placeholder}
        onSend={submit}
        onStop={stopQuickChat}
        isGenerating={isSending}
        disabled={Boolean(adoptingThreadId) || isSpectatingReply}
        enableSlashCommands={false}
        allowStop
        autoFocus={!isBackground}
        keepFocusAfterSend
        initialMessage={composerSeed.message}
        enableAttachments
        contextUsage={composerContextUsage}
        cacheExpiry={composerCacheExpiry}
      />
    </TopNavPane>
  );
}
