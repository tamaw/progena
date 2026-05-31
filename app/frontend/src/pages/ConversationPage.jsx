import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useApi } from '../hooks/useApi';
import { useEventsSSE } from '../hooks/useSSE';
import { useConfig } from '../context/ConfigContext';
import { useNotifications } from '../context/NotificationContext';
import ChatInput from '../components/chat/ChatInput';
import { Message } from '../components/chat/Message';
import QuickActions from '../components/chat/QuickActions';
import StreamFilterMenu from '../components/chat/StreamFilterMenu';
import {
  CHAT_AGENT_NAME,
  buildSessionItems,
  countToolInvocations,
  deriveChatTitle,
  formatStartedTime,
  groupSessionItems,
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
const CUSTOM_CHAT_TITLE_KEY = 'progenitor:chat-custom-title';
const PINNED_CHAT_SESSIONS_KEY = 'progenitor:pinned-chat-sessions';
const SHARED_CHAT_SESSIONS_KEY = 'progenitor:shared-chat-sessions';

function loadStoredArchivedChatSessions() {
  const saved = localStorage.getItem(ARCHIVED_CHAT_SESSIONS_KEY);
  if (!saved) {
    return [];
  }

  try {
    const parsed = JSON.parse(saved);
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    console.warn('Failed to load archived chat sessions:', error);
    return [];
  }
}

function loadStoredPinnedSessionIds() {
  const saved = localStorage.getItem(PINNED_CHAT_SESSIONS_KEY);
  if (!saved) {
    return [];
  }

  try {
    const parsed = JSON.parse(saved);
    return Array.isArray(parsed)
      ? parsed.filter((value) => typeof value === 'string' && value)
      : [];
  } catch (error) {
    console.warn('Failed to load pinned chat sessions:', error);
    return [];
  }
}

function persistPinnedSessionIds(pinnedSessionIds) {
  if (!Array.isArray(pinnedSessionIds) || pinnedSessionIds.length === 0) {
    localStorage.removeItem(PINNED_CHAT_SESSIONS_KEY);
    return;
  }

  localStorage.setItem(PINNED_CHAT_SESSIONS_KEY, JSON.stringify(pinnedSessionIds));
}

function loadStoredSharedSessionIds() {
  const saved = localStorage.getItem(SHARED_CHAT_SESSIONS_KEY);
  if (!saved) {
    return [];
  }

  try {
    const parsed = JSON.parse(saved);
    return Array.isArray(parsed)
      ? parsed.filter((value) => typeof value === 'string' && value)
      : [];
  } catch (error) {
    console.warn('Failed to load shared chat sessions:', error);
    return [];
  }
}

function persistSharedSessionIds(sharedSessionIds) {
  if (!Array.isArray(sharedSessionIds) || sharedSessionIds.length === 0) {
    localStorage.removeItem(SHARED_CHAT_SESSIONS_KEY);
    return;
  }

  localStorage.setItem(SHARED_CHAT_SESSIONS_KEY, JSON.stringify(sharedSessionIds));
}

function loadStoredCustomChatTitle() {
  const saved = localStorage.getItem(CUSTOM_CHAT_TITLE_KEY);
  if (!saved) {
    return { sessionId: null, title: '' };
  }

  try {
    const parsed = JSON.parse(saved);
    if (!parsed || typeof parsed !== 'object') {
      return { sessionId: null, title: '' };
    }
    return {
      sessionId: typeof parsed.sessionId === 'string' ? parsed.sessionId : null,
      title: typeof parsed.title === 'string' ? parsed.title : '',
    };
  } catch (error) {
    console.warn('Failed to load custom chat title:', error);
    return { sessionId: null, title: '' };
  }
}

function persistCustomChatTitle(sessionId, title) {
  const normalizedTitle = typeof title === 'string' ? title.trim() : '';
  if (!normalizedTitle) {
    localStorage.removeItem(CUSTOM_CHAT_TITLE_KEY);
    return;
  }

  localStorage.setItem(CUSTOM_CHAT_TITLE_KEY, JSON.stringify({
    sessionId: typeof sessionId === 'string' ? sessionId : null,
    title: normalizedTitle,
  }));
}

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
  const initialStoredCustomTitleRef = useRef(undefined);
  if (initialStoredCustomTitleRef.current === undefined) {
    initialStoredCustomTitleRef.current = loadStoredCustomChatTitle();
  }
  const initialStoredCustomTitle = initialStoredCustomTitleRef.current;

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
  const [sessionId, setSessionId] = useState(() => (
    typeof initialQuickChatHandoff?.sessionId === 'string' ? initialQuickChatHandoff.sessionId : null
  ));
  const [chatMenuOpen, setChatMenuOpen] = useState(false);
  const [streamFilters, setStreamFilters] = useState(() =>
    loadStreamFilters(config.storage.streamFilters),
  );
  const [customChatTitle, setCustomChatTitle] = useState(() => initialStoredCustomTitle.title);
  const [customChatTitleSessionId, setCustomChatTitleSessionId] = useState(() => initialStoredCustomTitle.sessionId);
  const [archivedSessions, setArchivedSessions] = useState(() => loadStoredArchivedChatSessions());
  const [pinnedSessionIds, setPinnedSessionIds] = useState(() => loadStoredPinnedSessionIds());
  const [sharedSessionIds, setSharedSessionIds] = useState(() => loadStoredSharedSessionIds());
  const [sidebarFilter, setSidebarFilter] = useState('all');
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');
  const messagesEndRef = useRef(null);
  const activeAssistantIdRef = useRef(null);
  const chatMenuRef = useRef(null);
  const titleInputRef = useRef(null);

  const derivedChatTitle = useMemo(() => deriveChatTitle(messages), [messages]);
  const chatTitle = customChatTitle || derivedChatTitle;

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
    if (!isEditingTitle) {
      return undefined;
    }
    titleInputRef.current?.focus();
    titleInputRef.current?.select();
    return undefined;
  }, [isEditingTitle]);

  useEffect(() => {
    if (!sessionId) {
      return;
    }
    if (!customChatTitle) {
      if (customChatTitleSessionId && customChatTitleSessionId !== sessionId) {
        setCustomChatTitleSessionId(sessionId);
      }
      return;
    }
    if (!customChatTitleSessionId) {
      persistCustomChatTitle(sessionId, customChatTitle);
      setCustomChatTitleSessionId(sessionId);
      return;
    }
    if (customChatTitleSessionId !== sessionId) {
      setCustomChatTitle('');
      setCustomChatTitleSessionId(sessionId);
      setTitleDraft('');
      setIsEditingTitle(false);
      localStorage.removeItem(CUSTOM_CHAT_TITLE_KEY);
    }
  }, [customChatTitle, customChatTitleSessionId, sessionId]);

  const cancelTitleEdit = useCallback(() => {
    setTitleDraft(chatTitle);
    setIsEditingTitle(false);
  }, [chatTitle]);

  const startTitleEdit = useCallback(() => {
    setChatMenuOpen(false);
    setTitleDraft(chatTitle);
    setIsEditingTitle(true);
  }, [chatTitle]);

  const saveTitleEdit = useCallback(() => {
    const normalizedTitle = titleDraft.replace(/\s+/g, ' ').trim();
    if (!normalizedTitle) {
      cancelTitleEdit();
      return;
    }

    const nextCustomTitle = normalizedTitle === derivedChatTitle ? '' : normalizedTitle;
    setCustomChatTitle(nextCustomTitle);
    setCustomChatTitleSessionId(sessionId);
    setTitleDraft(nextCustomTitle || derivedChatTitle);
    setIsEditingTitle(false);

    if (nextCustomTitle) {
      persistCustomChatTitle(sessionId, nextCustomTitle);
    } else {
      localStorage.removeItem(CUSTOM_CHAT_TITLE_KEY);
    }
  }, [cancelTitleEdit, derivedChatTitle, sessionId, titleDraft]);

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
      setIsEditingTitle(false);
      setTitleDraft('');
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
          assistantContent += chunk;
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
          const archivedToolCount = countToolInvocations(archivedMessages);
          const startedAt = archivedMessages.find((message) => Number.isFinite(message.timestamp))?.timestamp ?? null;
          const lastActivityAt = [...archivedMessages]
            .reverse()
            .find((message) => Number.isFinite(message.timestamp))?.timestamp ?? startedAt;
          const archiveEntry = {
            id: `archived_${Date.now()}`,
            sessionId,
            title: chatTitle,
            archivedAt: new Date().toISOString(),
            startedAt,
            lastActivityAt,
            messageCount: archivedMessages.length,
            toolCount: archivedToolCount,
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
          setArchivedSessions(nextArchive);
        } catch (error) {
          archiveError = error;
        }
      }
      setMessages([]);
      setIsGenerating(false);
      setCustomChatTitle('');
      setCustomChatTitleSessionId(session.session_id);
      setTitleDraft('');
      setIsEditingTitle(false);
      setSessionId(session.session_id);
      localStorage.removeItem(config.storage.chatHistory);
      localStorage.removeItem(CUSTOM_CHAT_TITLE_KEY);
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
      setIsEditingTitle(false);
      show('error', 'New chat failed', error.message);
    } finally {
      setIsClearingSession(false);
    }
  }, [cancelStream, chatTitle, config.storage.chatHistory, createSession, messages, sessionId, show]);

  const handleCommand = useCallback(async (commandName) => {
    if (commandName !== 'new') {
      return;
    }

    await startNewChatSession();
  }, [startNewChatSession]);

  const totalTokenCount = useMemo(
    () => messages.reduce((total, message) => total + (Number.isFinite(message.tokenCount) ? message.tokenCount : 0), 0),
    [messages],
  );
  const totalToolCount = useMemo(() => countToolInvocations(messages), [messages]);
  const firstMessageTime = useMemo(
    () => messages.find((message) => Number.isFinite(message.timestamp))?.timestamp,
    [messages],
  );
  const sessionItems = useMemo(
    () => buildSessionItems({
      messages,
      sessionId,
      chatTitle,
      totalTokenCount,
      totalToolCount,
      firstMessageTime,
      archivedSessions,
      pinnedSessionIds,
      sharedSessionIds,
    }),
    [
      archivedSessions,
      chatTitle,
      firstMessageTime,
      messages,
      pinnedSessionIds,
      sessionId,
      sharedSessionIds,
      totalTokenCount,
      totalToolCount,
    ],
  );
  const sessionGroups = useMemo(() => groupSessionItems(sessionItems), [sessionItems]);
  const currentSessionItem = useMemo(
    () => sessionItems.find((item) => item.selected) ?? null,
    [sessionItems],
  );
  const visibleSessionGroups = useMemo(() => {
    if (sidebarFilter === 'shared') {
      return [{ key: 'shared', label: 'Shared', items: sessionItems.filter((item) => item.shared) }]
        .filter((group) => group.items.length > 0);
    }
    if (sidebarFilter === 'mine') {
      return [{ key: 'mine', label: 'Mine', items: sessionItems.filter((item) => item.selected) }]
        .filter((group) => group.items.length > 0);
    }
    if (sidebarFilter === 'archived') {
      return [{ key: 'archived', label: 'Archived', items: sessionItems.filter((item) => !item.selected) }]
        .filter((group) => group.items.length > 0);
    }
    return sessionGroups;
  }, [sessionGroups, sessionItems, sidebarFilter]);
  const sidebarEmptyMessage = useMemo(() => {
    if (sidebarFilter === 'shared') {
      return 'No shared chats yet.';
    }
    if (sidebarFilter === 'archived') {
      return 'No archived chats yet.';
    }
    return 'No chats yet.';
  }, [sidebarFilter]);

  useEffect(() => {
    const activePinKeys = new Set(sessionItems.map((item) => item.pinKey));
    if (!pinnedSessionIds.some((pinKey) => !activePinKeys.has(pinKey))) {
      return;
    }

    const nextPinnedSessionIds = pinnedSessionIds.filter((pinKey) => activePinKeys.has(pinKey));
    try {
      persistPinnedSessionIds(nextPinnedSessionIds);
      setPinnedSessionIds(nextPinnedSessionIds);
    } catch (error) {
      show('error', 'Pinned chats sync failed', error.message);
    }
  }, [pinnedSessionIds, sessionItems, show]);

  const togglePinnedSession = useCallback((sessionItem) => {
    if (!sessionItem?.pinKey) {
      return;
    }

    setPinnedSessionIds((currentPinnedSessionIds) => {
      const nextPinnedSessionIds = currentPinnedSessionIds.includes(sessionItem.pinKey)
        ? currentPinnedSessionIds.filter((pinKey) => pinKey !== sessionItem.pinKey)
        : [sessionItem.pinKey, ...currentPinnedSessionIds];

      try {
        persistPinnedSessionIds(nextPinnedSessionIds);
        return nextPinnedSessionIds;
      } catch (error) {
        show('error', 'Pinning failed', error.message);
        return currentPinnedSessionIds;
      }
    });
  }, [show]);

  useEffect(() => {
    const activeShareKeys = new Set(sessionItems.map((item) => item.pinKey));
    if (!sharedSessionIds.some((shareKey) => !activeShareKeys.has(shareKey))) {
      return;
    }

    const nextSharedSessionIds = sharedSessionIds.filter((shareKey) => activeShareKeys.has(shareKey));
    try {
      persistSharedSessionIds(nextSharedSessionIds);
      setSharedSessionIds(nextSharedSessionIds);
    } catch (error) {
      show('error', 'Shared chats sync failed', error.message);
    }
  }, [sessionItems, sharedSessionIds, show]);

  const toggleSharedSession = useCallback((sessionItem) => {
    if (!sessionItem?.pinKey) {
      return;
    }

    setSharedSessionIds((currentSharedSessionIds) => {
      const isCurrentlyShared = currentSharedSessionIds.includes(sessionItem.pinKey);
      const nextSharedSessionIds = isCurrentlyShared
        ? currentSharedSessionIds.filter((shareKey) => shareKey !== sessionItem.pinKey)
        : [sessionItem.pinKey, ...currentSharedSessionIds];

      try {
        persistSharedSessionIds(nextSharedSessionIds);
        show(
          'success',
          isCurrentlyShared ? 'Chat unshared' : 'Chat shared',
          isCurrentlyShared
            ? 'Removed this chat from the shared list.'
            : 'Added this chat to the shared list.',
        );
        return nextSharedSessionIds;
      } catch (error) {
        show('error', 'Sharing failed', error.message);
        return currentSharedSessionIds;
      }
    });
  }, [show]);

  return (
    <div className="chat-page">
      <aside className="chat-sessions-pane" aria-label="Conversation sessions">
        <div className="chat-sessions-pane__head">
          <div className="chat-sessions-pane__title">Chats</div>
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
          <button
            type="button"
            className={`chat-pane-tab${sidebarFilter === 'all' ? ' is-active' : ''}`}
            onClick={() => setSidebarFilter('all')}
          >
            All
          </button>
          <button
            type="button"
            className={`chat-pane-tab${sidebarFilter === 'mine' ? ' is-active' : ''}`}
            onClick={() => setSidebarFilter('mine')}
          >
            Mine
          </button>
          <button
            type="button"
            className={`chat-pane-tab${sidebarFilter === 'shared' ? ' is-active' : ''}`}
            onClick={() => setSidebarFilter('shared')}
          >
            Shared
          </button>
          <button
            type="button"
            className={`chat-pane-tab chat-pane-tab--sort${sidebarFilter === 'archived' ? ' is-active' : ''}`}
            onClick={() => setSidebarFilter('archived')}
          >
            Archived
          </button>
        </div>
        <div className="chat-sessions-pane__scroll">
          {visibleSessionGroups.length === 0 ? (
            <div className="chat-pane-empty">{sidebarEmptyMessage}</div>
          ) : (
            visibleSessionGroups.map((group) => (
              <div className="chat-pane-group" key={group.key}>
                <div className="chat-pane-group__head">
                  {group.label}
                  <span className="muted">· {group.items.length}</span>
                </div>
                {group.items.map((item) => (
                  <article
                    key={item.id}
                    className={`chat-session-card${item.selected ? ' is-selected' : ''}`}
                  >
                    <div className="chat-session-card__top">
                      <div className="chat-session-card__title">{item.title}</div>
                      <button
                        type="button"
                        className={`chat-session-card__pin${item.pinned ? ' is-active' : ''}`}
                        aria-label={item.pinned ? `Unpin ${item.title}` : `Pin ${item.title}`}
                        title={item.pinned ? 'Unpin chat' : 'Pin chat'}
                        onClick={() => togglePinnedSession(item)}
                      >
                        {item.pinned ? (
                          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                            <path d="M5.1 2.75h5.8l-1.2 3.3 1.95 1.85v1.05H8.75v4.3l-.75-.65-.75.65v-4.3H4.35V7.9L6.3 6.05 5.1 2.75Z" fill="currentColor" />
                          </svg>
                        ) : (
                          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                            <path d="M5.1 2.75h5.8l-1.2 3.3 1.95 1.85v1.05H8.75v4.3l-.75-.65-.75.65v-4.3H4.35V7.9L6.3 6.05 5.1 2.75Z" fill="none" stroke="currentColor" strokeWidth="1.15" strokeLinejoin="round" />
                          </svg>
                        )}
                      </button>
                    </div>
                    <div className="chat-session-card__meta">
                      <span>{item.meta}</span>
                      <span className="chat-session-card__dot">·</span>
                      <span className="chat-session-card__state">{item.detail}</span>
                    </div>
                    <div className="chat-session-card__summary">{item.summary}</div>
                  </article>
                ))}
              </div>
            ))
          )}
        </div>
      </aside>

      <section className="chat-container" aria-label="Chat conversation">
        <header className="chat-page-header">
          <div className="chat-page-header__inner">
            <div className="chat-page-header__title-row">
              {isEditingTitle ? (
                <input
                  ref={titleInputRef}
                  type="text"
                  className="chat-page-header__title-input"
                  value={titleDraft}
                  maxLength={120}
                  aria-label="Conversation title"
                  onChange={(event) => setTitleDraft(event.target.value)}
                  onBlur={cancelTitleEdit}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault();
                      saveTitleEdit();
                    } else if (event.key === 'Escape') {
                      event.preventDefault();
                      cancelTitleEdit();
                    }
                  }}
                />
              ) : (
                <h1 className="chat-page-header__title">{chatTitle}</h1>
              )}
              <button
                type="button"
                className="chat-icon-button"
                title={isEditingTitle ? 'Save conversation name' : 'Rename conversation'}
                aria-label={isEditingTitle ? 'Save conversation name' : 'Rename conversation'}
                onMouseDown={isEditingTitle ? (event) => event.preventDefault() : undefined}
                onClick={isEditingTitle ? saveTitleEdit : startTitleEdit}
              >
                {isEditingTitle ? (
                  <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                    <path d="M3.25 8.25 6.5 11.5l6.25-6.75" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : (
                  <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                    <path d="M2 14h3l8-8-3-3-8 8z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
                  </svg>
                )}
              </button>
              <span className="chat-page-header__spacer" />
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
                      role="menuitemcheckbox"
                      aria-checked={currentSessionItem?.shared === true}
                      onClick={() => {
                        toggleSharedSession(currentSessionItem);
                        setChatMenuOpen(false);
                      }}
                      disabled={isClearingSession || messages.length === 0}
                    >
                      <span className="chat-options-item__icon" aria-hidden="true">
                        <svg viewBox="0 0 16 16" width="13" height="13">
                          <path d="M5.75 8.1 10.4 5.3M5.75 7.9l4.65 2.8" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
                          <circle cx="4" cy="8" r="1.6" fill="none" stroke="currentColor" strokeWidth="1.2" />
                          <circle cx="12" cy="4.4" r="1.6" fill="none" stroke="currentColor" strokeWidth="1.2" />
                          <circle cx="12" cy="11.6" r="1.6" fill="none" stroke="currentColor" strokeWidth="1.2" />
                        </svg>
                      </span>
                      <span>Shared</span>
                    </button>
                    <button
                      type="button"
                      className="chat-options-item"
                      role="menuitem"
                      onClick={() => startNewChatSession({
                        archive: true,
                      })}
                      disabled={isClearingSession || messages.length === 0}
                    >
                      <span className="chat-options-item__icon" aria-hidden="true">
                        <svg viewBox="0 0 16 16" width="13" height="13">
                          <path d="M2.5 4h11v2.25h-11zM3.5 7.25h9v5.25h-9z" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
                          <path d="M8 8.5v2.25M6.5 9.5 8 11l1.5-1.5" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </span>
                      <span>Archive</span>
                    </button>
                    <button
                      type="button"
                      className="chat-options-item chat-options-item--danger"
                      role="menuitem"
                      onClick={() => startNewChatSession()}
                      disabled={isClearingSession}
                    >
                      <span className="chat-options-item__icon" aria-hidden="true">
                        <svg viewBox="0 0 16 16" width="13" height="13">
                          <path d="M3.5 4.5h9M6.25 4.5V3.25h3.5V4.5M5 6.25v5.25M8 6.25v5.25M11 6.25v5.25" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
                          <path d="M4.5 4.5l.6 8A1.75 1.75 0 0 0 6.85 14h2.3a1.75 1.75 0 0 0 1.75-1.5l.6-8" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
                        </svg>
                      </span>
                      <span>Delete</span>
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
              <span className="chat-separator">·</span>
              <span className="muted">Started {formatStartedTime(firstMessageTime) || 'not started'}</span>
              <span className="chat-separator">·</span>
              <span className="muted">{messages.length} messages</span>
              <span className="chat-separator">·</span>
              <span className="muted">{totalToolCount} tools</span>
              <span className="chat-separator">·</span>
              <span className="muted">{new Intl.NumberFormat().format(totalTokenCount)} tokens</span>
              {sessionId && (
                <>
                  <span className="chat-separator">·</span>
                  <span className="chat-session-id">{sessionId}</span>
                </>
              )}
            </div>
          </div>
        </header>
        <StreamFilterMenu
          storageKey={config.storage.streamFilters}
          filters={streamFilters}
          onChange={updateStreamFilters}
        />

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
          autoFocus
          focusToken={sessionId}
          keepFocusAfterSend
        />
      </section>
    </div>
  );
}

export default ConversationPage;
