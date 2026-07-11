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

import { createStreamEvent, appendStreamEvent, appendReasoningText, extractReasoningTokenCount, mergeReasoningEvents } from './streamEventsModel';
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
  // in the timeline: the first delta seeds the row (so live reasoning is
  // visible while it streams, not just once a terminal summary lands), and
  // later deltas/summaries merge into that same row instead of replacing it
  // outright or creating duplicates. Since the backend can emit the final
  // reasoning_summary after the answer text has already started streaming,
  // a freshly-seeded row is inserted ahead of the assistant_message row
  // (reasoning belongs above the answer) rather than appended after it.
  const isReasoningKind = nextEvent.eventType === 'reasoning_delta' || nextEvent.eventType === 'reasoning_summary';
  if (isReasoningKind) {
    const reasoningIndex = next.timelineItems.findIndex((entry) =>
      entry.kind === 'stream_event'
      && (entry.event.eventType === 'reasoning_delta' || entry.event.eventType === 'reasoning_summary')
    );
    if (reasoningIndex >= 0) {
      const merged = mergeReasoningEvents(next.timelineItems[reasoningIndex].event, nextEvent);
      next.timelineItems = next.timelineItems.map((entry, idx) => (
        idx === reasoningIndex ? { kind: 'stream_event', id: merged.id, event: merged } : entry
      ));
    } else {
      const reasoningItem = { kind: 'stream_event', id: nextEvent.id, event: nextEvent };
      const assistantIndex = next.timelineItems.findIndex((entry) => entry.kind === 'assistant_message');
      next.timelineItems = assistantIndex >= 0
        ? [
          ...next.timelineItems.slice(0, assistantIndex),
          reasoningItem,
          ...next.timelineItems.slice(assistantIndex),
        ]
        : [...next.timelineItems, reasoningItem];
    }
    return next;
  }

  // todos_update carries the agent's FULL current plan snapshot on every call
  // (not a delta), but each call still gets its own timeline row -- the plan
  // can be many tool calls deep by the time it's next updated, so merging in
  // place would bury the current state under everything that ran since the
  // last update. A fresh row per update instead reads as plan progress
  // checkpoints threaded through the transcript. Falls through to the
  // generic append path below.

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

// The backend persists raw tool_start/tool_result events but not the
// synthetic todos_update events the live turn appends (see
// applyAssistantStreamEvent) -- there's no message backing a todos_update,
// it's a side-channel snapshot of graph state. Reconstruct one entry per
// write_todos call from its tool_start args (structured JSON, see
// build_tool_start_event on the backend), keyed by tool_call_id so each can
// be re-inserted next to its matching tool_result below.
function collectTodosByToolCallId(streamEvents) {
  const todosByCallId = new Map();
  for (const event of streamEvents) {
    if (event?.eventType !== 'tool_start' || event?.metadata?.tool_name !== 'write_todos') {
      continue;
    }
    const callId = event.metadata?.tool_call_id;
    const todos = event.metadata?.args_preview?.todos;
    if (callId && Array.isArray(todos)) {
      todosByCallId.set(callId, todos);
    }
  }
  return todosByCallId;
}

/**
 * Build a fallback timeline order for a committed assistant message that has
 * no recorded `timelineItems` (e.g. backend-loaded history).
 *
 * The backend attaches a turn's tool calls to whichever message carries the
 * final answer text, so the live interleave itself isn't recorded -- but
 * every item here carries a real timestamp (an AIMessage's
 * response_metadata.created_at: stamped onto each tool call by
 * _extract_raw_tool_calls on the backend, and onto the assistant row via
 * ChatMessage.created_at), so a plain sort on that reproduces true order.
 */
export function buildDefaultAssistantTimeline(message) {
  if (!message || message.role !== 'assistant') return undefined;
  const items = [];
  const streamEvents = Array.isArray(message.streamEvents) ? message.streamEvents : [];

  const todosByCallId = collectTodosByToolCallId(streamEvents);
  let todosSeq = 0;
  for (const event of streamEvents) {
    items.push({ kind: 'stream_event', id: event.id, event, timestamp: event.timestamp });

    if (event?.eventType !== 'tool_result' || event?.metadata?.tool_name !== 'write_todos') {
      continue;
    }
    const todos = todosByCallId.get(event.metadata?.tool_call_id);
    if (!todos) continue;

    todosSeq += 1;
    const restoredId = `${message.id || 'msg'}-todos-restored-${todosSeq}`;
    items.push({
      kind: 'stream_event',
      id: restoredId,
      timestamp: event.timestamp,
      event: {
        id: restoredId,
        eventType: 'todos_update',
        content: '',
        timestamp: event.timestamp,
        metadata: { todos },
      },
    });
  }

  items.push({
    kind: 'assistant_message',
    id: message.id || `msg_${Date.now()}`,
    content: typeof message.content === 'string' ? message.content : '',
    timestamp: message.timestamp,
    isStreaming: !!message.isStreaming,
    error: !!message.error,
    displayAgentName: typeof message.displayAgentName === 'string' ? message.displayAgentName : CHAT_AGENT_NAME,
    displayModelName: typeof message.displayModelName === 'string' ? message.displayModelName : '',
    displayToolCalls: Array.isArray(message.displayToolCalls) ? message.displayToolCalls : [],
    tokenCount: Number.isFinite(message.tokenCount) ? message.tokenCount : 0,
    reasoningTokenCount: Number.isFinite(message.reasoningTokenCount) ? message.reasoningTokenCount : 0,
    reasoningText: typeof message.reasoningText === 'string' ? message.reasoningText : '',
  });

  return items.sort((a, b) => a.timestamp - b.timestamp);
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
