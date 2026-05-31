export const CHAT_AGENT_NAME = 'Progenitor';

const SIDEBAR_GROUPS = [
  { key: 'pinned', label: 'Pinned' },
  { key: 'today', label: 'Today' },
  { key: 'yesterday', label: 'Yesterday' },
  { key: 'last-week', label: 'Last Week' },
  { key: 'earlier', label: 'Earlier' },
];

function toValidTimestamp(value) {
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

export function formatStartedTime(timestamp) {
  if (!timestamp) {
    return '';
  }
  return new Date(timestamp).toLocaleString([], {
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function deriveChatTitle(messages) {
  const firstUserMessage = messages.find((message) => message.role === 'user' && message.content);
  if (!firstUserMessage) {
    return 'New Progenitor conversation';
  }
  const title = firstUserMessage.content.replace(/\s+/g, ' ').trim();
  return title.length > 72 ? `${title.slice(0, 69)}...` : title;
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

function countTokens(messages) {
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

function buildSessionMeta({ startedAt, archivedAt, selected }) {
  if (startedAt) {
    return `Started ${formatStartedTime(startedAt)}`;
  }
  if (archivedAt) {
    return `Archived ${formatStartedTime(archivedAt)}`;
  }
  return selected ? 'Not started' : 'No activity captured';
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

function getTimeBucket(timestamp, now = Date.now()) {
  const activityAt = toValidTimestamp(timestamp);
  if (!activityAt) {
    return 'today';
  }

  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);

  const today = startOfToday.getTime();
  const yesterday = today - (24 * 60 * 60 * 1000);
  const lastWeek = today - (7 * 24 * 60 * 60 * 1000);

  if (activityAt >= today) {
    return 'today';
  }
  if (activityAt >= yesterday) {
    return 'yesterday';
  }
  if (activityAt >= lastWeek) {
    return 'last-week';
  }
  return 'earlier';
}

function buildArchivedSessionItem(entry, pinnedKeys) {
  const messages = Array.isArray(entry?.messages) ? entry.messages : [];
  const id = typeof entry?.id === 'string' ? entry.id : `archived_${Date.now()}`;
  const pinKey = typeof entry?.sessionId === 'string' && entry.sessionId ? entry.sessionId : id;
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
    meta: buildSessionMeta({ startedAt, archivedAt, selected: false }),
    detail: 'Archived',
    summary: buildSessionSummary({ messageCount, toolCount, tokenCount }),
    activityAt,
    bucket: getTimeBucket(activityAt),
    selected: false,
    pinned: pinnedKeys.has(pinKey),
  };
}

export function buildSessionItems({
  messages,
  sessionId,
  chatTitle,
  totalTokenCount,
  totalToolCount,
  firstMessageTime,
  archivedSessions = [],
  pinnedSessionIds = [],
  sharedSessionIds = [],
}) {
  const pinnedKeys = new Set(Array.isArray(pinnedSessionIds) ? pinnedSessionIds : []);
  const sharedKeys = new Set(Array.isArray(sharedSessionIds) ? sharedSessionIds : []);
  const currentSession = {
    id: sessionId || 'current-session',
    pinKey: sessionId || 'current-session',
    title: chatTitle,
    meta: buildSessionMeta({ startedAt: firstMessageTime, archivedAt: null, selected: true }),
    detail: 'Current',
    summary: buildSessionSummary({
      messageCount: messages.length,
      toolCount: totalToolCount,
      tokenCount: totalTokenCount,
    }),
    activityAt: getLastMessageTimestamp(messages) ?? firstMessageTime ?? Date.now(),
    bucket: getTimeBucket(getLastMessageTimestamp(messages) ?? firstMessageTime ?? Date.now()),
    selected: true,
    pinned: pinnedKeys.has(sessionId || 'current-session'),
    shared: sharedKeys.has(sessionId || 'current-session'),
  };

  const archivedItems = Array.isArray(archivedSessions)
    ? archivedSessions.map((entry) => ({
      ...buildArchivedSessionItem(entry, pinnedKeys),
      shared: sharedKeys.has(
        (typeof entry?.sessionId === 'string' && entry.sessionId)
          ? entry.sessionId
          : (typeof entry?.id === 'string' ? entry.id : ''),
      ),
    }))
    : [];

  return [currentSession, ...archivedItems].sort((left, right) => right.activityAt - left.activityAt);
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
      items: (buckets.get(group.key) || []).sort((left, right) => right.activityAt - left.activityAt),
    }))
    .filter((group) => group.items.length > 0);
}
