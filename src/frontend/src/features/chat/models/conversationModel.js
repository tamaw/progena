import { extractMarkdownImages } from '../../../lib/markdown';
import { PERSISTED_CATEGORIES, categoryForEventType } from './streamFilters';

export const DEFAULT_CHAT_TITLE = 'New chat';

export function shouldShowChatContentLoading({
  backendChatsLoaded,
  messages,
  selectedBackendChatId,
  activeChatLoading,
  activeChatThreadId,
  activeChatDetailThreadId,
  urlThreadId,
  initialSelectionPending = false,
}) {
  const hasMessages = Array.isArray(messages) && messages.length > 0;
  const urlChatLoading = Boolean(urlThreadId && initialSelectionPending);
  const selectedChatLoading = Boolean(selectedBackendChatId) && (
    activeChatLoading
    || activeChatThreadId !== selectedBackendChatId
    || activeChatDetailThreadId !== selectedBackendChatId
  );

  return urlChatLoading || selectedChatLoading || (
    !backendChatsLoaded
    && !hasMessages
    && !selectedBackendChatId
  );
}

export function shouldShowNewChatEmptyState({
  messages,
  selectedBackendChatId,
  threadId,
}) {
  return (
    Array.isArray(messages)
    && messages.length === 0
    && Boolean(selectedBackendChatId || threadId)
  );
}

export function getActiveRouteThreadId({
  selectedBackendChatId,
  messages,
  threadId,
  urlThreadId,
  initialSelectionPending,
}) {
  return (
    selectedBackendChatId
    || (Array.isArray(messages) && messages.length > 0 ? threadId : null)
    || (initialSelectionPending ? urlThreadId : null)
    || threadId
    || null
  );
}

export function shouldDeferInitialRosterSelection({
  draftBootstrapSettled,
  selectedBackendChatId,
  threadId,
  messageCount,
}) {
  return !draftBootstrapSettled || Boolean(selectedBackendChatId || threadId || messageCount > 0);
}

export function shouldDeferRouteSelection({
  pendingThreadId,
  selectionIntentThreadId,
  urlThreadId,
  selectedBackendChatId,
}) {
  return Boolean(
    pendingThreadId === urlThreadId
    || (selectionIntentThreadId && selectionIntentThreadId !== urlThreadId)
    || selectedBackendChatId === urlThreadId,
  );
}

export function isReopeningCurrentLocalDraft({
  selectedBackendChatId,
  currentThreadId,
  requestedThreadId,
}) {
  return selectedBackendChatId === null
    && Boolean(requestedThreadId)
    && currentThreadId === requestedThreadId;
}

export function shouldAdoptMaterializedDraft({
  selectedBackendChatId,
  threadId,
  sessionThreadId,
}) {
  return selectedBackendChatId === null
    && Boolean(threadId)
    && sessionThreadId === threadId;
}

// On mount, a routed URL must always end up with a real detail request behind
// it. Skipping the open is only safe when a request for that thread is already
// in flight or its detail is already loaded (SPA revisit). A bare
// selectedBackendChatId match is NOT enough: a fresh page load initializes the
// selection from the URL with no request behind it, and skipping the open left
// the page without a detail request — the empty-selection dismissal then
// bounced every direct /chat/<id> load back to /chat.
export function shouldSkipRoutedMountOpen({
  pendingThreadId,
  selectedBackendChatId,
  urlThreadId,
  activeDetailThreadId,
  activeLoading,
  activeLoadingThreadId,
}) {
  return Boolean(
    pendingThreadId === urlThreadId
    || (selectedBackendChatId === urlThreadId
      && (activeDetailThreadId === urlThreadId
        || (activeLoading && activeLoadingThreadId === urlThreadId))),
  );
}

// A selection with no detail, no error, and no request in flight refers to an
// empty or deleted chat and should be dismissed — but only after the mount
// effect has issued the initial URL/roster selection. Before that point a
// URL-initialized selection legitimately has no request yet, and dismissing
// it would discard the routed chat before its detail request even starts.
export function shouldDismissEmptySelection({
  hasAutoSelectedOnMount,
  selectedBackendChatId,
  activeLoading,
  activeDetail,
  activeError,
  hasMaterializedDraft = false,
}) {
  return Boolean(
    hasAutoSelectedOnMount
    && selectedBackendChatId
    && !activeLoading
    && !activeDetail
    && !activeError
    && !hasMaterializedDraft,
  );
}

function eventRenderSignature(event) {
  const metadata = event?.metadata && typeof event.metadata === 'object' ? event.metadata : {};
  return {
    eventType: typeof event?.eventType === 'string' ? event.eventType : '',
    content: typeof event?.content === 'string' ? event.content : '',
    toolCallId: typeof metadata.tool_call_id === 'string' ? metadata.tool_call_id : '',
    toolName: typeof metadata.tool_name === 'string' ? metadata.tool_name : '',
    success: metadata.success === true ? true : metadata.success === false ? false : null,
    resultPreview: typeof metadata.result_preview === 'string' ? metadata.result_preview : '',
    imageCount: Array.isArray(event?.images) ? event.images.length : 0,
  };
}

// Backend checkpoints never persist ephemeral/debug-only stream event
// categories (see PERSISTED_CATEGORIES), so a just-finalized local message
// always carries strictly more streamEvents than the canonical reconciliation
// fetch that follows it. Comparing on the full unfiltered list made every
// foreground send's own completion look non-equivalent to its own canonical
// echo, forcing an unwanted swap-and-remount (visible as a "refresh"/flicker,
// with the debug-only events disappearing) moments after every reply. Only
// persisted categories are meaningful for deciding whether the canonical copy
// actually differs from what's on screen.
function persistedStreamEvents(streamEvents) {
  if (!Array.isArray(streamEvents)) {
    return [];
  }
  return streamEvents.filter((event) => (
    PERSISTED_CATEGORIES.has(categoryForEventType(event?.eventType, event?.metadata))
  ));
}

// Provider usage is part of the rendered terminal result. Once a canonical
// response arrives, replace a same-content local row when any provider-reported
// usage/status value differs so live, reloaded, and reconciled views agree.
function contextUsageRenderSignature(contextUsage) {
  return {
    inputTokens: contextUsage?.inputTokens ?? null,
    outputTokens: contextUsage?.outputTokens ?? null,
    totalTokens: contextUsage?.totalTokens ?? null,
    profileName: contextUsage?.profileName ?? '',
    deployment: contextUsage?.deployment ?? '',
    providerId: contextUsage?.providerId ?? '',
    compaction: contextUsage?.compaction ?? null,
  };
}

function messageRenderSignature(message) {
  return {
    role: typeof message?.role === 'string' ? message.role : '',
    content: typeof message?.content === 'string' ? message.content : '',
    tokenCount: message?.tokenCount ?? null,
    finalCallTokenCount: message?.finalCallTokenCount ?? null,
    reasoningTokenCount: message?.reasoningTokenCount ?? null,
    cacheReadTokenCount: message?.cacheReadTokenCount ?? null,
    responseStatus: typeof message?.responseStatus === 'string' ? message.responseStatus : 'unknown',
    contextUsage: contextUsageRenderSignature(message?.contextUsage),
    reasoningText: typeof message?.reasoningText === 'string' ? message.reasoningText : '',
    agentName: typeof message?.agentName === 'string' ? message.agentName : '',
    streamEvents: persistedStreamEvents(message?.streamEvents).map(eventRenderSignature),
  };
}

// Persisted messages have backend IDs and rebuilt timeline items, while a
// foreground live turn has client IDs and incremental timeline state. Compare
// only fields that affect rendering before replacing the local transcript.
export function areChatMessagesRenderEquivalent(localMessage, persistedMessage) {
  if (localMessage?.role !== persistedMessage?.role) {
    return false;
  }
  return JSON.stringify(messageRenderSignature(localMessage))
    === JSON.stringify(messageRenderSignature(persistedMessage));
}

export function areChatTranscriptsEquivalent(localMessages, persistedMessages) {
  if (!Array.isArray(localMessages) || !Array.isArray(persistedMessages)) {
    return false;
  }
  if (localMessages.length !== persistedMessages.length) {
    return false;
  }
  return localMessages.every((message, index) => (
    areChatMessagesRenderEquivalent(message, persistedMessages[index])
  ));
}

// Canonical reconciliation may correct one terminal row. Keep every unchanged
// local object so memoized historical cards retain their rendered Markdown and
// do not re-highlight earlier code blocks when only another row changed.
export function mergeCanonicalMessages(localMessages, canonicalMessages) {
  if (!Array.isArray(localMessages) || !Array.isArray(canonicalMessages)) {
    return canonicalMessages;
  }

  const merged = canonicalMessages.map((canonicalMessage, index) => (
    areChatMessagesRenderEquivalent(localMessages[index], canonicalMessage)
      ? localMessages[index]
      : canonicalMessage
  ));
  const unchanged = merged.length === localMessages.length
    && merged.every((message, index) => message === localMessages[index]);
  return unchanged ? localMessages : merged;
}

// A non-equivalent canonical transcript carries backend identities. Retire a
// finalized live owner for that same terminal stream before both can render.
export function shouldRetireLiveOwnerAfterCanonicalReconciliation(liveTurn, streamId) {
  return Boolean(
    liveTurn?.assistantMessageId
    && liveTurn.streamId === streamId,
  );
}

// A settled foreground turn remains mounted briefly as the visual owner of its
// final row. Hide its committed history twin until that owner changes or is
// cleared, so terminal completion does not render the same assistant message
// twice.
export function excludeLiveOwnedMessage(messages, liveAssistantMessageId) {
  if (!Array.isArray(messages) || !liveAssistantMessageId) {
    return messages;
  }
  const liveMessageIndex = messages.findIndex((message) => message?.id === liveAssistantMessageId);
  if (liveMessageIndex < 0) {
    return messages;
  }
  return [
    ...messages.slice(0, liveMessageIndex),
    ...messages.slice(liveMessageIndex + 1),
  ];
}

const SIDEBAR_GROUPS = [
  { key: 'pinned', label: 'Pinned' },
  { key: 'today', label: 'Today' },
  { key: 'yesterday', label: 'Yesterday' },
  { key: 'this-week', label: 'This Week' },
  { key: 'last-week', label: 'Last Week' },
  { key: 'later', label: 'Later' },
];

export function toValidTimestamp(value) {
  if (Number.isFinite(value)) {
    return value;
  }

  if (typeof value === 'string' && value) {
    const timestamp = new Date(value).getTime();
    return Number.isFinite(timestamp) ? timestamp : null;
  }

  return null;
}

function pluralize(count, singular, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

// Weekday + time reads fine for anything from this week, but goes ambiguous
// past that ("Mon, 02:15 PM" -- which Monday?), so anything 7+ days old also
// gets a short date.
export function formatStartedTime(timestamp, now = Date.now()) {
  const value = toValidTimestamp(timestamp);
  if (!value) {
    return '';
  }
  const days = Math.floor(Math.max(0, now - value) / 86400000);
  return new Date(value).toLocaleString([], {
    ...(days >= 7 ? { month: 'short', day: 'numeric' } : { weekday: 'short' }),
    hour: '2-digit',
    minute: '2-digit',
  });
}

// Compact, glanceable age for sidebar cards: "now", "4m ago", "3h ago",
// "2d ago", "5w ago", then a short date once it stops being useful as a
// relative offset. Kept terse so it sits quietly in the card's meta row.
export function formatRelativeAge(timestamp, now = Date.now()) {
  const value = toValidTimestamp(timestamp);
  if (!value) {
    return '';
  }
  const diffSeconds = Math.max(0, Math.floor((now - value) / 1000));
  if (diffSeconds < 45) {
    return 'now';
  }
  const minutes = Math.floor(diffSeconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }
  const days = Math.floor(hours / 24);
  if (days < 7) {
    return `${days}d ago`;
  }
  const weeks = Math.floor(days / 7);
  if (weeks < 5) {
    return `${weeks}w ago`;
  }
  return new Date(value).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

// Flatten a stored message body into a single-line preview for the sidebar
// card: strip ":::directive" fences, collapse whitespace/newlines, trim. The
// CSS ellipsis handles truncation, so we only need a clean one-liner here.
export function formatPreview(text) {
  if (typeof text !== 'string' || !text) {
    return '';
  }
  return text
    .replace(/:::[^\n]*\n?/g, ' ')
    .replace(/`+/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

// Last non-empty message body in a thread, used to derive a card preview when
// the backend hasn't supplied one (e.g. the live local session).
export function lastMessageContent(messages) {
  if (!Array.isArray(messages)) {
    return '';
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const content = messages[index]?.content;
    if (typeof content === 'string' && content.trim()) {
      return content;
    }
  }
  return '';
}

export function deriveChatTitle(messages) {
  const firstUserMessage = messages.find((message) => message.role === 'user' && message.content);
  if (!firstUserMessage) {
    return DEFAULT_CHAT_TITLE;
  }
  const title = firstUserMessage.content.replace(/\s+/g, ' ').trim();
  return title.length > 72 ? `${title.slice(0, 69)}...` : title;
}

// Single source of truth for "what images does this message have" -- tool-result
// (base64) images and markdown images from the message text are both image
// responses in the same sense, so both the image strip (Message.jsx) and the
// Images filter count (ConversationPage.jsx) must agree on this list, or toggling
// the filter and the strip's contents can silently drift apart.
export function collectMessageImages(message) {
  const role = message?.role;
  const content = message?.content;
  const streamEvents = Array.isArray(message?.streamEvents) ? message.streamEvents : [];

  const toolImages = role === 'assistant'
    ? streamEvents
      // subagent_result carries a `task` tool delegation's own images the
      // same way tool_result does for a plain tool call (see
      // _expand_task_subagent_events in deepagent_stream.py, which fully
      // replaces the raw tool_result event with this one) -- both must be
      // recognized here or a subagent's images silently vanish from both
      // the strip and the Images filter count.
      .filter((event) => (
        (event?.eventType === 'tool_result' || event?.eventType === 'subagent_result')
        && Array.isArray(event.images) && event.images.length > 0
      ))
      .flatMap((event) => event.images)
      .map((image, index) => ({
        id: image.id || `tool-${index}`,
        src: image.url || `data:${image.media_type || 'image/jpeg'};base64,${image.base64}`,
        alt: `Image ${index + 1}`,
      }))
    : [];

  const markdownImages = extractMarkdownImages(content).map((image, index) => ({
    id: `md-${index}`,
    src: image.src,
    alt: image.alt || `Image ${index + 1}`,
  }));

  return [...toolImages, ...markdownImages];
}

export function countToolInvocations(messages) {
  return messages.reduce((total, message) => (
    total + (
      Array.isArray(message?.streamEvents)
        ? message.streamEvents.filter((event) => event?.eventType === 'tool_start').length
        : 0
    )
  ), 0);
}

export function countTokens(messages) {
  return messages.reduce(
    (total, message) => total + (Number.isFinite(message?.tokenCount) ? message.tokenCount : 0),
    0,
  );
}

function getFirstMessageTimestamp(messages) {
  return messages.find((message) => Number.isFinite(message?.timestamp))?.timestamp ?? null;
}

function getLastMessageTimestamp(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (Number.isFinite(messages[index]?.timestamp)) {
      return messages[index].timestamp;
    }
  }
  return null;
}

function buildSessionMeta({ activityAt, archivedAt }) {
  return formatRelativeAge(archivedAt ?? activityAt) || '';
}

function buildSessionSummary({ messageCount, toolCount, tokenCount }) {
  const parts = [
    pluralize(messageCount, 'message'),
    pluralize(toolCount, 'tool'),
  ];

  if (Number.isFinite(tokenCount) && tokenCount > 0) {
    parts.push(`${new Intl.NumberFormat().format(tokenCount)} tokens`);
  }

  return parts.join(' · ');
}

export function getTimeBucket(timestamp, now = Date.now()) {
  const activityAt = toValidTimestamp(timestamp);
  if (!activityAt) {
    return 'today';
  }

  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);

  const today = startOfToday.getTime();
  const yesterday = today - (24 * 60 * 60 * 1000);
  const thisWeek = today - (7 * 24 * 60 * 60 * 1000);
  const lastWeek = today - (14 * 24 * 60 * 60 * 1000);

  if (activityAt >= today) {
    return 'today';
  }
  if (activityAt >= yesterday) {
    return 'yesterday';
  }
  if (activityAt >= thisWeek) {
    return 'this-week';
  }
  if (activityAt >= lastWeek) {
    return 'last-week';
  }
  return 'later';
}

function buildArchivedSessionItem(entry, pinnedKeys) {
  const messages = Array.isArray(entry?.messages) ? entry.messages : [];
  const id = typeof entry?.id === 'string' ? entry.id : `archived_${Date.now()}`;
  const pinKey = typeof entry?.threadId === 'string' && entry.threadId ? entry.threadId : id;
  const startedAt = toValidTimestamp(entry?.startedAt) ?? getFirstMessageTimestamp(messages);
  const archivedAt = toValidTimestamp(entry?.archivedAt);
  const activityAt =
    toValidTimestamp(entry?.lastActivityAt)
    ?? getLastMessageTimestamp(messages)
    ?? archivedAt
    ?? startedAt
    ?? Date.now();
  const messageCount = Number.isFinite(entry?.messageCount) ? entry.messageCount : messages.length;
  const toolCount = Number.isFinite(entry?.toolCount) ? entry.toolCount : countToolInvocations(messages);
  const tokenCount = Number.isFinite(entry?.tokenCount) ? entry.tokenCount : countTokens(messages);

  return {
    id,
    pinKey,
    title: typeof entry?.title === 'string' && entry.title.trim()
      ? entry.title.trim()
      : deriveChatTitle(messages),
    meta: buildSessionMeta({ activityAt: activityAt, archivedAt }),
    detail: 'Archived',
    summary: buildSessionSummary({ messageCount, toolCount, tokenCount }),
    activityAt,
    bucket: getTimeBucket(activityAt),
    preview: formatPreview(lastMessageContent(messages)),
    selected: false,
    pinned: pinnedKeys.has(pinKey),
  };
}

export function buildArchivedSessionItems({
  archivedSessions = [],
  pinnedSessionIds = [],
}) {
  const pinnedKeys = new Set(Array.isArray(pinnedSessionIds) ? pinnedSessionIds : []);
  return Array.isArray(archivedSessions)
    ? archivedSessions.map((entry) => buildArchivedSessionItem(entry, pinnedKeys))
    : [];
}

// The current (unsaved) draft session card and the user's locally-archived
// chat history are two independent concerns bundled here only for shared
// pinned/shared-id bookkeeping. `includeCurrentSession` should be false once
// a backend chat is selected (the draft is no longer "current"), but archived
// items must NOT be gated on that same flag - they're persisted history, not
// the transient draft, and hiding them meant they briefly vanished from the
// sidebar (and got pruned out of pinned/shared ids) the instant any backend
// chat was selected.
export function buildSessionItems({
  messages,
  threadId,
  chatTitle,
  totalTokenCount,
  totalToolCount,
  firstMessageTime,
  archivedSessions = [],
  pinnedSessionIds = [],
  includeCurrentSession = true,
}) {
  const pinnedKeys = new Set(Array.isArray(pinnedSessionIds) ? pinnedSessionIds : []);
  const currentSession = includeCurrentSession ? {
    id: threadId || 'current-session',
    pinKey: threadId || 'current-session',
    title: chatTitle,
    meta: buildSessionMeta({ activityAt: getLastMessageTimestamp(messages) ?? firstMessageTime, archivedAt: null }),
    detail: '',
    summary: buildSessionSummary({
      messageCount: messages.length,
      toolCount: totalToolCount,
      tokenCount: totalTokenCount,
    }),
    activityAt: getLastMessageTimestamp(messages) ?? firstMessageTime ?? Date.now(),
    bucket: getTimeBucket(getLastMessageTimestamp(messages) ?? firstMessageTime ?? Date.now()),
    preview: formatPreview(lastMessageContent(messages)),
    selected: true,
    pinned: pinnedKeys.has(threadId || 'current-session'),
    messageCount: messages.length,
    toolCount: totalToolCount,
    errorCount: 0,
  } : null;

  const archivedItems = buildArchivedSessionItems({ archivedSessions, pinnedSessionIds });

  return [...(currentSession ? [currentSession] : []), ...archivedItems]
    .sort((left, right) => right.activityAt - left.activityAt);
}

export function groupSessionItems(items) {
  const buckets = new Map(SIDEBAR_GROUPS.map((group) => [group.key, []]));

  items.forEach((item) => {
    if (item.pinned) {
      buckets.get('pinned')?.push(item);
      return;
    }
    buckets.get(item.bucket)?.push(item);
  });

  return SIDEBAR_GROUPS
    .map((group) => ({
      ...group,
      items: buckets.get(group.key) || [],
    }))
    .filter((group) => group.items.length > 0);
}
