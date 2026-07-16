import { describe, it, expect } from 'vitest';
import {
  compareSessionItems,
  normalizeBackendChatMessage,
  buildChatSessionItems,
  findAdjacentSessionItem,
  shouldMaterializeBeforeSwitching,
  shouldLoadSelectedBackendChat,
  visibleGroupsForFilter,
  sidebarEmptyMessage,
} from './chatSessionsModel';
import { getAgentMentionContext } from './chatInputModel';
import { groupSessionItems } from './conversationModel';
import { formatDisplayToolCalls } from './messageDisplayModel';
import { CHAT_STATUS } from '../../lib/chatStatus';

describe('compareSessionItems', () => {
  it('orders pinned before unpinned', () => {
    expect(compareSessionItems(
      { pinned: true, activityAt: 1, id: 'a' },
      { pinned: false, activityAt: 100, id: 'b' },
    )).toBeLessThan(0);
  });
  it('orders by most-recent activity among unpinned', () => {
    expect(compareSessionItems(
      { pinned: false, activityAt: 1, id: 'a' },
      { pinned: false, activityAt: 2, id: 'b' },
    )).toBeGreaterThan(0);
  });

  // Regression: this comparator existed and was unit-tested (above) but was
  // never actually called from ConversationPage's "Recent" sort, so toggling
  // it was a no-op - items kept whatever order chats.order last happened to
  // be in. This mirrors the fix: sort with compareSessionItems before
  // grouping, so within-bucket order reflects recency instead of arrival order.
  it('sorting with compareSessionItems before grouping puts each bucket in recency order', () => {
    const items = [
      { id: 'old', title: 'Old', bucket: 'today', pinned: false, activityAt: 1 },
      { id: 'new', title: 'New', bucket: 'today', pinned: false, activityAt: 100 },
      { id: 'mid', title: 'Mid', bucket: 'today', pinned: false, activityAt: 50 },
    ];
    const sorted = [...items].sort(compareSessionItems);
    const groups = groupSessionItems(sorted);
    const today = groups.find((group) => group.key === 'today');
    expect(today.items.map((item) => item.id)).toEqual(['new', 'mid', 'old']);
  });
});

describe('normalizeBackendChatMessage', () => {
  it('returns null for non-objects', () => {
    expect(normalizeBackendChatMessage(null, 0)).toBeNull();
  });

  it('extracts the model from raw.response_metadata.model_name', () => {
    const m = normalizeBackendChatMessage({
      id: 'm1',
      role: 'assistant',
      content: 'hello',
      token_count: 5,
      raw: { response_metadata: { model_name: 'gpt-x' } },
    }, 0);
    expect(m.id).toBe('m1');
    expect(m.role).toBe('assistant');
    expect(m.content).toBe('hello');
    expect(m.tokenCount).toBe(5);
    expect(m.model).toBe('gpt-x');
  });

  it('preserves optional assistant display metadata', () => {
    const m = normalizeBackendChatMessage({
      id: 'm3',
      role: 'assistant',
      content: 'hello',
      display_agent_name: 'greetings_agent',
      display_model_name: 'gpt-5-mini',
      display_tool_calls: ['sleep'],
      raw: { response_metadata: {} },
    }, 0);
    expect(m.displayAgentName).toBe('greetings_agent');
    expect(m.displayModelName).toBe('gpt-5-mini');
    expect(m.displayToolCalls).toEqual(['sleep']);
    expect(m.model).toBe('gpt-5-mini');
  });

  it('expands tool_calls into tool_start + tool_result stream events', () => {
    const m = normalizeBackendChatMessage({
      id: 'm2',
      role: 'assistant',
      content: 'done',
      tool_calls: [{ id: 't1', name: 'search', args: { q: 'x' }, result: 'res' }],
    }, 1);
    const types = m.streamEvents.map((e) => e.eventType);
    expect(types).toEqual(['tool_start', 'tool_result']);
    expect(m.streamEvents[0].metadata.tool_name).toBe('search');
  });

  it('preserves persisted normal tool duration and curated artifact metadata', () => {
    const m = normalizeBackendChatMessage({
      id: 'm-tool-metadata',
      role: 'assistant',
      content: 'done',
      tool_calls: [{
        id: 'call-1',
        name: 'read_file',
        args: { path: 'README.md' },
        result: 'file contents',
        duration_ms: 123.4,
        artifact: { summary: 'README.md: 12 lines', count: 12 },
      }],
    }, 1);

    const result = m.streamEvents.find((event) => event.eventType === 'tool_result');
    expect(result.metadata.duration_ms).toBe(123.4);
    expect(result.metadata.artifact).toEqual({ summary: 'README.md: 12 lines', count: 12 });
    expect(result.metadata.result_preview).toBe('file contents');
  });

  it('carries the tool call created_at onto both synthesized events as a real timestamp', () => {
    const m = normalizeBackendChatMessage({
      id: 'm4',
      role: 'assistant',
      content: 'done',
      tool_calls: [
        { id: 't1', name: 'search', args: {}, result: 'res', created_at: '2026-07-11T02:31:08+00:00' },
      ],
    }, 1);
    const expected = new Date('2026-07-11T02:31:08+00:00').getTime();
    expect(m.streamEvents[0].timestamp).toBe(expected);
    expect(m.streamEvents[1].timestamp).toBe(expected);
  });

  it('leaves timestamp undefined when the tool call has no created_at', () => {
    const m = normalizeBackendChatMessage({
      id: 'm5',
      role: 'assistant',
      content: 'done',
      tool_calls: [{ id: 't1', name: 'search', args: {}, result: 'res' }],
    }, 1);
    expect(m.streamEvents[0].timestamp).toBeUndefined();
  });

  // Reloaded task calls preserve both the outer readable tool lifecycle and
  // the richer subagent dispatch events. buildDefaultAssistantTimeline groups
  // the latter into one card while retaining the former as normal disclosures.
  it('expands a task tool_call into outer tool rows plus subagent events', () => {
    const m = normalizeBackendChatMessage({
      id: 'm6',
      role: 'assistant',
      content: 'done',
      tool_calls: [{
        id: 'call_1',
        name: 'task',
        args: { subagent_type: 'researcher', description: 'look into it' },
        result: 'Here is what I found.',
      }],
    }, 1);
    const types = m.streamEvents.map((e) => e.eventType);
    expect(types).toHaveLength(4);
    expect(types.filter((type) => type === 'tool_start')).toHaveLength(1);
    expect(types.filter((type) => type === 'tool_result')).toHaveLength(1);
    expect(types.filter((type) => type === 'subagent_start')).toHaveLength(1);
    expect(types.filter((type) => type === 'subagent_result')).toHaveLength(1);
    const outerStart = m.streamEvents.find((e) => e.eventType === 'tool_start');
    const outerResult = m.streamEvents.find((e) => e.eventType === 'tool_result');
    expect(outerStart.metadata).toMatchObject({
      tool_name: 'task',
      tool_call_id: 'call_1',
      args_preview: { subagent_type: 'researcher', description: 'look into it' },
    });
    expect(outerResult.metadata).toMatchObject({
      tool_name: 'task',
      tool_call_id: 'call_1',
      result_preview: 'Here is what I found.',
    });
    const subagentStart = m.streamEvents.find((e) => e.eventType === 'subagent_start');
    const subagentResult = m.streamEvents.find((e) => e.eventType === 'subagent_result');
    expect(subagentStart.metadata).toMatchObject({ subagent_name: 'researcher', tool_call_id: 'call_1' });
    expect(subagentResult.metadata).toMatchObject({
      subagent_name: 'researcher',
      tool_call_id: 'call_1',
      success: true,
      result_preview: 'Here is what I found.',
    });
  });

  it('still emits a terminal subagent_result for an image-only task result', () => {
    const m = normalizeBackendChatMessage({
      id: 'm6b',
      role: 'assistant',
      content: 'done',
      tool_calls: [{
        id: 'call_1',
        name: 'task',
        args: { subagent_type: 'researcher', description: 'render a chart' },
        result: '',
        images: [{ url: 'data:image/png;base64,abc' }],
      }],
    }, 1);
    const types = m.streamEvents.map((e) => e.eventType);
    expect(types).toHaveLength(4);
    expect(types.filter((type) => type === 'tool_start')).toHaveLength(1);
    expect(types.filter((type) => type === 'tool_result')).toHaveLength(1);
    expect(types.filter((type) => type === 'subagent_start')).toHaveLength(1);
    expect(types.filter((type) => type === 'subagent_result')).toHaveLength(1);
    const subagentResult = m.streamEvents.find((e) => e.eventType === 'subagent_result');
    expect(subagentResult.metadata).toMatchObject({
      subagent_name: 'researcher',
      tool_call_id: 'call_1',
      success: true,
      result_preview: '1 image',
    });
    expect(subagentResult.images).toEqual([{ url: 'data:image/png;base64,abc' }]);
  });


  it('reconstructs a grouped subagent timeline card from the reloaded task call', () => {
    const m = normalizeBackendChatMessage({
      id: 'm7',
      role: 'assistant',
      content: 'done',
      tool_calls: [{
        id: 'call_1',
        name: 'task',
        args: { subagent_type: 'hello-world' },
        result: 'hello!',
      }],
    }, 1);
    const subagentItems = m.timelineItems.filter((item) => item.kind === 'subagent');
    expect(subagentItems).toHaveLength(1);
    expect(subagentItems[0]).toMatchObject({
      toolCallId: 'call_1',
      subagentName: 'hello-world',
      status: 'success',
      resultPreview: 'hello!',
    });
    expect(m.timelineItems.filter((item) => item.kind === 'stream_event')).toHaveLength(2);
    expect(m.timelineItems.some((item) => item.kind === 'assistant_message')).toBe(true);
  });

  it('leaves a task call with no result as a running subagent_start only', () => {
    const m = normalizeBackendChatMessage({
      id: 'm8',
      role: 'assistant',
      content: 'done',
      tool_calls: [{ id: 'call_1', name: 'task', args: { subagent_type: 'researcher' } }],
    }, 1);
    // No result and no images means the dispatch remains an open card, while
    // its outer invocation remains visible as a normal tool_start row.
    expect(m.streamEvents.map((e) => e.eventType)).toEqual(['tool_start', 'subagent_start']);
    const subagentItems = m.timelineItems.filter((item) => item.kind === 'subagent');
    expect(subagentItems[0].status).toBe('running');
    expect(m.timelineItems.filter((item) => item.kind === 'stream_event')).toHaveLength(1);
  });

  it.each([
    ['missing id', { name: 'task', args: { subagent_type: 'researcher' }, result: 'ignored' }],
    ['missing subagent name', { id: 'call_missing_name', name: 'task', args: {}, result: 'ignored' }],
    ['malformed id and name', { id: 42, name: 'task', args: { subagent_type: null }, result: 'ignored' }],
  ])('drops task calls with %s without a generic task row', (_caseName, toolCall) => {
    const m = normalizeBackendChatMessage({
      id: 'malformed-task-message',
      role: 'assistant',
      content: 'done',
      tool_calls: [toolCall],
    }, 1);

    expect(m.streamEvents).toEqual([]);
    expect(m.timelineItems.some((item) => item.kind === 'subagent')).toBe(false);
    expect(m.timelineItems.some((item) => item.kind === 'stream_event')).toBe(false);
  });

  // Covers the fallback for a persisted tool_calls entry with no
  // progress_log (older data predating that field, or a call that never
  // streamed intermediate updates) -- the reconstructed card has an empty
  // message log and falls back to this result_preview instead (see
  // A2ACallCard.jsx and extractCallAgentSummary in chatSessionsModel.js).
  // The raw result is the whole call_agent result dict, JSON-stringified --
  // showing that verbatim is exactly the "meaningless JSON blob" experience
  // this feature exists to fix, so just `summary` is pulled out of it when
  // the JSON is intact. See 'reconstructs the a2a_call sub-chain from a
  // persisted progress_log' below for the case where progress_log *is*
  // present.
  it('reconstructs an a2a_call timeline card and uses summary (not raw JSON) as its reload result_preview', () => {
    const resultJson = JSON.stringify({
      task_id: 'task-123',
      agent: 'greetings_agent',
      status: 'completed',
      summary: 'Hello! Completed a 3-step task.',
      task: { history: [{ role: 'agent', parts: [{ text: 'Accepted' }] }] },
    });
    const m = normalizeBackendChatMessage({
      id: 'm9',
      role: 'assistant',
      content: 'done',
      tool_calls: [{
        id: 'call_1',
        name: 'call_agent',
        args: { agent: 'greetings_agent', request: 'hi' },
        result: resultJson,
      }],
    }, 1);
    const a2aItems = m.timelineItems.filter((item) => item.kind === 'a2a_call');
    expect(a2aItems).toHaveLength(1);
    expect(a2aItems[0]).toMatchObject({
      toolCallId: 'call_1',
      status: 'success',
      resultPreview: 'Hello! Completed a 3-step task.',
      messages: [],
    });
  });

  // The fix for the above: build_task_response persists a compact
  // {state, message_text} log per intermediate status update (see
  // extract_progress_log in app/progenitor/a2a/payloads.py); the backend
  // extracts it onto tool_calls[].progress_log ahead of the 400-char result
  // truncation (chat_sessions.py). Reload should now replay it as
  // a2a_call_progress events, the same shape a live viewer saw, and end up
  // with the same chained sub-entries instead of an empty message log.
  it('reconstructs the a2a_call sub-chain from a persisted progress_log', () => {
    const resultJson = JSON.stringify({
      task_id: 'task-123',
      agent: 'greetings_agent',
      status: 'completed',
      summary: "Hello! Irish-style right back at you: Top o' the mornin'!",
    });
    const m = normalizeBackendChatMessage({
      id: 'm9b',
      role: 'assistant',
      content: 'done',
      tool_calls: [{
        id: 'call_1',
        name: 'call_agent',
        args: { agent: 'greetings_agent', request: 'hi' },
        result: resultJson,
        progress_log: [
          { state: 'submitted', message_text: 'Accepted: hi' },
          { state: 'working', message_text: 'Working on your greeting...' },
          { state: 'completed', message_text: "Hello! Irish-style right back at you: Top o' the mornin'!" },
        ],
      }],
    }, 1);
    const a2aItems = m.timelineItems.filter((item) => item.kind === 'a2a_call');
    expect(a2aItems).toHaveLength(1);
    expect(a2aItems[0].status).toBe('success');
    expect(a2aItems[0].toolCallId).toBe('call_1');
    expect(a2aItems[0].messages.map((msg) => msg.state)).toEqual(['submitted', 'working', 'completed']);
    expect(a2aItems[0].messages.map((msg) => msg.text)).toEqual([
      'Accepted: hi',
      'Working on your greeting...',
      "Hello! Irish-style right back at you: Top o' the mornin'!",
    ]);
  });

  // Each progress_log entry carries its own real arrival time (stamped by
  // A2AClient.call_agent(), see extract_progress_log in
  // app/progenitor/a2a/payloads.py) -- reload must use that per-entry time
  // rather than reusing the parent tool call's single created_at for every
  // sub-entry, or a viewer sees every update stamped at the exact same
  // moment regardless of how long the task actually ran.
  it('uses each progress_log entry\'s own timestamp, not the parent tool call\'s shared one', () => {
    const resultJson = JSON.stringify({ task_id: 'task-123', status: 'completed' });
    const m = normalizeBackendChatMessage({
      id: 'm9d',
      role: 'assistant',
      content: 'done',
      tool_calls: [{
        id: 'call_1',
        name: 'call_agent',
        args: { agent: 'greetings_agent', request: 'hi' },
        result: resultJson,
        created_at: '2026-07-11T02:31:00+00:00',
        progress_log: [
          { state: 'submitted', message_text: 'Accepted', timestamp: '2026-07-11T02:31:02+00:00' },
          { state: 'working', message_text: 'Working...', timestamp: '2026-07-11T02:31:09+00:00' },
        ],
      }],
    }, 1);
    const a2aItems = m.timelineItems.filter((item) => item.kind === 'a2a_call');
    expect(a2aItems[0].messages.map((msg) => msg.timestamp)).toEqual([
      new Date('2026-07-11T02:31:02+00:00').getTime(),
      new Date('2026-07-11T02:31:09+00:00').getTime(),
    ]);
  });
  it('derives call_agent result duration from the progress timestamp span', () => {
    const m = normalizeBackendChatMessage({
      id: 'm9-duration-derived',
      role: 'assistant',
      content: 'done',
      tool_calls: [{
        id: 'call-duration-derived',
        name: 'call_agent',
        args: { agent: 'greetings_agent', request: 'hi' },
        result: JSON.stringify({ task_id: 'task-123', status: 'completed' }),
        progress_log: [
          { state: 'submitted', message_text: 'Accepted', timestamp: '2026-07-11T02:31:02.000Z' },
          { state: 'completed', message_text: 'Done', timestamp: '2026-07-11T02:31:09.500Z' },
        ],
      }],
    }, 1);

    const result = m.streamEvents.find((event) => event.eventType === 'tool_result');
    expect(result.metadata.duration_ms).toBe(7500);
  });

  it('preserves explicit call_agent duration over the progress timestamp span', () => {
    const m = normalizeBackendChatMessage({
      id: 'm9-duration-explicit',
      role: 'assistant',
      content: 'done',
      tool_calls: [{
        id: 'call-duration-explicit',
        name: 'call_agent',
        args: { agent: 'greetings_agent', request: 'hi' },
        result: JSON.stringify({ task_id: 'task-123', status: 'completed' }),
        duration_ms: 42,
        progress_log: [
          { state: 'submitted', message_text: 'Accepted', timestamp: '2026-07-11T02:31:02.000Z' },
          { state: 'completed', message_text: 'Done', timestamp: '2026-07-11T02:31:09.500Z' },
        ],
      }],
    }, 1);

    const result = m.streamEvents.find((event) => event.eventType === 'tool_result');
    expect(result.metadata.duration_ms).toBe(42);
  });

  it('falls back to the parent tool call\'s timestamp for older progress_log entries with no timestamp of their own', () => {
    const resultJson = JSON.stringify({ task_id: 'task-123', status: 'completed' });
    const m = normalizeBackendChatMessage({
      id: 'm9e',
      role: 'assistant',
      content: 'done',
      tool_calls: [{
        id: 'call_1',
        name: 'call_agent',
        args: { agent: 'greetings_agent', request: 'hi' },
        result: resultJson,
        created_at: '2026-07-11T02:31:00+00:00',
        progress_log: [{ state: 'submitted', message_text: 'Accepted' }],
      }],
    }, 1);
    const a2aItems = m.timelineItems.filter((item) => item.kind === 'a2a_call');
    expect(a2aItems[0].messages[0].timestamp).toBe(new Date('2026-07-11T02:31:00+00:00').getTime());
  });

  // Regression: an auto-routed call never names its target agent in the raw
  // tool-call args (the LLM omits `agent`/`agent_name` entirely and lets
  // call_agent pick one internally, after tool_start has already fired), so
  // reload has no seed to fall back on -- progress_log's own agent_name (see
  // extract_progress_log) must be the one source of truth here, not args.
  it('recovers agentName from progress_log entries when args never named an agent (auto-route case)', () => {
    const m = normalizeBackendChatMessage({
      id: 'm9c',
      role: 'assistant',
      content: 'done',
      tool_calls: [{
        id: 'call_1',
        name: 'call_agent',
        args: { request: 'hi' },
        result: JSON.stringify({ task_id: 'task-123', agent: 'greetings_agent', status: 'completed' }),
        progress_log: [
          { state: 'submitted', message_text: 'Accepted: hi', agent_name: 'greetings_agent' },
          { state: 'completed', message_text: 'Hello!', agent_name: 'greetings_agent' },
        ],
      }],
    }, 1);
    const a2aItems = m.timelineItems.filter((item) => item.kind === 'a2a_call');
    expect(a2aItems[0].agentName).toBe('greetings_agent');
  });

  it('pulls summary out via regex when the JSON is truncated after it (the realistic case)', () => {
    // Matches the shape of the backend's actual 400-char preview truncation
    // (chat_sessions.py) -- cut off well past a short "summary" field, mid
    // way through a later key, never on a clean closing brace. A naive
    // JSON.parse-or-nothing approach fails this case entirely even though
    // summary itself is fully intact.
    const truncated = '{"task_id": "task-123", "agent": "greetings_agent", "status": "completed", '
      + '"summary": "Hello! Completed a 3-step task:\\n1. prep done", '
      + '"latest_message": "Hello! Completed a 3-step task:\\n1. prep done", "last_error": null, "no…';
    const m = normalizeBackendChatMessage({
      id: 'm10',
      role: 'assistant',
      content: 'done',
      tool_calls: [{ id: 'call_1', name: 'call_agent', args: {}, result: truncated }],
    }, 1);
    const a2aItems = m.timelineItems.filter((item) => item.kind === 'a2a_call');
    expect(a2aItems[0].resultPreview).toBe('Hello! Completed a 3-step task:\n1. prep done');
  });

  it('falls back to the raw result string when even the summary field itself was truncated', () => {
    const truncated = '{"task_id": "task-123", "agent": "greetings_agent", "summary": "Hello! Comp…';
    const m = normalizeBackendChatMessage({
      id: 'm11',
      role: 'assistant',
      content: 'done',
      tool_calls: [{ id: 'call_1', name: 'call_agent', args: {}, result: truncated }],
    }, 1);
    const a2aItems = m.timelineItems.filter((item) => item.kind === 'a2a_call');
    expect(a2aItems[0].resultPreview).toBe(truncated);
  });
});

describe('buildChatSessionItems', () => {
  const base = {
    messages: [],
    threadId: null,
    chatTitle: 'x',
    totalTokenCount: 0,
    totalToolCount: 0,
    firstMessageTime: undefined,
    archivedSessions: [],
    pinnedSessionIds: [],
    sharedSessionIds: [],
    backendChatsLoaded: true,
    unreadChatIds: new Set(),
    backgroundGeneratingChatIds: new Set(),
    backgroundErrorChatIds: new Set(),
  };

  it('derives backend card status with generating, error, complete, and idle precedence', () => {
    const items = buildChatSessionItems({
      ...base,
      backendChatSessions: [
        { thread_id: 'c1', title: 'Running' },
        { thread_id: 'c2', title: 'Error' },
        { thread_id: 'c3', title: 'Complete' },
        { thread_id: 'c4', title: 'Idle' },
      ],
      selectedBackendChatId: 'c1',
      unreadChatIds: new Set(['c2', 'c3']),
      backgroundGeneratingChatIds: new Set(['c1']),
      backgroundErrorChatIds: new Set(['c2']),
    });
    const byId = Object.fromEntries(items.map((item) => [item.id, item]));

    expect(byId.c1).toMatchObject({ generating: true, status: CHAT_STATUS.GENERATING });
    expect(byId.c2).toMatchObject({ unread: true, erroredInBackground: true, status: 'error' });
    expect(byId.c3).toMatchObject({ unread: true, status: 'complete' });
    expect(byId.c4).toMatchObject({ status: 'idle' });
  });

  it('exposes generating status for a selected local foreground generation', () => {
    const items = buildChatSessionItems({
      ...base,
      messages: [{ role: 'user', content: 'hi', timestamp: 1 }],
      threadId: 'local-1',
      firstMessageTime: 1,
      backendChatSessions: [{ thread_id: 'older', title: 'Earlier chat' }],
      selectedBackendChatId: null,
      isGenerating: true,
    });
    const byId = Object.fromEntries(items.map((item) => [item.id, item]));

    expect(byId['local-1']).toMatchObject({
      source: 'local',
      selected: true,
      generating: true,
      status: CHAT_STATUS.GENERATING,
    });
  });

  it('dedupes a backend card that matches the local current session', () => {
    const items = buildChatSessionItems({
      ...base,
      messages: [{ role: 'user', content: 'hi', timestamp: 1 }],
      threadId: 'sess1',
      firstMessageTime: 1,
      selectedBackendChatId: null,
      backendChatSessions: [
        { thread_id: 'sess1', title: 'dup' },
        { thread_id: 'other', title: 'other' },
      ],
    });
    expect(items.some((i) => i.source === 'local' && i.id === 'sess1')).toBe(true);
    expect(items.filter((i) => i.source === 'backend').map((i) => i.id)).toEqual(['other']);
  });

  it('does not synthesize an empty local draft card anymore', () => {
    const items = buildChatSessionItems({
      ...base,
      threadId: 'draft-1',
      chatTitle: 'New chat',
      selectedBackendChatId: null,
      backendChatSessions: [
        { thread_id: 'older', title: 'Earlier chat' },
      ],
    });
    expect(items).toEqual([
      expect.objectContaining({
        id: 'older',
        source: 'backend',
      }),
    ]);
  });

  // Regression: locally-archived sessions used to be gated behind the same
  // "is there a live draft to show" flag as the current-session card, so
  // selecting ANY backend chat (setting selectedBackendChatId) made every
  // archived chat vanish from the list - which also fed into
  // usePrunedSelection and permanently unpinned/unshared them.
  it('keeps locally-archived sessions visible once a backend chat is selected', () => {
    const items = buildChatSessionItems({
      ...base,
      messages: [{ role: 'user', content: 'hi', timestamp: 1 }],
      threadId: 'sess1',
      selectedBackendChatId: 'c1',
      archivedSessions: [
        { id: 'archived-1', threadId: 'archived-1', title: 'Old chat', lastActivityAt: 1 },
      ],
      backendChatSessions: [
        { thread_id: 'c1', title: 'One' },
      ],
    });
    expect(items.some((i) => i.source === 'local' && i.id === 'archived-1')).toBe(true);
    // The transient current-draft card is still correctly hidden.
    expect(items.some((i) => i.source === 'local' && i.id === 'sess1')).toBe(false);
  });
});

describe('shouldLoadSelectedBackendChat', () => {
  const selectedBackendCard = {
    selected: true,
    source: 'backend',
    threadId: 'chat-1',
  };

  it('loads a selected backend card when no active detail is hydrated', () => {
    expect(shouldLoadSelectedBackendChat({
      sessionItem: selectedBackendCard,
      activeBackendChat: null,
      activeBackendChatState: null,
      lastAppliedRequestId: null,
    })).toBe(true);
  });

  it('loads a selected backend card when the active detail belongs to another chat', () => {
    expect(shouldLoadSelectedBackendChat({
      sessionItem: selectedBackendCard,
      activeBackendChat: { thread_id: 'chat-2' },
      activeBackendChatState: { loading: false, requestId: 'request-1' },
      lastAppliedRequestId: 'request-1',
    })).toBe(true);
  });

  it('does not issue a duplicate load while the selected chat request is loading', () => {
    expect(shouldLoadSelectedBackendChat({
      sessionItem: selectedBackendCard,
      activeBackendChat: { thread_id: 'chat-2' },
      activeBackendChatState: { loading: true, requestId: 'request-2' },
      lastAppliedRequestId: null,
    })).toBe(false);
  });

  it('does not reload a hydrated selected chat whose request was applied', () => {
    expect(shouldLoadSelectedBackendChat({
      sessionItem: selectedBackendCard,
      activeBackendChat: { thread_id: 'chat-1' },
      activeBackendChatState: { loading: false, requestId: 'request-3' },
      lastAppliedRequestId: 'request-3',
    })).toBe(false);
  });

  it('reloads a selected chat when a stale request result was applied', () => {
    expect(shouldLoadSelectedBackendChat({
      sessionItem: selectedBackendCard,
      activeBackendChat: { thread_id: 'chat-1' },
      activeBackendChatState: { loading: false, requestId: 'request-new' },
      lastAppliedRequestId: 'request-old',
    })).toBe(true);
  });

  it.each([
    ['a local selected card', { selected: true, source: 'local', threadId: 'chat-1' }],
    ['an unselected backend card', { selected: false, source: 'backend', threadId: 'chat-1' }],
  ])('does not load %s', (_description, sessionItem) => {
    expect(shouldLoadSelectedBackendChat({
      sessionItem,
      activeBackendChat: null,
      activeBackendChatState: null,
      lastAppliedRequestId: null,
    })).toBe(false);
  });
});

describe('getAgentMentionContext', () => {
  it('detects an @mention token at the cursor', () => {
    expect(getAgentMentionContext('@greet', 6)).toEqual({
      query: 'greet',
      tokenStart: 0,
      tokenEnd: 6,
    });
  });

  it('returns null when the cursor is not inside a mention token', () => {
    expect(getAgentMentionContext('hello @greet there', 18)).toBeNull();
  });
});

describe('formatDisplayToolCalls', () => {
  it('renders string and object tool names compactly', () => {
    expect(formatDisplayToolCalls(['sleep', { name: 'probe_copilot' }])).toBe('sleep, probe_copilot');
  });

  it('omits output when metadata is absent', () => {
    expect(formatDisplayToolCalls([])).toBe('');
  });
});

describe('visibleGroupsForFilter', () => {
  const mk = (o) => ({
    id: o.id, title: o.id, summary: '', bucket: 'today', pinned: false,
    unread: !!o.unread, shared: !!o.shared, archived: !!o.archived,
  });
  const items = [
    mk({ id: 'u', unread: true }),
    mk({ id: 's', shared: true }),
    mk({ id: 'ar', archived: true }),
    mk({ id: 'uar', unread: true, archived: true }),
  ];

  it('unread filter excludes archived', () => {
    const groups = visibleGroupsForFilter({ sessionItems: items, sidebarFilter: 'unread', groupSessionItems });
    expect(groups.flatMap((g) => g.items.map((i) => i.id))).toEqual(['u']);
  });
  it('archived filter shows archived only', () => {
    const groups = visibleGroupsForFilter({ sessionItems: items, sidebarFilter: 'archived', groupSessionItems });
    expect(groups.flatMap((g) => g.items.map((i) => i.id)).sort()).toEqual(['ar', 'uar']);
  });
  it('all filter groups non-archived by time bucket', () => {
    const groups = visibleGroupsForFilter({ sessionItems: items, sidebarFilter: 'all', groupSessionItems });
    const today = groups.find((g) => g.key === 'today');
    expect(today.items.map((i) => i.id).sort()).toEqual(['s', 'u']);
  });
});

describe('findAdjacentSessionItem', () => {
  const items = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];

  it('picks the next item after the removed one', () => {
    expect(findAdjacentSessionItem(items, 'a')).toEqual({ id: 'b' });
  });

  it('falls back to the previous item when removing the last one', () => {
    expect(findAdjacentSessionItem(items, 'c')).toEqual({ id: 'b' });
  });

  it('returns null when the removed id is not in the list', () => {
    expect(findAdjacentSessionItem(items, 'missing')).toBeNull();
  });

  it('returns null for a single-item list', () => {
    expect(findAdjacentSessionItem([{ id: 'only' }], 'only')).toBeNull();
  });
});

describe('shouldMaterializeBeforeSwitching', () => {
  it('materializes a local session with messages that has no backend selection yet', () => {
    expect(shouldMaterializeBeforeSwitching({
      selectedBackendChatId: null,
      threadId: 'sess1',
      messageCount: 2,
    })).toBe(true);
  });

  it('does not materialize once a backend chat is already selected', () => {
    expect(shouldMaterializeBeforeSwitching({
      selectedBackendChatId: 'c1',
      threadId: 'sess1',
      messageCount: 2,
    })).toBe(false);
  });

  it('does not materialize an empty session', () => {
    expect(shouldMaterializeBeforeSwitching({
      selectedBackendChatId: null,
      threadId: 'sess1',
      messageCount: 0,
    })).toBe(false);
  });

  it('does not materialize without a thread id', () => {
    expect(shouldMaterializeBeforeSwitching({
      selectedBackendChatId: null,
      threadId: null,
      messageCount: 2,
    })).toBe(false);
  });
});

describe('sidebarEmptyMessage', () => {
  it('returns the right message per state', () => {
    expect(sidebarEmptyMessage({ sidebarFilter: 'all', backendChatsLoaded: false, backendChatsLoading: false })).toBe('Loading chats...');
    expect(sidebarEmptyMessage({ sidebarFilter: 'unread', backendChatsLoaded: true, backendChatsLoading: false })).toBe('No unread chats.');
    expect(sidebarEmptyMessage({ sidebarFilter: 'shared', backendChatsLoaded: true, backendChatsLoading: false })).toBe('No shared chats yet.');
    expect(sidebarEmptyMessage({ sidebarFilter: 'archived', backendChatsLoaded: true, backendChatsLoading: false })).toBe('No archived chats yet.');
    expect(sidebarEmptyMessage({ sidebarFilter: 'all', backendChatsLoaded: true, backendChatsLoading: false })).toBe('No conversations.');
  });
});
