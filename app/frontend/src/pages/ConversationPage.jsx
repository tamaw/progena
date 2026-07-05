import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useChatApi } from '../hooks/api/useChatApi';
import { useConfig } from '../context/ConfigContext';
import { useNotifications } from '../context/NotificationContext';
import { useChatStream } from '../hooks/useChatStream';
import { useChatSessionPrefs, usePrunedSelection } from '../hooks/useChatSessionPrefs';
import { selectLastUiEvent } from '../store/realtimeSlice';
import {
  clearChatErrored,
  clearChatGenerating,
  clearChatUnread,
  clearActiveChatSession,
  createChatSession,
  deleteChatSession,
  ensureDraftChatSession,
  markChatGenerating,
  requestChatSessionDetail,
  selectActiveChatSession,
  selectActiveChatSessionState,
  selectChatSessions,
  selectChatSessionsState,
  updateChatSessionMetadata,
} from '../store/uiDataSlice';
import ChatInput from '../components/chat/ChatInput';
import { Message } from '../components/chat/Message';
import QuickActions from '../components/chat/QuickActions';
import StreamFilterMenu from '../components/chat/StreamFilterMenu';
import DetailHeader, { DetailHeaderAction, DetailHeaderActions } from '../components/layout/DetailHeader';
import {
  PaneCollapseSeam,
  PaneExpandRail,
  PaneListFilters,
  PaneListGroup,
  PaneListScroll,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
} from '../components/layout/PaneList';
import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import { PaneSection } from '../components/layout/PaneList';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import {
  CHAT_AGENT_NAME,
  DEFAULT_CHAT_TITLE,
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
  countReasoningTimelineEntries,
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
// Sort modes for the chat sidebar dropdown (mirrors the Agents/Tasks dropdown).
// "Recent" keeps the recency-bucketed grouping; "Name" is a flat A→Z list.
const SIDEBAR_SORT_MODES = ['Recent', 'Name'];
const CHAT_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'unread', label: 'Unread' },
  { value: 'shared', label: 'Shared' },
  { value: 'archived', label: 'Archived' },
];

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
  const { collapsed: sessionsCollapsed, setCollapsed: setSessionsCollapsed, paneClassName: sessionsPaneClassName } = usePaneCollapse('chat-sessions-pane');
  const { config, getApiUrl } = useConfig();
  const { connectionState, sendMessageStream, cancelStream, getSession, createSession } = useChatApi();
  const { show } = useNotifications();
  const lastUiEvent = useSelector(selectLastUiEvent);
  const backendChatSessions = useSelector(selectChatSessions);
  const backendChatSessionsState = useSelector(selectChatSessionsState);
  const activeBackendChat = useSelector(selectActiveChatSession);
  const activeBackendChatState = useSelector(selectActiveChatSessionState);
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
  const [threadId, setThreadId] = useState(() => (
    typeof initialQuickChatHandoff?.threadId === 'string' ? initialQuickChatHandoff.threadId : null
  ));
  const [streamFilters, setStreamFilters] = useState(() =>
    loadStreamFilters(config.storage.streamFilters),
  );
  const [customChatTitle, setCustomChatTitle] = useState(() => initialStoredCustomTitle.title);
  const [customChatTitleThreadId, setCustomChatTitleThreadId] = useState(() => initialStoredCustomTitle.threadId);
  const [sidebarFilter, setSidebarFilter] = useState('all');
  const [sidebarQuery, setSidebarQuery] = useState('');
  const [sidebarSort, setSidebarSort] = useState('Recent');
  const [activeDetailTab, setActiveDetailTab] = useState('Chat');
  const [selectedBackendChatId, setSelectedBackendChatId] = useState(null);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');
  const [composerFocusTick, setComposerFocusTick] = useState(0);
  const messagesEndRef = useRef(null);
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
  const reloadOpenChat = useCallback((threadId) => {
    lastAppliedBackendChatIdRef.current = null;
    const requestId = `chat-detail-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    dispatch(requestChatSessionDetail({
      chatsUrl: getApiUrl('chats'),
      threadId,
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
    backgroundGeneratingChatIds,
    backgroundErrorChatIds,
    activeAssistantIdRef,
    backgroundOriginChatIdRef,
    handleSend,
    handleStop,
  } = useChatStream({
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
  });

  const openBackendChat = useCallback(async (threadId) => {
    if (!threadId) {
      return;
    }
    activeAssistantIdRef.current = null;
    setIsGenerating(false);
    setIsEditingTitle(false);
    setSelectedBackendChatId(threadId);
    dispatch(clearChatGenerating(threadId));
    dispatch(clearChatUnread(threadId));
    dispatch(clearChatErrored(threadId));
    lastAppliedBackendChatIdRef.current = null;
    const requestId = `chat-detail-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    await dispatch(requestChatSessionDetail({
      chatsUrl: getApiUrl('chats'),
      threadId,
      requestId,
    })).unwrap();
  }, [activeAssistantIdRef, dispatch, getApiUrl, setIsGenerating]);

  const materializeCurrentChatCard = useCallback(async (title) => {
    if (!threadId) {
      return null;
    }
    const existingBackendCard = backendChatSessions.find((chat) => (
      chat.thread_id === threadId
    ));
    if (existingBackendCard) {
      return existingBackendCard;
    }
    return dispatch(createChatSession({
      chatsUrl: getApiUrl('chats'),
      title,
      threadId,
    })).unwrap();
  }, [backendChatSessions, dispatch, getApiUrl, threadId]);

  const derivedChatTitle = useMemo(() => deriveChatTitle(messages), [messages]);
  const chatTitle = customChatTitle || derivedChatTitle;

  const streamEventFilter = useCallback(
    (event) => isEventVisible(event, streamFilters),
    [streamFilters],
  );
  const hasUnreadChats = unreadChatIds.size > 0;

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
    if (!threadId) {
      return;
    }
    if (!customChatTitle) {
      if (customChatTitleThreadId && customChatTitleThreadId !== threadId) {
        setCustomChatTitleThreadId(threadId);
      }
      return;
    }
    if (!customChatTitleThreadId) {
      persistCustomChatTitle(threadId, customChatTitle);
      setCustomChatTitleThreadId(threadId);
      return;
    }
    if (customChatTitleThreadId !== threadId) {
      setCustomChatTitle('');
      setCustomChatTitleThreadId(threadId);
      setTitleDraft('');
      setIsEditingTitle(false);
      localStorage.removeItem(CUSTOM_CHAT_TITLE_KEY);
    }
  }, [customChatTitle, customChatTitleThreadId, threadId]);

  const cancelTitleEdit = useCallback(() => {
    setTitleDraft(chatTitle);
    setIsEditingTitle(false);
  }, [chatTitle]);

  const startTitleEdit = useCallback(() => {
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
    setCustomChatTitleThreadId(threadId);
    setTitleDraft(nextCustomTitle || derivedChatTitle);
    setIsEditingTitle(false);

    if (nextCustomTitle) {
      persistCustomChatTitle(threadId, nextCustomTitle);
    } else {
      localStorage.removeItem(CUSTOM_CHAT_TITLE_KEY);
    }
  }, [cancelTitleEdit, derivedChatTitle, threadId, titleDraft]);

  useEffect(() => {
    let cancelled = false;

    const ensureSession = async () => {
      const quickChatHandoff = initialQuickChatHandoffRef.current;
      if (quickChatHandoff) {
        localStorage.removeItem(config.storage.quickChatHandoff);
        initialQuickChatHandoffRef.current = null;
        if (quickChatHandoff.threadId) {
          setThreadId(quickChatHandoff.threadId);
          return;
        }
      }

      try {
        const session = await getSession();
        if (!cancelled) {
          setThreadId(session.conversation_id || null);
        }
      } catch {
        try {
          const session = await createSession();
          if (!cancelled) {
            setThreadId(session.conversation_id || null);
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
      if (typeof event.detail?.threadId === 'string') {
        setThreadId(event.detail.threadId);
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
    if (!activeBackendChat?.thread_id) {
      return;
    }
    if (
      selectedBackendChatId !== activeBackendChat.thread_id
      || lastAppliedBackendChatIdRef.current === activeBackendChat.thread_id
    ) {
      return;
    }

    const backendMessages = Array.isArray(activeBackendChat.messages)
      ? activeBackendChat.messages
        .map((message, index) => normalizeBackendChatMessage(message, index))
        .filter(Boolean)
      : [];
    const nextSessionId =
      activeBackendChat.thread_id
      || null;

    activeAssistantIdRef.current = null;
    recentScheduledContent.current.clear();
    setMessages(backendMessages);
    setIsGenerating(false);
    setIsClearingSession(false);
    setCustomChatTitle(activeBackendChat.title || '');
    setCustomChatTitleThreadId(nextSessionId);
    setTitleDraft(activeBackendChat.title || '');
    setIsEditingTitle(false);
    setThreadId(nextSessionId);
    lastAppliedBackendChatIdRef.current = activeBackendChat.thread_id;
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

  const clearBackendSelection = useCallback(() => {
    setSelectedBackendChatId(null);
    dispatch(clearActiveChatSession());
    lastAppliedBackendChatIdRef.current = null;
  }, [dispatch]);

  const focusComposer = useCallback(() => {
    setComposerFocusTick((current) => current + 1);
  }, []);

  const dismissCurrentSession = useCallback(() => {
    activeAssistantIdRef.current = null;
    recentScheduledContent.current.clear();
    setMessages([]);
    setIsGenerating(false);
    setIsClearingSession(false);
    clearBackendSelection();
    setCustomChatTitle('');
    setCustomChatTitleThreadId(null);
    setTitleDraft('');
    setIsEditingTitle(false);
    setThreadId(null);
    setActiveDetailTab('Chat');
    localStorage.removeItem(config.storage.chatHistory);
    localStorage.removeItem(CUSTOM_CHAT_TITLE_KEY);
  }, [activeAssistantIdRef, clearBackendSelection, config.storage.chatHistory, setIsGenerating]);

  useEffect(() => {
    if (!selectedBackendChatId || activeBackendChatState.loading || activeBackendChat) {
      return;
    }
    dismissCurrentSession();
  }, [activeBackendChat, activeBackendChatState.loading, dismissCurrentSession, selectedBackendChatId]);

  const openDraftChat = useCallback(async ({ forceFreshSession = false } = {}) => {
    let draftThreadId = threadId;
    if (forceFreshSession || !draftThreadId) {
      const session = await createSession();
      draftThreadId = session.conversation_id || null;
    }

    setActiveDetailTab('Chat');
    setIsEditingTitle(false);
    const draftChat = await dispatch(ensureDraftChatSession({
      chatsUrl: getApiUrl('chats'),
      threadId: draftThreadId,
    })).unwrap();
    if (selectedBackendChatId !== draftChat.thread_id) {
      await openBackendChat(draftChat.thread_id);
    }
    return draftChat;
  }, [
    createSession,
    dispatch,
    getApiUrl,
    openBackendChat,
    selectedBackendChatId,
    threadId,
  ]);

  const startNewChatSession = useCallback(async ({ archive = false } = {}) => {
    if (selectedBackendChatId && messages.length === 0 && chatTitle === DEFAULT_CHAT_TITLE) {
      setActiveDetailTab('Chat');
      setIsEditingTitle(false);
      focusComposer();
      return;
    }

    const previousAssistantId = activeAssistantIdRef.current;
    const previousThreadId = threadId;
    const archivedMessages = messages.map((message) => stripEphemeralStreamEvents(message));
    setIsClearingSession(true);
    activeAssistantIdRef.current = null;
    recentScheduledContent.current.clear();
    cancelStream();

    try {
      const existingBackendCard = previousThreadId
        ? backendChatSessions.some((chat) => (
          chat.thread_id === previousThreadId
        ))
        : false;
      if (previousThreadId && !existingBackendCard && archivedMessages.length > 0) {
        await materializeCurrentChatCard(chatTitle || 'Untitled chat');
      }
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
            threadId,
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
      await openDraftChat({
        forceFreshSession: Boolean(selectedBackendChatId || archivedMessages.length > 0 || !previousSessionId),
      });
      focusComposer();
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
    focusComposer,
    materializeCurrentChatCard,
    messages,
    openDraftChat,
    selectedBackendChatId,
    threadId,
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
      && sessionItem.threadId
      && messages.length === 0;
    if (sessionItem.selected && !shouldReloadSelectedBackendChat) {
      return;
    }
    if (sessionItem.source !== 'backend' || !sessionItem.threadId) {
      clearBackendSelection();
      return;
    }

    // If we're leaving a local-only conversation, materialize it first so it
    // remains a distinct sidebar card instead of disappearing on selection.
    if (!selectedBackendChatId && threadId && messages.length > 0) {
      try {
        await materializeCurrentChatCard(chatTitle || 'Untitled chat');
      } catch (error) {
        show('error', 'Chat save failed', error.message);
        return;
      }
    }

    // If a stream is in progress for the chat we're leaving, keep it running
    // in the background and show an orange dot on that card.
    const streamingOriginId = backgroundOriginChatIdRef.current;
    if (streamingOriginId && activeAssistantIdRef.current !== null) {
      dispatch(markChatGenerating(streamingOriginId));
    }
    try {
      await openBackendChat(sessionItem.threadId);
    } catch (error) {
      show('error', 'Chat load failed', error.message);
    }
  }, [
    activeAssistantIdRef,
    backgroundOriginChatIdRef,
    clearBackendSelection,
    chatTitle,
    dispatch,
    materializeCurrentChatCard,
    messages.length,
    openBackendChat,
    selectedBackendChatId,
    threadId,
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
        counts.reasoning += countReasoningTimelineEntries(
          message.streamEvents,
          message.reasoningText,
        );
      }
      for (const event of (Array.isArray(message.streamEvents) ? message.streamEvents : [])) {
        const cat = categoryForEventType(event.eventType);
        if (cat === 'reasoning') continue;
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
  const isEmptyConversationPane =
    backendChatSessionsState.loaded
    && backendChatSessions.length === 0
    && !selectedBackendChatId
    && messages.length === 0;
  const sessionItems = useMemo(
    () => buildChatSessionItems({
      messages,
      threadId,
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
      threadId,
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
    const groups = visibleGroupsForFilter({ sessionItems: filtered, sidebarFilter, groupSessionItems });
    // "Name" collapses the recency buckets into a single flat A→Z list (no header),
    // keeping whatever the active filter already selected.
    if (sidebarSort === 'Name') {
      const items = groups
        .flatMap((group) => group.items)
        .sort((a, b) => String(a.title || '').localeCompare(String(b.title || '')));
      return items.length ? [{ key: 'name', label: null, items }] : [];
    }
    return groups;
  }, [sessionItems, sidebarFilter, sidebarQuery, sidebarSort]);
  const visibleSessionItems = useMemo(
    () => visibleSessionGroups.flatMap((group) => group.items),
    [visibleSessionGroups],
  );
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

  const archiveSession = useCallback(async (sessionItem) => {
    if (sessionItem?.source === 'backend' && sessionItem.threadId) {
      const visibleIndex = visibleSessionItems.findIndex((item) => item.id === sessionItem.id);
      const nextSessionItem = visibleIndex >= 0
        ? (visibleSessionItems[visibleIndex + 1] ?? visibleSessionItems[visibleIndex - 1] ?? null)
        : null;
      try {
        await dispatch(updateChatSessionMetadata({
          chatsUrl: getApiUrl('chats'),
          threadId: sessionItem.threadId,
          patch: { archived: true },
        })).unwrap();
      } catch (error) {
        show('error', 'Archive failed', error.message);
        return;
      }
      if (sessionItem.selected) {
        if (nextSessionItem?.source === 'backend' && nextSessionItem.threadId) {
          await selectSessionItem(nextSessionItem);
          return;
        }
        dismissCurrentSession();
        if (nextSessionItem?.source === 'local') {
          focusComposer();
        }
      }
      return;
    }
    startNewChatSession({ archive: true });
  }, [
    dismissCurrentSession,
    dispatch,
    focusComposer,
    getApiUrl,
    selectSessionItem,
    show,
    startNewChatSession,
    visibleSessionItems,
  ]);

  const deleteSession = useCallback(async (sessionItem) => {
    if (sessionItem?.source === 'backend' && sessionItem.threadId) {
      const visibleIndex = visibleSessionItems.findIndex((item) => item.id === sessionItem.id);
      const nextSessionItem = visibleIndex >= 0
        ? (visibleSessionItems[visibleIndex + 1] ?? visibleSessionItems[visibleIndex - 1] ?? null)
        : null;
      try {
        await dispatch(deleteChatSession({
          chatsUrl: getApiUrl('chats'),
          threadId: sessionItem.threadId,
        })).unwrap();
      } catch (error) {
        show('error', 'Delete failed', error.message);
        return;
      }
      if (sessionItem.selected) {
        if (nextSessionItem?.source === 'backend' && nextSessionItem.threadId) {
          await selectSessionItem(nextSessionItem);
          return;
        }
        dismissCurrentSession();
        if (nextSessionItem?.source === 'local') {
          focusComposer();
        }
      }
      return;
    }
    const visibleIndex = visibleSessionItems.findIndex((item) => item.id === sessionItem?.id);
    const nextSessionItem = visibleIndex >= 0
      ? (visibleSessionItems[visibleIndex + 1] ?? visibleSessionItems[visibleIndex - 1] ?? null)
      : null;
    dismissCurrentSession();
    if (nextSessionItem?.source === 'backend' && nextSessionItem.threadId) {
      await selectSessionItem(nextSessionItem);
      return;
    }
    if (nextSessionItem?.source === 'local') {
      focusComposer();
    }
  }, [
    dismissCurrentSession,
    dispatch,
    focusComposer,
    getApiUrl,
    selectSessionItem,
    show,
    visibleSessionItems,
  ]);

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
    <PageShell className="chat-page">
      <PaneSection as="aside" className={sessionsPaneClassName} aria-label="Conversation sessions" onItemSelect={() => setSessionsCollapsed(true)}>
        {sessionsCollapsed ? (
          <PaneExpandRail label="Chats" onExpand={() => setSessionsCollapsed(false)} />
        ) : (
        <>
        <PaneCollapseSeam label="Chats" onCollapse={() => setSessionsCollapsed(true)} />
        <PaneListFilters
          filters={CHAT_FILTERS}
          activeFilter={sidebarFilter}
          onFilterChange={setSidebarFilter}
          renderFilter={(item) => (
            <>
              {item.value === 'unread' && hasUnreadChats ? <span className="chat-pane-tab__dot" aria-hidden="true" /> : null}
              {item.label}
            </>
          )}
          actionLabel="+ New"
          onAction={() => startNewChatSession()}
          actionDisabled={isClearingSession}
        />
        <PaneListToolbar>
          <PaneListSearch value={sidebarQuery} placeholder="Search chats" onChange={setSidebarQuery} />
          <PaneListSortButton
            title="Sort chats"
            options={SIDEBAR_SORT_MODES}
            value={sidebarSort}
            onSelect={setSidebarSort}
          />
        </PaneListToolbar>
        <PaneListScroll>
          {visibleSessionGroups.length === 0 ? (
            <div className="chat-pane-empty">{sidebarEmptyMessageText}</div>
          ) : (
            visibleSessionGroups.map((group) => {
              const cards = group.items.map((item) => (
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
              ));
              return group.label ? (
                <PaneListGroup key={group.key} title={group.label} count={group.items.length}>
                  {cards}
                </PaneListGroup>
              ) : (
                <div key={group.key}>{cards}</div>
              );
            })
          )}
        </PaneListScroll>
        </>
        )}
      </PaneSection>

      <DetailPane flush className="chat-detail" label="Chat conversation">
        <DetailHeader
          className="chat-detail-header"
          titleClassName="chat-detail-header__title"
          title={(
            <>
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
                <span className="chat-detail-header__title-text">
                  {isContentLoading ? '' : (isEmptyConversationPane ? 'No conversation selected' : chatTitle)}
                </span>
              )}
              <button
                type="button"
                className="chat-icon-button chat-detail-header__rename"
                data-tooltip={isEditingTitle ? 'Save conversation name' : 'Rename conversation'}
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
            </>
          )}
          actions={(
            <DetailHeaderActions menuLabel="Conversation actions">
              {chatActions.map((action) => (
                <DetailHeaderAction
                  key={action.key}
                  icon={action.icon}
                  label={action.label}
                  tooltip={action.label}
                  ariaLabel={action.label}
                  variant={action.danger ? 'danger' : 'default'}
                  className={action.active ? 'is-active' : ''}
                  disabled={action.disabled}
                  aria-pressed={action.role === 'menuitemcheckbox' ? action.checked : undefined}
                  onClick={action.onClick}
                />
              ))}
            </DetailHeaderActions>
          )}
          meta={isContentLoading || isEmptyConversationPane ? [] : [
            <span key="model" className="chat-page-header__model">
              <span className="agent-dot agent-dot--purple" />
              {CHAT_AGENT_NAME}
            </span>,
            <span key="started" className="muted">Started {formatStartedTime(firstMessageTime) || 'not started'}</span>,
            <span key="msgs" className="muted">{messages.length} messages</span>,
            <span key="tools" className="muted">{totalToolCount} tools</span>,
            <span key="tokens" className="muted">{new Intl.NumberFormat().format(totalTokenCount)} tokens</span>,
            threadId ? <span key="id" className="chat-session-id">{threadId}</span> : null,
          ]}
          tabs={isContentLoading || isEmptyConversationPane ? [] : ['Chat', 'Text']}
          activeTab={activeDetailTab}
          onTabChange={setActiveDetailTab}
          tabLabel="Conversation detail tabs"
        />
        {!isContentLoading && !isEmptyConversationPane && activeDetailTab === 'Chat' && (
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
            ) : isEmptyConversationPane ? (
              <div className="welcome-message">
                <h2>No conversations yet</h2>
                <p className="muted">Click <strong>+ New</strong> to start a conversation.</p>
              </div>
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
          focusToken={`${threadId ?? 'no-session'}:${composerFocusTick}`}
          keepFocusAfterSend
        />
      </DetailPane>
    </PageShell>
  );
}

export default ConversationPage;
