import { describe, it, expect, vi } from 'vitest';
import {
  createLiveAssistantTurn,
  applyAssistantMetadata,
  applyAssistantStreamEvent,
  applyAssistantChunk,
  buildDefaultAssistantTimeline,
  finalizeLiveAssistantTurn,
  toCommittedAssistantMessage,
} from './liveAssistantTurnModel';
import { CHAT_AGENT_NAME } from './conversationModel';
import {
  createStreamEvent,
  appendStreamEvent,
} from './streamEventsModel';

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

  // Acceptance: assistant_message first, then stream_event items.
  it('produces an assistant_message item first, then stream_event items in order', () => {
    const message = {
      role: 'assistant',
      id: 'msg_1',
      content: 'Hello',
      timestamp: 1000,
      isStreaming: false,
      error: false,
      displayAgentName: 'MyAgent',
      displayModelName: 'gpt-4',
      displayToolCalls: [{ name: 'search' }],
      tokenCount: 5,
      reasoningTokenCount: 0,
      reasoningText: '',
      streamEvents: [
        { id: 'e1', eventType: 'tool_start', content: 'searching' },
        { id: 'e2', eventType: 'tool_result', content: 'result' },
      ],
    };
    const items = buildDefaultAssistantTimeline(message);
    expect(items).toHaveLength(3);
    expect(items[0].kind).toBe('assistant_message');
    expect(items[0].id).toBe('msg_1');
    expect(items[0].content).toBe('Hello');
    expect(items[0].timestamp).toBe(1000);
    expect(items[0].displayAgentName).toBe('MyAgent');
    expect(items[0].displayModelName).toBe('gpt-4');
    expect(items[0].displayToolCalls).toEqual([{ name: 'search' }]);
    expect(items[1].kind).toBe('stream_event');
    expect(items[1].id).toBe('e1');
    expect(items[2].kind).toBe('stream_event');
    expect(items[2].id).toBe('e2');
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

  it('uses default id and timestamp when message lacks them', () => {
    const items = buildDefaultAssistantTimeline({ role: 'assistant', content: 'txt' });
    expect(items[0].id).toBeTruthy();
    expect(typeof items[0].timestamp).toBe('number');
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
