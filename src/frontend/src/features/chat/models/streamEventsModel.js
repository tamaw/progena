// Stream-event normalization and merge helpers shared by the conversation page
// and the message renderer. These are pure functions over the in-memory event
// shape ({ id, eventType, content, metadata, images? }).

export const MAX_STREAM_EVENTS_PER_MESSAGE = 200;

function normalizeStreamEventTimestamp(value) {
  if (Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value) {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

export function normalizeStreamEvent(event, index) {
  if (!event || typeof event !== 'object') {
    return null;
  }

  const eventType = typeof event.eventType === 'string'
    ? event.eventType
    : (typeof event.event_type === 'string' ? event.event_type : 'stream_event');
  const metadata = event.metadata && typeof event.metadata === 'object' && !Array.isArray(event.metadata)
    ? event.metadata
    : {};
  const normalizedEvent = {
    id: typeof event.id === 'string' ? event.id : `evt_restored_${index}_${Date.now()}`,
    eventType,
    content: typeof event.content === 'string' ? event.content : '',
    metadata: isReasoningEvent({ eventType })
      ? { source: 'stream_reasoning', ...metadata }
      : metadata,
    timestamp: normalizeStreamEventTimestamp(event.timestamp),
  };

  if (Array.isArray(event.images) && event.images.length > 0) {
    normalizedEvent.images = event.images;
  }

  return normalizedEvent;
}

export function createStreamEvent(streamEvent) {
  if (!streamEvent || typeof streamEvent !== 'object') {
    return null;
  }

  const eventType = typeof streamEvent.event_type === 'string' ? streamEvent.event_type : 'stream_event';
  const metadata =
    streamEvent.metadata
    && typeof streamEvent.metadata === 'object'
    && !Array.isArray(streamEvent.metadata)
      ? streamEvent.metadata
      : {};
  const event = {
    id: `evt_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    eventType,
    content: typeof streamEvent.content === 'string' ? streamEvent.content : '',
    metadata: isReasoningEvent({ eventType })
      ? { source: 'stream_reasoning', ...metadata }
      : metadata,
    timestamp: Date.now(),
  };

  if (Array.isArray(streamEvent.images) && streamEvent.images.length > 0) {
    event.images = streamEvent.images;
  }

  return event;
}

export function isReasoningEvent(event) {
  return event?.eventType === 'reasoning_delta' || event?.eventType === 'reasoning_summary';
}

export function hasReasoningContent(event) {
  return typeof event?.content === 'string' && event.content.trim().length > 0;
}

export function isRenderableStreamEvent(event) {
  const eventType = typeof event?.eventType === 'string'
    ? event.eventType
    : (typeof event?.event_type === 'string' ? event.event_type : '');
  if (!eventType || eventType === 'message_delta' || eventType === 'assistant_metadata' || eventType === 'usage_update') {
    return false;
  }
  if (eventType !== 'node_update') {
    return true;
  }
  const metadata = event?.metadata && typeof event.metadata === 'object' && !Array.isArray(event.metadata)
    ? event.metadata
    : {};
  return typeof metadata.node_name === 'string'
    && metadata.node_name.length > 0
    && metadata.node_name !== 'pending_response';
}

export function countReasoningTimelineEntries(events, reasoningText) {
  const streamEvents = Array.isArray(events) ? events : [];
  const hasReasoningEvent = streamEvents.some((event) =>
    isReasoningEvent(event) && hasReasoningContent(event)
  );
  const reasoningEventCount = streamEvents.filter((event) =>
    event?.eventType === 'reasoning_summary' && hasReasoningContent(event)
  ).length;
  if (hasReasoningEvent) {
    return reasoningEventCount;
  }
  return typeof reasoningText === 'string' && reasoningText.trim().length > 0 ? 1 : 0;
}

export function extractReasoningTokenCount(event) {
  const tokenCount = event?.metadata?.reasoning_tokens;
  return Number.isFinite(tokenCount) && tokenCount > 0 ? tokenCount : null;
}

export function extractCacheReadTokenCount(event) {
  const tokenCount = event?.metadata?.cache_read_tokens;
  return Number.isFinite(tokenCount) && tokenCount > 0 ? tokenCount : null;
}

export function appendReasoningText(currentText, event) {
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

export function mergeReasoningEvents(existingEvent, nextEvent) {
  const existingContent = hasReasoningContent(existingEvent) ? existingEvent.content : '';
  let mergedContent = existingContent;

  if (nextEvent.eventType === 'reasoning_delta') {
    mergedContent = `${existingContent}${nextEvent.content}`;
  } else if (hasReasoningContent(nextEvent) && nextEvent.content.length >= existingContent.length) {
    mergedContent = nextEvent.content;
  }

  const metadata = {
    ...existingEvent.metadata,
    ...nextEvent.metadata,
  };
  if (nextEvent.eventType === 'reasoning_delta' && mergedContent) {
    metadata.summary = mergedContent;
    metadata.text_length = mergedContent.length;
  }

  return {
    ...existingEvent,
    eventType: nextEvent.eventType === 'reasoning_summary'
      ? 'reasoning_summary'
      : existingEvent.eventType,
    content: mergedContent,
    metadata,
  };
}

export function appendStreamEvent(events, nextEvent) {
  const currentEvents = Array.isArray(events) ? events : [];
  if (nextEvent.eventType === 'message_delta') {
    return currentEvents;
  }
  if (isReasoningEvent(nextEvent)) {
    if (
      nextEvent.eventType === 'reasoning_delta'
      && currentEvents.some((event) => event?.eventType === 'reasoning_summary')
    ) {
      return currentEvents;
    }
    const firstReasoningIndex = currentEvents.findIndex((event) =>
      isReasoningEvent(event) && hasReasoningContent(event)
    );
    if (nextEvent.eventType === 'reasoning_summary' && firstReasoningIndex >= 0) {
      return currentEvents.map((event, index) => (
        index === firstReasoningIndex ? mergeReasoningEvents(event, nextEvent) : event
      ));
    }

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

// Finalize an assistant placeholder that is being superseded (new send / new
// chat) so its spinner clears. Returns null to drop an empty placeholder.
export function finalizeSupersededAssistant(message) {
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
    interruptionReason: 'user_cancelled',
  };
}
