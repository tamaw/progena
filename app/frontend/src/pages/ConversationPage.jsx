import { memo, useState, useEffect, useRef, useCallback, useMemo, useSyncExternalStore } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useParams } from 'react-router-dom';
import { useChatApi } from '../hooks/api/useChatApi';
import { useConfig } from '../context/ConfigContext';
import { useNotifications } from '../context/NotificationContext';
import { useChatStream } from '../hooks/useChatStream';
import { useChatSessionPrefs, usePrunedSelection } from '../hooks/useChatSessionPrefs';
import { subscribeUiEvent } from '../store/realtimeEvents';
import {
  clearChatErrored,
  clearChatGenerating,
  clearChatUnread,
  clearActiveChatSession,
  createChatSession,
  deleteChatSession,
  ensureDraftChatSession,
  requestChatSessionDetail,
  requestChatSessionReconciliation,
  selectActiveChatSession,
  selectActiveChatSessionState,
  selectChatReconciliationState,
  selectChatSessions,
  selectChatSessionsState,
  updateChatSessionMetadata,
} from '../store/uiDataSlice';
import ChatInput from '../components/chat/ChatInput';
import { Message } from '../components/chat/Message';
import { RegenerateAvailabilityContext } from '../components/chat/MessageActions';
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
  collectMessageImages,
  areChatTranscriptsEquivalent,
  mergeCanonicalMessages,
  excludeLiveOwnedMessage,
  shouldRetireLiveOwnerAfterCanonicalReconciliation,
  committedReasoningTokenCounts,
  committedReasoningTokenDisplay,
  countTokens,
  countToolInvocations,
  deriveChatTitle,
  formatStartedTime,
  groupSessionItems,
  liveReasoningTokenAdjustment,
  shouldShowChatContentLoading,
  getActiveRouteThreadId,
  shouldShowNewChatEmptyState,
} from '../components/chat/conversationModel';
import {
  buildChatSessionItems,
  compareSessionItems,
  findAdjacentSessionItem,
  normalizeBackendChatMessage,
  shouldMaterializeBeforeSwitching,
  shouldLoadSelectedBackendChat,
  sidebarEmptyMessage,
  visibleGroupsForFilter,
} from '../components/chat/chatSessionsModel';
import {
  PERSISTED_CATEGORIES,
  STREAM_EVENT_CATEGORIES,
  categoryForEventType,
  countCategorizedEvents,
  isEventVisible,
  loadStreamFilters,
} from '../components/chat/streamFilters';
import {
  appendStreamEvent,
  countReasoningTimelineEntries,
  normalizeStreamEvent,
} from '../components/chat/streamEventsModel';
import { buildDefaultAssistantTimeline } from '../components/chat/liveAssistantTurnModel';
import LiveAssistantTurn from '../components/chat/LiveAssistantTurn';
import {
  ARCHIVED_CHAT_SESSIONS_KEY,
  CUSTOM_CHAT_TITLE_KEY,
  loadQuickChatHandoff,
  loadStoredCustomChatTitle,
  persistCustomChatTitle,
  persistPinnedSessionIds,
  persistSharedSessionIds,
} from '../components/chat/chatStorage';
import { useEntityUrlSync } from '../lib/routes';

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


function filterSupportedPersistedStreamEvents(streamEvents) {
  const startedSubagentIds = new Set();
  const startedTaskIds = new Set();
  const supportedSubagentEventTypes = new Set([
    'subagent_start',
    'subagent_result',
    'subagent_message_delta',
    'subagent_tool_start',
    'subagent_tool_result',
    'subagent_tool_error',
  ]);
  return streamEvents.filter((event) => {
    const eventType = typeof event?.eventType === 'string' ? event.eventType : '';
    const metadata = event?.metadata && typeof event.metadata === 'object' ? event.metadata : {};
    const toolName = typeof metadata.tool_name === 'string'
      ? metadata.tool_name.trim()
      : (typeof event?.content === 'string' ? event.content.trim() : '');
    const isTaskEvent = (
      (eventType === 'tool_start' || eventType === 'tool_result' || eventType === 'tool_error')
      && toolName === 'task'
    );
    if (isTaskEvent) {
      const toolCallId = typeof metadata.tool_call_id === 'string' ? metadata.tool_call_id.trim() : '';
      if (!toolCallId) return false;
      if (eventType === 'tool_start') {
        const args = metadata.args_preview && typeof metadata.args_preview === 'object'
          && !Array.isArray(metadata.args_preview) ? metadata.args_preview : {};
        const subagentName = typeof metadata.subagent_type === 'string'
          ? metadata.subagent_type.trim()
          : (typeof args.subagent_type === 'string' ? args.subagent_type.trim() : '');
        if (!subagentName) return false;
        startedTaskIds.add(toolCallId);
        return true;
      }
      return startedTaskIds.has(toolCallId);
    }
    if (!eventType.startsWith('subagent_')) return true;
    if (!supportedSubagentEventTypes.has(eventType)) return false;
    const toolCallId = typeof metadata.tool_call_id === 'string'
      ? metadata.tool_call_id.trim()
      : '';
    if (!toolCallId) return false;
    if (eventType === 'subagent_start') {
      const subagentName = typeof metadata.subagent_name === 'string'
        ? metadata.subagent_name.trim()
        : '';
      if (!subagentName) return false;
      startedSubagentIds.add(toolCallId);
      return true;
    }
    return startedSubagentIds.has(toolCallId);
  });
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

  const messageWithoutTimelineItems = { ...message };
  delete messageWithoutTimelineItems.timelineItems;

  const base = {
    ...messageWithoutTimelineItems,
    id: typeof message.id === 'string' ? message.id : `msg_restored_${index}_${timestamp}`,
    role,
    content,
    tokenCount: Number.isFinite(message.tokenCount) ? message.tokenCount : 0,
    reasoningTokenCount: Number.isFinite(message.reasoningTokenCount)
      ? message.reasoningTokenCount
      : 0,
    reasoningText: typeof message.reasoningText === 'string' ? message.reasoningText : '',
    model: typeof message.model === 'string' ? message.model : '',
    streamEvents: filterSupportedPersistedStreamEvents(
      Array.isArray(message.streamEvents)
        ? message.streamEvents.reduce((events, event, eventIndex) => {
          const normalizedEvent = normalizeStreamEvent(event, eventIndex);
          if (!normalizedEvent) {
            return events;
          }
          return appendStreamEvent(events, normalizedEvent);
        }, [])
        : [],
    ),
    timestamp,
  };

  // Stored timelineItems are legacy denormalized data. Always rebuild the
  // assistant timeline from the normalized streamEvents instead.
  if (role === 'assistant') {
    base.timelineItems = buildDefaultAssistantTimeline(base);
  }

  return base;
}

// Sort modes for the chat sidebar dropdown (mirrors the Agents/Tasks dropdown).
// "Recent" keeps the recency-bucketed grouping; "Name" is a flat A→Z list.
const SIDEBAR_SORT_MODES = ['Recent', 'Name'];
const CHAT_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'unread', label: 'Unread' },
  { value: 'shared', label: 'Shared' },
  { value: 'archived', label: 'Archived' },
];

function stripEphemeralStreamEvents(message) {
  const events = Array.isArray(message?.streamEvents) ? message.streamEvents : [];
  if (events.length === 0) return message;
  // Keep only persisted categories; also strip base64 images to keep localStorage bounded.
  const persistable = events
    .filter((event) => PERSISTED_CATEGORIES.has(categoryForEventType(event.eventType, event.metadata)))
    .map((event) => (event.images ? { ...event, images: undefined } : event));
  if (persistable.length === events.length && !events.some((e) => e.images)) {
    return message;
  }
  return { ...message, streamEvents: persistable };
}

const HistoricalMessage = memo(function HistoricalMessage({
  message,
  streamEventFilter,
  hideContent,
  showImages,
  onRegenerate,
}) {
  const handleMessageRegenerate = useCallback(
    () => onRegenerate?.(message.id),
    [message.id, onRegenerate],
  );

  return (
    <Message
      message={message}
      streamEventFilter={streamEventFilter}
      hideContent={hideContent}
      showImages={showImages}
      onRegenerate={message.role === 'assistant' ? handleMessageRegenerate : undefined}
    />
  );
});

const LiveConversationHeader = memo(function LiveConversationHeader({
  liveTurnStore,
  isRouteDetailError,
  messages,
  totalTokenCount,
  firstMessageTime,
  isContentLoading,
  isEmptyConversationPane,
  showEventFilters,
  streamFilters,
  streamFiltersStorageKey,
  updateStreamFilters,
  threadId,
  isEditingTitle,
  titleInputRef,
  titleDraft,
  setTitleDraft,
  saveTitleEdit,
  cancelTitleEdit,
  startTitleEdit,
  chatActions,
  title,
}) {
  const liveHeaderSnapshot = useSyncExternalStore(
    liveTurnStore.subscribeHeader,
    liveTurnStore.getHeaderSnapshot,
    liveTurnStore.getHeaderSnapshot,
  );
  const committedReasoningMeta = useMemo(
    () => committedReasoningTokenDisplay(messages),
    [messages],
  );
  const committedReasoningCounts = useMemo(
    () => committedReasoningTokenCounts(messages),
    [messages],
  );
  const liveReasoningMeta = useMemo(
    () => liveReasoningTokenAdjustment(liveHeaderSnapshot, committedReasoningCounts),
    [committedReasoningCounts, liveHeaderSnapshot],
  );
  const reasoningTokenMeta = useMemo(() => ({
    total: committedReasoningMeta.total + liveReasoningMeta.total,
    estimated: committedReasoningMeta.estimated || liveReasoningMeta.estimated,
  }), [committedReasoningMeta, liveReasoningMeta]);
  const committedEventCounts = useMemo(() => {
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
      counts.images += collectMessageImages(message).length;
      countCategorizedEvents(message.streamEvents, counts);
    }
    return counts;
  }, [messages]);
  const liveReasoningEventCount = liveHeaderSnapshot?.reasoningEventCount ?? 0;
  const eventCounts = useMemo(() => ({
    ...committedEventCounts,
    reasoning: committedEventCounts.reasoning + liveReasoningEventCount,
  }), [committedEventCounts, liveReasoningEventCount]);

  return (
    <>
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
                {isContentLoading ? '' : isRouteDetailError ? 'Conversation unavailable' : (isEmptyConversationPane ? 'No conversation selected' : title)}
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
          <span key="tokens" className="muted">{new Intl.NumberFormat().format(totalTokenCount)} tokens</span>,
          <span key="reasoning-tokens" className="muted">{reasoningTokenMeta.estimated ? '~' : ''}{new Intl.NumberFormat().format(reasoningTokenMeta.total)} reasoning tokens</span>,
          threadId ? <span key="id" className="chat-session-id">{threadId}</span> : null,
        ]}
      />
      {!isContentLoading && !isEmptyConversationPane && showEventFilters && (
        <StreamFilterMenu
          storageKey={streamFiltersStorageKey}
          filters={streamFilters}
          onChange={updateStreamFilters}
          eventCounts={eventCounts}
        />
      )}
    </>
  );
});

function ConversationPage() {
  const dispatch = useDispatch();
  // `titleSlug` is display context only (e.g. /chat/thread_123/my-chat) --
  // session selection is driven entirely by the id, never by the slug.
  const { threadId: urlThreadId } = useParams();
  const { collapsed: sessionsCollapsed, setCollapsed: setSessionsCollapsed, paneClassName: sessionsPaneClassName } = usePaneCollapse('chat-sessions-pane');
  const { config, getApiUrl } = useConfig();
  const { connectionState, sendMessageStream, cancelStream } = useChatApi();
  const { show } = useNotifications();
  const backendChatSessions = useSelector(selectChatSessions);
  const backendChatSessionsState = useSelector(selectChatSessionsState);
  const activeBackendChat = useSelector(selectActiveChatSession);
  const activeBackendChatState = useSelector(selectActiveChatSessionState);
  const chatReconciliationState = useSelector(selectChatReconciliationState);
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
  const [showEventFilters, setShowEventFilters] = useState(false);
  const [customChatTitle, setCustomChatTitle] = useState(() => initialStoredCustomTitle.title);
  const [customChatTitleThreadId, setCustomChatTitleThreadId] = useState(() => initialStoredCustomTitle.threadId);
  const [sidebarFilter, setSidebarFilter] = useState('all');
  const [sidebarQuery, setSidebarQuery] = useState('');
  const [sidebarSort, setSidebarSort] = useState('Recent');
  const [selectedBackendChatId, setSelectedBackendChatId] = useState(() => urlThreadId || null);
  const [foregroundReconciliationIntent, setForegroundReconciliationIntent] = useState(null);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');
  const [composerFocusTick, setComposerFocusTick] = useState(0);
  const messagesContainerRef = useRef(null);
  const isPinnedToBottomRef = useRef(true);
  const messagesScrollFrameRef = useRef(null);
  const handleMessagesScroll = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    isPinnedToBottomRef.current = container.scrollHeight - container.scrollTop - container.clientHeight < 24;
  }, []);
  const titleInputRef = useRef(null);
  // Tracks the requestId of the fetch last applied to local `messages` state.
  // Each dispatch of requestChatSessionDetail carries a fresh requestId
  // (chats.js's active.requestId), so this differs across any two distinct
  // fetches for the same thread_id — unlike comparing by thread_id, which (and
  // did) silently swallow a background stream's reload of its origin chat
  // whenever this component had remounted since the id was last seen — e.g.
  // navigating to another nav item and back mid-stream.
  const lastAppliedBackendChatRequestIdRef = useRef(null);
  const lastAppliedChatReconciliationRequestIdRef = useRef(null);
  // Draft creation is acknowledged separately from its causal session event.
  // Register resolvers before dispatch so a fast stream event cannot be missed.
  const pendingDraftSessionsRef = useRef(new Map());
  const hasAutoSelectedOnMountRef = useRef(false);
  // Current selection, mirrored into a ref so a stream that finishes in the
  // background can tell whether the user is now viewing its origin chat.
  const selectedBackendChatIdRef = useRef(null);
  // Track recently added scheduled messages to deduplicate.
  const recentScheduledContent = useRef(new Set());
  const foregroundOwnershipTokenRef = useRef(0);
  const reconciliationIntentRef = useRef(null);

  useEffect(() => {
    selectedBackendChatIdRef.current = selectedBackendChatId;
  }, [selectedBackendChatId]);

  // Reload the currently-open backend chat's detail (used when a background
  // stream for the chat the user is viewing finishes — so the reply shows
  // without marking the open chat unread).
  const reloadOpenChat = useCallback((threadId) => {
    lastAppliedBackendChatRequestIdRef.current = null;
    const requestId = crypto.randomUUID();
    dispatch(requestChatSessionDetail({
      chatsUrl: getApiUrl('chats'),
      threadId,
      requestId,
    }));
  }, [dispatch, getApiUrl]);

  const beginForegroundStream = useCallback(() => {
    foregroundOwnershipTokenRef.current += 1;
    reconciliationIntentRef.current = null;
    setForegroundReconciliationIntent(null);
  }, []);

  const requestForegroundReconciliation = useCallback(({ threadId: reconciliationThreadId, streamId }) => {
    if (!reconciliationThreadId || selectedBackendChatIdRef.current !== reconciliationThreadId) {
      return;
    }
    setForegroundReconciliationIntent({
      threadId: reconciliationThreadId,
      streamId,
      ownershipToken: foregroundOwnershipTokenRef.current,
    });
  }, []);

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
    activeStreamIdRef,
    backgroundOriginChatIdRef,
    liveTurnStore,
    setLiveAssistantTurn,
    preserveStreamInBackground,
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
    onForegroundStreamStarted: beginForegroundStream,
    requestForegroundReconciliation,
  });

  const openBackendChat = useCallback(async (threadId) => {
    if (!threadId) {
      return;
    }
    beginForegroundStream();
    activeStreamIdRef.current = null;
    setIsGenerating(false);
    setIsEditingTitle(false);
    setSelectedBackendChatId(threadId);
    dispatch(clearChatGenerating(threadId));
    dispatch(clearChatUnread(threadId));
    dispatch(clearChatErrored(threadId));
    lastAppliedBackendChatRequestIdRef.current = null;
    return dispatch(requestChatSessionDetail({
      chatsUrl: getApiUrl('chats'),
      threadId,
      requestId: crypto.randomUUID(),
    })).unwrap();
  }, [activeStreamIdRef, beginForegroundStream, dispatch, getApiUrl, setIsGenerating]);

  const acquireDraftChat = useCallback(async (requestedThreadId = null) => {
    const requestId = crypto.randomUUID();
    let resolveSession;
    const sessionEvent = new Promise((resolve) => {
      resolveSession = resolve;
    });
    pendingDraftSessionsRef.current.set(requestId, resolveSession);

    try {
      await dispatch(ensureDraftChatSession({
        chatsUrl: getApiUrl('chats'),
        threadId: requestedThreadId || crypto.randomUUID(),
        requestId,
      })).unwrap();
    } catch (error) {
      pendingDraftSessionsRef.current.delete(requestId);
      throw error;
    }

    return sessionEvent;
  }, [dispatch, getApiUrl]);

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
    await dispatch(createChatSession({
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
    // A routed detail owns initialization. Do not create an unrelated draft
    // while the requested chat is loading (or after its detail fails).
    if (urlThreadId) {
      return undefined;
    }

    let cancelled = false;

    const ensureSession = async () => {
      const quickChatHandoff = initialQuickChatHandoffRef.current;
      if (quickChatHandoff) {
        localStorage.removeItem(config.storage.quickChatHandoff);
        initialQuickChatHandoffRef.current = null;
      }

      try {
        const session = await acquireDraftChat(quickChatHandoff?.threadId || null);
        // A backend chat may have been selected while the draft was loading;
        // leave its detail request as the owner of the active transcript.
        if (!cancelled && !selectedBackendChatIdRef.current) {
          await openBackendChat(session.thread_id);
        }
      } catch (error) {
        if (!cancelled) {
          console.warn('[ConversationPage] Failed to initialize draft chat:', error);
        }
      }
    };

    ensureSession();
    return () => {
      cancelled = true;
    };
  }, [acquireDraftChat, config.storage.quickChatHandoff, openBackendChat, urlThreadId]);

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

      activeStreamIdRef.current = null;
      setMessages(normalizedMessages);
      setIsGenerating(false);
      setLiveAssistantTurn(null);
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
  }, [activeStreamIdRef, config.storage.quickChatHandoff, setIsGenerating, setLiveAssistantTurn]);

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
    if (!foregroundReconciliationIntent) {
      return;
    }
    if (
      foregroundReconciliationIntent.ownershipToken !== foregroundOwnershipTokenRef.current
      || selectedBackendChatIdRef.current !== foregroundReconciliationIntent.threadId
    ) {
      setForegroundReconciliationIntent(null);
      return;
    }
    const request = dispatch(requestChatSessionReconciliation({
      chatsUrl: getApiUrl('chats'),
      threadId: foregroundReconciliationIntent.threadId,
      requestId: crypto.randomUUID(),
    }));
    reconciliationIntentRef.current = {
      ...foregroundReconciliationIntent,
      requestId: request.requestId,
      messagesSnapshot: messages,
    };
    setForegroundReconciliationIntent(null);
  }, [dispatch, foregroundReconciliationIntent, getApiUrl, messages]);

  useEffect(() => {
    const requestId = chatReconciliationState.requestId;
    const intent = reconciliationIntentRef.current;
    if (
      !requestId
      || requestId === lastAppliedChatReconciliationRequestIdRef.current
      || !chatReconciliationState.detail
      || !intent
      || requestId !== intent.requestId
    ) {
      return;
    }
    lastAppliedChatReconciliationRequestIdRef.current = requestId;
    if (
      intent.ownershipToken !== foregroundOwnershipTokenRef.current
      || intent.threadId !== chatReconciliationState.threadId
      || selectedBackendChatId !== intent.threadId
      || selectedBackendChatIdRef.current !== intent.threadId
      || !areChatTranscriptsEquivalent(messages, intent.messagesSnapshot)
    ) {
      return;
    }
    const canonicalMessages = Array.isArray(chatReconciliationState.detail.messages)
      ? chatReconciliationState.detail.messages
        .map((message, index) => normalizeBackendChatMessage(message, index))
        .filter(Boolean)
      : [];
    if (!areChatTranscriptsEquivalent(messages, canonicalMessages)) {
      setMessages(mergeCanonicalMessages(messages, canonicalMessages));
      // Canonical reconciliation can replace the locally committed row with a
      // backend identity. The frozen live turn then cannot hide that row by
      // its client id, so hand visual ownership to the canonical transcript.
      if (shouldRetireLiveOwnerAfterCanonicalReconciliation(liveTurnStore.getSnapshot(), intent.streamId)) {
        setLiveAssistantTurn(null);
      }
    }
  }, [chatReconciliationState, liveTurnStore, messages, selectedBackendChatId, setLiveAssistantTurn]);

  useEffect(() => {
    if (!activeBackendChat?.thread_id) {
      return;
    }
    if (
      selectedBackendChatId !== activeBackendChat.thread_id
      || lastAppliedBackendChatRequestIdRef.current === activeBackendChatState?.requestId
    ) {
      return;
    }
    const isHydratingActiveStream = activeStreamIdRef.current !== null
      && backgroundOriginChatIdRef.current === activeBackendChat.thread_id
      && selectedBackendChatId === activeBackendChat.thread_id;
    if (isHydratingActiveStream) {
      lastAppliedBackendChatRequestIdRef.current = activeBackendChatState?.requestId ?? null;
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

    activeStreamIdRef.current = null;
    recentScheduledContent.current.clear();
    isPinnedToBottomRef.current = true;
    setMessages(backendMessages);
    setIsGenerating(false);
    // The chat being displayed just changed identity -- a live turn still
    // sitting in state belongs to whatever chat was previously on screen
    // (its background bookkeeping lives in activeStreamIdRef/originChatId,
    // not here), so drop it rather than let it render under the new chat.
    setLiveAssistantTurn(null);
    setIsClearingSession(false);
    setCustomChatTitle(activeBackendChat.title || '');
    setCustomChatTitleThreadId(nextSessionId);
    setTitleDraft(activeBackendChat.title || '');
    setIsEditingTitle(false);
    setThreadId(nextSessionId);
    lastAppliedBackendChatRequestIdRef.current = activeBackendChatState?.requestId ?? null;
    if (backendMessages.length === 0) {
      localStorage.removeItem(config.storage.chatHistory);
    }
  }, [activeStreamIdRef, activeBackendChat, activeBackendChatState, backgroundOriginChatIdRef, config.storage.chatHistory, selectedBackendChatId, setIsGenerating, setLiveAssistantTurn]);

  // Committed message changes and generation transitions keep the page at the
  // tail only while the user remains near the bottom. Per-frame live-turn
  // scrolling is owned by LiveAssistantTurn so streaming does not redraw this
  // page-wide component tree.
  useEffect(() => {
    if (!isPinnedToBottomRef.current || messagesScrollFrameRef.current !== null) return;

    const container = messagesContainerRef.current;
    if (!container) return;

    messagesScrollFrameRef.current = requestAnimationFrame(() => {
      messagesScrollFrameRef.current = null;
      const currentContainer = messagesContainerRef.current;
      if (currentContainer && isPinnedToBottomRef.current) {
        currentContainer.scrollTop = currentContainer.scrollHeight;
      }
    });
  }, [messages, isGenerating]);

  useEffect(() => () => {
    if (messagesScrollFrameRef.current !== null) {
      cancelAnimationFrame(messagesScrollFrameRef.current);
      messagesScrollFrameRef.current = null;
    }
  }, []);

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

  useEffect(() => subscribeUiEvent((event) => {
    const payload = event?.payload;
    if (
      (event?.type === 'chat.session.created' || event?.type === 'chat.session.metadata.updated')
      && typeof payload?.request_id === 'string'
      && typeof payload?.session?.thread_id === 'string'
    ) {
      const resolveSession = pendingDraftSessionsRef.current.get(payload.request_id);
      if (resolveSession) {
        pendingDraftSessionsRef.current.delete(payload.request_id);
        resolveSession(payload.session);
      }
    }
    handleChatEvent({ type: event?.type, data: payload });
  }), [handleChatEvent]);

  // Regenerate actions sit on every historical assistant card. Keep their
  // dispatcher identity stable so a new streamed reply cannot invalidate the
  // entire committed transcript; read the current transcript at click time.
  const regenerateStateRef = useRef({ messages, isGenerating, handleSend, show });
  regenerateStateRef.current.messages = messages;
  regenerateStateRef.current.isGenerating = isGenerating;
  regenerateStateRef.current.handleSend = handleSend;
  regenerateStateRef.current.show = show;
  const handleQuickAction = useCallback((message) => {
    handleSend(message);
  }, [handleSend]);

  const handleRegenerate = useCallback((assistantMessageId) => {
    const current = regenerateStateRef.current;
    if (current.isGenerating) return false;
    const assistantIndex = current.messages.findIndex((message) => message.id === assistantMessageId);
    const prompt = assistantIndex > 0
      ? current.messages
        .slice(0, assistantIndex)
        .reverse()
        .find((message) => (
          message.role === 'user'
          && typeof message.content === 'string'
          && message.content.trim()
        ))
      : null;
    if (!prompt) {
      current.show('error', 'Regeneration unavailable', 'The original user message is no longer available.');
      return false;
    }
    void current.handleSend(prompt.content);
    return true;
  }, []);

  const clearBackendSelection = useCallback(() => {
    setSelectedBackendChatId(null);
    dispatch(clearActiveChatSession());
    lastAppliedBackendChatRequestIdRef.current = null;
  }, [dispatch]);

  const focusComposer = useCallback(() => {
    setComposerFocusTick((current) => current + 1);
  }, []);

  const dismissCurrentSession = useCallback(() => {
    activeStreamIdRef.current = null;
    recentScheduledContent.current.clear();
    setMessages([]);
    setIsGenerating(false);
    setLiveAssistantTurn(null);
    setIsClearingSession(false);
    clearBackendSelection();
    setCustomChatTitle('');
    setCustomChatTitleThreadId(null);
    setTitleDraft('');
    setIsEditingTitle(false);
    setThreadId(null);
    localStorage.removeItem(config.storage.chatHistory);
    localStorage.removeItem(CUSTOM_CHAT_TITLE_KEY);
  }, [activeStreamIdRef, clearBackendSelection, config.storage.chatHistory, setIsGenerating, setLiveAssistantTurn]);

  useEffect(() => {
    if (!selectedBackendChatId || activeBackendChatState.loading || activeBackendChat || activeBackendChatState.error) {
      return;
    }
    dismissCurrentSession();
  }, [activeBackendChat, activeBackendChatState.error, activeBackendChatState.loading, dismissCurrentSession, selectedBackendChatId]);

  const openDraftChat = useCallback(async ({ forceFreshSession = false } = {}) => {
    const session = await acquireDraftChat(forceFreshSession ? null : threadId);
    const draftThreadId = session.thread_id;

    setIsEditingTitle(false);
    if (selectedBackendChatId !== draftThreadId) {
      await openBackendChat(draftThreadId);
    }
    return session;
  }, [acquireDraftChat, openBackendChat, selectedBackendChatId, threadId]);

  const startNewChatSession = useCallback(async ({ archive = false } = {}) => {
    if (selectedBackendChatId && messages.length === 0 && chatTitle === DEFAULT_CHAT_TITLE) {
      setIsEditingTitle(false);
      focusComposer();
      return;
    }

    const previousThreadId = threadId;
    const archivedMessages = messages.map((message) => stripEphemeralStreamEvents(message));
    setIsClearingSession(true);
    // If a stream is in progress for the chat we're leaving, keep it running in
    // the background (same as switching to a different existing chat) instead
    // of cancelling it — starting a new chat shouldn't discard another chat's
    // in-flight response.
    preserveStreamInBackground();
    activeStreamIdRef.current = null;
    recentScheduledContent.current.clear();

    try {
      // materializeCurrentChatCard already no-ops if a backend card for
      // previousThreadId exists, so no need to check for one here first.
      if (previousThreadId && archivedMessages.length > 0) {
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
        forceFreshSession: Boolean(selectedBackendChatId || archivedMessages.length > 0 || !previousThreadId),
      });
      focusComposer();
      if (archiveError) {
        show('error', 'Archive failed', archiveError.message);
      }
    } catch (error) {
      setIsGenerating(false);
      setIsEditingTitle(false);
      show('error', 'New chat failed', error.message);
    } finally {
      setIsClearingSession(false);
    }
  }, [
    activeStreamIdRef,
    chatTitle,
    focusComposer,
    materializeCurrentChatCard,
    messages,
    openDraftChat,
    preserveStreamInBackground,
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
    const shouldReloadSelectedBackendChat = shouldLoadSelectedBackendChat({
      sessionItem,
      activeBackendChat,
      activeBackendChatState,
      lastAppliedRequestId: lastAppliedBackendChatRequestIdRef.current,
    });
    if (sessionItem.selected && !shouldReloadSelectedBackendChat) {
      return;
    }
    if (sessionItem.source !== 'backend' || !sessionItem.threadId) {
      clearBackendSelection();
      return;
    }

    // If we're leaving a local-only conversation, materialize it first so it
    // remains a distinct sidebar card instead of disappearing on selection.
    if (shouldMaterializeBeforeSwitching({ selectedBackendChatId, threadId, messageCount: messages.length })) {
      try {
        await materializeCurrentChatCard(chatTitle || 'Untitled chat');
      } catch (error) {
        show('error', 'Chat save failed', error.message);
        return;
      }
    }

    // If a stream is in progress for the chat we're leaving, keep it running
    // in the background and show an orange dot on that card.
    preserveStreamInBackground();
    try {
      await openBackendChat(sessionItem.threadId);
    } catch (error) {
      show('error', 'Chat load failed', error.message);
    }
  }, [
    activeBackendChat,
    activeBackendChatState,
    clearBackendSelection,
    chatTitle,
    materializeCurrentChatCard,
    messages.length,
    openBackendChat,
    preserveStreamInBackground,
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
  const totalTokenCount = useMemo(() => countTokens(messages), [messages]);
  const totalToolCount = useMemo(() => countToolInvocations(messages), [messages]);
  const firstMessageTime = useMemo(
    () => messages.find((message) => Number.isFinite(message.timestamp))?.timestamp,
    [messages],
  );
  const chatDetailError = activeBackendChatState.threadId === selectedBackendChatId
    ? activeBackendChatState.error
    : null;
  const isContentLoading = !chatDetailError && shouldShowChatContentLoading({
    backendChatsLoaded: backendChatSessionsState.loaded,
    messages,
    selectedBackendChatId,
    activeChatLoading: activeBackendChatState.loading,
    activeChatThreadId: activeBackendChatState.threadId,
    activeChatDetailThreadId: activeBackendChat?.thread_id,
    urlThreadId,
    initialSelectionPending: !hasAutoSelectedOnMountRef.current,
  });
  const isRouteDetailError = Boolean(chatDetailError && selectedBackendChatId);
  const isEmptyConversationPane =
    backendChatSessionsState.loaded
    && backendChatSessions.length === 0
    && !selectedBackendChatId
    && messages.length === 0;
  const isNewChatEmpty =
    !isContentLoading
    && !isEmptyConversationPane
    && shouldShowNewChatEmptyState({ messages, selectedBackendChatId, threadId });
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
    // "Recent" (the default) sorts pinned-first then most-recent-activity
    // first; grouping still buckets by recency afterward, so this is what
    // actually determines order *within* each bucket instead of leaving it
    // however the items happened to arrive in chats.order.
    const sortedForGrouping = sidebarSort === 'Name' ? filtered : [...filtered].sort(compareSessionItems);
    const groups = visibleGroupsForFilter({ sessionItems: sortedForGrouping, sidebarFilter, groupSessionItems });
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

  // On mount, a URL that already names a thread (bookmark, reload, shared
  // link) opens that chat directly instead of auto-selecting the top card.
  // Otherwise, auto-select the top card once sessions load (or are already
  // loaded on remount). Fires once per component lifetime; if there are no
  // backend sessions, the new-chat pane is shown naturally since
  // selectedBackendChatId stays null.
  useEffect(() => {
    if (hasAutoSelectedOnMountRef.current) return;
    // The realtime listener is established before the roster becomes loaded.
    // Wait for that synchronization point before requesting a URL-selected
    // chat, otherwise its chat.session.selected event can arrive before the
    // listener is ready and leave the page loading forever.
    if (!backendChatSessionsState.loaded) return;
    if (urlThreadId) {
      hasAutoSelectedOnMountRef.current = true;
      openBackendChat(urlThreadId).catch(() => {
        // Keep the routed id and let the active detail error render in place.
        // Falling back to a new chat here would hide the actual route failure
        // and create an unrelated draft through the initialization effect.
      });
      return;
    }
    hasAutoSelectedOnMountRef.current = true;
    const topItem = visibleSessionGroups[0]?.items[0];
    if (topItem?.source === 'backend') {
      selectSessionItem(topItem);
    }
  }, [urlThreadId, backendChatSessionsState.loaded, visibleSessionGroups, selectSessionItem, openBackendChat]);

  // Keep the URL in sync with whichever session is actually selected (either
  // a backend chat, or a local draft that already has messages). The title
  // slug is cosmetic; only the id segment is ever read back.
  const activeRouteThreadId = getActiveRouteThreadId({
    selectedBackendChatId,
    messages,
    threadId,
    urlThreadId,
    initialSelectionPending: !hasAutoSelectedOnMountRef.current,
  });
  useEntityUrlSync('/chat', activeRouteThreadId, chatTitle);

  const archiveSession = useCallback(async (sessionItem) => {
    if (sessionItem?.source === 'backend' && sessionItem.threadId) {
      const nextSessionItem = findAdjacentSessionItem(visibleSessionItems, sessionItem.id);
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
      const nextSessionItem = findAdjacentSessionItem(visibleSessionItems, sessionItem.id);
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
    const nextSessionItem = findAdjacentSessionItem(visibleSessionItems, sessionItem?.id);
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
      key: 'filters',
      label: showEventFilters ? 'Hide filters' : 'Show filters',
      role: 'menuitemcheckbox',
      checked: showEventFilters,
      active: showEventFilters,
      disabled: isContentLoading || isEmptyConversationPane,
      onClick: () => setShowEventFilters((value) => !value),
      icon: (
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
          <path d="M2.5 3.5h11l-4 5v4l-3 1.5v-5.5z" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" strokeLinecap="round" />
        </svg>
      ),
    },
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

  const liveOwnedMessageId = liveTurnStore.getSnapshot()?.assistantMessageId ?? null;
  const visibleMessages = excludeLiveOwnedMessage(messages, liveOwnedMessageId);

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
        <LiveConversationHeader
          liveTurnStore={liveTurnStore}
          messages={messages}
          totalTokenCount={totalTokenCount}
          firstMessageTime={firstMessageTime}
          isContentLoading={isContentLoading}
          isEmptyConversationPane={isEmptyConversationPane}
          isRouteDetailError={isRouteDetailError}
          showEventFilters={showEventFilters}
          streamFilters={streamFilters}
          streamFiltersStorageKey={config.storage.streamFilters}
          updateStreamFilters={updateStreamFilters}
          threadId={threadId}
          isEditingTitle={isEditingTitle}
          titleInputRef={titleInputRef}
          titleDraft={titleDraft}
          setTitleDraft={setTitleDraft}
          saveTitleEdit={saveTitleEdit}
          cancelTitleEdit={cancelTitleEdit}
          startTitleEdit={startTitleEdit}
          chatActions={chatActions}
          title={chatTitle}
        />

        <RegenerateAvailabilityContext.Provider value={isGenerating}>
          <div
            className="chat-messages sess-detail__body chat-detail-body"
            ref={messagesContainerRef}
            onScroll={handleMessagesScroll}
          >
            <div className="chat-messages__inner">
              {isRouteDetailError ? (
                <div className="chat-load-error" role="alert">
                  <h2>Unable to load conversation</h2>
                  <p>{chatDetailError}</p>
                </div>
              ) : isContentLoading ? (
                <div className="chat-loading">Loading...</div>
              ) : isEmptyConversationPane ? (
                <div className="welcome-message">
                  <h2>No conversations yet</h2>
                  <p className="muted">Click <strong>+ New</strong> to start a conversation.</p>
                </div>
              ) : isNewChatEmpty ? (
                <div className="welcome-message">
                  <h2>{welcomeMessage}</h2>
                  <QuickActions onAction={handleQuickAction} />
                </div>
              ) : (
                <>
                  {visibleMessages.map((msg) => {
                    if (msg.role === 'user' && streamFilters.user === false) return null;
                    return (
                      <HistoricalMessage
                        key={msg.id}
                        message={msg}
                        streamEventFilter={streamEventFilter}
                        hideContent={msg.role === 'assistant' && streamFilters.agent === false}
                        showImages={streamFilters.images !== false}
                        onRegenerate={msg.role === 'assistant' ? handleRegenerate : undefined}
                      />
                    );
                  })}
                  <LiveAssistantTurn
                    store={liveTurnStore}
                    messagesContainerRef={messagesContainerRef}
                    isPinnedToBottomRef={isPinnedToBottomRef}
                    streamEventFilter={streamEventFilter}
                    hideContent={streamFilters.agent === false}
                    showImages={streamFilters.images !== false}
                  />
                </>
              )}
            </div>
          </div>
        </RegenerateAvailabilityContext.Provider>

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
