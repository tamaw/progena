import {
  buildSessionItems,
  formatPreview,
  formatRelativeAge,
  getTimeBucket,
  toValidTimestamp,
} from './conversationModel';
import { buildDefaultAssistantTimeline } from './liveAssistantTurnModel';
import { extractCallAgentSummary } from './messageRenderUtils';
export function compareSessionItems(left, right) {
  const pinnedDelta = Number(Boolean(right.pinned)) - Number(Boolean(left.pinned));
  if (pinnedDelta !== 0) {
    return pinnedDelta;
  }

  const activityDelta = (right.activityAt || 0) - (left.activityAt || 0);
  if (activityDelta !== 0) {
    return activityDelta;
  }

  return String(left.id || '').localeCompare(String(right.id || ''));
}

// `task` tool calls are subagent dispatches, not plain tool calls -- mirrors
// _expand_task_subagent_events on the backend's live SSE path (deepagent_stream.py)
// so a reloaded chat produces the same subagent_start/subagent_result shape
// applySubagentEvent already knows how to group, instead of falling through to
// generic tool_start/tool_result rows (which is what made subagent cards vanish
// on refresh -- the checkpoint has no record of the subagent's own nested tool
// calls or streamed deltas, only the task call's args and final result, so the
// reconstructed card carries no `children` and no incremental `content`).
function buildSubagentDispatchStreamEvents(tc, messageId, index, timestamp) {
  const subagentName = tc.args && typeof tc.args.subagent_type === 'string' ? tc.args.subagent_type : '';
  const toolCallId = typeof tc.id === 'string' ? tc.id : '';
  const result = typeof tc.result === 'string' ? tc.result : '';
  const images = Array.isArray(tc.images) && tc.images.length > 0 ? tc.images : null;

  const events = [
    {
      id: `${messageId}-subagent-start-${index}`,
      eventType: 'subagent_start',
      content: subagentName,
      timestamp,
      metadata: {
        subagent_name: subagentName,
        tool_call_id: toolCallId,
        source: 'checkpoint',
      },
    },
  ];
  // An image-only result has empty extracted text -- without also checking
  // `images`, a subagent that only returns pictures would never get a
  // terminal event here, leaving its reconstructed card stuck "running"
  // forever on reload and silently dropping the image (mirrors the same
  // `result || images` check buildToolStreamEvents uses below for plain
  // tool_result rows).
  if (result || images) {
    const resultEvent = {
      id: `${messageId}-subagent-result-${index}`,
      eventType: 'subagent_result',
      content: subagentName,
      timestamp,
      metadata: {
        subagent_name: subagentName,
        tool_call_id: toolCallId,
        success: true,
        result_preview: images ? `${images.length} image${images.length !== 1 ? 's' : ''}` : result,
        source: 'checkpoint',
      },
    };
    if (images) {
      resultEvent.images = images;
    }
    events.push(resultEvent);
  }
  return events;
}

function buildToolStreamEvents(toolCalls, messageId) {
  if (!Array.isArray(toolCalls) || toolCalls.length === 0) {
    return [];
  }
  const events = [];
  toolCalls.forEach((tc, index) => {
    const toolName = typeof tc.name === 'string' ? tc.name : 'tool';
    const timestamp = toValidTimestamp(tc.created_at) ?? undefined;

    if (toolName === 'task') {
      events.push(...buildSubagentDispatchStreamEvents(tc, messageId, index, timestamp));
      return;
    }

    const args = tc.args && typeof tc.args === 'object' && !Array.isArray(tc.args) ? tc.args : {};
    const rawResult = typeof tc.result === 'string' ? tc.result : '';
    const result = toolName === 'call_agent'
      ? (extractCallAgentSummary(rawResult) || rawResult)
      : rawResult;
    const toolCallId = typeof tc.id === 'string' ? tc.id : '';
    // The backend stamps the declaring AIMessage's created_at onto each tool
    // call (see _extract_raw_tool_calls in chat_sessions.py) specifically so
    // history reloads can sort tool calls against the answer text by real
    // time instead of guessing an order. No separate timestamp exists for
    // the result, so it reuses the call's.
    events.push({
      id: `${messageId}-tool-start-${index}`,
      eventType: 'tool_start',
      content: '',
      timestamp,
      metadata: {
        tool_name: toolName,
        args_preview: args,
        tool_call_id: toolCallId,
        source: 'checkpoint',
      },
    });
    // Replays the same submitted/working/completed/... sequence a live
    // viewer saw as synthetic a2a_call_progress events (progress_log comes
    // from build_task_response on the backend, populated from
    // A2AClient.call_agent()'s own progress_updates -- see
    // app/progenitor/a2a/payloads.py's extract_progress_log). Without this,
    // applyA2ACallEvent has nothing to group on reload, and the card falls
    // back to a single result blob instead of the chained sub-entries the
    // live path shows.
    if (toolName === 'call_agent' && Array.isArray(tc.progress_log)) {
      tc.progress_log.forEach((entry, progressIndex) => {
        if (!entry || typeof entry !== 'object') return;
        const messageText = typeof entry.message_text === 'string' ? entry.message_text : '';
        // Each entry carries its own real arrival time (stamped by
        // A2AClient.call_agent(), see extract_progress_log in
        // app/progenitor/a2a/payloads.py) -- falls back to the parent tool
        // call's shared timestamp only for older persisted data from before
        // that field existed, rather than showing every sub-entry at the
        // exact same clock time.
        const entryTimestamp = toValidTimestamp(entry.timestamp) ?? timestamp;
        events.push({
          id: `${messageId}-a2a-progress-${index}-${progressIndex}`,
          eventType: 'a2a_call_progress',
          content: messageText,
          timestamp: entryTimestamp,
          metadata: {
            tool_call_id: toolCallId,
            state: typeof entry.state === 'string' ? entry.state : '',
            message_text: messageText,
            agent_name: typeof entry.agent_name === 'string' ? entry.agent_name : '',
            source: 'checkpoint',
          },
        });
      });
    }
    if (result || (Array.isArray(tc.images) && tc.images.length > 0)) {
      const resultEvent = {
        id: `${messageId}-tool-result-${index}`,
        eventType: 'tool_result',
        content: '',
        timestamp,
        metadata: {
          tool_name: toolName,
          result_preview: Array.isArray(tc.images) && tc.images.length > 0
            ? `${tc.images.length} image${tc.images.length !== 1 ? 's' : ''}`
            : result,
          tool_call_id: toolCallId,
          source: 'checkpoint',
        },
      };
      if (Array.isArray(tc.images) && tc.images.length > 0) {
        resultEvent.images = tc.images;
      }
      events.push(resultEvent);
    }
  });
  return events;
}

export function normalizeBackendChatMessage(message, index) {
  if (!message || typeof message !== 'object') {
    return null;
  }
  const raw = message.raw && typeof message.raw === 'object' && !Array.isArray(message.raw)
    ? message.raw
    : {};
  const createdAt = message.created_at || raw.created_at || raw.timestamp || raw.time;
  const timestamp = toValidTimestamp(createdAt) ?? Date.now();
  const role = typeof message.role === 'string' ? message.role : 'assistant';
  const content = typeof message.content === 'string'
    ? message.content
    : String(message.content ?? '');
  const id = typeof message.id === 'string' ? message.id : `backend_msg_${index}_${timestamp}`;
  const streamEvents = buildToolStreamEvents(
    Array.isArray(message.tool_calls) ? message.tool_calls : [],
    id,
  );

  const normalized = {
    id,
    role,
    content,
    timestamp,
    tokenCount: Number.isFinite(message.token_count) ? message.token_count : 0,
    reasoningTokenCount: Number.isFinite(message.reasoning_token_count)
      ? message.reasoning_token_count
      : 0,
    reasoningText: typeof message.reasoning_text === 'string' ? message.reasoning_text : '',
    displayAgentName: typeof message.display_agent_name === 'string' ? message.display_agent_name : '',
    displayModelName: typeof message.display_model_name === 'string' ? message.display_model_name : '',
    displayToolCalls: Array.isArray(message.display_tool_calls) ? message.display_tool_calls : [],
    model: (() => {
      if (typeof message.display_model_name === 'string' && message.display_model_name) return message.display_model_name;
      if (typeof message.model === 'string' && message.model) return message.model;
      if (typeof raw.model === 'string' && raw.model) return raw.model;
      const rm = raw.response_metadata && typeof raw.response_metadata === 'object' ? raw.response_metadata : {};
      return typeof rm.model_name === 'string' ? rm.model_name : (typeof rm.model === 'string' ? rm.model : '');
    })(),
    streamEvents,
    raw,
  };

  // Attach a synthetic fallback timeline for backend-loaded messages.
  // Backend checkpoint data does not preserve the exact live interleave
  // between assistant text and stream events, so we build a fallback order
  // by sorting everything (stream events, restored todos_update rows, and
  // the assistant row itself) by real timestamp -- see
  // buildDefaultAssistantTimeline's own docstring for the ordering contract.
  if (role === 'assistant') {
    normalized.timelineItems = buildDefaultAssistantTimeline(normalized);
  }
  return normalized;
}

function buildBackendSessionItem(chat, selectedBackendChatId, unreadChatIds, backgroundGeneratingChatIds, backgroundErrorChatIds, isGenerating) {
  const activityAt = toValidTimestamp(chat.last_activity || chat.updated_at)
    ?? toValidTimestamp(chat.created_at)
    ?? 0;
  const messageCount = Number.isFinite(chat.message_count) ? chat.message_count : 0;
  const summary = chat.preview || `${messageCount} ${messageCount === 1 ? 'message' : 'messages'}`;
  const selected = selectedBackendChatId === chat.thread_id;

  return {
    id: chat.thread_id,
    pinKey: chat.thread_id,
    title: chat.title || 'Untitled chat',
    meta: formatRelativeAge(activityAt) || formatRelativeAge(chat.created_at) || '',
    detail: chat.archived ? 'Archived' : (chat.shared ? 'Shared' : ''),
    summary,
    activityAt,
    bucket: getTimeBucket(activityAt),
    preview: formatPreview(chat.preview),
    selected,
    unread: unreadChatIds instanceof Set ? unreadChatIds.has(chat.thread_id) : false,
    // Generating whether the stream is in the foreground (the chat being
    // viewed) or carried on in the background — the card shows one dot either
    // way, so the two sources collapse into a single flag here.
    generating: (backgroundGeneratingChatIds instanceof Set
      && backgroundGeneratingChatIds.has(chat.thread_id))
      || (Boolean(isGenerating) && selected),
    erroredInBackground: backgroundErrorChatIds instanceof Set
      ? backgroundErrorChatIds.has(chat.thread_id)
      : false,
    pinned: Boolean(chat.pinned),
    shared: Boolean(chat.shared),
    archived: Boolean(chat.archived),
    source: 'backend',
    threadId: chat.thread_id,
    messageCount,
    toolCount: Number.isFinite(chat.tool_count) ? chat.tool_count : 0,
    errorCount: Number.isFinite(chat.error_count) ? chat.error_count : 0,
  };
}

export function buildChatSessionItems({
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
  selectedBackendChatId,
  unreadChatIds,
  backgroundGeneratingChatIds,
  backgroundErrorChatIds,
  isGenerating,
}) {
  const shouldShowLocalCurrentSession =
    !selectedBackendChatId
    && messages.length > 0;
  // Locally-archived sessions are independent persisted history, not the
  // transient current draft - they must always be included regardless of
  // whether a backend chat is currently selected. Only the draft card itself
  // is gated on `shouldShowLocalCurrentSession`.
  const localItems = buildSessionItems({
    messages,
    threadId,
    chatTitle,
    totalTokenCount,
    totalToolCount,
    firstMessageTime,
    archivedSessions,
    pinnedSessionIds,
    sharedSessionIds,
    includeCurrentSession: shouldShowLocalCurrentSession,
  })
    .map((item) => ({
      ...item,
      source: 'local',
      archived: item.detail === 'Archived',
      selected: item.selected,
      // The live local session is the active chat, so it generates in the
      // foreground exactly when the composer is streaming.
      generating: Boolean(isGenerating) && item.selected,
    }));

  const localIds = new Set(localItems.map((item) => item.pinKey));
  const backendItems = backendChatSessions
    .filter((chat) => (
      chat.thread_id
      && !localIds.has(chat.thread_id)
    ))
    .map((chat) => buildBackendSessionItem(chat, selectedBackendChatId, unreadChatIds, backgroundGeneratingChatIds, backgroundErrorChatIds, isGenerating));

  return [...localItems, ...backendItems];
}

export function visibleGroupsForFilter({ sessionItems, sidebarFilter, groupSessionItems }) {
  if (sidebarFilter === 'unread') {
    return [{ key: 'unread', label: 'Unread', items: sessionItems.filter((item) => item.unread && !item.archived) }]
      .filter((group) => group.items.length > 0);
  }
  if (sidebarFilter === 'shared') {
    return [{ key: 'shared', label: 'Shared', items: sessionItems.filter((item) => item.shared && !item.archived) }]
      .filter((group) => group.items.length > 0);
  }
  if (sidebarFilter === 'archived') {
    return [{ key: 'archived', label: 'Archived', items: sessionItems.filter((item) => item.archived) }]
      .filter((group) => group.items.length > 0);
  }
  return groupSessionItems(sessionItems.filter((item) => !item.archived));
}

// Picks which sidebar item to fall back to once `removedItemId` is gone
// (archived, deleted) -- prefers the item just after it in the visible list,
// falling back to the one just before it (e.g. removing the last item).
export function findAdjacentSessionItem(visibleSessionItems, removedItemId) {
  const visibleIndex = visibleSessionItems.findIndex((item) => item.id === removedItemId);
  if (visibleIndex < 0) {
    return null;
  }
  return visibleSessionItems[visibleIndex + 1] ?? visibleSessionItems[visibleIndex - 1] ?? null;
}

// Whether the local-only "current session" (not yet a backend chat card)
// needs to be persisted as its own sidebar card before switching away from
// it -- otherwise it would simply disappear once selection moves elsewhere.
export function shouldMaterializeBeforeSwitching({ selectedBackendChatId, threadId, messageCount }) {
  return !selectedBackendChatId && Boolean(threadId) && messageCount > 0;
}

export function sidebarEmptyMessage({ sidebarFilter, backendChatsLoaded, backendChatsLoading }) {
  if (!backendChatsLoaded || backendChatsLoading) {
    return 'Loading chats...';
  }
  if (sidebarFilter === 'unread') {
    return 'No unread chats.';
  }
  if (sidebarFilter === 'shared') {
    return 'No shared chats yet.';
  }
  if (sidebarFilter === 'archived') {
    return 'No archived chats yet.';
  }
  return 'No conversations.';
}
