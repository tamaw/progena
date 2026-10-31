import { describe, it, expect, vi } from 'vitest';
import {
  createLiveAssistantTurn,
  applyAssistantMetadata,
  applyAssistantStreamEvent,
  applySubagentEvent,
  applyAsyncSubagentEvent,
  applyA2ACallEvent,
  applyAssistantChunk,
  buildDefaultAssistantTimeline,
  finalizeLiveAssistantTurn,
  toCommittedAssistantMessage,
  normalizeTaskSummary,
} from '@frontend/features/chat/models/liveAssistantTurnModel';

// ─── Helpers ──────────────────────────────────────────────────────────────

const NOW = 1_700_000_000_000;
const STREAM_ID = 'stream_001';

function baseTurn() {
  return createLiveAssistantTurn({ streamId: STREAM_ID, startedAt: NOW - 1000 });
}

/**
 * Build a minimal stream_event payload shaped like what the SSE layer emits
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

  it('keeps native summary callback activity out of assistant timeline content', () => {
    const started = applyAssistantStreamEvent(baseTurn(), rawStreamEvent('conversation_summary_started', {
      metadata: {
        summary_id: 'callback-1',
        scope: { kind: 'conversation', id: 'thread-1' },
      },
    }), NOW);
    const finished = applyAssistantStreamEvent(started, rawStreamEvent('conversation_summary_finished', {
      metadata: {
        summary_id: 'callback-1',
        scope: { kind: 'conversation', id: 'thread-1' },
      },
    }), NOW);

    expect(started.summaryActivities).toEqual([
      { id: 'callback-1', scope: { kind: 'conversation', id: 'thread-1' } },
    ]);
    expect(started.timelineItems).toEqual([]);
    expect(finished.summaryActivities).toEqual([]);
  });

  it('retains terminal latest-call context usage without a visual timeline row', () => {
    const turn = applyAssistantChunk(baseTurn(), 'Done', NOW);
    const next = applyAssistantStreamEvent(turn, rawStreamEvent('usage_update', {
      metadata: {
        output_tokens: null,
        final_call_output_tokens: null,
        context_usage: {
          input_tokens: 120,
          output_tokens: 30,
          total_tokens: 150,
          profile_name: 'general',
          provider_id: 'provider-a',
          deployment: 'gpt-a',
        },
      },
    }), NOW);

    expect(next.timelineItems).toHaveLength(1);
    expect(next.tokenCount).toBeNull();
    expect(next.finalCallTokenCount).toBeNull();
    expect(next.contextUsage).toMatchObject({ totalTokens: 150, profileName: 'general' });
    expect(finalizeLiveAssistantTurn(next, { status: 'completed', now: NOW }).contextUsage)
      .toMatchObject({ totalTokens: 150, providerId: 'provider-a' });
  });

  it('uses terminal provider output totals once instead of transport chunk counts', () => {
    const chunks = applyAssistantChunk(applyAssistantChunk(baseTurn(), 'First ', NOW), 'second', NOW + 1);
    const next = applyAssistantStreamEvent(chunks, rawStreamEvent('usage_update', {
      metadata: {
        output_tokens: 18,
        final_call_output_tokens: 7,
      },
    }), NOW + 2);

    expect(chunks.tokenCount).toBeNull();
    expect(next.tokenCount).toBe(18);
    expect(next.finalCallTokenCount).toBe(7);
    expect(next.timelineItems[0]).toMatchObject({ tokenCount: 18, finalCallTokenCount: 7 });
  });

  it('keeps provider interruption authoritative when the transport completes', () => {
    const live = applyAssistantChunk(baseTurn(), 'Partial answer', NOW);
    const withTerminalUsage = applyAssistantStreamEvent(live, rawStreamEvent('usage_update', {
      metadata: {
        output_tokens: 0,
        final_call_output_tokens: 0,
        response_status: 'interrupted',
      },
    }), NOW + 1);
    const committed = finalizeLiveAssistantTurn(withTerminalUsage, {
      status: 'completed',
      now: NOW + 2,
    });

    expect(committed).toMatchObject({
      content: 'Partial answer',
      tokenCount: 0,
      finalCallTokenCount: 0,
      usageProvenance: 'provider',
      responseStatus: 'interrupted',
      responseTimestamp: null,
    });
    expect(committed.timelineItems[0]).toMatchObject({
      tokenCount: 0,
      finalCallTokenCount: 0,
      usageProvenance: 'provider',
      responseStatus: 'interrupted',
    });
  });

  it('does not turn an explicit unknown provider status into completion', () => {
    const live = applyAssistantStreamEvent(
      applyAssistantChunk(baseTurn(), 'Answer', NOW),
      rawStreamEvent('usage_update', { metadata: { response_status: 'unknown' } }),
      NOW + 1,
    );
    const committed = finalizeLiveAssistantTurn(live, { status: 'completed', now: NOW + 2 });

    expect(committed).toMatchObject({
      responseStatus: 'unknown',
      responseTimestamp: null,
    });
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
  it('handles reasoning_delta: accumulates text and token count in one visible timeline row', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('reasoning_delta', {
      content: 'thinking step 1',
      metadata: { reasoning_tokens: 5 },
    });
    const next = applyAssistantStreamEvent(turn, evt, NOW);
    expect(next.reasoningText).toBe('thinking step 1');
    expect(next.reasoningTokenCount).toBe(5);
    expect(next.timelineItems).toHaveLength(1);
    expect(next.timelineItems[0].kind).toBe('stream_event');
    expect(next.timelineItems[0].event.eventType).toBe('reasoning_delta');
    expect(next.timelineItems[0].event.content).toBe('thinking step 1');
  });

  it('reasoning_delta: accumulates multiple fragments in one stable visible reasoning row', () => {
    const turn = baseTurn();
    const first = applyAssistantStreamEvent(turn, rawStreamEvent('reasoning_delta', {
      content: 'thinking step 1',
      metadata: { reasoning_tokens: 5 },
    }), NOW);
    const firstReasoningItem = first.timelineItems[0];
    const second = applyAssistantStreamEvent(first, rawStreamEvent('reasoning_delta', {
      content: ' and step 2',
      metadata: { reasoning_tokens: 7 },
    }), NOW);

    expect(second.reasoningText).toBe('thinking step 1 and step 2');
    expect(second.reasoningTokenCount).toBe(7);
    expect(second.timelineItems).toHaveLength(1);
    expect(second.timelineItems[0].id).toBe(firstReasoningItem.id);
    expect(second.timelineItems[0].event.eventType).toBe('reasoning_delta');
    expect(second.timelineItems[0].event.content).toBe('thinking step 1 and step 2');
  });

  it('reasoning_summary before a later delta keeps one summary row and ignores the delta', () => {
    const turn = baseTurn();
    const summary = applyAssistantStreamEvent(turn, rawStreamEvent('reasoning_summary', {
      content: 'final reasoning',
      metadata: { reasoning_tokens: 10 },
    }), NOW);
    const summaryRow = summary.timelineItems[0];
    const afterDelta = applyAssistantStreamEvent(summary, rawStreamEvent('reasoning_delta', {
      content: 'late fragment that must be ignored',
      metadata: { reasoning_tokens: 11 },
    }), NOW);

    expect(afterDelta.timelineItems).toHaveLength(1);
    expect(afterDelta.timelineItems[0].id).toBe(summaryRow.id);
    expect(afterDelta.timelineItems[0].event.eventType).toBe('reasoning_summary');
    expect(afterDelta.timelineItems[0].event.content).toBe('final reasoning');
    expect(afterDelta.reasoningText).toBe('final reasoning');
    expect(afterDelta.reasoningTokenCount).toBe(10);
    expect(afterDelta.streamEvents).toHaveLength(1);
    expect(afterDelta.streamEvents[0].eventType).toBe('reasoning_summary');
  });

  it('reasoning_delta then reasoning_summary: replaces one stable row with complete terminal summary', () => {
    const turn = baseTurn();
    const withDelta = applyAssistantStreamEvent(turn, rawStreamEvent('reasoning_delta', {
      content: 'partial thought',
      metadata: { reasoning_tokens: 5, summary: 'delta fragment' },
    }), NOW);
    expect(withDelta.timelineItems).toHaveLength(1);
    expect(withDelta.timelineItems[0].event.eventType).toBe('reasoning_delta');
    expect(withDelta.timelineItems[0].event.content).toBe('partial thought');
    const reasoningRowId = withDelta.timelineItems[0].id;

    const withTool = applyAssistantStreamEvent(
      withDelta,
      rawStreamEvent('tool_start', { content: 'searching' }),
      NOW,
    );
    const completeReasoning = 'complete reasoning trace';
    const summary = applyAssistantStreamEvent(withTool, rawStreamEvent('reasoning_summary', {
      content: completeReasoning,
      metadata: {
        reasoning_tokens: 42,
        summary: 'Final reasoning summary',
        detail: 'Provider detail',
      },
    }), NOW);

    const reasoningItems = summary.timelineItems.filter((item) => item.event.eventType === 'reasoning_summary');
    expect(reasoningItems).toHaveLength(1);
    expect(reasoningItems[0].id).toBe(reasoningRowId);
    expect(reasoningItems[0].event.content).toBe(completeReasoning);
    expect(reasoningItems[0].event.metadata).toEqual(expect.objectContaining({
      reasoning_tokens: 42,
      summary: 'Final reasoning summary',
      detail: 'Provider detail',
    }));
    expect(summary.reasoningText).toBe(completeReasoning);
    expect(summary.reasoningTokenCount).toBe(42);
  });
  it('places a terminal reasoning summary before later tool rows using the delta timestamp', () => {
    vi.useFakeTimers({ now: NOW });
    try {
      let turn = baseTurn();

      vi.setSystemTime(NOW + 100);
      turn = applyAssistantStreamEvent(turn, rawStreamEvent('reasoning_delta', {
        content: 'early thought',
        metadata: { reasoning_tokens: 3 },
      }));

      vi.setSystemTime(NOW + 200);
      turn = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', { content: 'searching' }));
      vi.setSystemTime(NOW + 300);
      turn = applyAssistantStreamEvent(turn, rawStreamEvent('tool_result', { content: 'result' }));

      vi.setSystemTime(NOW + 400);
      turn = applyAssistantStreamEvent(turn, rawStreamEvent('reasoning_summary', {
        content: 'complete reasoning trace',
        metadata: { reasoning_tokens: 42 },
      }));

      expect(turn.timelineItems.map((item) => item.event.eventType)).toEqual([
        'reasoning_summary',
        'tool_start',
        'tool_result',
      ]);
    } finally {
      vi.useRealTimers();
    }
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

  it('opens a new reasoning row for each model call and keeps them when the turn-wide summary arrives', () => {
    let turn = baseTurn();
    turn = applyAssistantStreamEvent(turn, rawStreamEvent('reasoning_delta', { content: '**Plan**\n\nfirst' }));
    turn = applyAssistantStreamEvent(turn, rawStreamEvent('reasoning_delta', { content: ' call' }));
    turn = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', { metadata: { tool_name: 'ls', tool_call_id: 'a' } }));
    turn = applyAssistantStreamEvent(turn, rawStreamEvent('reasoning_delta', { content: '**Read**\n\nsecond call' }));
    // The terminal summary repeats everything already streamed.
    turn = applyAssistantStreamEvent(turn, rawStreamEvent('reasoning_summary', {
      content: '**Plan**\n\nfirst call**Read**\n\nsecond call',
      metadata: { reasoning_tokens: 30 },
    }));

    const reasoningRows = turn.timelineItems
      .filter((item) => item.kind === 'stream_event' && item.event.eventType.startsWith('reasoning'))
      .map((item) => item.event.content);
    expect(reasoningRows).toEqual(['**Plan**\n\nfirst call', '**Read**\n\nsecond call']);
    expect(turn.timelineItems.map((item) => item.event.eventType)).toEqual(['reasoning_delta', 'tool_start', 'reasoning_delta']);
  });

  it('moves text streamed just before a tool call off the answer and onto that call as commentary', () => {
    let turn = baseTurn();
    turn = applyAssistantChunk(turn, 'I will check the repo first.', NOW);
    turn = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', { metadata: { tool_name: 'ls', tool_call_id: 'a' } }));
    turn = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', { metadata: { tool_name: 'ls', tool_call_id: 'b' } }));
    turn = applyAssistantChunk(turn, 'It looks fine.', NOW);

    const tools = turn.timelineItems.filter((item) => item.event?.eventType === 'tool_start');
    // Parallel calls: only the first carries the narration.
    expect(tools.map((item) => item.event.metadata.commentary)).toEqual(['I will check the repo first.', undefined]);
    expect(turn.content).toBe('It looks fine.');
    expect(turn.timelineItems.find((item) => item.kind === 'assistant_message').content).toBe('It looks fine.');
  });

  // Subagent execution events (nested `task` tool delegation) get their own
  // grouped card while the task's outer lifecycle remains a flat tool row.
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
  it('normalizes bounded task summaries on live subagent cards', () => {
    expect(normalizeTaskSummary('  Read   the\n README.md\tand summarize its purpose  '))
      .toBe('Read the README.md and summarize its purpose');
    expect(normalizeTaskSummary('x'.repeat(200))).toBe(`${'x'.repeat(139)}…`);
    expect(normalizeTaskSummary('   \n\t ')).toBe('');
  });

  it('carries task_summary from start and preserves it through the final result', () => {
    const turn = baseTurn();
    const withStart = applyAssistantStreamEvent(turn, rawStreamEvent('subagent_start', {
      content: 'researcher',
      metadata: {
        subagent_name: 'researcher',
        tool_call_id: 'call_summary',
        task_summary: '  Read   the README.md and summarize its purpose  ',
      },
    }), NOW);
    const withResult = applyAssistantStreamEvent(withStart, rawStreamEvent('subagent_result', {
      content: 'researcher',
      metadata: {
        subagent_name: 'researcher',
        tool_call_id: 'call_summary',
        success: true,
        result_preview: 'Final answer',
      },
    }), NOW + 100);

    expect(withResult.timelineItems[0]).toMatchObject({
      kind: 'subagent',
      taskSummary: 'Read the README.md and summarize its purpose',
      status: 'success',
      resultPreview: 'Final answer',
    });
  });

  it('does not promote args_preview.description when task_summary is absent', () => {
    const next = applyAssistantStreamEvent(baseTurn(), rawStreamEvent('subagent_start', {
      metadata: {
        subagent_name: 'researcher',
        tool_call_id: 'call_no_summary',
        args_preview: { description: 'legacy description must stay hidden' },
      },
    }), NOW);
    expect(next.timelineItems[0].taskSummary).toBe('');
  });
  it('task lifecycle keeps outer tool rows alongside its grouped subagent card', () => {
    const turn = baseTurn();
    let next = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', {
      content: 'task',
      metadata: {
        tool_name: 'task',
        tool_call_id: 'call_1',
        args_preview: { subagent_type: 'researcher' },
      },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('subagent_start', {
      content: 'researcher',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('tool_result', {
      content: 'task',
      metadata: {
        tool_name: 'task',
        tool_call_id: 'call_1',
        result_preview: 'Found it',
        success: true,
      },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('subagent_result', {
      content: 'researcher',
      metadata: {
        subagent_name: 'researcher',
        tool_call_id: 'call_1',
        result_preview: 'Found it',
        success: true,
      },
    }), NOW);

    expect(next.streamEvents.map((event) => event.eventType)).toEqual([
      'tool_start', 'subagent_start', 'tool_result', 'subagent_result',
    ]);
    expect(next.timelineItems.map((item) => item.kind)).toEqual([
      'stream_event', 'subagent', 'stream_event',
    ]);
    expect(next.timelineItems.find((item) => item.kind === 'stream_event').event.metadata.tool_name).toBe('task');
    expect(next.timelineItems.find((item) => item.kind === 'subagent')).toMatchObject({
      toolCallId: 'call_1',
      subagentName: 'researcher',
      status: 'success',
      resultPreview: 'Found it',
    });
  });
  it('keeps identical adjacent subagent deltas as distinct persisted events while accumulating grouped text', () => {
    const turn = baseTurn();
    const delta = rawStreamEvent('subagent_message_delta', {
      content: 'same',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_duplicate' },
    });
    const first = applyAssistantStreamEvent(turn, delta, NOW);
    const next = applyAssistantStreamEvent(first, delta, NOW);

    expect(next.streamEvents).toHaveLength(2);
    expect(next.streamEvents.map((event) => event.content)).toEqual(['same', 'same']);
    expect(next.streamEvents[0].id).not.toBe(next.streamEvents[1].id);
    expect(next.timelineItems).toEqual([
      expect.objectContaining({
        kind: 'subagent',
        toolCallId: 'call_duplicate',
        entries: [{ kind: 'message', id: 'msg-0', text: 'samesame' }],
      }),
    ]);
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
    // A nested tool closes alpha's first message, making both entries settled
    // before a later alpha delta arrives.
    next = applyAssistantStreamEvent(next, rawStreamEvent('subagent_tool_start', {
      metadata: {
        tool_call_id: 'call_a',
        nested_tool_call_id: 'nested_1',
        nested_tool_name: 'read_file',
      },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('subagent_tool_result', {
      metadata: {
        tool_call_id: 'call_a',
        nested_tool_call_id: 'nested_1',
        nested_tool_name: 'read_file',
        result_preview: 'read complete',
      },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('subagent_message_delta', {
      content: 'hello',
      metadata: { tool_call_id: 'call_b' },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('subagent_message_delta', {
      content: 'Alpha concludes: ',
      metadata: { tool_call_id: 'call_a' },
    }), NOW);
    const alphaBeforeFinalDelta = next.timelineItems.find((item) => item.toolCallId === 'call_a');
    const betaBeforeFinalDelta = next.timelineItems.find((item) => item.toolCallId === 'call_b');
    const settledAlphaMessage = alphaBeforeFinalDelta.entries[0];
    const settledAlphaTool = alphaBeforeFinalDelta.entries[1];
    const alphaTrailingMessage = alphaBeforeFinalDelta.entries[2];
    const betaMessage = betaBeforeFinalDelta.entries[0];
    next = applyAssistantStreamEvent(next, rawStreamEvent('subagent_message_delta', {
      content: 'hi',
      metadata: { tool_call_id: 'call_a' },
    }), NOW);

    expect(next.timelineItems).toHaveLength(2);
    const alphaItem = next.timelineItems.find((item) => item.toolCallId === 'call_a');
    const betaItem = next.timelineItems.find((item) => item.toolCallId === 'call_b');
    expect(alphaItem).not.toBe(alphaBeforeFinalDelta);
    expect(alphaItem.entries[0]).toBe(settledAlphaMessage);
    expect(alphaItem.entries[1]).toBe(settledAlphaTool);
    expect(alphaItem.entries[2]).not.toBe(alphaTrailingMessage);
    expect(alphaItem.entries[2].text).toBe('Alpha concludes: hi');
    expect(betaItem).toBe(betaBeforeFinalDelta);
    expect(betaItem.entries[0]).toBe(betaMessage);
    expect(alphaItem.entries.map((entry) => entry.kind)).toEqual(['message', 'tool', 'message']);
    expect(alphaItem.entries[0].text).toBe('Alpha says: ');
    expect(betaItem.entries).toEqual([{ kind: 'message', id: 'msg-0', text: 'Beta says: hello' }]);
  });

  // A2A tools get their own grouped card, keyed off their generic lifecycle
  // events rather than a dedicated event-type prefix. Dispatch and return
  // remain ordinary tool rows in addition to updating the card.
  it('a2a_send tool_start creates an a2a_call timeline item AND a plain stream_event row', () => {
    const turn = baseTurn();
    const evt = rawStreamEvent('tool_start', {
      content: 'a2a_send',
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', args_preview: { agent: 'example_agent' } },
    });
    const next = applyAssistantStreamEvent(turn, evt, NOW);

    expect(next.timelineItems).toHaveLength(2);
    // The plain dispatch row has to read before the card it explains -- the
    // call itself comes first, same as any other tool_start.
    expect(next.timelineItems.map((item) => item.kind)).toEqual(['stream_event', 'a2a_call']);
    const a2aItem = next.timelineItems.find((item) => item.kind === 'a2a_call');
    expect(a2aItem.id).toBe('a2a-call_1');
    expect(a2aItem.agentName).toBe('example_agent');
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
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_a', args_preview: { agent: 'alpha_agent' } },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('tool_start', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_b', args_preview: { agent: 'beta_agent' } },
    }), NOW);
    // Scrambled interleave: beta's progress arrives between alpha's.
    next = applyAssistantStreamEvent(next, rawStreamEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_b', state: 'working', message_text: 'beta step 1' },
    }), NOW);
    next = applyAssistantStreamEvent(next, rawStreamEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_a', state: 'working', message_text: 'alpha step 1' },
    }), NOW);
    const alphaBeforeSecondProgress = next.timelineItems.find((item) => item.kind === 'a2a_call' && item.toolCallId === 'call_a');
    const betaBeforeSecondProgress = next.timelineItems.find((item) => item.kind === 'a2a_call' && item.toolCallId === 'call_b');
    const alphaFirstMessage = alphaBeforeSecondProgress.messages[0];
    const betaFirstMessage = betaBeforeSecondProgress.messages[0];
    next = applyAssistantStreamEvent(next, rawStreamEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_a', state: 'working', message_text: 'alpha step 2' },
    }), NOW);
    const alphaAfterSecondProgress = next.timelineItems.find((item) => item.kind === 'a2a_call' && item.toolCallId === 'call_a');
    const betaAfterSecondProgress = next.timelineItems.find((item) => item.kind === 'a2a_call' && item.toolCallId === 'call_b');
    expect(alphaAfterSecondProgress).not.toBe(alphaBeforeSecondProgress);
    expect(alphaAfterSecondProgress.messages[0]).toBe(alphaFirstMessage);
    expect(alphaAfterSecondProgress.messages.map((message) => message.text)).toEqual(['alpha step 1', 'alpha step 2']);
    expect(betaAfterSecondProgress).toBe(betaBeforeSecondProgress);
    expect(betaAfterSecondProgress.messages[0]).toBe(betaFirstMessage);
    next = applyAssistantStreamEvent(next, rawStreamEvent('tool_result', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_a', duration_ms: 5, result_preview: 'alpha done' },
    }), NOW);

    // 5 items: an a2a_call card per concurrent call (2), plus a plain
    // stream_event row for each A2A tool_start/tool_result (3: two starts
    // plus alpha's result). Progress updates their cards in place.
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
    expect(alphaItem.messages.map((m) => m.text)).toEqual(['alpha step 1', 'alpha step 2']);
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
  // fallback for the reload path (see the matching A2A lifecycle fallback
  // test below).
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
      entries: [],
    });
  });

  it('defensively creates a minimal row for an out-of-order non-start event (no prior subagent_start seen)', () => {
    const items = applySubagentEvent([], subagentEvent('subagent_message_delta', {
      content: 'late',
      metadata: { tool_call_id: 'call_1' },
    }));
    expect(items).toHaveLength(1);
    expect(items[0].toolCallId).toBe('call_1');
    expect(items[0].entries).toEqual([{ kind: 'message', id: 'msg-0', text: 'late' }]);
  });

  it('accumulates subagent_message_delta text into a single trailing message entry across multiple calls', () => {
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
    expect(items[0].entries).toEqual([{ kind: 'message', id: 'msg-0', text: 'Hello world' }]);
  });

  it('a nested tool call between two bursts of text splits them into separate message entries', () => {
    let items = applySubagentEvent([], subagentEvent('subagent_start', {
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    }));
    items = applySubagentEvent(items, subagentEvent('subagent_message_delta', {
      content: 'Let me check that file.',
      metadata: { tool_call_id: 'call_1' },
    }));
    items = applySubagentEvent(items, subagentEvent('subagent_tool_start', {
      metadata: { tool_call_id: 'call_1', nested_tool_call_id: 'nested_1', nested_tool_name: 'read_file' },
    }));
    items = applySubagentEvent(items, subagentEvent('subagent_message_delta', {
      content: 'Here is the summary.',
      metadata: { tool_call_id: 'call_1' },
    }));
    expect(items[0].entries.map((entry) => entry.kind)).toEqual(['message', 'tool', 'message']);
    expect(items[0].entries[0].text).toBe('Let me check that file.');
    expect(items[0].entries[2].text).toBe('Here is the summary.');
  });

  it('find-or-creates nested tool entries keyed by nested_tool_call_id, in place', () => {
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
    expect(items[0].entries).toHaveLength(1);
    expect(items[0].entries[0]).toMatchObject({
      kind: 'tool',
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
    // Same entry updated in place, not duplicated.
    expect(items[0].entries).toHaveLength(1);
    expect(items[0].entries[0]).toMatchObject({
      kind: 'tool',
      id: 'nested_1',
      status: 'success',
      durationMs: 4.5,
      resultPreview: '# Title',
    });
  });

  it('subagent_tool_error marks the matching tool entry as errored', () => {
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
    expect(items[0].entries[0]).toMatchObject({ status: 'error', error: 'boom', durationMs: 2 });
  });

  it('subagent_tool_result marks a returned execute failure as errored', () => {
    let items = applySubagentEvent([], subagentEvent('subagent_tool_start', {
      metadata: { tool_call_id: 'call_1', nested_tool_call_id: 'nested_1', nested_tool_name: 'execute' },
    }));
    items = applySubagentEvent(items, subagentEvent('subagent_tool_result', {
      metadata: {
        tool_call_id: 'call_1',
        nested_tool_call_id: 'nested_1',
        nested_tool_name: 'execute',
        success: false,
        result_preview: 'long output cut before the footer…',
      },
    }));
    expect(items[0].entries[0]).toMatchObject({ status: 'error' });
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

  it('ignores stale non-terminal subagent events after the card has completed', () => {
    let items = applySubagentEvent([], subagentEvent('subagent_start', {
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    }));
    items = applySubagentEvent(items, subagentEvent('subagent_result', {
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1', success: true, result_preview: 'done' },
    }));
    const settled = items[0];

    items = applySubagentEvent(items, subagentEvent('subagent_start', {
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    }));
    items = applySubagentEvent(items, subagentEvent('subagent_message_delta', {
      content: 'late',
      metadata: { tool_call_id: 'call_1' },
    }));

    expect(items[0]).toBe(settled);
    expect(items[0]).toMatchObject({ status: 'success', resultPreview: 'done', entries: [] });
  });

  it('keeps the first terminal subagent result when a contradictory terminal update arrives later', () => {
    let items = applySubagentEvent([], subagentEvent('subagent_start', {
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    }));
    items = applySubagentEvent(items, subagentEvent('subagent_result', {
      metadata: { tool_call_id: 'call_1', success: false, error: 'subagent crashed' },
    }));
    const settled = items[0];

    items = applySubagentEvent(items, subagentEvent('subagent_result', {
      metadata: {
        subagent_name: 'researcher',
        tool_call_id: 'call_1',
        success: true,
        result_preview: 'late success must be ignored',
      },
    }));

    expect(items[0]).toBe(settled);
    expect(items[0]).toMatchObject({ status: 'error', error: 'subagent crashed', resultPreview: '' });
  });
});

// ─── applyAsyncSubagentEvent ─────────────────────────────────────────────

describe('applyAsyncSubagentEvent', () => {
  function asyncEvent(type, metadata = {}) {
    return {
      id: `evt-${type}`,
      eventType: type,
      content: 'researcher',
      timestamp: NOW,
      metadata,
    };
  }

  it.each(['success', 'error'])('retains an observed %s result when its start was not streamed', (status) => {
    const items = applyAsyncSubagentEvent([], asyncEvent('async_subagent_result', {
      task_id: 'existing-task',
      status,
      result_preview: 'Existing task result',
      error: 'Existing task failure',
    }));
    expect(items[0]).toMatchObject({
      kind: 'async_subagent',
      status,
      resultPreview: 'Existing task result',
      error: status === 'error' ? 'Existing task failure' : null,
      startedAt: null,
      finishedAt: NOW,
    });
  });

  it('keeps one task card through pause, resume, and final result while concurrent work stays separate', () => {
    let items = applyAsyncSubagentEvent([], asyncEvent('async_subagent_start', {
      task_id: 'task-a', tool_call_id: 'start-a', subagent_name: 'alpha', status: 'running',
    }));
    items = applyAsyncSubagentEvent(items, asyncEvent('async_subagent_start', {
      task_id: 'task-b', tool_call_id: 'start-b', subagent_name: 'beta', status: 'running',
    }));
    items = applyAsyncSubagentEvent(items, asyncEvent('async_subagent_pause', {
      task_id: 'task-a', tool_call_id: 'check-a', subagent_name: 'alpha', status: 'interrupted',
    }));
    expect(items.find((item) => item.taskId === 'task-a')).toMatchObject({
      status: 'interrupted',
      finishedAt: null,
    });
    items = applyAsyncSubagentEvent(items, asyncEvent('async_subagent_update', {
      task_id: 'task-a', tool_call_id: 'resume-a', subagent_name: 'alpha', status: 'running',
    }));
    expect(items.find((item) => item.taskId === 'task-a')).toMatchObject({
      status: 'running',
      finishedAt: null,
    });
    items = applyAsyncSubagentEvent(items, asyncEvent('async_subagent_result', {
      task_id: 'task-a', tool_call_id: 'check-a', subagent_name: 'alpha', status: 'success',
      result_preview: 'Completed result',
    }));

    expect(items).toHaveLength(2);
    expect(items.find((item) => item.taskId === 'task-a')).toMatchObject({
      id: 'async-subagent-task-a',
      toolCallId: 'start-a',
      status: 'success',
      resultPreview: 'Completed result',
    });
    expect(items.find((item) => item.taskId === 'task-b')).toMatchObject({
      status: 'running',
      subagentName: 'beta',
    });
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
      metadata: { tool_name: 'a2a_send' },
    }))).toBeNull();
  });

  it('creates a new card on a2a_send tool_start with running status', () => {
    const items = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', args_preview: { agent: 'example_agent' } },
    }));
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      kind: 'a2a_call',
      id: 'a2a-call_1',
      toolCallId: 'call_1',
      agentName: 'example_agent',
      status: 'running',
      messages: [],
    });
  });

  it('does not seed agentName when args_preview.agent is absent', () => {
    const items = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1' },
    }));
    expect(items[0].agentName).toBe('');
  });

  it('does not use args_preview.agent_name as an alias', () => {
    const items = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', args_preview: { agent_name: 'example_agent' } },
    }));
    expect(items[0].agentName).toBe('');
  });

  it('ignores a2a_call_progress with no prior tool_start (unlike subagent, does not defensively create)', () => {
    const items = applyA2ACallEvent([], a2aEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_1', state: 'working', message_text: 'late' },
    }));
    expect(items).toBeNull();
  });

  it('ignores tool_result for an A2A tool_call_id with no prior card', () => {
    const items = applyA2ACallEvent([], a2aEvent('tool_result', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', duration_ms: 5 },
    }));
    expect(items).toBeNull();
  });

  it('appends a2a_call_progress messages without changing args.agent display name', () => {
    let items = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', args_preview: { agent: 'example_agent' } },
    }));
    items = applyA2ACallEvent(items, a2aEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_1', agent_name: 'example_agent', state: 'working', message_text: 'Step 1/3: prep...' },
    }));
    items = applyA2ACallEvent(items, a2aEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_1', agent_name: 'example_agent', state: 'working', message_text: 'Step 2/3: build...' },
    }));
    expect(items[0].agentName).toBe('example_agent');
    expect(items[0].messages.map((m) => m.text)).toEqual(['Step 1/3: prep...', 'Step 2/3: build...']);
    expect(items[0].messages.map((m) => m.state)).toEqual(['working', 'working']);
    // Still running -- only tool_result/tool_error finalize status.
    expect(items[0].status).toBe('running');
  });

  it('tool_result finalizes status/finishedAt/durationMs/resultPreview on success', () => {
    let items = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1' },
    }));
    items = applyA2ACallEvent(items, a2aEvent('tool_result', {
      timestamp: NOW + 500,
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', duration_ms: 187, result_preview: 'Hello!' },
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
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1' },
    }));
    items = applyA2ACallEvent(items, a2aEvent('tool_error', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', duration_ms: 12, error: 'agent unreachable' },
    }));
    expect(items[0].status).toBe('error');
    expect(items[0].error).toBe('agent unreachable');
  });

  it('never mutates the input array (immutable updates)', () => {
    const original = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1' },
    }));
    const snapshot = JSON.parse(JSON.stringify(original));
    applyA2ACallEvent(original, a2aEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_1', state: 'working', message_text: 'more' },
    }));
    expect(original).toEqual(snapshot);
  });

  it('ignores stale start/progress events after an A2A card has completed', () => {
    let items = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', args_preview: { agent: 'example_agent' } },
    }));
    items = applyA2ACallEvent(items, a2aEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_1', state: 'working', message_text: 'Step 1' },
    }));
    items = applyA2ACallEvent(items, a2aEvent('tool_result', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', result_preview: 'done' },
    }));
    const settled = items[0];

    items = applyA2ACallEvent(items, a2aEvent('tool_start', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1' },
    }));
    items = applyA2ACallEvent(items, a2aEvent('a2a_call_progress', {
      metadata: { tool_call_id: 'call_1', state: 'working', message_text: 'late step' },
    }));

    expect(items[0]).toBe(settled);
    expect(items[0].status).toBe('success');
    expect(items[0].messages.map((message) => message.text)).toEqual(['Step 1']);
  });

  it('keeps the first terminal A2A outcome when a later terminal error arrives', () => {
    let items = applyA2ACallEvent([], a2aEvent('tool_start', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', args_preview: { agent: 'example_agent' } },
    }));
    items = applyA2ACallEvent(items, a2aEvent('tool_result', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', result_preview: 'done' },
    }));
    const settled = items[0];

    items = applyA2ACallEvent(items, a2aEvent('tool_error', {
      metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', error: 'late failure' },
    }));

    expect(items[0]).toBe(settled);
    expect(items[0]).toMatchObject({ status: 'success', resultPreview: 'done', error: null });
  });
});

// ─── applyAssistantChunk ─────────────────────────────────────────────────

describe('applyAssistantChunk', () => {
  it('appends content without treating transport chunks as token counts', () => {
    const turn = baseTurn();
    const next = applyAssistantChunk(turn, 'Hello', NOW);
    expect(next.content).toBe('Hello');
    expect(next.tokenCount).toBeNull();
  });

  it('keeps usage unknown across multiple chunks', () => {
    const turn = baseTurn();
    const a = applyAssistantChunk(turn, 'Hello ', NOW);
    const b = applyAssistantChunk(a, 'World', NOW);
    expect(b.content).toBe('Hello World');
    expect(b.tokenCount).toBeNull();
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

  it('on first chunk: leaves displayAgentName empty when no metadata is set', () => {
    const turn = baseTurn();
    const next = applyAssistantChunk(turn, 'text', NOW);
    expect(next.timelineItems[0].displayAgentName).toBe('');
  });

  it('on first chunk: assistant_message uses displayAgentName when metadata was provided earlier', () => {
    const turn = baseTurn();
    const withMeta = applyAssistantMetadata(turn, { display_agent_name: 'CustomAgent' });
    const next = applyAssistantChunk(withMeta, 'text', NOW);
    expect(next.timelineItems[0].displayAgentName).toBe('CustomAgent');
  });

  it('on subsequent chunks: updates the existing assistant_message while usage remains unknown', () => {
    const turn = baseTurn();
    const first = applyAssistantChunk(turn, 'Hello ', NOW);
    const second = applyAssistantChunk(first, 'World', NOW);
    expect(second.timelineItems).toHaveLength(1);
    expect(second.timelineItems[0].content).toBe('Hello World');
    expect(second.timelineItems[0].tokenCount).toBeNull();
  });

  it('on subsequent chunks: preserves stream-event identity while replacing the assistant row immutably', () => {
    const turn = baseTurn();
    const withEvent = applyAssistantStreamEvent(turn, rawStreamEvent('tool_start', { content: 'searching' }), NOW);
    const first = applyAssistantChunk(withEvent, 'Answer', NOW + 100);
    const priorTimelineItems = first.timelineItems;
    const priorStreamEvent = priorTimelineItems.find((item) => item.kind === 'stream_event');
    const priorAssistant = priorTimelineItems.find((item) => item.kind === 'assistant_message');

    const second = applyAssistantChunk(first, ' more', NOW + 200);
    const nextStreamEvent = second.timelineItems.find((item) => item.kind === 'stream_event');
    const nextAssistant = second.timelineItems.find((item) => item.kind === 'assistant_message');

    expect(second.content).toBe('Answer more');
    expect(second.tokenCount).toBeNull();
    expect(second.timelineItems).not.toBe(priorTimelineItems);
    expect(nextStreamEvent).toBe(priorStreamEvent);
    expect(nextAssistant).not.toBe(priorAssistant);
    expect(nextAssistant.content).toBe('Answer more');
    expect(nextAssistant.tokenCount).toBeNull();

    expect(first.content).toBe('Answer');
    expect(first.tokenCount).toBeNull();
    expect(first.timelineItems).toBe(priorTimelineItems);
    expect(priorAssistant.content).toBe('Answer');
    expect(priorAssistant.tokenCount).toBeNull();
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
    expect(turn.tokenCount).toBeNull();
    expect(turn.assistantMessageId).toBeNull();
    expect(turn.timelineItems).toEqual(copy.timelineItems);
  });
});

// ─── buildDefaultAssistantTimeline ───────────────────────────────────────

describe('buildDefaultAssistantTimeline', () => {
  it('replays one async delegation card through approval pause, resume, and completion', () => {
    const items = buildDefaultAssistantTimeline({
      role: 'assistant',
      id: 'msg_async',
      timestamp: NOW + 1000,
      streamEvents: [
        {
          id: 'start',
          eventType: 'async_subagent_start',
          timestamp: NOW,
          content: 'researcher',
          metadata: { task_id: 'task-1', tool_call_id: 'call-start', status: 'running' },
        },
        {
          id: 'pause',
          eventType: 'async_subagent_pause',
          timestamp: NOW + 10,
          content: 'researcher',
          metadata: { task_id: 'task-1', tool_call_id: 'call-check', status: 'interrupted' },
        },
        {
          id: 'resume',
          eventType: 'async_subagent_update',
          timestamp: NOW + 20,
          content: 'researcher',
          metadata: { task_id: 'task-1', tool_call_id: 'call-resume', status: 'running' },
        },
        {
          id: 'result',
          eventType: 'async_subagent_result',
          timestamp: NOW + 30,
          content: 'researcher',
          metadata: {
            task_id: 'task-1',
            tool_call_id: 'call-check',
            status: 'success',
            result_preview: 'Completed result',
          },
        },
      ],
    });

    const cards = items.filter((item) => item.kind === 'async_subagent');
    expect(cards).toHaveLength(1);
    expect(cards[0]).toMatchObject({
      id: 'async-subagent-task-1',
      toolCallId: 'call-start',
      status: 'success',
      resultPreview: 'Completed result',
    });
  });
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

  it('leaves displayAgentName empty when it is missing', () => {
    const items = buildDefaultAssistantTimeline({ role: 'assistant', content: 'hi' });
    expect(items[0].displayAgentName).toBe('');
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
    });
    expect(subagentItems[0].entries.map((entry) => entry.kind)).toEqual(['tool', 'message']);
    expect(subagentItems[0].entries[0]).toMatchObject({ id: 'nested_1', toolName: 'read_file' });
    expect(subagentItems[0].entries[1]).toMatchObject({ text: 'Findings: done.' });

    // Spliced in once, at the earliest event's timestamp (1000) -- before the
    // assistant_message row (timestamped 5000), not once per flat event.
    expect(items.map((item) => item.kind)).toEqual(['subagent', 'assistant_message']);
    expect(subagentItems[0].timestamp).toBe(1000);
  });

  it('reconstructs a correlated subagent card when a delta arrives before subagent_start', () => {
    const message = {
      role: 'assistant',
      id: 'msg_1',
      content: 'All done.',
      timestamp: 5000,
      streamEvents: [
        {
          id: 'e1',
          eventType: 'subagent_message_delta',
          content: 'Recovered text',
          timestamp: 1000,
          metadata: { tool_call_id: 'call_late' },
        },
        {
          id: 'e2',
          eventType: 'subagent_start',
          timestamp: 1010,
          metadata: { subagent_name: 'researcher', tool_call_id: 'call_late' },
        },
        {
          id: 'e3',
          eventType: 'subagent_result',
          timestamp: 1020,
          metadata: {
            subagent_name: 'researcher',
            tool_call_id: 'call_late',
            success: true,
            result_preview: 'Recovered result',
          },
        },
      ],
    };

    const items = buildDefaultAssistantTimeline(message);
    const subagentItems = items.filter((item) => item.kind === 'subagent');

    expect(subagentItems).toHaveLength(1);
    expect(subagentItems[0]).toMatchObject({
      id: 'subagent-call_late',
      toolCallId: 'call_late',
      subagentName: 'researcher',
      status: 'success',
      resultPreview: 'Recovered result',
      entries: [{ kind: 'message', id: 'msg-0', text: 'Recovered text' }],
    });
    expect(items.map((item) => item.kind)).toEqual(['subagent', 'assistant_message']);
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
    expect(subagentItems.find((item) => item.toolCallId === 'call_a').entries).toEqual([{ kind: 'message', id: 'msg-0', text: 'A' }]);
    expect(subagentItems.find((item) => item.toolCallId === 'call_b').entries).toEqual([{ kind: 'message', id: 'msg-0', text: 'B' }]);
  });

  // Flat persisted A2A lifecycle/a2a_call_progress events reconstruct into a
  // grouped card while lifecycle events retain their own stream rows.
  it('reconstructs flat A2A tool events into a grouped card plus lifecycle rows', () => {
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
          metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', args_preview: { agent: 'example_agent' } },
        },
        {
          id: 'e2',
          eventType: 'a2a_call_progress',
          timestamp: 1100,
          metadata: { tool_call_id: 'call_1', agent_name: 'example_agent', state: 'working', message_text: 'Step 1/3: prep...' },
        },
        {
          id: 'e3',
          eventType: 'a2a_call_progress',
          timestamp: 1150,
          metadata: { tool_call_id: 'call_1', agent_name: 'example_agent', state: 'working', message_text: 'Step 2/3: build...' },
        },
        {
          id: 'e4',
          eventType: 'tool_result',
          timestamp: 1200,
          metadata: { tool_name: 'a2a_send', tool_call_id: 'call_1', duration_ms: 187, result_preview: 'Hello!' },
        },
      ],
    };
    const items = buildDefaultAssistantTimeline(message);

    const a2aItems = items.filter((item) => item.kind === 'a2a_call');
    expect(a2aItems).toHaveLength(1);
    expect(a2aItems[0]).toMatchObject({
      id: 'a2a-call_1',
      toolCallId: 'call_1',
      agentName: 'example_agent',
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
        { id: 'e1', eventType: 'tool_start', timestamp: 1000, metadata: { tool_name: 'a2a_send', tool_call_id: 'call_a', args_preview: { agent: 'alpha_agent' } } },
        { id: 'e2', eventType: 'tool_start', timestamp: 1010, metadata: { tool_name: 'a2a_send', tool_call_id: 'call_b', args_preview: { agent: 'beta_agent' } } },
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

  // A malformed A2A lifecycle event cannot form a group, so it must fall
  // through to a generic stream row instead of being silently dropped.
  it('falls through to a flat stream_event row for A2A events with no tool_call_id', () => {
    const message = {
      role: 'assistant',
      id: 'msg_1',
      content: 'done',
      timestamp: 5000,
      streamEvents: [
        { id: 'e1', eventType: 'tool_start', timestamp: 1000, metadata: { tool_name: 'a2a_send', args_preview: { agent: 'example_agent' } } },
        { id: 'e2', eventType: 'a2a_call_progress', timestamp: 1010, metadata: { state: 'working', message_text: 'orphaned' } },
      ],
    };
    const items = buildDefaultAssistantTimeline(message);
    expect(items.filter((item) => item.kind === 'a2a_call')).toHaveLength(0);
    expect(items.find((item) => item.kind === 'stream_event' && item.id === 'e1')).toBeDefined();
    expect(items.find((item) => item.kind === 'stream_event' && item.id === 'e2')).toBeDefined();
  });

  it('skips hidden pending_response node updates when rebuilding persisted timelines', () => {
    const items = buildDefaultAssistantTimeline({
      role: 'assistant',
      id: 'msg_hidden',
      content: 'done',
      timestamp: 5000,
      streamEvents: [
        { id: 'hidden-node', eventType: 'node_update', timestamp: 1000, metadata: { node_name: 'pending_response' } },
      ],
    });

    expect(items.map((item) => item.kind)).toEqual(['assistant_message']);
  });

  // Malformed subagent events cannot form a grouped card and are dropped
  // rather than exposed as generic stream-event rows.
  it('drops a subagent_start with no tool_call_id instead of rendering a card or flat stream_event row', () => {
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
    expect(items.filter((item) => item.kind === 'stream_event')).toHaveLength(0);
  });
});

// ─── toCommittedAssistantMessage ─────────────────────────────────────────

describe('toCommittedAssistantMessage', () => {

  it('marks a streaming turn as working until answer text arrives, and again when tools follow text', () => {
    const toolStart = (id) => ({ event_type: 'tool_start', metadata: { tool_name: 'ls', tool_call_id: id } });
    let turn = createLiveAssistantTurn({ streamId: STREAM_ID, startedAt: NOW });
    turn = applyAssistantStreamEvent(turn, toolStart('a'));
    expect(toCommittedAssistantMessage(turn).workActive).toBe(true);

    turn = applyAssistantChunk(turn, 'Looking', NOW);
    expect(toCommittedAssistantMessage(turn).workActive).toBe(false);

    // Providers deliver the reasoning summary at the end, and graph steps only
    // show in the expanded detail; neither may reopen the preview.
    turn = applyAssistantStreamEvent(turn, { event_type: 'reasoning_summary', content: 'why' });
    turn = applyAssistantStreamEvent(turn, { event_type: 'node_update', metadata: { node_name: 'model' } });
    expect(toCommittedAssistantMessage(turn).workActive).toBe(false);

    turn = applyAssistantStreamEvent(turn, toolStart('b'));
    expect(toCommittedAssistantMessage(turn).workActive).toBe(true);

    expect(toCommittedAssistantMessage({ ...turn, isStreaming: false }).workActive).toBe(false);
  });

  it('falls back streamId-based id and startedAt timestamp when assistantMessageId/timestamp null', () => {
    const turn = baseTurn();
    const msg = toCommittedAssistantMessage(turn);
    expect(msg.id).toBe(`${STREAM_ID}-assistant`);
    expect(msg.timestamp).toBe(NOW - 1000);
  });

  it('preserves live ownership identity on the terminal committed copy', () => {
    const streamingTurn = applyAssistantChunk(baseTurn(), 'Terminal answer', NOW);
    const committed = finalizeLiveAssistantTurn(streamingTurn, {
      status: 'completed',
      now: NOW + 100,
    });

    expect(committed).toMatchObject({
      id: streamingTurn.assistantMessageId,
      assistantMessageId: streamingTurn.assistantMessageId,
      streamId: STREAM_ID,
      startedAt: NOW - 1000,
      isStreaming: false,
    });
  });
});

// ─── finalizeLiveAssistantTurn ───────────────────────────────────────────

describe('finalizeLiveAssistantTurn', () => {
  it('returns null when turn is null/undefined', () => {
    expect(finalizeLiveAssistantTurn(null, { status: 'completed', now: NOW })).toBeNull();
    expect(finalizeLiveAssistantTurn(undefined, { status: 'completed', now: NOW })).toBeNull();
  });
  it.each([
    ['completed', 'completed'],
    ['error', 'failed'],
    ['cancelled', 'cancelled'],
    ['interrupted', 'interrupted'],
    ['unrecognised-terminal-event', 'unknown'],
  ])('records responseStatus from the terminal lifecycle event: %s', (status, responseStatus) => {
    const result = finalizeLiveAssistantTurn(applyAssistantChunk(baseTurn(), 'Answer', NOW), { status, now: NOW });
    expect(result.responseStatus).toBe(responseStatus);
  });

  it('materializes a failed provider usage update after normal transport completion', () => {
    const turn = applyAssistantStreamEvent(baseTurn(), rawStreamEvent('usage_update', {
      metadata: {
        output_tokens: 0,
        final_call_output_tokens: 0,
        response_status: 'failed',
        context_usage: {
          input_tokens: 120,
          output_tokens: 0,
          total_tokens: 120,
          profile_name: 'general',
          provider_id: 'provider-a',
        },
      },
    }), NOW);

    const result = finalizeLiveAssistantTurn(turn, { status: 'completed', now: NOW + 1 });

    expect(result).toMatchObject({
      content: 'Error: Response failed.',
      error: true,
      responseStatus: 'failed',
      tokenCount: 0,
      finalCallTokenCount: 0,
      usageProvenance: 'provider',
      responseTimestamp: null,
      contextUsage: { totalTokens: 120, providerId: 'provider-a' },
    });
    expect(result.timelineItems).toHaveLength(1);
    expect(result.timelineItems[0]).toMatchObject({
      kind: 'assistant_message',
      content: 'Error: Response failed.',
      error: true,
      responseStatus: 'failed',
      tokenCount: 0,
      finalCallTokenCount: 0,
      usageProvenance: 'provider',
    });
  });

  it('surfaces a provider failure after hidden debug activity without dropping the event', () => {
    let turn = applyAssistantStreamEvent(
      baseTurn(),
      rawStreamEvent('node_update', { metadata: { node_name: 'pending_response' } }),
      NOW,
    );
    turn = applyAssistantStreamEvent(turn, rawStreamEvent('usage_update', {
      metadata: { response_status: 'failed' },
    }), NOW + 1);

    const result = finalizeLiveAssistantTurn(turn, { status: 'completed', now: NOW + 2 });

    expect(result.content).toBe('Error: Response failed.');
    expect(result.streamEvents).toHaveLength(1);
    expect(result.streamEvents[0].eventType).toBe('node_update');
    expect(result.timelineItems).toHaveLength(1);
    expect(result.timelineItems[0]).toMatchObject({ error: true, responseStatus: 'failed' });
  });

  it('keeps partial provider output as the only answer row when its terminal usage fails', () => {
    let turn = applyAssistantChunk(baseTurn(), 'Partial provider answer', NOW);
    turn = applyAssistantStreamEvent(turn, rawStreamEvent('usage_update', {
      metadata: { response_status: 'failed' },
    }), NOW + 1);

    const result = finalizeLiveAssistantTurn(turn, { status: 'completed', now: NOW + 2 });

    expect(result).toMatchObject({
      content: 'Partial provider answer',
      error: false,
      responseStatus: 'failed',
    });
    expect(result.timelineItems).toHaveLength(1);
    expect(result.timelineItems[0]).toMatchObject({
      kind: 'assistant_message',
      content: 'Partial provider answer',
      error: false,
      responseStatus: 'failed',
    });
  });

  it('does not append a duplicate failure row when visible work already reports the provider failure', () => {
    let turn = applyAssistantStreamEvent(
      baseTurn(),
      rawStreamEvent('tool_start', { content: 'searching' }),
      NOW,
    );
    turn = applyAssistantStreamEvent(turn, rawStreamEvent('usage_update', {
      metadata: { response_status: 'failed' },
    }), NOW + 1);

    const result = finalizeLiveAssistantTurn(turn, { status: 'completed', now: NOW + 2 });

    expect(result).toMatchObject({ content: '', error: false, responseStatus: 'failed' });
    expect(result.timelineItems).toHaveLength(1);
    expect(result.timelineItems[0].kind).toBe('stream_event');
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

    it('records a cache response timestamp only when the reply has answer text', () => {
      const toolOnly = applyAssistantStreamEvent(baseTurn(), rawStreamEvent('tool_start', { content: 'x' }), NOW);
      const answered = applyAssistantChunk(baseTurn(), 'Hello', NOW);

      expect(finalizeLiveAssistantTurn(toolOnly, { status: 'completed', now: NOW + 100 }).responseTimestamp)
        .toBeNull();
      expect(finalizeLiveAssistantTurn(answered, { status: 'completed', now: NOW + 100 }).responseTimestamp)
        .toBe(NOW + 100);
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

    it('ignores hidden pending_response node updates when deciding whether to show the no-response placeholder', () => {
      const turn = applyAssistantStreamEvent(
        baseTurn(),
        rawStreamEvent('node_update', { metadata: { node_name: 'pending_response' } }),
        NOW,
      );
      const result = finalizeLiveAssistantTurn(turn, { status: 'completed', now: NOW });

      expect(result).not.toBeNull();
      expect(result.content).toBe('(No response text)');
      expect(result.timelineItems).toHaveLength(1);
      expect(result.timelineItems[0].kind).toBe('assistant_message');
    });
  });

  describe('status = cancelled', () => {
    it('derives a durable user-cancelled reason for an empty cancelled turn', () => {
      const result = finalizeLiveAssistantTurn(baseTurn(), {
        status: 'cancelled',
        now: NOW,
      });

      expect(result).toMatchObject({
        content: '',
        interruptionReason: 'user_cancelled',
        responseStatus: 'cancelled',
        timelineItems: [],
      });
    });

    it('preserves partial output without appending a cancellation sentinel', () => {
      const result = finalizeLiveAssistantTurn(applyAssistantChunk(baseTurn(), 'Partial', NOW), {
        status: 'cancelled',
        now: NOW,
        interruptionReason: 'user_cancelled',
      });

      expect(result).toMatchObject({ content: 'Partial', interruptionReason: 'user_cancelled', isStreaming: false });
    });

    it('retains an empty safe-boundary turn without creating a response placeholder', () => {
      const result = finalizeLiveAssistantTurn(baseTurn(), {
        status: 'completed',
        now: NOW,
        interruptionReason: 'new_message',
      });

      expect(result).toMatchObject({
        content: '',
        interruptionReason: 'new_message',
        responseStatus: 'completed',
        timelineItems: [],
      });
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

    it('retains the streamed timeline before appending the terminal error row', () => {
      const turn = applyAssistantStreamEvent(
        baseTurn(),
        rawStreamEvent('tool_start', { content: 'searching' }),
        NOW,
      );
      const result = finalizeLiveAssistantTurn(turn, {
        status: 'error',
        now: NOW + 100,
        errorMessage: 'backend unavailable',
      });

      expect(result.error).toBe(true);
      expect(result.timelineItems.map((item) => item.kind)).toEqual([
        'stream_event',
        'assistant_message',
      ]);
      expect(result.timelineItems[0].event.eventType).toBe('tool_start');
      expect(result.timelineItems[1].content).toBe('Error: backend unavailable');
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

  it('full lifecycle: empty turn → cancelled retains its interruption reason', () => {
    let turn = baseTurn();
    turn = applyAssistantMetadata(turn, { display_agent_name: 'Agent' });
    const result = finalizeLiveAssistantTurn(turn, { status: 'cancelled', now: NOW });
    expect(result).toMatchObject({ interruptionReason: 'user_cancelled', responseStatus: 'cancelled' });
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


describe('spectator terminal evidence', () => {
  it.each(['completed', 'failed', 'cancelled', 'interrupted'])(
    'retains provider %s when the spectator transport has no terminal status',
    (status) => {
      let turn = applyAssistantChunk(baseTurn(), 'Observed answer');
      turn = applyAssistantStreamEvent(turn, rawStreamEvent('usage_update', {
        metadata: { output_tokens: 7, final_call_output_tokens: 7, response_status: status },
      }));
      const committed = finalizeLiveAssistantTurn(turn, { status: 'unknown', now: NOW });
      expect(committed.responseStatus).toBe(status);
      expect(committed.timelineItems.find((item) => item.kind === 'assistant_message').responseStatus).toBe(status);
      expect(committed.responseTimestamp).toBe(status === 'completed' ? NOW : null);
    },
  );
});
