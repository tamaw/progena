import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useApi } from '../hooks/useApi';
import { useConfig } from '../context/ConfigContext';
import { useNotifications } from '../context/NotificationContext';
import { useChatStream } from '../hooks/useChatStream';
import { useChatSessionPrefs, usePrunedSelection } from '../hooks/useChatSessionPrefs';
import { selectLastUiEvent } from '../store/realtimeSlice';
import {
  createChatSession,
  deleteChatSession,
  requestChatSessionDetail,
  selectActiveChatSession,
  selectChatSessions,
  selectChatSessionsState,
  setChatIndicators,
  updateChatSessionMetadata,
} from '../store/uiDataSlice';
import ChatInput from '../components/chat/ChatInput';
import { Message } from '../components/chat/Message';
import QuickActions from '../components/chat/QuickActions';
import StreamFilterMenu from '../components/chat/StreamFilterMenu';
import { DetailTabs } from '../components/layout/DetailHeader';
import {
  CHAT_AGENT_NAME,
  countToolInvocations,
  deriveChatTitle,
  formatStartedTime,
  groupSessionItems,
} from '../components/chat/conversationModel';
import {
  buildChatSessionItems,
  normalizeBackendChatMessage,
  sidebarEmptyMessage,
  visibleGroupsForFilter,
} from '../components/chat/chatSessionsModel';
import {
  PERSISTED_CATEGORIES,
  STREAM_EVENT_CATEGORIES,
  categoryForEventType,
  isEventVisible,
  loadStreamFilters,
} from '../components/chat/streamFilters';
import {
  appendStreamEvent,
  finalizeSupersededAssistant,
  normalizeStreamEvent,
} from '../components/chat/streamEventsModel';
import {
  ARCHIVED_CHAT_SESSIONS_KEY,
  CUSTOM_CHAT_TITLE_KEY,
  loadQuickChatHandoff,
  loadStoredCustomChatTitle,
  persistCustomChatTitle,
  persistPinnedSessionIds,
  persistSharedSessionIds,
} from '../components/chat/chatStorage';

const WEEKDAY_WELCOME_MESSAGES = [
  'Sunday reset 🌿',
  'Monday kickoff ☕',
  'Tuesday tune-up 🔧',
  'Wednesday momentum ⚡',
  'Thursday deep dive 🤿',
  'Friday finish line 🏁',
  'Saturday side quest ✨',
];

function getDailyWelcomeMessage(date = new Date()) {
  return WEEKDAY_WELCOME_MESSAGES[date.getDay()] ?? 'Let\'s get started ✨';
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
    model: typeof message.model === 'string' ? message.model : '',
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

// Stable per-nick colours, the way IRC clients hash a nick to a palette entry.
const NICK_COLORS = [
  '#e06c75',
  '#e5c07b',
  '#98c379',
  '#56b6c2',
  '#61afef',
  '#c678dd',
  '#d19a66',
  '#7fdbb6',
  '#f0a0c0',
  '#b0a0ff',
];

// Your own nick gets a fixed warm signature colour (IRC clients highlight your
// own nick); everyone else hashes to a palette entry.
const OWN_NICK_COLOR = '#f5b942';

function nickColor(name) {
  if (name === 'You') {
    return OWN_NICK_COLOR;
  }
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) | 0;
  }
  return NICK_COLORS[Math.abs(hash) % NICK_COLORS.length];
}

function formatIrcTime(timestamp) {
  if (!Number.isFinite(timestamp)) {
    return '';
  }
  return new Date(timestamp).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

// Plain-text (IRC-style) transcript rows: one line per message, no images.
function buildTranscriptRows(messages) {
  return messages.map((message) => {
    const nick = message.role === 'assistant' ? CHAT_AGENT_NAME : 'You';
    return {
      id: message.id,
      time: formatIrcTime(message.timestamp),
      nick,
      color: nickColor(nick),
      content: typeof message.content === 'string' ? message.content : '',
    };
  });
}

function stripEphemeralStreamEvents(message) {
  const events = Array.isArray(message?.streamEvents) ? message.streamEvents : [];
  if (events.length === 0) return message;
  // Keep only persisted categories; also strip base64 images to keep localStorage bounded.
  const persistable = events
    .filter((event) => PERSISTED_CATEGORIES.has(categoryForEventType(event.eventType)))
    .map((event) => (event.images ? { ...event, images: undefined } : event));
  if (persistable.length === events.length && !events.some((e) => e.images)) {
    return message;
  }
  return { ...message, streamEvents: persistable };
}

function ConversationPage() {
  const dispatch = useDispatch();
  const { config, getApiUrl } = useConfig();
  const {
    connectionState,
    sendMessageStream,
    cancelStream,
    getSession,
    createSession,
  } = useApi();
  const { show } = useNotifications();
  const lastUiEvent = useSelector(selectLastUiEvent);
  const backendChatSessions = useSelector(selectChatSessions);
  const backendChatSessionsState = useSelector(selectChatSessionsState);
  const activeBackendChat = useSelector(selectActiveChatSession);
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
  const [sidebarFilter, setSidebarFilter] = useState('all');
  const [sidebarQuery, setSidebarQuery] = useState('');
  const [activeDetailTab, setActiveDetailTab] = useState('Chat');
  const [selectedBackendChatId, setSelectedBackendChatId] = useState(null);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');
  const messagesEndRef = useRef(null);
  const chatMenuRef = useRef(null);
  const titleInputRef = useRef(null);
  const lastProcessedUiEventIdRef = useRef(null);
  const lastAppliedBackendChatIdRef = useRef(null);
  const hasAutoSelectedOnMountRef = useRef(false);
  // Current selection, mirrored into a ref so a stream that finishes in the
  // background can tell whether the user is now viewing its origin chat.
  const selectedBackendChatIdRef = useRef(null);
  // Track recently added scheduled messages to deduplicate.
  const recentScheduledContent = useRef(new Set());

  useEffect(() => {
    selectedBackendChatIdRef.current = selectedBackendChatId;
  }, [selectedBackendChatId]);

  // Reload the currently-open backend chat's detail (used when a background
  // stream for the chat the user is viewing finishes — so the reply shows
  // without marking the open chat unread).
  const reloadOpenChat = useCallback((chatSessionId) => {
    lastAppliedBackendChatIdRef.current = null;
    const requestId = `chat-detail-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    dispatch(requestChatSessionDetail({
      chatsUrl: getApiUrl('chats'),
      chatSessionId,
      requestId,
    })).unwrap().catch((error) => show('error', 'Chat reload failed', error.message));
  }, [dispatch, getApiUrl, show]);

  const {
    archivedSessions,
    setArchivedSessions,
    pinnedSessionIds,
    setPinnedSessionIds,
    sharedSessionIds,
    setSharedSessionIds,
    togglePinnedSession,
    toggleSharedSession,
  } = useChatSessionPrefs({ dispatch, getApiUrl, show });

  const {
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
  } = useChatStream({
    messages,
    setMessages,
    activeBackendChat,
    sessionId,
    sendMessageStream,
    cancelStream,
    dispatch,
    getApiUrl,
    show,
    selectedBackendChatIdRef,
    reloadOpenChat,
  });

  const derivedChatTitle = useMemo(() => deriveChatTitle(messages), [messages]);
  const chatTitle = customChatTitle || derivedChatTitle;

  const streamEventFilter = useCallback(
    (event) => isEventVisible(event, streamFilters),
    [streamFilters],
  );

  // Surface unread / generating state to the primary nav dot (rendered outside
  // this page). Green when there are unread chats, orange while generating.
  const hasUnreadChats = unreadChatIds.size > 0;
  const isAnyChatGenerating = isGenerating || backgroundGeneratingChatIds.size > 0;
  useEffect(() => {
    dispatch(setChatIndicators({ hasUnread: hasUnreadChats, isGenerating: isAnyChatGenerating }));
  }, [dispatch, hasUnreadChats, isAnyChatGenerating]);
  useEffect(() => () => {
    dispatch(setChatIndicators({ hasUnread: false, isGenerating: false }));
  }, [dispatch]);

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
  }, [activeAssistantIdRef, config.storage.quickChatHandoff, setIsGenerating]);

  // Save chat history to localStorage only for locally-owned sessions.
  // Backend chat messages must not leak into localStorage — they are the server's
  // source of truth and would create a ghost "Current" card on the next page visit.
  useEffect(() => {
    if (selectedBackendChatId) {
      localStorage.removeItem(config.storage.chatHistory);
    } else if (messages.length > 0) {
      const persistable = messages.slice(-100).map(stripEphemeralStreamEvents);
      localStorage.setItem(config.storage.chatHistory, JSON.stringify(persistable));
    }
  }, [messages, selectedBackendChatId, config.storage.chatHistory]);

  useEffect(() => {
    if (!activeBackendChat?.chat_session_id) {
      return;
    }
    if (
      selectedBackendChatId !== activeBackendChat.chat_session_id
      || lastAppliedBackendChatIdRef.current === activeBackendChat.chat_session_id
    ) {
      return;
    }

    const backendMessages = Array.isArray(activeBackendChat.messages)
      ? activeBackendChat.messages
        .map((message, index) => normalizeBackendChatMessage(message, index))
        .filter(Boolean)
      : [];
    const nextSessionId =
      activeBackendChat.progenitor_session_id
      || activeBackendChat.thread_id
      || activeBackendChat.chat_session_id;

    activeAssistantIdRef.current = null;
    recentScheduledContent.current.clear();
    setMessages(backendMessages);
    setIsGenerating(false);
    setIsClearingSession(false);
    setCustomChatTitle(activeBackendChat.title || '');
    setCustomChatTitleSessionId(nextSessionId);
    setTitleDraft(activeBackendChat.title || '');
    setIsEditingTitle(false);
    setSessionId(nextSessionId);
    lastAppliedBackendChatIdRef.current = activeBackendChat.chat_session_id;
    if (backendMessages.length === 0) {
      localStorage.removeItem(config.storage.chatHistory);
    }
  }, [activeAssistantIdRef, activeBackendChat, config.storage.chatHistory, selectedBackendChatId, setIsGenerating]);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' });
  }, [messages, isGenerating]);

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

  useEffect(() => {
    if (!lastUiEvent) {
      return;
    }
    if (lastProcessedUiEventIdRef.current === lastUiEvent.id) {
      return;
    }
    lastProcessedUiEventIdRef.current = lastUiEvent.id;
    handleChatEvent({
      type: lastUiEvent.type,
      data: lastUiEvent.payload,
    });
  }, [handleChatEvent, lastUiEvent]);

  const handleQuickAction = useCallback((message) => {
    handleSend(message);
  }, [handleSend]);

  const startNewChatSession = useCallback(async ({ archive = false } = {}) => {
    const previousAssistantId = activeAssistantIdRef.current;
    const previousSessionId = sessionId;
    const archivedMessages = messages.map((message) => stripEphemeralStreamEvents(message));
    setIsClearingSession(true);
    setChatMenuOpen(false);
    activeAssistantIdRef.current = null;
    recentScheduledContent.current.clear();
    cancelStream();

    try {
      const existingBackendCard = previousSessionId
        ? backendChatSessions.some((chat) => (
          chat.progenitor_session_id === previousSessionId
          || chat.thread_id === previousSessionId
          || chat.chat_session_id === previousSessionId
        ))
        : false;
      if (previousSessionId && !existingBackendCard && archivedMessages.length > 0) {
        await dispatch(createChatSession({
          chatsUrl: getApiUrl('chats'),
          title: chatTitle || 'Untitled chat',
          progenitorSessionId: previousSessionId,
        })).unwrap();
      }
      const session = await createSession();
      await dispatch(createChatSession({
        chatsUrl: getApiUrl('chats'),
        title: 'New chat',
        progenitorSessionId: session.session_id,
      })).unwrap();
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
      setSelectedBackendChatId(null);
      lastAppliedBackendChatIdRef.current = null;
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
  }, [
    activeAssistantIdRef,
    backendChatSessions,
    cancelStream,
    chatTitle,
    config.storage.chatHistory,
    dispatch,
    getApiUrl,
    createSession,
    messages,
    sessionId,
    setArchivedSessions,
    setIsGenerating,
    show,
  ]);

  const selectSessionItem = useCallback(async (sessionItem) => {
    if (!sessionItem) {
      return;
    }
    const shouldReloadSelectedBackendChat =
      sessionItem.selected
      && sessionItem.source === 'backend'
      && sessionItem.chatSessionId
      && messages.length === 0;
    if (sessionItem.selected && !shouldReloadSelectedBackendChat) {
      return;
    }
    if (sessionItem.source !== 'backend' || !sessionItem.chatSessionId) {
      setSelectedBackendChatId(null);
      lastAppliedBackendChatIdRef.current = null;
      return;
    }

    // If a stream is in progress for the chat we're leaving, keep it running
    // in the background and show an orange dot on that card.
    const streamingOriginId = backgroundOriginChatIdRef.current;
    if (streamingOriginId && activeAssistantIdRef.current !== null) {
      setBackgroundGeneratingChatIds((prev) => {
        if (prev.has(streamingOriginId)) return prev;
        const next = new Set(prev);
        next.add(streamingOriginId);
        return next;
      });
    }
    activeAssistantIdRef.current = null;
    setIsGenerating(false);
    setIsEditingTitle(false);
    setChatMenuOpen(false);
    setSelectedBackendChatId(sessionItem.chatSessionId);
    setBackgroundGeneratingChatIds((prev) => {
      if (!prev.has(sessionItem.chatSessionId)) return prev;
      const next = new Set(prev);
      next.delete(sessionItem.chatSessionId);
      return next;
    });
    setUnreadChatIds((prev) => {
      if (!prev.has(sessionItem.chatSessionId)) return prev;
      const next = new Set(prev);
      next.delete(sessionItem.chatSessionId);
      return next;
    });
    setBackgroundErrorChatIds((prev) => {
      if (!prev.has(sessionItem.chatSessionId)) return prev;
      const next = new Set(prev);
      next.delete(sessionItem.chatSessionId);
      return next;
    });
    lastAppliedBackendChatIdRef.current = null;
    const requestId = `chat-detail-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    try {
      await dispatch(requestChatSessionDetail({
        chatsUrl: getApiUrl('chats'),
        chatSessionId: sessionItem.chatSessionId,
        requestId,
      })).unwrap();
    } catch (error) {
      show('error', 'Chat load failed', error.message);
    }
  }, [
    activeAssistantIdRef,
    backgroundOriginChatIdRef,
    dispatch,
    getApiUrl,
    messages.length,
    setBackgroundErrorChatIds,
    setBackgroundGeneratingChatIds,
    setIsGenerating,
    setUnreadChatIds,
    show,
  ]);

  const handleCommand = useCallback(async (commandName) => {
    if (commandName !== 'new') {
      return;
    }

    await startNewChatSession();
  }, [startNewChatSession]);
  const welcomeMessage = useMemo(() => getDailyWelcomeMessage(), []);
  const transcriptRows = useMemo(() => buildTranscriptRows(messages), [messages]);
  const totalTokenCount = useMemo(
    () => messages.reduce((total, message) => total + (Number.isFinite(message.tokenCount) ? message.tokenCount : 0), 0),
    [messages],
  );
  const totalToolCount = useMemo(() => countToolInvocations(messages), [messages]);
  const eventCounts = useMemo(() => {
    const counts = STREAM_EVENT_CATEGORIES.reduce((acc, c) => { acc[c.id] = 0; return acc; }, {});
    for (const message of messages) {
      if (message.role === 'user') { counts.user++; continue; }
      if (message.role === 'assistant') {
        counts.agent++;
        if (message.reasoningText) counts.reasoning++;
      }
      for (const event of (Array.isArray(message.streamEvents) ? message.streamEvents : [])) {
        const cat = categoryForEventType(event.eventType);
        if (cat && cat in counts) counts[cat]++;
        if (Array.isArray(event.images) && event.images.length > 0) counts.images++;
      }
    }
    return counts;
  }, [messages]);
  const firstMessageTime = useMemo(
    () => messages.find((message) => Number.isFinite(message.timestamp))?.timestamp,
    [messages],
  );
  const isContentLoading =
    !backendChatSessionsState.loaded && messages.length === 0 && !selectedBackendChatId;
  const sessionItems = useMemo(
    () => buildChatSessionItems({
      messages,
      sessionId,
      chatTitle,
      totalTokenCount,
      totalToolCount,
      firstMessageTime,
      archivedSessions,
      pinnedSessionIds,
      sharedSessionIds,
      backendChatSessions,
      backendChatsLoaded: backendChatSessionsState.loaded,
      selectedBackendChatId,
      unreadChatIds,
      backgroundGeneratingChatIds,
      backgroundErrorChatIds,
      isGenerating,
    }),
    [
      archivedSessions,
      backendChatSessions,
      backendChatSessionsState.loaded,
      backgroundErrorChatIds,
      backgroundGeneratingChatIds,
      chatTitle,
      firstMessageTime,
      isGenerating,
      messages,
      pinnedSessionIds,
      selectedBackendChatId,
      sessionId,
      sharedSessionIds,
      totalTokenCount,
      totalToolCount,
      unreadChatIds,
    ],
  );
  const currentSessionItem = useMemo(
    () => sessionItems.find((item) => item.selected) ?? null,
    [sessionItems],
  );
  const visibleSessionGroups = useMemo(() => {
    const normalizedQuery = sidebarQuery.trim().toLowerCase();
    const filtered = normalizedQuery
      ? sessionItems.filter((item) => `${item.title} ${item.summary ?? ''}`.toLowerCase().includes(normalizedQuery))
      : sessionItems;
    return visibleGroupsForFilter({ sessionItems: filtered, sidebarFilter, groupSessionItems });
  }, [sessionItems, sidebarFilter, sidebarQuery]);
  const sidebarEmptyMessageText = useMemo(
    () => sidebarEmptyMessage({
      sidebarFilter,
      backendChatsLoaded: backendChatSessionsState.loaded,
      backendChatsLoading: backendChatSessionsState.loading,
    }),
    [backendChatSessionsState.loaded, backendChatSessionsState.loading, sidebarFilter],
  );

  // Drop persisted pinned/shared ids whose chats no longer exist.
  usePrunedSelection({
    ids: pinnedSessionIds,
    setIds: setPinnedSessionIds,
    persist: persistPinnedSessionIds,
    sessionItems,
    show,
    errorTitle: 'Pinned chats sync failed',
  });
  usePrunedSelection({
    ids: sharedSessionIds,
    setIds: setSharedSessionIds,
    persist: persistSharedSessionIds,
    sessionItems,
    show,
    errorTitle: 'Shared chats sync failed',
  });

  // Auto-select the top card when sessions load (or are already loaded on remount).
  // Fires once per component lifetime; if there are no backend sessions, the new-chat
  // pane is shown naturally since selectedBackendChatId stays null.
  useEffect(() => {
    if (hasAutoSelectedOnMountRef.current) return;
    if (!backendChatSessionsState.loaded) return;
    hasAutoSelectedOnMountRef.current = true;
    const topItem = visibleSessionGroups[0]?.items[0];
    if (topItem?.source === 'backend') {
      selectSessionItem(topItem);
    }
  }, [backendChatSessionsState.loaded, visibleSessionGroups, selectSessionItem]);

  const archiveSession = useCallback((sessionItem) => {
    if (sessionItem?.source === 'backend' && sessionItem.chatSessionId) {
      dispatch(updateChatSessionMetadata({
        chatsUrl: getApiUrl('chats'),
        chatSessionId: sessionItem.chatSessionId,
        patch: { archived: true },
      })).unwrap()
        .catch((error) => show('error', 'Archive failed', error.message));
      if (sessionItem.selected) {
        setSelectedBackendChatId(null);
        lastAppliedBackendChatIdRef.current = null;
        setMessages([]);
        localStorage.removeItem(config.storage.chatHistory);
      }
      return;
    }
    startNewChatSession({ archive: true });
  }, [config.storage.chatHistory, dispatch, getApiUrl, show, startNewChatSession]);

  const deleteSession = useCallback((sessionItem) => {
    if (sessionItem?.source === 'backend' && sessionItem.chatSessionId) {
      dispatch(deleteChatSession({
        chatsUrl: getApiUrl('chats'),
        chatSessionId: sessionItem.chatSessionId,
      })).unwrap()
        .catch((error) => show('error', 'Delete failed', error.message));
      if (sessionItem.selected) {
        setSelectedBackendChatId(null);
        lastAppliedBackendChatIdRef.current = null;
        setMessages([]);
        localStorage.removeItem(config.storage.chatHistory);
      }
      return;
    }
    startNewChatSession();
  }, [config.storage.chatHistory, dispatch, getApiUrl, show, startNewChatSession]);

  // Conversation actions, shared between the inline header icon row (desktop)
  // and the collapsed "⋮" dropdown (narrow / mobile widths).
  const isPinned = currentSessionItem?.pinned === true;
  const isShared = currentSessionItem?.shared === true;
  const chatActions = [
    {
      key: 'pin',
      label: isPinned ? 'Unpin' : 'Pin',
      role: 'menuitemcheckbox',
      checked: isPinned,
      active: isPinned,
      disabled: isClearingSession || !currentSessionItem,
      onClick: () => togglePinnedSession(currentSessionItem),
      icon: (
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
          {isPinned ? (
            <path d="M5.1 2.75h5.8l-1.2 3.3 1.95 1.85v1.05H8.75v4.3l-.75-.65-.75.65v-4.3H4.35V7.9L6.3 6.05 5.1 2.75Z" fill="currentColor" />
          ) : (
            <path d="M5.1 2.75h5.8l-1.2 3.3 1.95 1.85v1.05H8.75v4.3l-.75-.65-.75.65v-4.3H4.35V7.9L6.3 6.05 5.1 2.75Z" fill="none" stroke="currentColor" strokeWidth="1.15" strokeLinejoin="round" />
          )}
        </svg>
      ),
    },
    {
      key: 'share',
      label: 'Share',
      role: 'menuitemcheckbox',
      checked: isShared,
      active: isShared,
      disabled: isClearingSession || !currentSessionItem,
      onClick: () => toggleSharedSession(currentSessionItem),
      icon: (
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
          <path d="M5.75 8.1 10.4 5.3M5.75 7.9l4.65 2.8" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
          <circle cx="4" cy="8" r="1.6" fill="none" stroke="currentColor" strokeWidth="1.2" />
          <circle cx="12" cy="4.4" r="1.6" fill="none" stroke="currentColor" strokeWidth="1.2" />
          <circle cx="12" cy="11.6" r="1.6" fill="none" stroke="currentColor" strokeWidth="1.2" />
        </svg>
      ),
    },
    {
      key: 'archive',
      label: 'Archive',
      role: 'menuitem',
      disabled: isClearingSession || !currentSessionItem,
      onClick: () => archiveSession(currentSessionItem),
      icon: (
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
          <path d="M2.5 4h11v2.25h-11zM3.5 7.25h9v5.25h-9z" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
          <path d="M8 8.5v2.25M6.5 9.5 8 11l1.5-1.5" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      ),
    },
    {
      key: 'delete',
      label: 'Delete',
      role: 'menuitem',
      danger: true,
      disabled: isClearingSession,
      onClick: () => deleteSession(currentSessionItem),
      icon: (
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
          <path d="M3.5 4.5h9M6.25 4.5V3.25h3.5V4.5M5 6.25v5.25M8 6.25v5.25M11 6.25v5.25" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
          <path d="M4.5 4.5l.6 8A1.75 1.75 0 0 0 6.85 14h2.3a1.75 1.75 0 0 0 1.75-1.5l.6-8" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
        </svg>
      ),
    },
  ];

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
            + New chat
          </button>
        </div>
        <div className="chat-pane-tabs sess-list__filters" role="tablist" aria-label="Session filters">
          <button
            type="button"
            className={`chat-pane-tab sess-filter${sidebarFilter === 'all' ? ' is-active' : ''}`}
            onClick={() => setSidebarFilter('all')}
          >
            All
          </button>
          <button
            type="button"
            className={`chat-pane-tab sess-filter${sidebarFilter === 'unread' ? ' is-active' : ''}`}
            onClick={() => setSidebarFilter('unread')}
          >
            {hasUnreadChats && <span className="chat-pane-tab__dot" aria-hidden="true" />}
            Unread
          </button>
          <button
            type="button"
            className={`chat-pane-tab sess-filter${sidebarFilter === 'shared' ? ' is-active' : ''}`}
            onClick={() => setSidebarFilter('shared')}
          >
            Shared
          </button>
          <button
            type="button"
            className={`chat-pane-tab sess-filter${sidebarFilter === 'archived' ? ' is-active' : ''}`}
            onClick={() => setSidebarFilter('archived')}
          >
            Archived
          </button>
        </div>
        <div className="chat-sessions-pane__search">
          <label className="sess-search">
            <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
              <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.4" />
              <path d="M11 11l3 3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
            <input
              value={sidebarQuery}
              type="text"
              placeholder="Search chats"
              onChange={(e) => setSidebarQuery(e.target.value)}
            />
          </label>
        </div>
        <div className="chat-sessions-pane__scroll">
          {visibleSessionGroups.length === 0 ? (
            <div className="chat-pane-empty">{sidebarEmptyMessageText}</div>
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
                    className={`chat-session-card${item.selected ? ' is-selected' : ''}${item.unread ? ' is-unread' : ''}`}
                    role="button"
                    tabIndex={0}
                    aria-current={item.selected ? 'true' : undefined}
                    onClick={() => selectSessionItem(item)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        selectSessionItem(item);
                      }
                    }}
                  >
                    <span
                      className={`chat-session-card__dot${
                        item.generating
                          ? ' chat-session-card__dot--generating'
                          : item.erroredInBackground
                            ? ' chat-session-card__dot--error'
                            : item.unread
                              ? ' chat-session-card__dot--unread'
                              : ''
                      }`}
                      aria-hidden="true"
                    />
                    <div className="chat-session-card__top">
                      <span className="chat-session-card__title">{item.title}</span>
                      <span className="chat-session-card__age">{item.meta}</span>
                    </div>
                    {(item.preview || item.detail) && (
                      <div className="chat-session-card__preview">
                        {item.detail && (
                          <span className="chat-session-card__state">{item.detail}</span>
                        )}
                        {item.preview}
                      </div>
                    )}
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
                <h1 className="chat-page-header__title">{isContentLoading ? '' : chatTitle}</h1>
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
              <div className="chat-header-actions" role="group" aria-label="Conversation actions">
                {chatActions.map((action) => (
                  <button
                    key={action.key}
                    type="button"
                    className={`chat-icon-button chat-icon-button--lg${action.danger ? ' chat-icon-button--danger' : ''}${action.active ? ' is-active' : ''}`}
                    title={action.label}
                    aria-label={action.label}
                    aria-pressed={action.role === 'menuitemcheckbox' ? action.checked : undefined}
                    disabled={action.disabled}
                    onClick={action.onClick}
                  >
                    {action.icon}
                  </button>
                ))}
              </div>
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
                    {chatActions.map((action) => (
                      <button
                        key={action.key}
                        type="button"
                        className={`chat-options-item${action.danger ? ' chat-options-item--danger' : ''}`}
                        role={action.role}
                        aria-checked={action.role === 'menuitemcheckbox' ? action.checked : undefined}
                        disabled={action.disabled}
                        onClick={() => {
                          action.onClick();
                          setChatMenuOpen(false);
                        }}
                      >
                        <span className="chat-options-item__icon" aria-hidden="true">{action.icon}</span>
                        <span>{action.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
            {!isContentLoading && (
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
            )}
            {!isContentLoading && (
              <DetailTabs
                tabs={['Chat', 'Text']}
                activeTab={activeDetailTab}
                onTabChange={setActiveDetailTab}
                tabLabel="Conversation detail tabs"
              />
            )}
          </div>
        </header>
        {!isContentLoading && activeDetailTab === 'Chat' && (
          <StreamFilterMenu
            storageKey={config.storage.streamFilters}
            filters={streamFilters}
            onChange={updateStreamFilters}
            eventCounts={eventCounts}
          />
        )}

        <div className="chat-messages sess-detail__body chat-detail-body">
          <div className="chat-messages__inner">
            {isContentLoading ? (
              <div className="chat-loading">Loading...</div>
            ) : messages.length === 0 ? (
              <div className="welcome-message">
                <h2>{welcomeMessage}</h2>
                <QuickActions onAction={handleQuickAction} />
              </div>
            ) : activeDetailTab === 'Text' ? (
              <div className="chat-text-export" role="log" aria-label="Conversation transcript">
                {transcriptRows.map((row) => (
                  <div className="irc-line" key={row.id}>
                    <span className="irc-line__time">{row.time}</span>
                    <span className="irc-line__nick" style={{ color: row.color }}>
                      {`<${row.nick}>`}
                    </span>
                    <span className="irc-line__text">{row.content}</span>
                  </div>
                ))}
              </div>
            ) : (
              <>
                {messages.map((msg) => {
                  if (msg.role === 'user' && streamFilters.user === false) return null;
                  return (
                    <Message
                      key={msg.id}
                      message={msg}
                      streamEventFilter={streamEventFilter}
                      hideContent={msg.role === 'assistant' && streamFilters.agent === false}
                      showImages={streamFilters.images !== false}
                    />
                  );
                })}
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
