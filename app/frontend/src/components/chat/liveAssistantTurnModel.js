/**
 * Pure model for an in-flight assistant turn (live turn).
 *
 * A live assistant turn holds everything the frontend knows about an ongoing
 * assistant response before it is finalised and pushed into the committed
 * `messages` array.  State transitions are driven by stream events arriving
 * from the backend IPC stream:
 *
 *   1. `assistant_metadata` → updates display metadata only.
 *   2. First visible stream event (`tool_start`, `tool_result`, `node_update`,
 *      reasoning events) → appends a stream_event timeline item but does NOT
 *      create the assistant text row.
 *   3. First text chunk → materialises the assistant text row (assigns
 *      `assistantMessageId`, stamps `assistantTimestamp = now`).
 *   4. Later chunks → update only that assistant row's content/tokenCount.
 *   5. Completion / error → finalises the turn into a committed message.
 *
 * Kept as a separate module so the state machine can be unit tested without
 * rendering a React hook.
 */

import { createStreamEvent, appendStreamEvent, appendReasoningText, extractReasoningTokenCount } from './streamEventsModel';
import { CHAT_AGENT_NAME } from './conversationModel';

/** Create a fresh live assistant turn with no visible output yet. */
export function createLiveAssistantTurn({ streamId, startedAt }) {
  return {
    streamId,
    startedAt,
    assistantMessageId: null,
    assistantTimestamp: null,
    content: '',
    tokenCount: 0,
    reasoningText: '',
    reasoningTokenCount: 0,
    streamEvents: [],
    displayAgentName: '',
    displayModelName: '',
    displayToolCalls: [],
    timelineItems: [],
    isStreaming: true,
    error: false,
  };
}

/** Apply display metadata from an `assistant_metadata` control event. */
export function applyAssistantMetadata(turn, metadata) {
  const next = { ...turn };
  if (typeof metadata.display_agent_name === 'string' && metadata.display_agent_name) {
    next.displayAgentName = metadata.display_agent_name;
  }
  if (typeof metadata.display_model_name === 'string' && metadata.display_model_name) {
    next.displayModelName = metadata.display_model_name;
  }
  if (Array.isArray(metadata.display_tool_calls)) {
    next.displayToolCalls = metadata.display_tool_calls;
  }
  return next;
}

/** Apply a non-visual stream event (control event like node metadata). */
export function applyAssistantStreamEvent(turn, streamEvent, now) {
  const nextEvent = createStreamEvent(streamEvent);
  if (!nextEvent) return turn;

  // assistant_metadata is handled by applyAssistantMetadata, not here.
  if (nextEvent.eventType === 'assistant_metadata' || nextEvent.eventType === 'message_delta') {
    return turn;
  }

  const next = { ...turn };
  next.streamEvents = appendStreamEvent(next.streamEvents, nextEvent);
  next.reasoningText = appendReasoningText(next.reasoningText, nextEvent);
  const rt = extractReasoningTokenCount(nextEvent);
  if (rt !== null) next.reasoningTokenCount = rt;

  // Reasoning events (reasoning_delta / reasoning_summary) share a single slot
  // at the end of the timeline so streaming deltas don't create duplicate rows.
  const isReasoningKind = nextEvent.eventType === 'reasoning_delta' || nextEvent.eventType === 'reasoning_summary';
  if (isReasoningKind) {
    const lastTimeline = next.timelineItems[next.timelineItems.length - 1];
    const lastIsReasoning = lastTimeline?.kind === 'stream_event' && (
      lastTimeline.event.eventType === 'reasoning_delta' || lastTimeline.event.eventType === 'reasoning_summary'
    );
    if (lastIsReasoning) {
      // Replace the trailing reasoning item (streaming update or summary upgrade).
      next.timelineItems = [
        ...next.timelineItems.slice(0, -1),
        { kind: 'stream_event', id: nextEvent.id, event: nextEvent },
      ];
    } else if (nextEvent.eventType === 'reasoning_summary') {
      // First reasoning_summary with no prior row — add it.
      next.timelineItems = [...next.timelineItems, { kind: 'stream_event', id: nextEvent.id, event: nextEvent }];
    }
    // reasoning_delta with no prior reasoning row is silently dropped.
    return next;
  }

  // All other visible events.
  const isVisibleKind = nextEvent.eventType !== 'node_update' || (
    nextEvent.metadata?.node_name && nextEvent.metadata.node_name !== 'pending_response'
  );
  if (isVisibleKind) {
    next.timelineItems = [...next.timelineItems, { kind: 'stream_event', id: nextEvent.id, event: nextEvent }];
  }

  return next;
}

/** Apply a text chunk (stream_chunk event). */
export function applyAssistantChunk(turn, chunk, materialiseAt) {
  const next = { ...turn };
  next.content += chunk;
  next.tokenCount += 1;

  // Materialise the assistant row on first text chunk.
  if (!next.assistantMessageId) {
    next.assistantMessageId = `${turn.streamId}-assistant`;
    next.assistantTimestamp = materialiseAt;
    next.timelineItems = [
      ...next.timelineItems,
      {
        kind: 'assistant_message',
        id: next.assistantMessageId,
        content: next.content,
        timestamp: next.assistantTimestamp,
        isStreaming: true,
        error: false,
        displayAgentName: next.displayAgentName || CHAT_AGENT_NAME,
        displayModelName: next.displayModelName,
        displayToolCalls: next.displayToolCalls,
        tokenCount: next.tokenCount,
        reasoningTokenCount: next.reasoningTokenCount,
        reasoningText: next.reasoningText,
      },
    ];
  } else {
    // Update existing assistant row in timelineItems.
    next.timelineItems = next.timelineItems.map((item) => {
      if (item.kind === 'assistant_message' && item.id === next.assistantMessageId) {
        return {
          ...item,
          content: next.content,
          tokenCount: next.tokenCount,
          reasoningTokenCount: next.reasoningTokenCount,
          reasoningText: next.reasoningText,
        };
      }
      return item;
    });
  }

  return next;
}

/**
 * Build a fallback timeline order for a committed assistant message that has
 * no recorded `timelineItems` (e.g. backend-loaded history).
 *
 * The synthetic order places the assistant text row first, then known stream
 * events — this avoids reintroducing the "tool rows above agent row" layout
 * on reload even though exact history is unavailable.
 */
export function buildDefaultAssistantTimeline(message) {
  if (!message || message.role !== 'assistant') return undefined;
  const items = [];
  const streamEvents = Array.isArray(message.streamEvents) ? message.streamEvents : [];

  items.push({
    kind: 'assistant_message',
    id: message.id || `msg_${Date.now()}`,
    content: typeof message.content === 'string' ? message.content : '',
    timestamp: typeof message.timestamp === 'number' ? message.timestamp : Date.now(),
    isStreaming: !!message.isStreaming,
    error: !!message.error,
    displayAgentName: typeof message.displayAgentName === 'string' ? message.displayAgentName : CHAT_AGENT_NAME,
    displayModelName: typeof message.displayModelName === 'string' ? message.displayModelName : '',
    displayToolCalls: Array.isArray(message.displayToolCalls) ? message.displayToolCalls : [],
    tokenCount: Number.isFinite(message.tokenCount) ? message.tokenCount : 0,
    reasoningTokenCount: Number.isFinite(message.reasoningTokenCount) ? message.reasoningTokenCount : 0,
    reasoningText: typeof message.reasoningText === 'string' ? message.reasoningText : '',
  });

  for (const event of streamEvents) {
    items.push({ kind: 'stream_event', id: event.id, event });
  }

  return items;
}

/**
 * Convert a live turn to a message-like object suitable for rendering with
 * the `<Message>` component. Only meaningful once the turn has an assistant
 * text row or has been finalised.
 */
export function toCommittedAssistantMessage(turn) {
  return {
    id: turn.assistantMessageId || `${turn.streamId}-assistant`,
    role: 'assistant',
    content: turn.content,
    timestamp: turn.assistantTimestamp || turn.startedAt,
    tokenCount: turn.tokenCount,
    reasoningTokenCount: turn.reasoningTokenCount,
    reasoningText: turn.reasoningText,
    streamEvents: turn.streamEvents,
    displayAgentName: turn.displayAgentName,
    displayModelName: turn.displayModelName,
    displayToolCalls: turn.displayToolCalls,
    isStreaming: turn.isStreaming,
    error: turn.error,
    timelineItems: turn.timelineItems,
  };
}

/**
 * Finalise a live assistant turn — freeze it and return a committed message.
 *
 * Rules:
 * - `completed` with visible output → normal message.
 * - `completed` with **no** visible output → "(No response text)" placeholder.
 * - `cancelled` with no visible output → drop (return null).
 * - `cancelled` with partial visible output → keep, append cancelled suffix once.
 * - `error` → message with `Error: ...` content and `error: true`.
 */
export function finalizeLiveAssistantTurn(turn, { status, now, errorMessage }) {
  if (!turn) return null;

  const hasVisibleOutput = (
    turn.content.trim().length > 0
    || turn.timelineItems.length > 0
    || turn.streamEvents.length > 0
  );

  if (status === 'cancelled' && !hasVisibleOutput) {
    return null;
  }

  const finalTurn = { ...turn, isStreaming: false, assistantTimestamp: now || turn.startedAt };

  if (status === 'error') {
    finalTurn.content = `Error: ${errorMessage || 'Unknown error'}`;
    finalTurn.error = true;
    finalTurn.timelineItems = [
      {
        kind: 'assistant_message',
        id: finalTurn.assistantMessageId || `${finalTurn.streamId}-assistant`,
        content: finalTurn.content,
        timestamp: finalTurn.assistantTimestamp,
        isStreaming: false,
        error: true,
        displayAgentName: finalTurn.displayAgentName || CHAT_AGENT_NAME,
        displayModelName: finalTurn.displayModelName,
        displayToolCalls: finalTurn.displayToolCalls,
        tokenCount: finalTurn.tokenCount,
        reasoningTokenCount: finalTurn.reasoningTokenCount,
        reasoningText: finalTurn.reasoningText,
      },
    ];
    return toCommittedAssistantMessage(finalTurn);
  }

  if (status === 'completed' && !hasVisibleOutput) {
    // No text and no visible events — show placeholder so the cursor doesn't vanish.
    finalTurn.content = '(No response text)';
    if (!finalTurn.assistantMessageId) {
      finalTurn.assistantMessageId = `${finalTurn.streamId}-assistant`;
    }
    finalTurn.timelineItems = [
      {
        kind: 'assistant_message',
        id: finalTurn.assistantMessageId,
        content: finalTurn.content,
        timestamp: finalTurn.assistantTimestamp,
        isStreaming: false,
        error: false,
        displayAgentName: finalTurn.displayAgentName || CHAT_AGENT_NAME,
        displayModelName: finalTurn.displayModelName,
        displayToolCalls: finalTurn.displayToolCalls,
        tokenCount: finalTurn.tokenCount,
        reasoningTokenCount: finalTurn.reasoningTokenCount,
        reasoningText: finalTurn.reasoningText,
      },
    ];
    return toCommittedAssistantMessage(finalTurn);
  }

  if (status === 'cancelled' && hasVisibleOutput) {
    finalTurn.content = finalTurn.content.endsWith(' [Cancelled]')
      ? finalTurn.content
      : `${finalTurn.content} [Cancelled]`;
  }

  // Ensure the timeline items reflect the final state.
  finalTurn.timelineItems = finalTurn.timelineItems.map((item) => {
    if (item.kind === 'assistant_message') {
      return {
        ...item,
        isStreaming: false,
        content: finalTurn.content,
        tokenCount: finalTurn.tokenCount,
        reasoningTokenCount: finalTurn.reasoningTokenCount,
        reasoningText: finalTurn.reasoningText,
      };
    }
    return item;
  });

  return toCommittedAssistantMessage(finalTurn);
}
