import { describe, it, expect } from 'vitest';
import {
  createLiveAssistantTurn,
  applyAssistantMetadata,
  applyAssistantStreamEvent,
  applySubagentEvent,
  applyA2ACallEvent,
  applyAssistantChunk,
  buildDefaultAssistantTimeline,
  finalizeLiveAssistantTurn,
  toCommittedAssistantMessage,
} from './liveAssistantTurnModel';
import { CHAT_AGENT_NAME } from './conversationModel';

// ─── Helpers ──────────────────────────────────────────────────────────────

const NOW = 1_700_000_000_000;
const STREAM_ID = 'stream_001';

function baseTurn() {
  return createLiveAssistantTurn({ streamId: STREAM_ID, startedAt: NOW - 1000 });
}

/**
 * Build a minimal stream_event payload shaped like what the IPC layer emits
 * (snake_case).  `createStreamEvent()` in streamEventsModel normalises this
 * into the internal camelCase `{ id, eventType, content, metadata }` shape.
 */
function rawStreamEvent(type, overrides = {}) {
  return {
    event_type: type,
    content: '',
    metadata: {},
    ...overrides,
  };
}

// ─── createLiveAssistantTurn ─────────────────────────────────────────────

describe('createLiveAssistantTurn', () => {
  it('returns a fresh turn with null assistantMessageId and empty timeline', () => {
    const turn = createLiveAssistantTurn({ streamId: STREAM_ID, startedAt: NOW });
    expect(turn).toEqual({
      streamId: STREAM_ID,
      startedAt: NOW,
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
    });
  });

  it('preserves the given streamId and startedAt', () => {
    const turn = createLiveAssistantTurn({ streamId: 'abc', startedAt: 42 });
    expect(turn.streamId).toBe('abc');
    expect(turn.startedAt).toBe(42);
  });
});

// ─── applyAssistantMetadata ──────────────────────────────────────────────

describe('applyAssistantMetadata', () => {
  it('sets display_agent_name when present and non-empty', () => {
    const turn = baseTurn();
    const next = applyAssistantMetadata(turn, { display_agent_name: 'MyAgent' });
    expect(next.displayAgentName).toBe('MyAgent');
    expect(next.displayModelName).toBe('');
    expect(next.displayToolCalls).toEqual([]);
  });

  it('does NOT set display_agent_name for empty string', () => {
    const turn = baseTurn();
    const next = applyAssistantMetadata(turn, { display_agent_name: '' });
    expect(next.displayAgentName).toBe('');
  });

  it('does NOT set display_agent_name for non-string value', () => {
    const turn = baseTurn();
    const next = applyAssistantMetadata(turn, { display_agent_name: 42 });
    expect(next.displayAgentName).toBe('');
  });

  it('sets display_model_name when present and non-empty', () => {
    const turn = baseTurn();
    const next = applyAssistantMetadata(turn, { display_model_name: 'gpt-4' });
    expect(next.displayModelName).toBe('gpt-4');
  });

  it('sets display_tool_calls when provided as array', () => {
    const turn = baseTurn();
    const calls = [{ name: 'search', args: { q: 'hi' } }];
    const next = applyAssistantMetadata(turn, { display_tool_calls: calls });
    expect(next.displayToolCalls).toEqual(calls);
  });

  it('ignores non-array display_tool_calls', () => {
    const turn = baseTurn();
    const next = applyAssistantMetadata(turn, { display_tool_calls: 'nope' });
    expect(next.displayToolCalls).toEqual([]);
  });

  it('merges partial metadata without clearing previously known values', () => {
    const turn = baseTurn();
    const first = applyAssistantMetadata(turn, { display_agent_name: 'AgentA' });
    const second = applyAssistantMetadata(first, { display_model_name: 'ModelX' });
    expect(second.displayAgentName).toBe('AgentA');
    expect(second.displayModelName).toBe('ModelX');
  });

  it('does NOT mutate the original turn', () => {
    const turn = baseTurn();
    const copy = { ...turn };
    applyAssistantMetadata(turn, { display_agent_name: 'Mutant' });
    expect(turn).toEqual(copy);
  });

  // A key acceptance criterion: metadata-only events do NOT add timeline items.
  it('does NOT add any timeline items', () => {
    const turn = baseTurn();
    const next = applyAssistantMetadata(turn, { display_agent_name: 'Agent' });
    expect(next.timelineItems).toEqual([]);
  });
});

// ─── applyAssistantStreamEvent ───────────────────────────────────────────

describe('applyAssistantStreamEvent', () => {
  it('ignores a null/undefined event and returns the turn unchanged', () => {
    const turn = baseTurn();
    expect(applyAssistantStreamEvent(turn, null, NOW)).toBe(turn);
    expect(applyAssistantStreamEvent(turn, undefined, NOW)).toBe(turn);
  });

  it('ignores assistant_metadata control events (handled by applyAssistantMetadata)', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('assistant_metadata', { metadata: { agent: 'A' } });
    const next = applyAssistantStreamEvent(turn, evt, NOW);
    // Must return the same reference when nothing changes.
    expect(next).toBe(turn);
  });

  it('ignores message_delta events', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('message_delta');
    const next = applyAssistantStreamEvent(turn, evt, NOW);
    expect(next).toBe(turn);
  });

  it('appends a stream_event timeline item for todos_update', () => {
    const turn = baseTurn();
    const todos = [
      { content: 'Investigate the bug', status: 'completed' },
      { content: 'Write the fix', status: 'in_progress' },
    ];
    const evt = rawStreamEvent('todos_update', { metadata: { todos } });
    const next = applyAssistantStreamEvent(turn, evt, NOW);

    expect(next.timelineItems).toHaveLength(1);
    expect(next.timelineItems[0].kind).toBe('stream_event');
    expect(next.timelineItems[0].event.eventType).toBe('todos_update');
    expect(next.timelineItems[0].event.metadata.todos).toEqual(todos);
  });

  it('appends a new row for each later todos_update instead of replacing in place', () => {
    const turn = baseTurn();
    const withTool = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', { content: 'searching' }), NOW);
    const first = applyAssistantStreamEvent(
      withTool,
      rawStreamEvent('todos_update', { metadata: { todos: [{ content: 'Step 1', status: 'in_progress' }] } }),
      NOW,
    );
    const second = applyAssistantStreamEvent(
      first,
      rawStreamEvent('todos_update', {
        metadata: {
          todos: [
            { content: 'Step 1', status: 'completed' },
            { content: 'Step 2', status: 'in_progress' },
          ],
        },
      }),
      NOW,
    );

    // tool_start + two distinct todos rows, one per update.
    expect(second.timelineItems).toHaveLength(3);
    const todosItems = second.timelineItems.filter((item) => item.event.eventType === 'todos_update');
    expect(todosItems).toHaveLength(2);
    expect(todosItems[0].id).not.toBe(todosItems[1].id);
    expect(todosItems[0].event.metadata.todos).toEqual([{ content: 'Step 1', status: 'in_progress' }]);
    expect(todosItems[1].event.metadata.todos).toEqual([
      { content: 'Step 1', status: 'completed' },
      { content: 'Step 2', status: 'in_progress' },
    ]);
  });

  // Acceptance: first tool_start creates ONLY a stream-event row.
  it('appends a stream_event timeline item for tool_start (no assistant row)', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('tool_start', { content: 'searching...' });
    const next = applyAssistantStreamEvent(turn, evt, NOW);

    expect(next.timelineItems).toHaveLength(1);
    expect(next.timelineItems[0].kind).toBe('stream_event');
    expect(next.timelineItems[0].event.eventType).toBe('tool_start');
    // Assistant row is NOT created.
    expect(next.assistantMessageId).toBeNull();
    expect(next.content).toBe('');
  });

  it('appends a stream_event timeline item for tool_result', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('tool_result', { content: 'result data' });
    const next = applyAssistantStreamEvent(turn, evt, NOW);

    expect(next.timelineItems).toHaveLength(1);
    expect(next.timelineItems[0].kind).toBe('stream_event');
  });

  it('appends a stream_event timeline item for tool_error', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('tool_error', { content: 'fail' });
    const next = applyAssistantStreamEvent(turn, evt, NOW);
    expect(next.timelineItems).toHaveLength(1);
    expect(next.timelineItems[0].kind).toBe('stream_event');
  });

  it('appends a stream_event for node_update with a named node (not pending_response)', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('node_update', { metadata: { node_name: 'planner' } });
    const next = applyAssistantStreamEvent(turn, evt, NOW);
    expect(next.timelineItems).toHaveLength(1);
    expect(next.timelineItems[0].event.eventType).toBe('node_update');
  });

  it('skips node_update when node_name is pending_response', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('node_update', { metadata: { node_name: 'pending_response' } });
    const next = applyAssistantStreamEvent(turn, evt, NOW);
    expect(next.timelineItems).toHaveLength(0);
  });

  it('skips node_update when there is no node_name in metadata', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('node_update');
    const next = applyAssistantStreamEvent(turn, evt, NOW);
    expect(next.timelineItems).toHaveLength(0);
  });

  it('accumulates multiple stream events in arrival order', () => {
    const turn = baseTurn();
    const a = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', { content: 'first' }), NOW);
    const b = applyAssistantStreamEvent(a, rawStreamEvent('tool_result', { content: 'second' }), NOW);
    const c = applyAssistantStreamEvent(b, rawStreamEvent('node_update', { metadata: { node_name: 'coder' } }), NOW);

    expect(c.timelineItems).toHaveLength(3);
    expect(c.timelineItems[0].event.content).toBe('first');
    expect(c.timelineItems[1].event.content).toBe('second');
    expect(c.timelineItems[2].event.metadata.node_name).toBe('coder');
  });

  it('does NOT mutate the original turn', () => {
    const turn = baseTurn();
    const copy = { ...turn, streamEvents: [...turn.streamEvents], timelineItems: [...turn.timelineItems] };
    applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', { content: 'x' }), NOW);
    expect(turn.streamEvents).toEqual(copy.streamEvents);
    expect(turn.timelineItems).toEqual(copy.timelineItems);
  });

  // Reasoning events
  it('handles reasoning_delta: updates reasoningText/reasoningTokenCount and seeds a timeline row', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('reasoning_delta', {
      content: 'thinking step 1',
      metadata: { reasoning_tokens: 5 },
    });
    const next = applyAssistantStreamEvent(turn, evt, NOW);
    expect(next.reasoningText).toBe('thinking step 1');
    expect(next.reasoningTokenCount).toBe(5);
    // The first reasoning_delta seeds a row so live reasoning is visible while
    // it streams, instead of waiting for a terminal reasoning_summary.
    expect(next.timelineItems).toHaveLength(1);
    expect(next.timelineItems[0].kind).toBe('stream_event');
    expect(next.timelineItems[0].event.eventType).toBe('reasoning_delta');
    expect(next.timelineItems[0].event.content).toBe('thinking step 1');
  });

  it('reasoning_delta: merges into an existing reasoning row instead of replacing its content', () => {
    const turn = baseTurn();
    const first = applyAssistantStreamEvent(turn, rawStreamEvent('reasoning_summary', {
      content: 'initial',
      metadata: { reasoning_tokens: 3 },
    }), NOW);
    // reasoning_summary adds a timeline item.
    expect(first.timelineItems).toHaveLength(1);
    expect(first.timelineItems[0].event.eventType).toBe('reasoning_summary');

    const second = applyAssistantStreamEvent(first, rawStreamEvent('reasoning_delta', {
      content: ' step 2',
      metadata: { reasoning_tokens: 7 },
    }), NOW);
    // Still 1 item, with the delta concatenated onto the existing content
    // rather than replacing it outright.
    expect(second.timelineItems).toHaveLength(1);
    expect(second.reasoningText).toBe('initial step 2');
    expect(second.timelineItems[0].event.content).toBe('initial step 2');
  });

  it('reasoning_summary: adds a timeline item when there is no prior reasoning item', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('reasoning_summary', {
      content: 'final reasoning',
      metadata: { reasoning_tokens: 10 },
    });
    const next = applyAssistantStreamEvent(turn, evt, NOW);
    expect(next.timelineItems).toHaveLength(1);
    expect(next.timelineItems[0].kind).toBe('stream_event');
    expect(next.timelineItems[0].event.eventType).toBe('reasoning_summary');
    expect(next.reasoningTokenCount).toBe(10);
  });

  it('reasoning_summary: merges into the row seeded by an earlier reasoning_delta', () => {
    const turn = baseTurn();
    const first = applyAssistantStreamEvent(turn, rawStreamEvent('reasoning_delta', {
      content: 'thinking...',
      metadata: { reasoning_tokens: 2 },
    }), NOW);
    // reasoning_delta with no prior item seeds a new row.
    expect(first.timelineItems).toHaveLength(1);
    expect(first.timelineItems[0].event.eventType).toBe('reasoning_delta');

    // Now push a non-reasoning event so there's another row after it.
    const withTool = applyAssistantStreamEvent(first, rawStreamEvent('tool_start', { content: 'searching' }), NOW);
    expect(withTool.timelineItems).toHaveLength(2);

    const summary = applyAssistantStreamEvent(withTool, rawStreamEvent('reasoning_summary', {
      content: 'summary',
      metadata: { reasoning_tokens: 42 },
    }), NOW);
    // Merges into the existing reasoning row (still at its original position)
    // instead of appending a duplicate row after tool_start.
    expect(summary.timelineItems).toHaveLength(2);
    expect(summary.timelineItems[0].event.eventType).toBe('reasoning_delta');
    expect(summary.reasoningTokenCount).toBe(42);
  });

  it('reasoning arriving after the assistant_message row is inserted before it, not appended after', () => {
    const turn = baseTurn();
    const withText = applyAssistantChunk(turn, 'Hello', NOW);
    expect(withText.timelineItems).toHaveLength(1);
    expect(withText.timelineItems[0].kind).toBe('assistant_message');

    // The backend can retrieve/emit the reasoning summary only after the
    // answer text has already streamed -- the row must still land above it.
    const withReasoning = applyAssistantStreamEvent(withText, rawStreamEvent('reasoning_summary', {
      content: 'because reasons',
      metadata: { reasoning_tokens: 9 },
    }), NOW);
    expect(withReasoning.timelineItems).toHaveLength(2);
    expect(withReasoning.timelineItems[0].kind).toBe('stream_event');
    expect(withReasoning.timelineItems[0].event.eventType).toBe('reasoning_summary');
    expect(withReasoning.timelineItems[1].kind).toBe('assistant_message');
  });

  // Subagent execution events (nested `task` tool delegation) get their own
  // grouped card rather than a flat stream_event row.
  it('subagent_start creates a subagent timeline item, not a stream_event row', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('subagent_start', {
      content: 'researcher',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    });
    const next = applyAssistantStreamEvent(turn, evt, NOW);

    expect(next.timelineItems).toHaveLength(1);
    expect(next.timelineItems[0].kind).toBe('subagent');
    expect(next.timelineItems[0].id).toBe('subagent-call_1');
    expect(next.timelineItems[0].subagentName).toBe('researcher');
    expect(next.timelineItems[0].status).toBe('running');
  });

  it('two concurrent subagents update independently with no cross-contamination', () => {
    const turn = baseTurn();
    let next = applyAssistantStreamEvent(turn, rawStreamEvent('subagent_start', {
      metadata: { subagent_name: 'alpha', tool_call_id: 'call_a' },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('subagent_start', {
      metadata: { subagent_name: 'beta', tool_call_id: 'call_b' },
    }), NOW);
    // Scrambled interleave: beta's events arrive between alpha's.
    next = applyAssistantStreamEvent(next, rawStreamEvent('subagent_message_delta', {
      content: 'Beta says: ',
      metadata: { tool_call_id: 'call_b' },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('subagent_message_delta', {
      content: 'Alpha says: ',
      metadata: { tool_call_id: 'call_a' },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('subagent_message_delta', {
      content: 'hello',
      metadata: { tool_call_id: 'call_b' },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('subagent_message_delta', {
      content: 'hi',
      metadata: { tool_call_id: 'call_a' },
    }), NOW);

    expect(next.timelineItems).toHaveLength(2);
    const alphaItem = next.timelineItems.find((item) => item.toolCallId === 'call_a');
    const betaItem = next.timelineItems.find((item) => item.toolCallId === 'call_b');
    expect(alphaItem.content).toBe('Alpha says: hi');
    expect(betaItem.content).toBe('Beta says: hello');
  });

  // A2A call_agent invocations get their own grouped card too, but keyed off
  // metadata.tool_name === 'call_agent' on the generic tool_start/tool_result
  // events rather than a dedicated event-type prefix -- Progenitor's own
  // dispatch/return for that call is an ordinary tool call, handled no
  // differently than search_web or read_file, and reads as one: it falls
  // through to the generic stream_event push in addition to updating the
  // card (unlike subagent, whose dispatch has no such "regular tool call"
  // meaning of its own).
  it('call_agent tool_start creates an a2a_call timeline item AND a plain stream_event row', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('tool_start', {
      content: 'call_agent',
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_1', args_preview: { agent: 'greetings_agent' } },
    });
    const next = applyAssistantStreamEvent(turn, evt, NOW);

    expect(next.timelineItems).toHaveLength(2);
    // The plain dispatch row has to read before the card it explains -- the
    // call itself comes first, same as any other tool_start.
    expect(next.timelineItems.map((item) => item.kind)).toEqual(['stream_event', 'a2a_call']);
    const a2aItem = next.timelineItems.find((item) => item.kind === 'a2a_call');
    expect(a2aItem.id).toBe('a2a-call_1');
    expect(a2aItem.agentName).toBe('greetings_agent');
    expect(a2aItem.status).toBe('running');
    const streamItem = next.timelineItems.find((item) => item.kind === 'stream_event');
    expect(streamItem.event.eventType).toBe('tool_start');
    expect(streamItem.event.metadata.tool_call_id).toBe('call_1');
  });

  it('a tool_start for a different tool is unaffected (falls through to generic stream_event)', () => {
    const turn = baseTurn();
    const next = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', {
      metadata: { tool_name: 'search_web', tool_call_id: 'call_1' },
    }), NOW);
    expect(next.timelineItems).toHaveLength(1);
    expect(next.timelineItems[0].kind).toBe('stream_event');
  });

  it('two concurrent A2A calls update independently with no cross-contamination', () => {
    const turn = baseTurn();
    let next = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', {
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_a', args_preview: { agent: 'alpha_agent' } },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('tool_start', {
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_b', args_preview: { agent: 'beta_agent' } },
    }), NOW);
    // Scrambled interleave: beta's progress arrives between alpha's.
    next = applyAssistantStreamEvent(next, rawStreamEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_b', state: 'working', message_text: 'beta step 1' },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_a', state: 'working', message_text: 'alpha step 1' },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('tool_result', {
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_a', duration_ms: 5, result_preview: 'alpha done' },
    }), NOW);

    // 5 items: an a2a_call card per concurrent call (2), plus a plain
    // stream_event row for each call_agent tool_start/tool_result (3: two
    // starts + alpha's result) -- progress events update their card in
    // place and don't get their own row.
    expect(next.timelineItems).toHaveLength(5);
    // Each call's dispatch marker reads before its own card, and cards
    // update in place (no re-append) as progress/result land -- this is the
    // exact "invoke it twice concurrently" shape that originally regressed:
    // start_a, card_a, start_b, card_b, then alpha's result appended last.
    expect(next.timelineItems.map((item) => (
      item.kind === 'a2a_call' ? ['a2a_call', item.toolCallId] : [item.event.eventType, item.event.metadata.tool_call_id]
    ))).toEqual([
      ['tool_start', 'call_a'],
      ['a2a_call', 'call_a'],
      ['tool_start', 'call_b'],
      ['a2a_call', 'call_b'],
      ['tool_result', 'call_a'],
    ]);
    const alphaItem = next.timelineItems.find((item) => item.kind === 'a2a_call' && item.toolCallId === 'call_a');
    const betaItem = next.timelineItems.find((item) => item.kind === 'a2a_call' && item.toolCallId === 'call_b');
    expect(alphaItem.status).toBe('success');
    expect(alphaItem.messages.map((m) => m.text)).toEqual(['alpha step 1']);
    expect(betaItem.status).toBe('running');
    expect(betaItem.messages.map((m) => m.text)).toEqual(['beta step 1']);

    const streamRows = next.timelineItems.filter((item) => item.kind === 'stream_event');
    expect(streamRows.map((item) => [item.event.eventType, item.event.metadata.tool_call_id])).toEqual([
      ['tool_start', 'call_a'],
      ['tool_start', 'call_b'],
      ['tool_result', 'call_a'],
    ]);
  });

  // Regression: an a2a_call_progress with no matching card (tool_start frame
  // lost, or a malformed event with no tool_call_id) used to vanish
  // entirely -- applyA2ACallEvent returns null (see 'ignores a2a_call_progress
  // with no prior tool_start' below), but applyAssistantStreamEvent
  // unconditionally short-circuited on eventType === 'a2a_call_progress'
  // regardless of whether anything actually consumed it, skipping the
  // generic stream_event push that every other unhandled event type falls
  // through to. Must match buildDefaultAssistantTimeline's identical
  // fallback for the reload path (see 'falls through to a flat stream_event
  // row for call_agent events with no tool_call_id' below).
  it('falls through to a flat stream_event row for an orphaned a2a_call_progress with no matching card', () => {
    const turn = baseTurn();
    const next = applyAssistantStreamEvent(turn, rawStreamEvent('a2a_call_progress', {
      content: 'late',
      metadata: { tool_call_id: 'call_missing', state: 'working', message_text: 'late' },
    }), NOW);

    expect(next.timelineItems.filter((item) => item.kind === 'a2a_call')).toHaveLength(0);
    expect(next.timelineItems).toHaveLength(1);
    expect(next.timelineItems[0].kind).toBe('stream_event');
    expect(next.timelineItems[0].event.eventType).toBe('a2a_call_progress');
    expect(next.timelineItems[0].event.metadata.tool_call_id).toBe('call_missing');
  });
});

// ─── applySubagentEvent ──────────────────────────────────────────────────

describe('applySubagentEvent', () => {
  function subagentEvent(type, overrides = {}) {
    return {
      id: `evt-${type}`,
      eventType: type,
      content: '',
      timestamp: NOW,
      metadata: {},
      ...overrides,
    };
  }

  it('returns null for a non-subagent event type', () => {
    expect(applySubagentEvent([], subagentEvent('tool_start', { metadata: { tool_call_id: 'x' } }))).toBeNull();
  });

  it('returns null when tool_call_id is missing from metadata', () => {
    expect(applySubagentEvent([], subagentEvent('subagent_start', { metadata: {} }))).toBeNull();
  });

  it('creates a new card on subagent_start with running status', () => {
    const items = applySubagentEvent([], subagentEvent('subagent_start', {
      content: 'researcher',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    }));
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      kind: 'subagent',
      id: 'subagent-call_1',
      toolCallId: 'call_1',
      subagentName: 'researcher',
      status: 'running',
      content: '',
      children: [],
    });
  });

  it('defensively creates a minimal row for an out-of-order non-start event (no prior subagent_start seen)', () => {
    const items = applySubagentEvent([], subagentEvent('subagent_message_delta', {
      content: 'late',
      metadata: { tool_call_id: 'call_1' },
    }));
    expect(items).toHaveLength(1);
    expect(items[0].toolCallId).toBe('call_1');
    expect(items[0].content).toBe('late');
  });

  it('accumulates subagent_message_delta content across multiple calls', () => {
    let items = applySubagentEvent([], subagentEvent('subagent_start', {
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    }));
    items = applySubagentEvent(items, subagentEvent('subagent_message_delta', {
      content: 'Hello ',
      metadata: { tool_call_id: 'call_1' },
    }));
    items = applySubagentEvent(items, subagentEvent('subagent_message_delta', {
      content: 'world',
      metadata: { tool_call_id: 'call_1' },
    }));
    expect(items).toHaveLength(1);
    expect(items[0].content).toBe('Hello world');
  });

  it('find-or-creates nested tool children keyed by nested_tool_call_id, in place', () => {
    let items = applySubagentEvent([], subagentEvent('subagent_start', {
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    }));
    items = applySubagentEvent(items, subagentEvent('subagent_tool_start', {
      metadata: {
        tool_call_id: 'call_1',
        subagent_name: 'researcher',
        nested_tool_call_id: 'nested_1',
        nested_tool_name: 'read_file',
        args_preview: { path: 'README.md' },
      },
    }));
    expect(items[0].children).toHaveLength(1);
    expect(items[0].children[0]).toMatchObject({
      id: 'nested_1',
      toolName: 'read_file',
      status: 'running',
      argsPreview: { path: 'README.md' },
    });

    items = applySubagentEvent(items, subagentEvent('subagent_tool_result', {
      metadata: {
        tool_call_id: 'call_1',
        nested_tool_call_id: 'nested_1',
        nested_tool_name: 'read_file',
        duration_ms: 4.5,
        result_preview: '# Title',
      },
    }));
    // Same child updated in place, not duplicated.
    expect(items[0].children).toHaveLength(1);
    expect(items[0].children[0]).toMatchObject({
      id: 'nested_1',
      status: 'success',
      durationMs: 4.5,
      resultPreview: '# Title',
    });
  });

  it('subagent_tool_error marks the matching child as errored', () => {
    let items = applySubagentEvent([], subagentEvent('subagent_tool_start', {
      metadata: { tool_call_id: 'call_1', nested_tool_call_id: 'nested_1', nested_tool_name: 'shell_exec' },
    }));
    items = applySubagentEvent(items, subagentEvent('subagent_tool_error', {
      metadata: {
        tool_call_id: 'call_1',
        nested_tool_call_id: 'nested_1',
        nested_tool_name: 'shell_exec',
        duration_ms: 2,
        error: 'boom',
      },
    }));
    expect(items[0].children[0]).toMatchObject({ status: 'error', error: 'boom', durationMs: 2 });
  });

  it('subagent_result finalizes status/finishedAt/durationMs/resultPreview on success', () => {
    let items = applySubagentEvent([], subagentEvent('subagent_start', {
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    }));
    items = applySubagentEvent(items, subagentEvent('subagent_result', {
      timestamp: NOW + 500,
      metadata: {
        subagent_name: 'researcher',
        tool_call_id: 'call_1',
        success: true,
        duration_ms: 120,
        result_preview: 'done',
      },
    }));
    expect(items[0]).toMatchObject({
      status: 'success',
      finishedAt: NOW + 500,
      durationMs: 120,
      resultPreview: 'done',
    });
  });

  it('subagent_result with success:false finalizes status as error and records the error', () => {
    let items = applySubagentEvent([], subagentEvent('subagent_start', {
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    }));
    items = applySubagentEvent(items, subagentEvent('subagent_result', {
      metadata: { tool_call_id: 'call_1', success: false, error: 'subagent crashed' },
    }));
    expect(items[0].status).toBe('error');
    expect(items[0].error).toBe('subagent crashed');
  });

  it('never mutates the input array (immutable updates)', () => {
    const original = applySubagentEvent([], subagentEvent('subagent_start', {
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    }));
    const snapshot = JSON.parse(JSON.stringify(original));
    applySubagentEvent(original, subagentEvent('subagent_message_delta', {
      content: 'more',
      metadata: { tool_call_id: 'call_1' },
    }));
    expect(original).toEqual(snapshot);
  });
});

// ─── applyA2ACallEvent ───────────────────────────────────────────────────

describe('applyA2ACallEvent', () => {
  function a2aEvent(type, overrides = {}) {
    return {
      id: `evt-${type}`,
      eventType: type,
      content: '',
      timestamp: NOW,
      metadata: {},
      ...overrides,
    };
  }

  it('returns null for an unrelated tool_start (different tool_name)', () => {
    expect(applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'search_web', tool_call_id: 'call_1' },
    }))).toBeNull();
  });

  it('returns null when tool_call_id is missing from metadata', () => {
    expect(applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'call_agent' },
    }))).toBeNull();
  });

  it('creates a new card on call_agent tool_start with running status', () => {
    const items = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_1', args_preview: { agent: 'greetings_agent' } },
    }));
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      kind: 'a2a_call',
      id: 'a2a-call_1',
      toolCallId: 'call_1',
      agentName: 'greetings_agent',
      status: 'running',
      messages: [],
    });
  });

  it('does not seed agentName when args_preview.agent is absent (auto-route case)', () => {
    const items = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_1' },
    }));
    expect(items[0].agentName).toBe('');
  });

  it('seeds agentName from args_preview.agent_name when the LLM used that alias key', () => {
    const items = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_1', args_preview: { agent_name: 'greetings_agent' } },
    }));
    expect(items[0].agentName).toBe('greetings_agent');
  });

  it('ignores a2a_call_progress with no prior tool_start (unlike subagent, does not defensively create)', () => {
    const items = applyA2ACallEvent([], a2aEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_1', state: 'working', message_text: 'late' },
    }));
    expect(items).toBeNull();
  });

  it('ignores tool_result for a call_agent tool_call_id with no prior card', () => {
    const items = applyA2ACallEvent([], a2aEvent('tool_result', {
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_1', duration_ms: 5 },
    }));
    expect(items).toBeNull();
  });

  it('appends a2a_call_progress messages in order and backfills agentName from metadata.agent_name', () => {
    let items = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_1' },
    }));
    items = applyA2ACallEvent(items, a2aEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_1', agent_name: 'greetings_agent', state: 'working', message_text: 'Step 1/3: prep...' },
    }));
    items = applyA2ACallEvent(items, a2aEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_1', agent_name: 'greetings_agent', state: 'working', message_text: 'Step 2/3: build...' },
    }));
    expect(items[0].agentName).toBe('greetings_agent');
    expect(items[0].messages.map((m) => m.text)).toEqual(['Step 1/3: prep...', 'Step 2/3: build...']);
    expect(items[0].messages.map((m) => m.state)).toEqual(['working', 'working']);
    // Still running -- only tool_result/tool_error finalize status.
    expect(items[0].status).toBe('running');
  });

  it('tool_result finalizes status/finishedAt/durationMs/resultPreview on success', () => {
    let items = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_1' },
    }));
    items = applyA2ACallEvent(items, a2aEvent('tool_result', {
      timestamp: NOW + 500,
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_1', duration_ms: 187, result_preview: 'Hello!' },
    }));
    expect(items[0]).toMatchObject({
      status: 'success',
      finishedAt: NOW + 500,
      durationMs: 187,
      resultPreview: 'Hello!',
    });
  });

  it('tool_error finalizes status as error and records the error', () => {
    let items = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_1' },
    }));
    items = applyA2ACallEvent(items, a2aEvent('tool_error', {
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_1', duration_ms: 12, error: 'agent unreachable' },
    }));
    expect(items[0].status).toBe('error');
    expect(items[0].error).toBe('agent unreachable');
  });

  it('never mutates the input array (immutable updates)', () => {
    const original = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'call_agent', tool_call_id: 'call_1' },
    }));
    const snapshot = JSON.parse(JSON.stringify(original));
    applyA2ACallEvent(original, a2aEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_1', state: 'working', message_text: 'more' },
    }));
    expect(original).toEqual(snapshot);
  });
});

// ─── applyAssistantChunk ─────────────────────────────────────────────────

describe('applyAssistantChunk', () => {
  it('appends content and increments tokenCount', () => {
    const turn = baseTurn();
    const next = applyAssistantChunk(turn, 'Hello', NOW);
    expect(next.content).toBe('Hello');
    expect(next.tokenCount).toBe(1);
  });

  it('accumulates multiple chunks', () => {
    const turn = baseTurn();
    const a = applyAssistantChunk(turn, 'Hello ', NOW);
    const b = applyAssistantChunk(a, 'World', NOW);
    expect(b.content).toBe('Hello World');
    expect(b.tokenCount).toBe(2);
  });

  // Acceptance: first chunk creates assistant row and stamps assistantTimestamp.
  it('on first chunk: creates assistantMessageId, stamps assistantTimestamp, adds assistant_message timeline item', () => {
    const turn = baseTurn();
    const next = applyAssistantChunk(turn, 'Hi', NOW);
    expect(next.assistantMessageId).toBe(`${STREAM_ID}-assistant`);
    expect(next.assistantTimestamp).toBe(NOW);
    expect(next.timelineItems).toHaveLength(1);
    expect(next.timelineItems[0].kind).toBe('assistant_message');
    expect(next.timelineItems[0].content).toBe('Hi');
    expect(next.timelineItems[0].timestamp).toBe(NOW);
    expect(next.timelineItems[0].isStreaming).toBe(true);
    expect(next.timelineItems[0].error).toBe(false);
  });

  it('on first chunk: assistant_message uses CHAT_AGENT_NAME when no displayAgentName set', () => {
    const turn = baseTurn();
    const next = applyAssistantChunk(turn, 'text', NOW);
    expect(next.timelineItems[0].displayAgentName).toBe(CHAT_AGENT_NAME);
  });

  it('on first chunk: assistant_message uses displayAgentName when metadata was provided earlier', () => {
    const turn = baseTurn();
    const withMeta = applyAssistantMetadata(turn, { display_agent_name: 'CustomAgent' });
    const next = applyAssistantChunk(withMeta, 'text', NOW);
    expect(next.timelineItems[0].displayAgentName).toBe('CustomAgent');
  });

  it('on subsequent chunks: updates the existing assistant_message timeline item content/tokenCount', () => {
    const turn = baseTurn();
    const first = applyAssistantChunk(turn, 'Hello ', NOW);
    const second = applyAssistantChunk(first, 'World', NOW);
    expect(second.timelineItems).toHaveLength(1);
    expect(second.timelineItems[0].content).toBe('Hello World');
    expect(second.timelineItems[0].tokenCount).toBe(2);
  });

  it('does NOT overwrite assistantMessageId or assistantTimestamp on subsequent chunks', () => {
    const turn = baseTurn();
    const first = applyAssistantChunk(turn, 'A', NOW);
    const second = applyAssistantChunk(first, 'B', NOW + 5000);
    expect(second.assistantMessageId).toBe(first.assistantMessageId);
    expect(second.assistantTimestamp).toBe(NOW); // still first stamp
  });

  it('preserves stream_event timeline items when adding the assistant row', () => {
    const turn = baseTurn();
    const withEvent = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', { content: 'searching' }), NOW);
    const withChunk = applyAssistantChunk(withEvent, 'Answer', NOW + 100);

    expect(withChunk.timelineItems).toHaveLength(2);
    expect(withChunk.timelineItems[0].kind).toBe('stream_event');
    expect(withChunk.timelineItems[1].kind).toBe('assistant_message');
    // Assistant row content is the chunk text, not the event content.
    expect(withChunk.timelineItems[1].content).toBe('Answer');
  });

  it('does NOT mutate the original turn', () => {
    const turn = baseTurn();
    const copy = { ...turn, timelineItems: [...turn.timelineItems] };
    applyAssistantChunk(turn, 'data', NOW);
    expect(turn.content).toBe('');
    expect(turn.tokenCount).toBe(0);
    expect(turn.assistantMessageId).toBeNull();
    expect(turn.timelineItems).toEqual(copy.timelineItems);
  });
});

// ─── buildDefaultAssistantTimeline ───────────────────────────────────────

describe('buildDefaultAssistantTimeline', () => {
  it('returns undefined for non-assistant messages', () => {
    expect(buildDefaultAssistantTimeline({ role: 'user', content: 'hi' })).toBeUndefined();
  });

  it('returns undefined for null/undefined input', () => {
    expect(buildDefaultAssistantTimeline(null)).toBeUndefined();
    expect(buildDefaultAssistantTimeline(undefined)).toBeUndefined();
  });

  // Acceptance: items are ordered purely by real timestamp -- a tool call
  // timestamped after the answer message sorts after it, one timestamped
  // before sorts before it. No rule about tool calls vs. text is encoded.
  it('sorts items by real timestamp', () => {
    const message = {
      role: 'assistant',
      id: 'msg_1',
      content: 'Hello',
      timestamp: 2000,
      isStreaming: false,
      error: false,
      displayAgentName: 'MyAgent',
      displayModelName: 'gpt-4',
      displayToolCalls: [{ name: 'search' }],
      tokenCount: 5,
      reasoningTokenCount: 0,
      reasoningText: '',
      streamEvents: [
        { id: 'before', eventType: 'tool_start', content: 'searching', timestamp: 1000 },
        { id: 'after', eventType: 'tool_start', content: 'follow-up', timestamp: 3000 },
      ],
    };
    const items = buildDefaultAssistantTimeline(message);
    expect(items.map((item) => item.id)).toEqual(['before', 'msg_1', 'after']);
    const assistantItem = items[1];
    expect(assistantItem.kind).toBe('assistant_message');
    expect(assistantItem.content).toBe('Hello');
    expect(assistantItem.timestamp).toBe(2000);
    expect(assistantItem.displayAgentName).toBe('MyAgent');
    expect(assistantItem.displayModelName).toBe('gpt-4');
    expect(assistantItem.displayToolCalls).toEqual([{ name: 'search' }]);
  });

  // Acceptance: a stream event with no timestamp (e.g. reconstructed from
  // checkpoint data predating created_at stamping) must not corrupt the
  // sort with NaN -- it falls back to the assistant row's own timestamp
  // instead of landing in an undefined position.
  it('sorts items with a missing timestamp next to the assistant row instead of producing NaN order', () => {
    const message = {
      role: 'assistant',
      id: 'msg_1',
      content: 'Hello',
      timestamp: 2000,
      streamEvents: [
        { id: 'has_ts', eventType: 'tool_start', content: 'searching', timestamp: 1000 },
        { id: 'no_ts', eventType: 'tool_start', content: 'legacy call', timestamp: undefined },
      ],
    };
    const items = buildDefaultAssistantTimeline(message);
    expect(items.map((item) => item.id)).toEqual(['has_ts', 'no_ts', 'msg_1']);
  });

  // Acceptance: the backend persists raw write_todos tool_start/tool_result
  // events but not the synthetic todos_update rows the live turn appends --
  // reconstruct one row per write_todos call, next to its tool_result, keyed
  // by tool_call_id.
  it('synthesizes a todos_update item next to each write_todos tool_result', () => {
    const message = {
      role: 'assistant',
      id: 'msg_1',
      content: 'done',
      streamEvents: [
        {
          id: 'e1',
          eventType: 'tool_start',
          metadata: {
            tool_name: 'write_todos',
            tool_call_id: 'call_1',
            args_preview: { todos: [{ content: 'a', status: 'in_progress' }] },
          },
        },
        { id: 'e2', eventType: 'tool_result', metadata: { tool_name: 'write_todos', tool_call_id: 'call_1' } },
        { id: 'e3', eventType: 'tool_start', metadata: { tool_name: 'search' } },
        { id: 'e4', eventType: 'tool_result', metadata: { tool_name: 'search' } },
        {
          id: 'e5',
          eventType: 'tool_start',
          metadata: {
            tool_name: 'write_todos',
            tool_call_id: 'call_2',
            args_preview: { todos: [{ content: 'a', status: 'completed' }] },
          },
        },
        { id: 'e6', eventType: 'tool_result', metadata: { tool_name: 'write_todos', tool_call_id: 'call_2' } },
      ],
    };
    const items = buildDefaultAssistantTimeline(message);
    const todosItems = items.filter((item) => item.event?.eventType === 'todos_update');

    // One per write_todos call, not just the last -- each threaded in right
    // after its tool_result so the reload order matches the live order.
    expect(todosItems).toHaveLength(2);
    expect(items[items.findIndex((item) => item.id === 'e2') + 1]).toBe(todosItems[0]);
    expect(todosItems[0].event.metadata.todos).toEqual([{ content: 'a', status: 'in_progress' }]);
    expect(items[items.findIndex((item) => item.id === 'e6') + 1]).toBe(todosItems[1]);
    expect(todosItems[1].event.metadata.todos).toEqual([{ content: 'a', status: 'completed' }]);
  });

  it('does not add a todos_update item when there was no write_todos call', () => {
    const message = {
      role: 'assistant',
      id: 'msg_1',
      content: 'hi',
      streamEvents: [{ id: 'e1', eventType: 'tool_start', metadata: { tool_name: 'search' } }],
    };
    const items = buildDefaultAssistantTimeline(message);
    expect(items.some((item) => item.event?.eventType === 'todos_update')).toBe(false);
  });

  it('uses CHAT_AGENT_NAME when displayAgentName is missing', () => {
    const items = buildDefaultAssistantTimeline({ role: 'assistant', content: 'hi' });
    expect(items[0].displayAgentName).toBe(CHAT_AGENT_NAME);
  });

  it('falls back to empty string for displayModelName when missing', () => {
    const items = buildDefaultAssistantTimeline({ role: 'assistant', content: 'hi' });
    expect(items[0].displayModelName).toBe('');
  });

  it('falls back to empty array for displayToolCalls when missing', () => {
    const items = buildDefaultAssistantTimeline({ role: 'assistant', content: 'hi' });
    expect(items[0].displayToolCalls).toEqual([]);
  });

  it('uses an empty streamEvents array when message has no streamEvents', () => {
    const items = buildDefaultAssistantTimeline({ role: 'assistant', content: 'hi' });
    // Only the assistant_message, no stream_event items.
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe('assistant_message');
  });

  it('uses a default id when the message lacks one; timestamp is passed through as-is', () => {
    const items = buildDefaultAssistantTimeline({ role: 'assistant', content: 'txt' });
    expect(items[0].id).toBeTruthy();
    expect(items[0].timestamp).toBeUndefined();
  });

  // Acceptance: flat persisted subagent_* events reconstruct into a single
  // grouped 'subagent' card (via collectSubagentGroups replaying
  // applySubagentEvent), spliced in once at the earliest event's timestamp --
  // not as N separate flat stream_event rows.
  it('reconstructs flat subagent_* events into a single grouped subagent card', () => {
    const message = {
      role: 'assistant',
      id: 'msg_1',
      content: 'All done.',
      timestamp: 5000,
      streamEvents: [
        {
          id: 'e1',
          eventType: 'subagent_start',
          timestamp: 1000,
          metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
        },
        {
          id: 'e2',
          eventType: 'subagent_tool_start',
          timestamp: 1100,
          metadata: {
            tool_call_id: 'call_1',
            subagent_name: 'researcher',
            nested_tool_call_id: 'nested_1',
            nested_tool_name: 'read_file',
          },
        },
        {
          id: 'e3',
          eventType: 'subagent_message_delta',
          content: 'Findings: ',
          timestamp: 1150,
          metadata: { tool_call_id: 'call_1' },
        },
        {
          id: 'e4',
          eventType: 'subagent_message_delta',
          content: 'done.',
          timestamp: 1180,
          metadata: { tool_call_id: 'call_1' },
        },
        {
          id: 'e5',
          eventType: 'subagent_result',
          timestamp: 1200,
          metadata: { subagent_name: 'researcher', tool_call_id: 'call_1', success: true, duration_ms: 42 },
        },
      ],
    };
    const items = buildDefaultAssistantTimeline(message);

    const subagentItems = items.filter((item) => item.kind === 'subagent');
    expect(subagentItems).toHaveLength(1);
    expect(subagentItems[0]).toMatchObject({
      id: 'subagent-call_1',
      toolCallId: 'call_1',
      subagentName: 'researcher',
      status: 'success',
      durationMs: 42,
      content: 'Findings: done.',
    });
    expect(subagentItems[0].children).toHaveLength(1);
    expect(subagentItems[0].children[0]).toMatchObject({ id: 'nested_1', toolName: 'read_file' });

    // Spliced in once, at the earliest event's timestamp (1000) -- before the
    // assistant_message row (timestamped 5000), not once per flat event.
    expect(items.map((item) => item.kind)).toEqual(['subagent', 'assistant_message']);
    expect(subagentItems[0].timestamp).toBe(1000);
  });

  it('keeps two concurrent subagents as two separate grouped cards on reload', () => {
    const message = {
      role: 'assistant',
      id: 'msg_1',
      content: 'done',
      timestamp: 5000,
      streamEvents: [
        { id: 'e1', eventType: 'subagent_start', timestamp: 1000, metadata: { subagent_name: 'alpha', tool_call_id: 'call_a' } },
        { id: 'e2', eventType: 'subagent_start', timestamp: 1010, metadata: { subagent_name: 'beta', tool_call_id: 'call_b' } },
        { id: 'e3', eventType: 'subagent_message_delta', content: 'A', timestamp: 1020, metadata: { tool_call_id: 'call_a' } },
        { id: 'e4', eventType: 'subagent_message_delta', content: 'B', timestamp: 1030, metadata: { tool_call_id: 'call_b' } },
      ],
    };
    const items = buildDefaultAssistantTimeline(message);
    const subagentItems = items.filter((item) => item.kind === 'subagent');
    expect(subagentItems).toHaveLength(2);
    expect(subagentItems.find((item) => item.toolCallId === 'call_a').content).toBe('A');
    expect(subagentItems.find((item) => item.toolCallId === 'call_b').content).toBe('B');
  });

  // Acceptance: flat persisted call_agent tool_start/a2a_call_progress/
  // tool_result events reconstruct into a single grouped 'a2a_call' card
  // (via collectA2ACallGroups replaying applyA2ACallEvent), spliced in once
  // at the earliest event's timestamp -- while the tool_start/tool_result
  // ALSO still appear as their own flat stream_event rows (Progenitor's own
  // dispatch/return is a regular tool call and reads as one -- see the
  // matching comment in buildDefaultAssistantTimeline). Only the two
  // a2a_call_progress updates are consumed into the card alone.
  it('reconstructs flat call_agent tool events into a single grouped a2a_call card plus plain start/result rows', () => {
    const message = {
      role: 'assistant',
      id: 'msg_1',
      content: 'All done.',
      timestamp: 5000,
      streamEvents: [
        {
          id: 'e1',
          eventType: 'tool_start',
          timestamp: 1000,
          metadata: { tool_name: 'call_agent', tool_call_id: 'call_1', args_preview: { agent: 'greetings_agent' } },
        },
        {
          id: 'e2',
          eventType: 'a2a_call_progress',
          timestamp: 1100,
          metadata: { tool_call_id: 'call_1', agent_name: 'greetings_agent', state: 'working', message_text: 'Step 1/3: prep...' },
        },
        {
          id: 'e3',
          eventType: 'a2a_call_progress',
          timestamp: 1150,
          metadata: { tool_call_id: 'call_1', agent_name: 'greetings_agent', state: 'working', message_text: 'Step 2/3: build...' },
        },
        {
          id: 'e4',
          eventType: 'tool_result',
          timestamp: 1200,
          metadata: { tool_name: 'call_agent', tool_call_id: 'call_1', duration_ms: 187, result_preview: 'Hello!' },
        },
      ],
    };
    const items = buildDefaultAssistantTimeline(message);

    const a2aItems = items.filter((item) => item.kind === 'a2a_call');
    expect(a2aItems).toHaveLength(1);
    expect(a2aItems[0]).toMatchObject({
      id: 'a2a-call_1',
      toolCallId: 'call_1',
      agentName: 'greetings_agent',
      status: 'success',
      durationMs: 187,
      resultPreview: 'Hello!',
    });
    expect(a2aItems[0].messages.map((m) => m.text)).toEqual(['Step 1/3: prep...', 'Step 2/3: build...']);

    // Card spliced in once, right after the tool_start (e1) that seeds it --
    // not once per flat event, and not ahead of that tool_start either: the
    // dispatch marker has to read before the progress card it explains. e1/e4
    // (start/result) each still get their own plain stream_event row; e2/e3
    // (progress) are fully consumed.
    expect(items.map((item) => item.kind)).toEqual([
      'stream_event', 'a2a_call', 'stream_event', 'assistant_message',
    ]);
    expect(a2aItems[0].timestamp).toBe(1000);

    const flatIds = items.filter((item) => item.kind === 'stream_event').map((item) => item.id);
    expect(flatIds).toEqual(['e1', 'e4']);
  });

  it('keeps two concurrent A2A calls as two separate grouped cards on reload', () => {
    const message = {
      role: 'assistant',
      id: 'msg_1',
      content: 'done',
      timestamp: 5000,
      streamEvents: [
        { id: 'e1', eventType: 'tool_start', timestamp: 1000, metadata: { tool_name: 'call_agent', tool_call_id: 'call_a', args_preview: { agent: 'alpha_agent' } } },
        { id: 'e2', eventType: 'tool_start', timestamp: 1010, metadata: { tool_name: 'call_agent', tool_call_id: 'call_b', args_preview: { agent: 'beta_agent' } } },
        { id: 'e3', eventType: 'a2a_call_progress', timestamp: 1020, metadata: { tool_call_id: 'call_a', state: 'working', message_text: 'A' } },
        { id: 'e4', eventType: 'a2a_call_progress', timestamp: 1030, metadata: { tool_call_id: 'call_b', state: 'working', message_text: 'B' } },
      ],
    };
    const items = buildDefaultAssistantTimeline(message);
    const a2aItems = items.filter((item) => item.kind === 'a2a_call');
    expect(a2aItems).toHaveLength(2);
    expect(a2aItems.find((item) => item.toolCallId === 'call_a').messages[0].text).toBe('A');
    expect(a2aItems.find((item) => item.toolCallId === 'call_b').messages[0].text).toBe('B');
  });

  // Regression: a call_agent-tagged event with no tool_call_id (malformed
  // persisted data) can never form a group -- it must fall through to the
  // generic stream_event row instead of being silently dropped. The
  // grouping block used to `continue` unconditionally for a2a_call_progress,
  // group-found-or-not, which would have vanished a malformed progress
  // event entirely: no card, no fallback row.
  it('falls through to a flat stream_event row for call_agent events with no tool_call_id, instead of dropping them', () => {
    const message = {
      role: 'assistant',
      id: 'msg_1',
      content: 'done',
      timestamp: 5000,
      streamEvents: [
        { id: 'e1', eventType: 'tool_start', timestamp: 1000, metadata: { tool_name: 'call_agent', args_preview: { agent: 'greetings_agent' } } },
        { id: 'e2', eventType: 'a2a_call_progress', timestamp: 1010, metadata: { state: 'working', message_text: 'orphaned' } },
      ],
    };
    const items = buildDefaultAssistantTimeline(message);
    expect(items.filter((item) => item.kind === 'a2a_call')).toHaveLength(0);
    expect(items.find((item) => item.kind === 'stream_event' && item.id === 'e1')).toBeDefined();
    expect(items.find((item) => item.kind === 'stream_event' && item.id === 'e2')).toBeDefined();
  });

  // Same regression for the subagent grouping block (identical pre-existing
  // pattern, fixed the same way).
  it('falls through to a flat stream_event row for a subagent_start with no tool_call_id, instead of dropping it', () => {
    const message = {
      role: 'assistant',
      id: 'msg_1',
      content: 'done',
      timestamp: 5000,
      streamEvents: [
        { id: 'e1', eventType: 'subagent_start', timestamp: 1000, metadata: { subagent_name: 'hello-world' } },
      ],
    };
    const items = buildDefaultAssistantTimeline(message);
    expect(items.filter((item) => item.kind === 'subagent')).toHaveLength(0);
    const flatItem = items.find((item) => item.kind === 'stream_event' && item.id === 'e1');
    expect(flatItem).toBeDefined();
  });
});

// ─── toCommittedAssistantMessage ─────────────────────────────────────────

describe('toCommittedAssistantMessage', () => {
  it('converts a complete turn to a message-like object', () => {
    const turn = {
      ...baseTurn(),
      assistantMessageId: 'msg_42',
      assistantTimestamp: NOW,
      content: 'Hello',
      tokenCount: 5,
      reasoningText: 'thinking',
      reasoningTokenCount: 3,
      streamEvents: [{ id: 'e1' }],
      displayAgentName: 'Agent',
      displayModelName: 'gpt-4',
      displayToolCalls: [{ name: 'search' }],
      isStreaming: false,
      error: false,
      timelineItems: [{ kind: 'assistant_message', id: 'msg_42', content: 'Hello' }],
    };
    const msg = toCommittedAssistantMessage(turn);
    expect(msg).toEqual({
      id: 'msg_42',
      role: 'assistant',
      content: 'Hello',
      timestamp: NOW,
      tokenCount: 5,
      reasoningTokenCount: 3,
      reasoningText: 'thinking',
      streamEvents: [{ id: 'e1' }],
      displayAgentName: 'Agent',
      displayModelName: 'gpt-4',
      displayToolCalls: [{ name: 'search' }],
      isStreaming: false,
      error: false,
      timelineItems: [{ kind: 'assistant_message', id: 'msg_42', content: 'Hello' }],
    });
  });

  it('falls back streamId-based id and startedAt timestamp when assistantMessageId/timestamp null', () => {
    const turn = baseTurn();
    const msg = toCommittedAssistantMessage(turn);
    expect(msg.id).toBe(`${STREAM_ID}-assistant`);
    expect(msg.timestamp).toBe(NOW - 1000);
  });
});

// ─── finalizeLiveAssistantTurn ───────────────────────────────────────────

describe('finalizeLiveAssistantTurn', () => {
  it('returns null when turn is null/undefined', () => {
    expect(finalizeLiveAssistantTurn(null, { status: 'completed', now: NOW })).toBeNull();
    expect(finalizeLiveAssistantTurn(undefined, { status: 'completed', now: NOW })).toBeNull();
  });

  // Acceptance: completion with output returns a normal committed message.
  describe('status = completed', () => {
    it('with visible output (content) returns a committed assistant message', () => {
      const turn = baseTurn();
      const withText = applyAssistantChunk(turn, 'Hello', NOW);
      const result = finalizeLiveAssistantTurn(withText, { status: 'completed', now: NOW + 100 });

      expect(result).not.toBeNull();
      expect(result.role).toBe('assistant');
      expect(result.content).toBe('Hello');
      expect(result.isStreaming).toBe(false);
      expect(result.error).toBe(false);
      expect(result.timelineItems[0].isStreaming).toBe(false);
    });

    it('with visible output (stream events but no text) returns a committed message', () => {
      const turn = baseTurn();
      const withEvent = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', { content: 'searching' }), NOW);
      const result = finalizeLiveAssistantTurn(withEvent, { status: 'completed', now: NOW + 100 });

      expect(result).not.toBeNull();
      expect(result.isStreaming).toBe(false);
      // There are stream events but no text content.
      expect(result.timelineItems[0].kind).toBe('stream_event');
    });

    // Acceptance: completion with NO output AND no events returns '(No response text)'.
    it('with NO visible output returns a placeholder message "(No response text)"', () => {
      const turn = baseTurn();
      const result = finalizeLiveAssistantTurn(turn, { status: 'completed', now: NOW });

      expect(result).not.toBeNull();
      expect(result.content).toBe('(No response text)');
      expect(result.isStreaming).toBe(false);
      expect(result.error).toBe(false);
      // Should have an assistant_message timeline item for the placeholder.
      expect(result.timelineItems).toHaveLength(1);
      expect(result.timelineItems[0].kind).toBe('assistant_message');
      expect(result.timelineItems[0].content).toBe('(No response text)');
    });
  });

  // Acceptance: cancelled with no output returns null.
  describe('status = cancelled', () => {
    it('with no visible output returns null', () => {
      const turn = baseTurn();
      const result = finalizeLiveAssistantTurn(turn, { status: 'cancelled', now: NOW });
      expect(result).toBeNull();
    });

    // Acceptance: cancelled with partial output keeps output with ' [Cancelled]' suffix.
    it('with partial visible output (content only) appends " [Cancelled]" suffix', () => {
      const turn = baseTurn();
      const withText = applyAssistantChunk(turn, 'Partial', NOW);
      const result = finalizeLiveAssistantTurn(withText, { status: 'cancelled', now: NOW });

      expect(result).not.toBeNull();
      expect(result.content).toBe('Partial [Cancelled]');
      expect(result.isStreaming).toBe(false);
    });

    it('with partial visible output (stream events only) returns a message', () => {
      const turn = baseTurn();
      const withEvent = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', { content: 'searching' }), NOW);
      const result = finalizeLiveAssistantTurn(withEvent, { status: 'cancelled', now: NOW });

      expect(result).not.toBeNull();
      expect(result.isStreaming).toBe(false);
    });

    it('does not double-append " [Cancelled]" suffix if already present', () => {
      const turn = baseTurn();
      const withText = applyAssistantChunk(turn, 'Partial', NOW);
      // Manually set the content to end with the suffix to test idempotency.
      withText.content = 'Partial [Cancelled]';
      const result = finalizeLiveAssistantTurn(withText, { status: 'cancelled', now: NOW });

      expect(result.content).toBe('Partial [Cancelled]');
    });
  });

  // Acceptance: error creates an error message.
  describe('status = error', () => {
    it('creates an error message with Error: prefix', () => {
      const turn = baseTurn();
      const result = finalizeLiveAssistantTurn(turn, {
        status: 'error',
        now: NOW,
        errorMessage: 'Something broke',
      });

      expect(result).not.toBeNull();
      expect(result.content).toBe('Error: Something broke');
      expect(result.error).toBe(true);
      expect(result.isStreaming).toBe(false);
    });

    it('uses a default message when errorMessage is omitted', () => {
      const turn = baseTurn();
      const result = finalizeLiveAssistantTurn(turn, { status: 'error', now: NOW });

      expect(result.content).toBe('Error: Unknown error');
      expect(result.error).toBe(true);
    });

    it('timeline contains an assistant_message item for the error', () => {
      const turn = baseTurn();
      const result = finalizeLiveAssistantTurn(turn, {
        status: 'error',
        now: NOW,
        errorMessage: 'fail',
      });

      expect(result.timelineItems).toHaveLength(1);
      expect(result.timelineItems[0].kind).toBe('assistant_message');
      expect(result.timelineItems[0].content).toBe('Error: fail');
      expect(result.timelineItems[0].error).toBe(true);
      expect(result.timelineItems[0].isStreaming).toBe(false);
    });
  });

  // Integration: full lifecycle — metadata, then events, then chunks, then finalize.
  it('full lifecycle: metadata → events → chunks → complete materializes correctly', () => {
    let turn = baseTurn();

    // Metadata arrives before any output.
    turn = applyAssistantMetadata(turn, { display_agent_name: 'DirectAgent' });
    expect(turn.displayAgentName).toBe('DirectAgent');
    expect(turn.timelineItems).toHaveLength(0); // no visible items yet

    // A tool_start event arrives.
    turn = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', { content: 'searching DB' }), NOW);
    expect(turn.timelineItems).toHaveLength(1);
    expect(turn.timelineItems[0].kind).toBe('stream_event');
    expect(turn.assistantMessageId).toBeNull(); // still no assistant row

    // A node_update event arrives.
    turn = applyAssistantStreamEvent(turn, rawStreamEvent('node_update', {
      metadata: { node_name: 'coder' },
    }), NOW + 50);
    expect(turn.timelineItems).toHaveLength(2);

    // First text chunk materialises the assistant row.
    turn = applyAssistantChunk(turn, 'Here is the answer', NOW + 100);
    expect(turn.timelineItems).toHaveLength(3);
    expect(turn.timelineItems[0].kind).toBe('stream_event');
    expect(turn.timelineItems[1].kind).toBe('stream_event');
    expect(turn.timelineItems[2].kind).toBe('assistant_message');
    expect(turn.assistantTimestamp).toBe(NOW + 100);
    expect(turn.timelineItems[2].displayAgentName).toBe('DirectAgent');

    // Finalize as completed.
    const result = finalizeLiveAssistantTurn(turn, { status: 'completed', now: NOW + 200 });
    expect(result).not.toBeNull();
    expect(result.content).toBe('Here is the answer');
    expect(result.isStreaming).toBe(false);
    expect(result.displayAgentName).toBe('DirectAgent');
    expect(result.timelineItems).toHaveLength(3);
    expect(result.timelineItems[2].isStreaming).toBe(false);
    expect(result.timelineItems[2].content).toBe('Here is the answer');
  });

  it('full lifecycle: empty turn → cancelled returns null', () => {
    let turn = baseTurn();
    turn = applyAssistantMetadata(turn, { display_agent_name: 'Agent' });
    const result = finalizeLiveAssistantTurn(turn, { status: 'cancelled', now: NOW });
    // Even though metadata was applied, there's no visible output.
    expect(result).toBeNull();
  });

  it('finalize sets isStreaming=false on all assistant_message timeline items', () => {
    const turn = baseTurn();
    const withStream = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', { content: 'x' }), NOW);
    const withChunk = applyAssistantChunk(withStream, 'Text', NOW);
    const result = finalizeLiveAssistantTurn(withChunk, { status: 'completed', now: NOW });

    const assistantItem = result.timelineItems.find((i) => i.kind === 'assistant_message');
    expect(assistantItem.isStreaming).toBe(false);
  });
});
