import { PERSISTED_CATEGORIES, categoryForEventType } from './streamFilters';
import { appendStreamEvent, normalizeStreamEvent } from './streamEventsModel';
import { buildDefaultAssistantTimeline } from './liveAssistantTurnModel';
import { normalizeContextUsage } from './contextWindowModel';

const WEEKDAY_WELCOME_MESSAGES = [
  'Sunday reset 🌿',
  'Monday kickoff ☕',
  'Tuesday tune-up 🔧',
  'Wednesday momentum ⚡',
  'Thursday deep dive 🤿',
  'Friday finish line 🏁',
  'Saturday side quest ✨',
];

export function getDailyWelcomeMessage(date = new Date()) {
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

const RESPONSE_STATUSES = new Set(['running', 'completed', 'failed', 'cancelled', 'interrupted', 'unknown']);

function normalizeResponseStatus(value) {
  if (value === 'incomplete') return 'interrupted';
  return typeof value === 'string' && RESPONSE_STATUSES.has(value) ? value : 'unknown';
}

function normalizeOutputTokenCount(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}

const PROVIDER_USAGE_PROVENANCE = 'provider';

function hasAuthoritativeUsage(message) {
  return message?.usageProvenance === PROVIDER_USAGE_PROVENANCE;
}

function normalizeTimelineUsage(timelineItems, message) {
  if (!Array.isArray(timelineItems)) return timelineItems;
  const authoritative = hasAuthoritativeUsage(message);
  const tokenCount = authoritative ? normalizeOutputTokenCount(message.tokenCount) : null;
  const finalCallTokenCount = authoritative ? normalizeOutputTokenCount(message.finalCallTokenCount) : null;
  return timelineItems.map((item) => {
    if (item?.kind !== 'assistant_message') return item;
    const {
      tokenCount: _tokenCount,
      finalCallTokenCount: _finalCallTokenCount,
      usageProvenance: _usageProvenance,
      ...withoutUsage
    } = item;
    return authoritative
      ? {
        ...withoutUsage,
        tokenCount,
        finalCallTokenCount,
        usageProvenance: PROVIDER_USAGE_PROVENANCE,
      }
      : withoutUsage;
  });
}

// Local cache and handoffs can contain prior releases' transport chunk
// counters. Preserve counts only when their provider provenance is explicit.
export function retainAuthoritativeUsage(message) {
  if (!message || typeof message !== 'object') return message;
  const {
    tokenCount: _tokenCount,
    finalCallTokenCount: _finalCallTokenCount,
    usageProvenance: _usageProvenance,
    ...withoutUsage
  } = message;
  if (!hasAuthoritativeUsage(message)) {
    return Array.isArray(message.timelineItems)
      ? { ...withoutUsage, timelineItems: normalizeTimelineUsage(message.timelineItems, message) }
      : withoutUsage;
  }
  const retained = {
    ...withoutUsage,
    tokenCount: normalizeOutputTokenCount(message.tokenCount),
    finalCallTokenCount: normalizeOutputTokenCount(message.finalCallTokenCount),
    usageProvenance: PROVIDER_USAGE_PROVENANCE,
  };
  return Array.isArray(message.timelineItems)
    ? { ...retained, timelineItems: normalizeTimelineUsage(message.timelineItems, retained) }
    : retained;
}

/**
 * `preserveTimeline` is only for trusted in-memory handoffs (Quick Chat
 * promotion), whose timelines can hold partial answers absent from `content`.
 */
export function normalizeStoredMessage(message, index, { preserveTimeline = false } = {}) {
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
    tokenCount: hasAuthoritativeUsage(message) ? normalizeOutputTokenCount(message.tokenCount) : null,
    finalCallTokenCount: hasAuthoritativeUsage(message)
      ? normalizeOutputTokenCount(message.finalCallTokenCount)
      : null,
    usageProvenance: hasAuthoritativeUsage(message) ? PROVIDER_USAGE_PROVENANCE : null,
    responseStatus: normalizeResponseStatus(message.responseStatus),
    reasoningTokenCount: Number.isFinite(message.reasoningTokenCount)
      ? message.reasoningTokenCount
      : 0,
    cacheReadTokenCount: Number.isFinite(message.cacheReadTokenCount)
      ? message.cacheReadTokenCount
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
    contextUsage: normalizeContextUsage(message.contextUsage ?? message.context_usage),
  };

  // Stored timelineItems are legacy denormalized data. Rebuild the assistant
  // timeline from the normalized streamEvents unless the caller trusts it.
  if (role === 'assistant') {
    base.timelineItems = preserveTimeline && Array.isArray(message.timelineItems) && message.timelineItems.length > 0
      ? normalizeTimelineUsage(message.timelineItems, base)
      : buildDefaultAssistantTimeline(base);
  }

  return base;
}

export function stripEphemeralStreamEvents(message) {
  const retainedUsage = retainAuthoritativeUsage(message);
  const events = Array.isArray(retainedUsage?.streamEvents) ? retainedUsage.streamEvents : [];
  // Keep only persisted categories; also strip base64 images to keep localStorage bounded.
  const persistable = events
    .filter((event) => PERSISTED_CATEGORIES.has(categoryForEventType(event.eventType, event.metadata)))
    .map((event) => (event.images ? { ...event, images: undefined } : event));
  if (persistable.length === events.length && !events.some((event) => event.images)) {
    return retainedUsage;
  }
  return { ...retainedUsage, streamEvents: persistable };
}
