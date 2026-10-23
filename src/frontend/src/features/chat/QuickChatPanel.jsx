import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useLocation, useNavigate } from 'react-router-dom';
import { fetchChatMessages, selectGeneratingChatIds, selectRequestIdByThreadId } from './state/chats';
import { savePreferences, selectPreferences, selectPreferencesSaveRequest } from '../settings/state/preferences';
import { selectUiEventQueue } from '../../realtime/realtimeSlice';
import { consumeUiEventQueue } from '../../realtime/eventQueue';
import { useConfig } from '../../context/ConfigContext';
import { useChatApi } from './hooks/useChatApi';
import MarkdownContent from '../../components/MarkdownContent';
import { createUuid } from '../../lib/uuid';
import ChatInput from './components/ChatInput';
import { OpenChatIcon, QuickChatIcon, RefreshIcon } from '../../components/navIcons';
import { defaultQuickChatConfig, normalizeQuickChatConfig } from './models/quickChatConfig';
import { derivePageContext, formatPageContextBlock, stripPageContextBlock } from '../../lib/pageContext';
import { createSpectatorReplayState } from './models/quickChatStreamModel';
import { PanelIconButton, TopNavPane } from '../../components/panels/PanelFrame';
import { quickChatMemory } from './models/quickChatMemory';

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
  const [scratchThreadId, setScratchThreadId] = useState(quickChatMemory.threadId);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState('');
  const [composerSeed, setComposerSeed] = useState({ key: 0, message: '' });
  const sessionPromiseRef = useRef(null);
  const pendingRehydrateRef = useRef(null);
  const lastPersistedThreadIdRef = useRef(null);
  const hasInitializedThreadRef = useRef(false);
  const lastSentPageContextRef = useRef(null);
  const eventSequenceRef = useRef(0);
  const isQuickChatMountedRef = useRef(false);
  const activeAssistantIdRef = useRef(null);
  const consumedSubmissionIdRef = useRef(null);
  const scrollEndRef = useRef(null);

  // Keep the transcript pinned to the latest message, including as it streams
  // in token by token (mirrors the main Chat page's scroll-to-bottom effect).
  useEffect(() => {
    scrollEndRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' });
  }, [history, isSending]);
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
        const pendingRehydrate = pendingRehydrateRef.current;
        if (
          pendingRehydrate
          && event?.type === 'chat.messages.page.loaded'
          && event?.payload?.request_id === pendingRehydrate.requestId
        ) {
          pendingRehydrateRef.current = null;
          pendingRehydrate.resolve(Array.isArray(event.payload.messages) ? event.payload.messages : []);
          return false;
        }

        return undefined;
      },
    );
  }, [uiEventQueue]);

  // Local-only draft: the session document is created by Postgres when the
  // first user message is checkpointed.
  const startFreshQuickThread = useCallback(() => {
    const threadId = createUuid();
    sessionPromiseRef.current = Promise.resolve(threadId);
    if (isQuickChatMountedRef.current) {
      setScratchThreadId(threadId);
    }
    return threadId;
  }, []);

  // Resume a thread persisted in preferences (see the mount effect below).
  // Returns true once resumed (even with zero messages, e.g. a thread that
  // was created but never sent to), or false if it couldn't be resumed
  // (deleted/archived elsewhere) so the caller falls back to a fresh thread.
  const rehydrateFromPersistedThread = useCallback(async (persistedThreadId) => {
    const requestId = createUuid();
    let resolveMessages;
    const messagesPromise = new Promise((resolve) => {
      resolveMessages = resolve;
    });
    pendingRehydrateRef.current = { requestId, resolve: resolveMessages };

    const fetchRequest = dispatch(fetchChatMessages({
      chatsUrl: getApiUrl('chats'),
      threadId: persistedThreadId,
      limit: 50,
      requestId,
    }));

    let messages;
    try {
      await fetchRequest.unwrap();
      messages = await messagesPromise;
    } catch {
      if (pendingRehydrateRef.current?.requestId === requestId) {
        pendingRehydrateRef.current = null;
      }
      return false;
    }

    if (!isQuickChatMountedRef.current) {
      return true;
    }
    setHistory(messages.map((message) => ({
      id: message.id,
      role: message.role,
      content: stripPageContextBlock(message.content),
      timestamp: (typeof message.created_at === 'string' && Date.parse(message.created_at)) || Date.now(),
      isStreaming: false,
    })));
    setScratchThreadId(persistedThreadId);
    sessionPromiseRef.current = Promise.resolve(persistedThreadId);
    lastPersistedThreadIdRef.current = persistedThreadId;
    return true;
  }, [dispatch, getApiUrl]);

  // Spectator support: render live deltas for a generation this panel did not
  // itself dispatch -- e.g. the same thread open in the main Chat page (or
  // another tab) was sent a message. `isSending` already covers "this panel
  // is the one generating" (its own send flips it before `chat.response.started`
  // ever reaches Redux), so this only attaches for everyone else's sends.
  const spectatorCleanupRef = useRef(null);
  const spectatedRequestIdRef = useRef(null);

  useEffect(() => {
    const shouldSpectate = Boolean(scratchThreadId)
      && !isSending
      && generatingChatIds.includes(scratchThreadId);
    const requestId = shouldSpectate ? (requestIdByThreadId[scratchThreadId] || null) : null;

    if (!requestId) {
      if (spectatedRequestIdRef.current) {
        spectatorCleanupRef.current?.();
        spectatorCleanupRef.current = null;
        spectatedRequestIdRef.current = null;
        // The spectated generation just ended -- swap the live bubble for
        // the persisted, canonical transcript, same as a refresh rehydrate.
        void rehydrateFromPersistedThread(scratchThreadId);
      }
      return;
    }
    if (spectatedRequestIdRef.current === requestId) {
      return;
    }
    spectatorCleanupRef.current?.();

    const existingAssistant = [...history].reverse().find(
      (turn) => turn.role === 'assistant' && turn.isStreaming,
    );
    const { assistantId, assistantContent: initialAssistantContent } = createSpectatorReplayState(
      existingAssistant,
      requestId,
    );
    let assistantContent = initialAssistantContent;
    spectatedRequestIdRef.current = requestId;
    if (!existingAssistant) {
      setHistory((currentHistory) => currentHistory.concat({
        id: assistantId,
        role: 'assistant',
        content: '',
        timestamp: Date.now(),
        isStreaming: true,
      }));
    }

    spectatorCleanupRef.current = observeResponseStream(requestId, {
      onChunk: (chunk) => {
        if (spectatedRequestIdRef.current !== requestId) return;
        assistantContent += chunk;
        setHistory((currentHistory) => currentHistory.map((turn) => (
          turn.id === assistantId ? { ...turn, content: assistantContent } : turn
        )));
      },
    });
  }, [scratchThreadId, isSending, generatingChatIds, requestIdByThreadId, history, observeResponseStream, rehydrateFromPersistedThread]);

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

  // Mirror the transcript and its active streaming turn into module-level
  // memory so closing the panel does not discard an in-flight response.
  useEffect(() => {
    quickChatMemory.history = history;
  }, [history]);
  useEffect(() => {
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
    return () => {
      activeAssistantIdRef.current = null;
      // If a rehydrate was still in flight, this abort leaves
      // sessionPromiseRef resolved to null with nothing to retry it -- reset
      // the "already decided" guard below so a remount (a real popover
      // reopen, or React StrictMode's dev-only double-invoke of mount
      // effects) starts over instead of treating the aborted attempt as done.
      if (pendingRehydrateRef.current) {
        hasInitializedThreadRef.current = false;
      }
      pendingRehydrateRef.current?.resolve([]);
      pendingRehydrateRef.current = null;
      isQuickChatMountedRef.current = false;
    };
  }, []);

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
      // Closed mid-generation: the cached turn is still marked streaming but
      // this mount owns no stream for it. A generation still running is picked
      // up by the spectator effect; a finished one has its transcript reloaded.
      const orphanedStream = quickChatMemory.history.some(
        (turn) => turn.role === 'assistant' && turn.isStreaming,
      );
      if (orphanedStream && !generatingChatIds.includes(threadId)) {
        void rehydrateFromPersistedThread(threadId).then((resumed) => {
          if (!resumed && isQuickChatMountedRef.current) {
            setHistory((current) => current.map((turn) => (
              turn.isStreaming ? { ...turn, isStreaming: false } : turn
            )));
          }
        });
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
    if ((!text && attachments.length === 0) || isSending) return;
    const attachmentIds = attachments.map((attachment) => attachment.id).filter(Boolean);

    const now = Date.now();
    const assistantId = `qchat_assistant_${now + 1}`;
    const streamHistory = history.map((turn) => ({
      role: turn.role,
      content: turn.content,
    }));
    const nextHistory = history.concat(
      { id: `qchat_user_${now}`, role: 'user', content: text, timestamp: now },
      {
        id: assistantId,
        role: 'assistant',
        content: '',
        timestamp: now + 1,
        isStreaming: true,
      },
    );
    activeAssistantIdRef.current = assistantId;
    setHistory(nextHistory);
    setIsSending(true);
    setError('');
    let terminalStatus = 'failed';
    let terminalError = '';

    try {
      const threadId = await ensureQuickThread();

      // Tell the agent what the user is looking at, without cluttering the
      // visible transcript: only the outgoing API payload for this one
      // message gets the block prepended, never the displayed bubble or
      // the prior-turn history resent on the next message. Only sent when
      // it's actually new information for this thread -- a fresh thread
      // (New chat, or a new page) always gets it; an unchanged page on an
      // established thread doesn't, since the agent already has it.
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

      let assistantContent = '';
      const isStale = () => activeAssistantIdRef.current !== assistantId;
      await sendMessageStream(
        outgoingText,
        streamHistory,
        (chunk) => {
          if (isStale()) return;
          assistantContent += chunk;
          setHistory((currentHistory) => currentHistory.map((turn) => (
            turn.id === assistantId
              ? { ...turn, content: assistantContent }
              : turn
          )));
        },
        (status) => {
          terminalStatus = status === 'cancelled' ? 'cancelled' : 'completed';
          if (isStale()) return;
          setHistory((currentHistory) => currentHistory.map((turn) => {
            if (turn.id !== assistantId) return turn;
            const content = assistantContent || turn.content || (status === 'cancelled'
              ? '(Response cancelled)'
              : '(No response text)');
            return {
              ...turn,
              content: status === 'cancelled' && content !== '(Response cancelled)'
                ? `${content} [Cancelled]`
                : content,
              isStreaming: false,
            };
          }));
          activeAssistantIdRef.current = null;
        },
        (streamError) => {
          terminalStatus = 'failed';
          terminalError = streamError.message;
          if (isStale()) return;
          setError(streamError.message);
          setHistory((currentHistory) => currentHistory.map((turn) => (
            turn.id === assistantId
              ? { ...turn, content: turn.content || `Error: ${streamError.message}`, isStreaming: false, error: true }
              : turn
          )));
          activeAssistantIdRef.current = null;
        },
        undefined,
        { thread_id: threadId, ...(attachmentIds.length > 0 ? { attachments: attachmentIds } : {}) },
      );

      if (!isStale()) {
        setHistory((currentHistory) => currentHistory.map((turn) => {
          if (turn.id !== assistantId) return turn;
          const content = assistantContent || turn.content || '(No response text)';
          return { ...turn, content, isStreaming: false };
        }));
        activeAssistantIdRef.current = null;
      }
    } catch (sendError) {
      terminalStatus = 'failed';
      terminalError = sendError.message;
      if (sendError.name !== 'AbortError' && activeAssistantIdRef.current === assistantId) {
        setError(sendError.message);
        setHistory((currentHistory) => currentHistory.map((turn) => (
          turn.id === assistantId
            ? { ...turn, content: turn.content || `Error: ${sendError.message}`, isStreaming: false, error: true }
            : turn
        )));
      }
    } finally {
      if (activeAssistantIdRef.current === assistantId) {
        activeAssistantIdRef.current = null;
      }
      setIsSending(false);
      onSettled?.(terminalStatus, terminalError);
    }
  }, [ensureQuickThread, history, isSending, location.pathname, sendMessageStream]);
  useEffect(() => {
    if (!isSending || !pendingSubmission?.inline || consumedSubmissionIdRef.current === pendingSubmission.id) return;
    consumedSubmissionIdRef.current = pendingSubmission.id;
    onSubmissionConsumed?.(pendingSubmission.id);
    onInlineSubmissionSettled?.(pendingSubmission.clientId, 'failed', 'Quick chat is busy. Try again when it finishes.');
  }, [isSending, pendingSubmission, onSubmissionConsumed, onInlineSubmissionSettled]);
  useEffect(() => {
    if (!pendingSubmission?.message || isSending || consumedSubmissionIdRef.current === pendingSubmission.id) {
      return;
    }

    consumedSubmissionIdRef.current = pendingSubmission.id;
    onSubmissionConsumed?.(pendingSubmission.id);
    setComposerSeed((current) => ({ key: current.key + 1, message: '' }));
    void submit(pendingSubmission.message, [], pendingSubmission.inline
      ? (status, error) => onInlineSubmissionSettled?.(pendingSubmission.clientId, status, error)
      : undefined);
  }, [isSending, onInlineSubmissionSettled, onSubmissionConsumed, pendingSubmission, submit]);
  useEffect(() => {
    if (!pendingComposerDraft?.message) return;
    onComposerDraftConsumed?.(pendingComposerDraft.id);
    setComposerSeed((current) => ({ key: current.key + 1, message: pendingComposerDraft.message }));
  }, [pendingComposerDraft, onComposerDraftConsumed]);

  const openInChat = useCallback(() => {
    const hasQuickChatHandoff = history.length > 0;
    if (hasQuickChatHandoff) {
      const promotedMessages = history.map((turn, index) => ({
        id: `msg_quick_${Date.now()}_${index}`,
        role: turn.role,
        content: turn.content,
        timestamp: Number.isFinite(turn.timestamp) ? turn.timestamp : Date.now() + index,
        tokenCount: turn.role === 'assistant' ? turn.content.split(/\s+/).filter(Boolean).length : 0,
        reasoningTokenCount: 0,
        reasoningText: '',
        streamEvents: [],
      }));
      localStorage.setItem(config.storage.chatHistory, JSON.stringify(promotedMessages));
      localStorage.setItem(
        config.storage.quickChatHandoff,
        JSON.stringify({
          messages: promotedMessages,
          threadId: scratchThreadId,
        }),
      );
      window.dispatchEvent(new CustomEvent('progena:load-chat-history', {
        detail: {
          messages: promotedMessages,
          threadId: scratchThreadId,
        },
      }));
    }
    onClose();
    if (!isOnChatPage) {
      navigate('/chat', hasQuickChatHandoff ? { state: { quickChatHandoff: true } } : undefined);
    }
  }, [config.storage.chatHistory, config.storage.quickChatHandoff, history, isOnChatPage, navigate, onClose, scratchThreadId]);

  const resetQuickChat = useCallback(() => {
    void cancelStream();
    activeAssistantIdRef.current = null;
    quickChatMemory.history = [];
    quickChatMemory.threadId = null;
    setHistory([]);
    sessionPromiseRef.current = null;
    setIsSending(false);
    setError('');
    setComposerSeed((current) => ({ key: current.key + 1, message: '' }));
    startFreshQuickThread();
  }, [cancelStream, startFreshQuickThread]);

  const selectPrompt = useCallback((prompt) => {
    setComposerSeed((current) => ({ key: current.key + 1, message: prompt }));
  }, []);

  const stopQuickChat = useCallback(() => {
    const assistantId = activeAssistantIdRef.current;
    cancelStream();
    activeAssistantIdRef.current = null;
    setIsSending(false);
    if (!assistantId) return;

    setHistory((currentHistory) => currentHistory
      .map((turn) => {
        if (turn.id !== assistantId) return turn;
        if (!turn.content) return null;
        return { ...turn, content: `${turn.content} [Cancelled]`, isStreaming: false };
      })
      .filter(Boolean));
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
            <RefreshIcon />
          </PanelIconButton>
          <PanelIconButton label="Open in chat" onClick={openInChat} disabled={isSending}>
            <OpenChatIcon />
          </PanelIconButton>
        </>
      )}
    >
      <div className={`qchat-pane__scroll${history.length === 0 && !isSending && !error ? ' is-empty' : ''}`}>
        {history.map((turn, index) => (
          <article key={turn.id ?? `${turn.role}-${index}`} className="qchat-turn">
            <div className={`${turn.role === 'user' ? 'qchat-user' : 'qchat-assistant'}${turn.isStreaming && !turn.content ? ' is-loading' : ''}${turn.error ? ' is-error' : ''}`}>
              {turn.isStreaming && !turn.content ? (
                <>
                  Thinking
                  <span className="qchat-typing"><span /><span /><span /></span>
                </>
              ) : (
                <MarkdownContent content={turn.content} hiddenWhenEmpty />
              )}
            </div>
          </article>
        ))}
        {error ? <div className="qchat-assistant is-error">{error}</div> : null}
        <div ref={scrollEndRef} />
      </div>
      {history.length === 0 && !isSending && !error ? (
        <div className="qchat-pane__hints" aria-label="Quick chat suggestions">
          {normalizedConfig.prompts.map((prompt) => (
            <button key={prompt} type="button" className="qchat-chip" onClick={() => selectPrompt(prompt)}>
              {prompt}
            </button>
          ))}
        </div>
      ) : null}
      <ChatInput
        key={composerSeed.key}
        className="qchat-input"
        placeholder={normalizedConfig.placeholder}
        onSend={submit}
        onStop={stopQuickChat}
        isGenerating={isSending}
        disabled={false}
        enableSlashCommands={false}
        allowStop
        autoFocus={!isBackground}
        keepFocusAfterSend
        initialMessage={composerSeed.message}
        enableAttachments
      />
    </TopNavPane>
  );
}
