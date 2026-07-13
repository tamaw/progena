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

// Subagent execution events (nested `task` tool delegation). Unlike the
// reasoning/assistant-message rows below (at most one per turn), the agent
// can delegate to several subagents in parallel within one turn, so these
// are keyed by the dispatching tool_call_id and each gets its own
// independently-updatable card -- see applySubagentEvent.
const SUBAGENT_EVENT_TYPES = new Set([
  'subagent_start',
  'subagent_result',
  'subagent_message_delta',
  'subagent_tool_start',
  'subagent_tool_result',
  'subagent_tool_error',
]);

function subagentItemId(toolCallId) {
  return `subagent-${toolCallId}`;
}

function createSubagentItem(toolCallId, subagentName, startedAt) {
  return {
    kind: 'subagent',
    id: subagentItemId(toolCallId),
    toolCallId,
    subagentName: subagentName || '',
    status: 'running',
    startedAt,
    finishedAt: null,
    durationMs: null,
    resultPreview: '',
    error: null,
    content: '',
    children: [],
  };
}

// Find-or-create-or-update a nested tool call within a subagent card's
// children, keyed by the nested tool's own call id (distinct from the
// subagent card's own toolCallId, which is the *parent* task call).
function upsertSubagentChild(children, nestedToolCallId, patch) {
  if (!nestedToolCallId) return children;
  const index = children.findIndex((child) => child.id === nestedToolCallId);
  if (index === -1) {
    return [
      ...children,
      {
        id: nestedToolCallId,
        toolName: '',
        status: 'running',
        argsPreview: null,
        resultPreview: '',
        error: null,
        durationMs: null,
        ...patch,
      },
    ];
  }
  return children.map((child, idx) => (idx === index ? { ...child, ...patch } : child));
}

function applySubagentEventToItem(item, nextEvent) {
  const metadata = nextEvent.metadata || {};
  const durationMs = Number.isFinite(metadata.duration_ms) ? metadata.duration_ms : null;

  switch (nextEvent.eventType) {
    case 'subagent_start':
      return {
        ...item,
        subagentName: metadata.subagent_name || item.subagentName,
        status: 'running',
      };

    case 'subagent_message_delta':
      return { ...item, content: item.content + (nextEvent.content || '') };

    case 'subagent_tool_start':
      return {
        ...item,
        subagentName: metadata.subagent_name || item.subagentName,
        children: upsertSubagentChild(item.children, metadata.nested_tool_call_id, {
          toolName: metadata.nested_tool_name || '',
          status: 'running',
          argsPreview: metadata.args_preview ?? null,
        }),
      };

    case 'subagent_tool_result':
      return {
        ...item,
        children: upsertSubagentChild(item.children, metadata.nested_tool_call_id, {
          toolName: metadata.nested_tool_name || '',
          status: 'success',
          resultPreview: typeof metadata.result_preview === 'string' ? metadata.result_preview : '',
          durationMs,
        }),
      };

    case 'subagent_tool_error':
      return {
        ...item,
        children: upsertSubagentChild(item.children, metadata.nested_tool_call_id, {
          toolName: metadata.nested_tool_name || '',
          status: 'error',
          error: typeof metadata.error === 'string' ? metadata.error : '',
          durationMs,
        }),
      };

    case 'subagent_result':
      return {
        ...item,
        subagentName: metadata.subagent_name || item.subagentName,
        status: metadata.success === false ? 'error' : 'success',
        finishedAt: nextEvent.timestamp ?? item.finishedAt,
        durationMs: durationMs ?? item.durationMs,
        resultPreview: typeof metadata.result_preview === 'string' ? metadata.result_preview : item.resultPreview,
        error: metadata.success === false ? (metadata.error || '') : item.error,
      };

    default:
      return item;
  }
}

/**
 * Find-or-create-or-update the subagent card for a `subagent_*` stream
 * event, keyed strictly by the event's own `tool_call_id` so concurrent
 * subagents' cards never touch each other regardless of arrival
 * interleaving. Returns `null` (not an unchanged array) when `nextEvent`
 * isn't a subagent event or carries no `tool_call_id`, so callers can fall
 * through to their own generic handling.
 */
export function applySubagentEvent(timelineItems, nextEvent) {
  if (!nextEvent || !SUBAGENT_EVENT_TYPES.has(nextEvent.eventType)) return null;
  const toolCallId = nextEvent.metadata?.tool_call_id;
  if (!toolCallId) return null;

  const itemId = subagentItemId(toolCallId);
  const index = timelineItems.findIndex((entry) => entry.kind === 'subagent' && entry.id === itemId);

  if (index === -1) {
    const seed = createSubagentItem(toolCallId, nextEvent.metadata?.subagent_name, nextEvent.timestamp);
    return [...timelineItems, applySubagentEventToItem(seed, nextEvent)];
  }

  return timelineItems.map((entry, idx) => (
    idx === index ? applySubagentEventToItem(entry, nextEvent) : entry
  ));
}

// A2A call_agent progress cards. Unlike subagents (dedicated subagent_*
// event types for every phase), Progenitor's own dispatch/return for
// call_agent is left as the exact same generic tool_start/tool_result/
// tool_error every other tool call gets -- deliberately: from Progenitor's
// side, calling call_agent is an ordinary tool call, same as search_web or
// read_file, and it should be handled no differently. Only the intermediate
// progress is a dedicated type, a2a_call_progress -- the A2A protocol's own
// status stream, which has no tool-call equivalent to reuse. All four are
// correlated by the same tool_call_id.
function isCallAgentToolEvent(event) {
  return (
    (event?.eventType === 'tool_start' || event?.eventType === 'tool_result' || event?.eventType === 'tool_error')
    && event?.metadata?.tool_name === 'call_agent'
  );
}

function a2aCallItemId(toolCallId) {
  return `a2a-${toolCallId}`;
}

function createA2ACallItem(toolCallId, agentName, startedAt) {
  return {
    kind: 'a2a_call',
    id: a2aCallItemId(toolCallId),
    toolCallId,
    agentName: agentName || '',
    status: 'running',
    startedAt,
    finishedAt: null,
    durationMs: null,
    resultPreview: '',
    error: null,
    messages: [],
  };
}

function applyA2ACallEventToItem(item, nextEvent) {
  const metadata = nextEvent.metadata || {};
  const durationMs = Number.isFinite(metadata.duration_ms) ? metadata.duration_ms : null;

  switch (nextEvent.eventType) {
    case 'tool_start':
      return { ...item, status: 'running' };

    case 'a2a_call_progress':
      return {
        ...item,
        agentName: metadata.agent_name || item.agentName,
        messages: [
          ...item.messages,
          {
            id: `${item.toolCallId}-${item.messages.length}`,
            state: typeof metadata.state === 'string' ? metadata.state : '',
            text: typeof metadata.message_text === 'string' ? metadata.message_text : (nextEvent.content || ''),
            timestamp: nextEvent.timestamp,
          },
        ],
      };

    case 'tool_result':
      return {
        ...item,
        status: 'success',
        finishedAt: nextEvent.timestamp ?? item.finishedAt,
        durationMs: durationMs ?? item.durationMs,
        resultPreview: typeof metadata.result_preview === 'string' ? metadata.result_preview : item.resultPreview,
      };

    case 'tool_error':
      return {
        ...item,
        status: 'error',
        finishedAt: nextEvent.timestamp ?? item.finishedAt,
        durationMs: durationMs ?? item.durationMs,
        error: typeof metadata.error === 'string' ? metadata.error : '',
      };

    default:
      return item;
  }
}

/**
 * Find-or-create-or-update the A2A call card for a call_agent tool_start/
 * tool_result/tool_error or a2a_call_progress event, keyed by tool_call_id.
 * Mirrors applySubagentEvent's null-sentinel contract ("not mine, fall
 * through" vs. "handled, here's the updated array"). Unlike subagent's
 * defensive create-on-anything, a2a_call_progress (or tool_result/
 * tool_error) with no existing card is ignored rather than guessed at --
 * a call_agent tool_start should always arrive first.
 */
export function applyA2ACallEvent(timelineItems, nextEvent) {
  if (!nextEvent) return null;
  const isProgressEvent = nextEvent.eventType === 'a2a_call_progress';
  if (!isCallAgentToolEvent(nextEvent) && !isProgressEvent) return null;

  const toolCallId = nextEvent.metadata?.tool_call_id;
  if (!toolCallId) return null;

  const itemId = a2aCallItemId(toolCallId);
  const index = timelineItems.findIndex((entry) => entry.kind === 'a2a_call' && entry.id === itemId);

  if (index === -1) {
    if (nextEvent.eventType !== 'tool_start') return null;
    // call_agent's args schema accepts either key for the same field (`agent`
    // is primary, `agent_name` an explicit alias -- see CallAgentArgs on the
    // backend) and the calling LLM is free to use either, so both must be
    // checked here. Still empty for the auto-route case (agent omitted
    // entirely, resolved only after tool_start fires) -- that gets backfilled
    // by the first a2a_call_progress event instead, see below.
    const argsPreview = nextEvent.metadata?.args_preview || {};
    const agentName = typeof argsPreview.agent === 'string'
      ? argsPreview.agent
      : (typeof argsPreview.agent_name === 'string' ? argsPreview.agent_name : '');
    const seed = createA2ACallItem(toolCallId, agentName, nextEvent.timestamp);
    return [...timelineItems, applyA2ACallEventToItem(seed, nextEvent)];
  }

  return timelineItems.map((entry, idx) => (
    idx === index ? applyA2ACallEventToItem(entry, nextEvent) : entry
  ));
}

/** Apply a non-visual stream event (control event like node metadata). */
export function applyAssistantStreamEvent(turn, streamEvent) {
  const nextEvent = createStreamEvent(streamEvent);
  if (!nextEvent) return turn;

  // assistant_metadata is handled by applyAssistantMetadata, not here.
  // subagent_message_delta is NOT short-circuited here like message_delta is:
  // unlike the outer turn's text (redundantly available via message.content
  // after finalization), nested subagent text has no other persisted home.
  if (nextEvent.eventType === 'assistant_metadata' || nextEvent.eventType === 'message_delta') {
    return turn;
  }

  const next = { ...turn };
  next.streamEvents = appendStreamEvent(next.streamEvents, nextEvent);
  next.reasoningText = appendReasoningText(next.reasoningText, nextEvent);
  const rt = extractReasoningTokenCount(nextEvent);
  if (rt !== null) next.reasoningTokenCount = rt;

  const updatedSubagentItems = applySubagentEvent(next.timelineItems, nextEvent);
  if (updatedSubagentItems) {
    next.timelineItems = updatedSubagentItems;
    return next;
  }

  // call_agent's tool_start/tool_result/tool_error update the rich a2a_call
  // card (the remote agent's own submitted/working/completed self-reporting)
  // but deliberately do NOT short-circuit here -- Progenitor's own act of
  // dispatching/receiving that call is a regular tool call like any other,
  // and gets its own plain stream_event row too, right alongside the richer
  // card (unmodified -- no dedicated event type, no special badge; see
  // describeStreamEvent's plain tool_start/tool_result/tool_error branches,
  // messageRenderUtils.js). That plain row is pushed *before* applyA2ACallEvent
  // runs so a freshly-seeded card is always appended after it -- the call
  // itself has to read before the progress card that explains it, not after.
  // a2a_call_progress has no such equivalent -- a plain tool call never has
  // an in-flight progress stream -- so it stays card-only.
  const isCallAgentEvt = isCallAgentToolEvent(nextEvent);
  if (isCallAgentEvt) {
    next.timelineItems = [...next.timelineItems, { kind: 'stream_event', id: nextEvent.id, event: nextEvent }];
  }
  const updatedA2ACallItems = applyA2ACallEvent(next.timelineItems, nextEvent);
  if (updatedA2ACallItems) {
    next.timelineItems = updatedA2ACallItems;
  }
  // call_agent's own tool_start/tool_result/tool_error always got their
  // plain row above regardless of whether applyA2ACallEvent could build a
  // card for them, so they always short-circuit here. a2a_call_progress has
  // no such guaranteed row of its own -- only short-circuit for it when a
  // card was actually found and updated; an orphaned progress event (no
  // matching prior tool_start, e.g. a dropped frame or missing
  // tool_call_id) must fall through to the generic stream_event push below
  // instead of silently vanishing, matching buildDefaultAssistantTimeline's
  // identical fallback for the reload path.
  if (isCallAgentEvt || (nextEvent.eventType === 'a2a_call_progress' && updatedA2ACallItems)) {
    return next;
  }

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

// Reconstructs grouped subagent cards from a flat, persisted stream_events
// array (e.g. backend-loaded history). Rather than a hand-written parallel
// reconstruction algorithm, this replays applySubagentEvent over the flat
// events in order, so the live and reload code paths structurally can't
// drift apart. Keyed by tool_call_id (the same key applySubagentEvent uses).
function collectSubagentGroups(streamEvents) {
  let items = [];
  for (const event of streamEvents) {
    if (typeof event?.eventType !== 'string' || !event.eventType.startsWith('subagent_')) {
      continue;
    }
    const updated = applySubagentEvent(items, event);
    if (updated) items = updated;
  }
  const groups = new Map();
  for (const item of items) {
    groups.set(item.toolCallId, item);
  }
  return groups;
}

// Reconstructs grouped A2A call cards from a flat, persisted stream_events
// array -- same rationale and mechanism as collectSubagentGroups (replays
// applyA2ACallEvent over the flat events in order so live and reload can't
// drift apart), just matched by metadata.tool_name === 'call_agent' instead
// of an eventType prefix.
function collectA2ACallGroups(streamEvents) {
  let items = [];
  for (const event of streamEvents) {
    if (!isCallAgentToolEvent(event) && event?.eventType !== 'a2a_call_progress') continue;
    const updated = applyA2ACallEvent(items, event);
    if (updated) items = updated;
  }
  const groups = new Map();
  for (const item of items) {
    groups.set(item.toolCallId, item);
  }
  return groups;
}

/**
 * Build a fallback timeline order for a committed assistant message that has
 * no recorded `timelineItems` (e.g. backend-loaded history).
 *
 * The backend attaches a turn's tool calls to whichever message carries the
 * final answer text, so the live interleave itself isn't recorded -- but
 * most items here carry a real timestamp (an AIMessage's
 * response_metadata.created_at: stamped onto each tool call by
 * _extract_raw_tool_calls on the backend, and onto the assistant row via
 * ChatMessage.created_at), so sorting on that reproduces true order. Older
 * persisted tool calls predating that stamping fall back to the assistant
 * row's own timestamp instead (see the sort below) rather than corrupting
 * the order with NaN.
 */
export function buildDefaultAssistantTimeline(message) {
  if (!message || message.role !== 'assistant') return undefined;
  const items = [];
  const streamEvents = Array.isArray(message.streamEvents) ? message.streamEvents : [];

  const todosByCallId = collectTodosByToolCallId(streamEvents);
  const subagentGroups = collectSubagentGroups(streamEvents);
  const a2aCallGroups = collectA2ACallGroups(streamEvents);
  const insertedSubagentIds = new Set();
  const insertedA2ACallIds = new Set();
  let todosSeq = 0;
  for (const event of streamEvents) {
    // Grouped subagent cards replace their flat subagent_* events entirely --
    // spliced in once, at the earliest event's timestamp, rather than once
    // per underlying event. Only skip the flat event when a group was
    // actually found: a missing/empty tool_call_id (malformed persisted
    // data) means no group could ever be built for it, and it must fall
    // through to the generic stream_event push below instead of silently
    // vanishing -- unconditionally `continue`-ing group-or-not used to drop
    // it entirely, with no card and no fallback row.
    if (typeof event?.eventType === 'string' && event.eventType.startsWith('subagent_')) {
      const toolCallId = event.metadata?.tool_call_id;
      const group = toolCallId ? subagentGroups.get(toolCallId) : undefined;
      if (group) {
        if (!insertedSubagentIds.has(group.id)) {
          insertedSubagentIds.add(group.id);
          items.push({ ...group, timestamp: event.timestamp });
        }
        continue;
      }
    }

    // The grouped A2A call card splices in once, right after the earliest
    // call_agent tool_start/tool_result/tool_error event carrying its
    // tool_call_id -- but unlike subagent groups above, call_agent's own
    // tool_start/tool_result/tool_error deliberately keep their own plain
    // stream_event row too, right alongside the richer card (see the
    // matching comment in applyAssistantStreamEvent), pushed *before* the
    // card so the dispatch/return marker always reads first -- matches the
    // live ordering instead of showing the (possibly already-complete) card
    // ahead of the tool_start that kicked it off. Only a2a_call_progress is
    // consumed with no row of its own -- a plain tool call has no equivalent
    // of an in-flight progress stream -- and, same as the subagent rule
    // above, only when a group was actually found for it: a missing/empty
    // tool_call_id must still fall through instead of silently vanishing.
    if (isCallAgentToolEvent(event) || event?.eventType === 'a2a_call_progress') {
      const toolCallId = event.metadata?.tool_call_id;
      const group = toolCallId ? a2aCallGroups.get(toolCallId) : undefined;
      if (group) {
        if (event.eventType === 'a2a_call_progress') {
          if (!insertedA2ACallIds.has(group.id)) {
            insertedA2ACallIds.add(group.id);
            items.push({ ...group, timestamp: event.timestamp });
          }
          continue;
        }
        items.push({ kind: 'stream_event', id: event.id, event, timestamp: event.timestamp });
        if (!insertedA2ACallIds.has(group.id)) {
          insertedA2ACallIds.add(group.id);
          items.push({ ...group, timestamp: event.timestamp });
        }
        continue;
      }
    }

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

  // Stream events reconstructed from pre-timestamp checkpoint data (or any
  // malformed persisted event) can carry an undefined timestamp -- subtracting
  // undefined produces NaN, which Array.sort's comparator contract treats as
  // "equal" inconsistently across engines. Fall back to the message's own
  // timestamp (always valid, see normalizeBackendChatMessage) so those items
  // sort next to the turn they belong to instead of corrupting the order.
  const fallbackTimestamp = Number.isFinite(message.timestamp) ? message.timestamp : 0;
  return items.sort((a, b) => {
    const at = Number.isFinite(a.timestamp) ? a.timestamp : fallbackTimestamp;
    const bt = Number.isFinite(b.timestamp) ? b.timestamp : fallbackTimestamp;
    return at - bt;
  });
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
