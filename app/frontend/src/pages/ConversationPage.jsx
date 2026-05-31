import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useApi } from '../hooks/useApi';
import { useEventsSSE } from '../hooks/useSSE';
import { useConfig } from '../context/ConfigContext';
import { useNotifications } from '../context/NotificationContext';
import ChatInput from '../components/chat/ChatInput';
import { Message } from '../components/chat/Message';
import TypingIndicator from '../components/chat/TypingIndicator';
import QuickActions from '../components/chat/QuickActions';
import StreamFilterMenu from '../components/chat/StreamFilterMenu';
import {
  CHAT_AGENT_NAME,
  buildSessionItems,
  deriveChatTitle,
  formatClockTime,
} from '../components/chat/conversationModel';
import {
  PERSISTED_CATEGORIES,
  STREAM_EVENT_CATEGORIES,
  categoryForEventType,
  isEventVisible,
  loadStreamFilters,
} from '../components/chat/streamFilters';

const MAX_STREAM_EVENTS_PER_MESSAGE = 200;
const CANCELLED_SUFFIX = ' [Cancelled]';
const ARCHIVED_CHAT_SESSIONS_KEY = 'progenitor:archived-chat-sessions';

function normalizeStreamEvent(event, index) {
  if (!event || typeof event !== 'object') {
    return null;
  }

  return {
    id: typeof event.id === 'string' ? event.id : `evt_restored_${index}_${Date.now()}`,
    eventType: typeof event.eventType === 'string'
      ? event.eventType
      : (typeof event.event_type === 'string' ? event.event_type : 'stream_event'),
    content: typeof event.content === 'string' ? event.content : '',
    metadata: event.metadata && typeof event.metadata === 'object' && !Array.isArray(event.metadata)
      ? event.metadata
      : {},
  };
}

function isReasoningEvent(event) {
  return event?.eventType === 'reasoning_delta' || event?.eventType === 'reasoning_summary';
}

function hasReasoningContent(event) {
  return typeof event?.content === 'string' && event.content.trim().length > 0;
}

function extractReasoningTokenCount(event) {
  const tokenCount = event?.metadata?.reasoning_tokens;
  return Number.isFinite(tokenCount) && tokenCount > 0 ? tokenCount : null;
}

function appendReasoningText(currentText, event) {
  if (!isReasoningEvent(event) || !hasReasoningContent(event)) {
    return currentText;
  }

  const existingText = typeof currentText === 'string' ? currentText : '';
  if (event.eventType === 'reasoning_delta') {
    return `${existingText}${event.content}`;
  }

  if (existingText.trim().length === 0) {
    return event.content;
  }
  return event.content.length >= existingText.length ? event.content : existingText;
}

function mergeReasoningEvents(existingEvent, nextEvent) {
  const existingContent = hasReasoningContent(existingEvent) ? existingEvent.content : '';
  let mergedContent = existingContent;

  if (nextEvent.eventType === 'reasoning_delta') {
    mergedContent = `${existingContent}${nextEvent.content}`;
  } else if (hasReasoningContent(nextEvent)) {
    mergedContent = nextEvent.content;
  }

  return {
    ...existingEvent,
    eventType: nextEvent.eventType === 'reasoning_summary'
      ? 'reasoning_summary'
      : existingEvent.eventType,
    content: mergedContent,
    metadata: {
      ...existingEvent.metadata,
      ...nextEvent.metadata,
    },
  };
}

function normalizeStoredMessage(message, index) {
  if (!message || typeof message !== 'object') {
    return null;
  }

  const role = typeof message.role === 'string' ? message.role : 'assistant';
  const timestamp = typeof message.timestamp === 'number' ? message.timestamp : Date.now();
  let content = message.content;

  if (typeof content !== 'string') {
    if (content === null || content === undefined) {
      content = '';
    } else {
      try {
        content = JSON.stringify(content);
      } catch {
        content = String(content);
      }
    }
  }

  return {
    ...message,
    id: typeof message.id === 'string' ? message.id : `msg_restored_${index}_${timestamp}`,
    role,
    content,
    tokenCount: Number.isFinite(message.tokenCount) ? message.tokenCount : 0,
    reasoningTokenCount: Number.isFinite(message.reasoningTokenCount)
      ? message.reasoningTokenCount
      : 0,
    reasoningText: typeof message.reasoningText === 'string' ? message.reasoningText : '',
    streamEvents: Array.isArray(message.streamEvents)
      ? message.streamEvents.reduce((events, event, eventIndex) => {
        const normalizedEvent = normalizeStreamEvent(event, eventIndex);
        if (!normalizedEvent) {
          return events;
        }
        return appendStreamEvent(events, normalizedEvent);
      }, [])
      : [],
    timestamp,
  };
}

function loadQuickChatHandoff(storageKey) {
  const saved = localStorage.getItem(storageKey);
  if (!saved) {
    return null;
  }

  try {
    const parsed = JSON.parse(saved);
    if (!parsed || typeof parsed !== 'object') {
      return null;
    }
    return {
      messages: Array.isArray(parsed.messages) ? parsed.messages : [],
      sessionId: typeof parsed.sessionId === 'string' ? parsed.sessionId : null,
    };
  } catch (error) {
    console.warn('Failed to load quick chat handoff:', error);
    return null;
  }
}

function createStreamEvent(streamEvent) {
  if (!streamEvent || typeof streamEvent !== 'object') {
    return null;
  }

  return {
    id: `evt_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    eventType: typeof streamEvent.event_type === 'string' ? streamEvent.event_type : 'stream_event',
    content: typeof streamEvent.content === 'string' ? streamEvent.content : '',
    metadata:
      streamEvent.metadata
      && typeof streamEvent.metadata === 'object'
      && !Array.isArray(streamEvent.metadata)
        ? streamEvent.metadata
        : {},
  };
}

function appendStreamEvent(events, nextEvent) {
  const currentEvents = Array.isArray(events) ? events : [];
  if (nextEvent.eventType === 'message_delta') {
    return currentEvents;
  }
  if (isReasoningEvent(nextEvent)) {
    const lastIndex = currentEvents.length - 1;
    const lastEvent = currentEvents[lastIndex];
    if (isReasoningEvent(lastEvent)) {
      return currentEvents.map((event, index) => (
        index === lastIndex ? mergeReasoningEvents(lastEvent, nextEvent) : event
      ));
    }
    if (!hasReasoningContent(nextEvent)) {
      return currentEvents;
    }
  }
  const lastEvent = currentEvents[currentEvents.length - 1];
  if (
    lastEvent
    && lastEvent.eventType === nextEvent.eventType
    && lastEvent.content === nextEvent.content
    && JSON.stringify(lastEvent.metadata) === JSON.stringify(nextEvent.metadata)
  ) {
    return currentEvents;
  }
  const next = currentEvents.concat(nextEvent);
  // Cap timeline events so a long run cannot overwhelm the chat transcript.
  const eventLimit = MAX_STREAM_EVENTS_PER_MESSAGE;
  let eventCount = 0;
  for (let i = next.length - 1; i >= 0; i -= 1) {
    eventCount += 1;
    if (eventCount > eventLimit) {
      next.splice(i, 1);
    }
  }
  return next;
}

function stripEphemeralStreamEvents(message) {
  const events = Array.isArray(message?.streamEvents) ? message.streamEvents : [];
  if (events.length === 0) return message;
  const persistable = events.filter((event) =>
    PERSISTED_CATEGORIES.has(categoryForEventType(event.eventType))
  );
  if (persistable.length === events.length) {
    return message;
  }
  return { ...message, streamEvents: persistable };
}

function finalizeSupersededAssistant(message) {
  if (!message?.isStreaming) {
    return message;
  }

  const content = typeof message.content === 'string' ? message.content : '';
  const reasoningText = typeof message.reasoningText === 'string' ? message.reasoningText : '';
  const streamEvents = Array.isArray(message.streamEvents) ? message.streamEvents : [];
  const hasVisibleOutput =
    content.trim().length > 0
    || reasoningText.trim().length > 0
    || streamEvents.length > 0;

  if (!hasVisibleOutput) {
    return null;
  }

  return {
    ...message,
    isStreaming: false,
    content: content.endsWith(CANCELLED_SUFFIX) ? content : `${content}${CANCELLED_SUFFIX}`,
  };
}

function ConversationPage() {
  const { config } = useConfig();
  const {
    connectionState,
    sendMessageStream,
    cancelStream,
    getSession,
    createSession,
  } = useApi();
  const { show } = useNotifications();
  const initialQuickChatHandoffRef = useRef(undefined);
  if (initialQuickChatHandoffRef.current === undefined) {
    initialQuickChatHandoffRef.current = loadQuickChatHandoff(config.storage.quickChatHandoff);
  }
  const initialQuickChatHandoff = initialQuickChatHandoffRef.current;

  const [messages, setMessages] = useState(() => {
    if (Array.isArray(initialQuickChatHandoff?.messages) && initialQuickChatHandoff.messages.length > 0) {
      return initialQuickChatHandoff.messages
        .map((message, index) => normalizeStoredMessage(message, index))
        .filter(Boolean);
    }

    const saved = localStorage.getItem(config.storage.chatHistory);
    if (!saved) {
      return [];
    }

    try {
      const parsed = JSON.parse(saved);
      if (!Array.isArray(parsed)) {
        return [];
      }
      return parsed
        .map((message, index) => normalizeStoredMessage(message, index))
        .filter(Boolean);
    } catch (error) {
      console.warn('Failed to load chat history:', error);
      return [];
    }
  });
  const [isGenerating, setIsGenerating] = useState(false);
  const [isClearingSession, setIsClearingSession] = useState(false);
  const [typingState, setTypingState] = useState('thinking');
  const [tokenCount, setTokenCount] = useState(0);
  const [sessionId, setSessionId] = useState(() => (
    typeof initialQuickChatHandoff?.sessionId === 'string' ? initialQuickChatHandoff.sessionId : null
  ));
  const [chatMenuOpen, setChatMenuOpen] = useState(false);
  const [streamFilters, setStreamFilters] = useState(() =>
    loadStreamFilters(config.storage.streamFilters),
  );
  const messagesEndRef = useRef(null);
  const activeAssistantIdRef = useRef(null);
  const chatMenuRef = useRef(null);

  const streamEventFilter = useCallback(
    (event) => isEventVisible(event, streamFilters),
    [streamFilters],
  );

  const updateStreamFilters = useCallback(
    (nextFilters) => {
      setStreamFilters(nextFilters);
      try {
        localStorage.setItem(config.storage.streamFilters, JSON.stringify(nextFilters));
      } catch {
        // Keep UI responsive if browser storage is unavailable.
      }
    },
    [config.storage.streamFilters],
  );

  useEffect(() => {
    if (!chatMenuOpen) return undefined;

    const handlePointerDown = (event) => {
      if (chatMenuRef.current && !chatMenuRef.current.contains(event.target)) {
        setChatMenuOpen(false);
      }
    };
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        setChatMenuOpen(false);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [chatMenuOpen]);

  useEffect(() => {
    let cancelled = false;

    const ensureSession = async () => {
      const quickChatHandoff = initialQuickChatHandoffRef.current;
      if (quickChatHandoff) {
        localStorage.removeItem(config.storage.quickChatHandoff);
        initialQuickChatHandoffRef.current = null;
        if (quickChatHandoff.sessionId) {
          setSessionId(quickChatHandoff.sessionId);
          return;
        }
      }

      try {
        const session = await getSession();
        if (!cancelled) {
          setSessionId(session.session_id);
        }
      } catch {
        try {
          const session = await createSession();
          if (!cancelled) {
            setSessionId(session.session_id);
          }
        } catch (error) {
          console.warn('[ConversationPage] Failed to initialize session:', error);
        }
      }
    };

    ensureSession();
    return () => {
      cancelled = true;
    };
  }, [config.storage.quickChatHandoff, createSession, getSession]);

  useEffect(() => {
    const handlePromotedQuickChat = (event) => {
      const promotedMessages = Array.isArray(event.detail?.messages)
        ? event.detail.messages
        : [];
      if (promotedMessages.length === 0) {
        return;
      }

      const normalizedMessages = promotedMessages
        .map((message, index) => normalizeStoredMessage(message, index))
        .filter(Boolean);
      if (normalizedMessages.length === 0) {
        return;
      }

      activeAssistantIdRef.current = null;
      setMessages(normalizedMessages);
      setIsGenerating(false);
      setTypingState('thinking');
      setTokenCount(0);
      if (typeof event.detail?.sessionId === 'string') {
        setSessionId(event.detail.sessionId);
      }
      localStorage.removeItem(config.storage.quickChatHandoff);
    };

    window.addEventListener('progenitor:load-chat-history', handlePromotedQuickChat);
    return () => {
      window.removeEventListener('progenitor:load-chat-history', handlePromotedQuickChat);
    };
  }, [config.storage.quickChatHandoff]);

  // Save chat history to localStorage (strip ephemeral stream events to
  // keep storage bounded; debug events are session-only).
  useEffect(() => {
    if (messages.length > 0) {
      const persistable = messages.slice(-100).map(stripEphemeralStreamEvents);
      localStorage.setItem(config.storage.chatHistory, JSON.stringify(persistable));
    }
  }, [messages, config.storage.chatHistory]);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' });
  }, [messages, isGenerating]);

  // Track recently added scheduled messages to deduplicate
  const recentScheduledContent = useRef(new Set());

  // Handle chat events (from /chat SSE stream)
  const handleChatEvent = useCallback((event) => {
    if (!event?.data) return;

    // Handle scheduled/background messages from the agent.
    // Prefer agent_message/agent_question over scheduled_response as they have cleaner content.
    if (
      event.type === 'agent_message'
      || event.type === 'agent_question'
      || event.type === 'scheduled_response'
    ) {
      const content = event.data?.content || '';
      const metadata = event.data?.metadata || {};
      const isQuestion = event.type === 'agent_question';

      // Clean up the content (remove "SAY: " prefix if present)
      let cleanContent = content;
      if (cleanContent.startsWith('SAY: ')) {
        cleanContent = cleanContent.slice(5);
      }

      if (cleanContent) {
        // Deduplicate: check if we recently added a message with the same content
        const contentKey = cleanContent.slice(0, 100);
        if (recentScheduledContent.current.has(contentKey)) {
          // Already added this message, skip
          return;
        }

        // Mark as recently added (auto-expire after 5 seconds)
        recentScheduledContent.current.add(contentKey);
        setTimeout(() => {
          recentScheduledContent.current.delete(contentKey);
        }, 5000);

        const scheduledMessage = {
          id: `msg_${Date.now()}_scheduled`,
          role: 'assistant',
          content: cleanContent,
          timestamp: Date.now(),
          isScheduled: true,
          isQuestion,
          scheduleId: metadata?.schedule_id,
          originalRequest: metadata?.original_request,
        };

        setMessages(prev => [...prev, scheduledMessage]);

        // Show a notification
        show(
          isQuestion ? 'warning' : 'info',
          metadata?.title || (isQuestion ? 'Question from Progenitor' : 'Scheduled Message'),
          metadata?.schedule_id
            ? `Reminder: ${cleanContent.slice(0, 50)}...`
            : cleanContent.slice(0, 100)
        );
      }
    }

    // Handle alerts
    if (event.type === 'alert') {
      show(event.data?.type || 'info', event.data?.title || 'Alert', event.data?.message || '');
    }

    if (event.type === 'message_queued') {
      show(
        'info',
        'Message queued',
        event.data?.content || 'A previous message is still running; your message will be handled next.'
      );
    }
  }, [show]);

  // Connect to chat SSE stream
  useEventsSSE(handleChatEvent);

  const handleSend = useCallback(async (content) => {
    const userMessage = {
      id: `msg_${Date.now()}`,
      role: 'user',
      content,
      timestamp: Date.now()
    };

    setMessages(prev => [...prev, userMessage]);
    setIsGenerating(true);
    setTypingState('thinking');
    setTokenCount(0);

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

    try {
      const isStale = () => activeAssistantIdRef.current !== assistantId;
      await sendMessageStream(
        content,
        history,
        // onChunk
        (chunk) => {
          if (isStale()) return;
          setTypingState('generating');
          assistantContent += chunk;
          setTokenCount(prev => prev + 1);
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? { ...m, content: assistantContent, tokenCount: (m.tokenCount || 0) + 1 }
              : m
          ));
        },
        // onComplete
        (status) => {
          if (isStale()) return;
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? { ...m, isStreaming: false, content: assistantContent + (status === 'cancelled' ? ' [Cancelled]' : '') }
              : m
          ));
          setIsGenerating(false);
        },
        // onError
        (error) => {
          if (isStale()) return;
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

          if (nextEvent.eventType === 'reasoning_delta') {
            setTypingState('thinking');
          } else if (
            nextEvent.eventType !== 'message_delta'
            && nextEvent.eventType !== 'reasoning_summary'
          ) {
            setTypingState('tool-use');
          }
          const reasoningTokenCount = extractReasoningTokenCount(nextEvent);
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? {
                ...m,
                streamEvents: appendStreamEvent(m.streamEvents, nextEvent),
                reasoningText: appendReasoningText(m.reasoningText, nextEvent),
                reasoningTokenCount: reasoningTokenCount ?? m.reasoningTokenCount ?? 0,
              }
              : m
          ));
        },
      );
    } catch (error) {
      show('error', 'Chat Error', error.message);
      setIsGenerating(false);
    }
  }, [messages, sendMessageStream, show]);

  const handleStop = useCallback(() => {
    cancelStream();
  }, [cancelStream]);

  const handleQuickAction = useCallback((message) => {
    handleSend(message);
  }, [handleSend]);

  const startNewChatSession = useCallback(async ({ archive = false } = {}) => {
    const previousAssistantId = activeAssistantIdRef.current;
    const archivedMessages = messages.map((message) => stripEphemeralStreamEvents(message));
    setIsClearingSession(true);
    setChatMenuOpen(false);
    activeAssistantIdRef.current = null;
    recentScheduledContent.current.clear();
    cancelStream();

    try {
      const session = await createSession();
      let archiveError = null;
      if (archive && archivedMessages.length > 0) {
        try {
          const archiveEntry = {
            id: `archived_${Date.now()}`,
            sessionId,
            title: deriveChatTitle(archivedMessages),
            archivedAt: new Date().toISOString(),
            tokenCount: archivedMessages.reduce(
              (total, message) => total + (Number.isFinite(message.tokenCount) ? message.tokenCount : 0),
              0,
            ),
            messages: archivedMessages,
          };
          const currentArchive = JSON.parse(localStorage.getItem(ARCHIVED_CHAT_SESSIONS_KEY) || '[]');
          const nextArchive = Array.isArray(currentArchive)
            ? [archiveEntry, ...currentArchive].slice(0, 25)
            : [archiveEntry];
          localStorage.setItem(ARCHIVED_CHAT_SESSIONS_KEY, JSON.stringify(nextArchive));
        } catch (error) {
          archiveError = error;
        }
      }
      setMessages([]);
      setIsGenerating(false);
      setTypingState('thinking');
      setTokenCount(0);
      setSessionId(session.session_id);
      localStorage.removeItem(config.storage.chatHistory);
      if (archiveError) {
        show('error', 'Archive failed', archiveError.message);
      }
    } catch (error) {
      if (previousAssistantId) {
        setMessages((prev) => prev
          .map((message) => (
            message.id === previousAssistantId
              ? finalizeSupersededAssistant(message)
              : message
          ))
          .filter(Boolean));
      }
      setIsGenerating(false);
      setTypingState('thinking');
      setTokenCount(0);
      show('error', 'New chat failed', error.message);
    } finally {
      setIsClearingSession(false);
    }
  }, [cancelStream, config.storage.chatHistory, createSession, messages, sessionId, show]);

  const handleCommand = useCallback(async (commandName) => {
    if (commandName !== 'new') {
      return;
    }

    await startNewChatSession();
  }, [startNewChatSession]);

  const chatTitle = useMemo(() => deriveChatTitle(messages), [messages]);
  const totalTokenCount = useMemo(
    () => messages.reduce((total, message) => total + (Number.isFinite(message.tokenCount) ? message.tokenCount : 0), 0),
    [messages],
  );
  const firstMessageTime = useMemo(
    () => messages.find((message) => Number.isFinite(message.timestamp))?.timestamp,
    [messages],
  );
  const visibleEventCount = useMemo(
    () => messages.reduce((total, message) => (
      total + (Array.isArray(message.streamEvents) ? message.streamEvents.filter(streamEventFilter).length : 0)
    ), 0),
    [messages, streamEventFilter],
  );
  const sessionItems = useMemo(
    () => buildSessionItems(messages, sessionId, chatTitle, totalTokenCount),
    [chatTitle, messages, sessionId, totalTokenCount],
  );
  const activeFilterCount = useMemo(
    () => STREAM_EVENT_CATEGORIES.filter((category) => streamFilters[category.id]).length,
    [streamFilters],
  );

  return (
    <div className="chat-page">
      <aside className="chat-sessions-pane" aria-label="Conversation sessions">
        <div className="chat-sessions-pane__head">
          <div className="chat-sessions-pane__title">
            Sessions <span className="muted">{sessionItems.length}</span>
          </div>
          <button
            type="button"
            className="chat-pane-action"
            onClick={() => startNewChatSession()}
            disabled={isClearingSession}
          >
            + New
          </button>
        </div>
        <div className="chat-pane-tabs" role="tablist" aria-label="Session filters">
          <button type="button" className="chat-pane-tab is-active">All</button>
          <button type="button" className="chat-pane-tab">Mine</button>
          <button type="button" className="chat-pane-tab">Pinned</button>
          <button type="button" className="chat-pane-tab chat-pane-tab--sort">Recent</button>
        </div>
        <div className="chat-sessions-pane__scroll">
          <div className="chat-pane-group">
            <div className="chat-pane-group__head">Conversations</div>
            {sessionItems.map((item) => (
              <article
                key={item.id}
                className={`chat-session-card${item.selected ? ' is-selected' : ''}`}
              >
                <div className="chat-session-card__title">{item.title}</div>
                <div className="chat-session-card__meta">
                  <span className="chat-session-card__age">{item.age}</span>
                </div>
                <div className="chat-session-card__preview">{item.preview}</div>
                {Number.isFinite(item.tokenCount) && item.tokenCount > 0 && (
                  <div className="chat-session-card__foot">
                    <span>{new Intl.NumberFormat().format(item.tokenCount)} tokens</span>
                  </div>
                )}
              </article>
            ))}
          </div>
        </div>
      </aside>

      <section className="chat-container" aria-label="Chat conversation">
        <header className="chat-page-header">
          <div className="chat-page-header__inner">
            <div className="chat-page-header__title-row">
              <h1 className="chat-page-header__title">{chatTitle}</h1>
              <button type="button" className="chat-icon-button" title="Rename conversation">
                <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                  <path d="M2 14h3l8-8-3-3-8 8z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
                </svg>
              </button>
              <span className="chat-page-header__spacer" />
              <StreamFilterMenu
                storageKey={config.storage.streamFilters}
                filters={streamFilters}
                onChange={updateStreamFilters}
              />
              <div className="chat-options-menu" ref={chatMenuRef}>
                <button
                  type="button"
                  className="chat-icon-button"
                  title="More options"
                  aria-haspopup="menu"
                  aria-expanded={chatMenuOpen}
                  onClick={() => setChatMenuOpen((open) => !open)}
                >
                  <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                    <circle cx="3.5" cy="8" r="1.2" fill="currentColor" />
                    <circle cx="8" cy="8" r="1.2" fill="currentColor" />
                    <circle cx="12.5" cy="8" r="1.2" fill="currentColor" />
                  </svg>
                </button>
                {chatMenuOpen && (
                  <div className="chat-options-dropdown" role="menu" aria-label="Conversation options">
                    <button
                      type="button"
                      className="chat-options-item"
                      role="menuitem"
                      onClick={() => startNewChatSession({
                        archive: true,
                      })}
                      disabled={isClearingSession || messages.length === 0}
                    >
                      Archive
                    </button>
                    <button
                      type="button"
                      className="chat-options-item chat-options-item--danger"
                      role="menuitem"
                      onClick={() => startNewChatSession()}
                      disabled={isClearingSession}
                    >
                      Delete
                    </button>
                  </div>
                )}
              </div>
            </div>
            <div className="chat-page-header__meta">
              <span className="chat-page-header__model">
                <span className="agent-dot agent-dot--purple" />
                {CHAT_AGENT_NAME}
              </span>
              <span className="chat-separator">|</span>
              <span className="muted">Started {formatClockTime(firstMessageTime) || 'not started'}</span>
              <span className="chat-separator">|</span>
              <span className="muted">{new Intl.NumberFormat().format(totalTokenCount)} tokens</span>
              <span className="chat-separator">|</span>
              <span className="muted">Events {visibleEventCount}</span>
              <span className="chat-separator">|</span>
              <span className="muted">Filters {activeFilterCount}/{STREAM_EVENT_CATEGORIES.length}</span>
              {sessionId && (
                <>
                  <span className="chat-separator">|</span>
                  <span className="chat-session-id">{sessionId}</span>
                </>
              )}
            </div>
          </div>
        </header>

        <div className="chat-messages sess-detail__body chat-detail-body">
          <div className="chat-messages__inner">
            {messages.length === 0 ? (
              <div className="welcome-message">
                <h2>Welcome to Progenitor Chat</h2>
                <p>Start a conversation with the Progenitor AI agent.</p>
                {sessionId && <p>Session: {sessionId}</p>}
                <QuickActions onAction={handleQuickAction} />
              </div>
            ) : (
              <>
                {messages.map((msg) => (
                  <Message
                    key={msg.id}
                    message={msg}
                    streamEventFilter={streamEventFilter}
                  />
                ))}
                {isGenerating && (
                  <TypingIndicator state={typingState} tokenCount={tokenCount} />
                )}
              </>
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>

        <ChatInput
          onSend={handleSend}
          onStop={handleStop}
          onCommand={handleCommand}
          isGenerating={isGenerating}
          disabled={connectionState === 'error' || isClearingSession}
          keepFocusAfterSend
        />
      </section>
    </div>
  );
}

export default ConversationPage;
