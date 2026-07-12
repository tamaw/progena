import { describe, it, expect } from 'vitest';
import { describeStreamEvent, toolDetailsForEvent } from './messageRenderUtils';

function todosEvent(todos) {
  return { eventType: 'todos_update', metadata: { todos } };
}

describe('describeStreamEvent (todos_update)', () => {
  it('titles the entry after the in_progress todo when one exists', () => {
    const description = describeStreamEvent(todosEvent([
      { content: 'Read the config', status: 'completed' },
      { content: 'Patch the handler', status: 'in_progress' },
      { content: 'Run the tests', status: 'pending' },
    ]));
    expect(description.badge).toBe('Plan');
    expect(description.title).toBe('Patch the handler');
    expect(description.metaDetail).toBe('1/3 done');
  });

  it('falls back to the first pending todo when nothing is in_progress', () => {
    const description = describeStreamEvent(todosEvent([
      { content: 'Read the config', status: 'completed' },
      { content: 'Run the tests', status: 'pending' },
    ]));
    expect(description.title).toBe('Run the tests');
  });

  it('falls back to the last todo once everything is completed', () => {
    const description = describeStreamEvent(todosEvent([
      { content: 'Read the config', status: 'completed' },
      { content: 'Run the tests', status: 'completed' },
    ]));
    expect(description.title).toBe('Run the tests');
  });

  it('falls back to the generic "Plan" label when there are no todos', () => {
    const description = describeStreamEvent(todosEvent([]));
    expect(description.title).toBe('Plan');
    expect(description.metaDetail).toBe('');
  });
});

// These six event types only reach describeStreamEvent via the generic
// stream_event fallback renderer -- e.g. chats persisted before subagent
// cards were grouped into their own timeline item kind (see
// buildDefaultAssistantTimeline / applySubagentEvent in
// liveAssistantTurnModel.js). Before this fix, the unexported
// badgeForEventType() helper's `.includes('agent')` check accidentally
// matched every "subagent_*" type (since "subagent" contains "agent") and
// mislabeled them "Agent".
describe('describeStreamEvent (subagent_*)', () => {
  it('never returns the accidental "Agent" badge for any subagent_* event type', () => {
    const eventTypes = [
      'subagent_start',
      'subagent_result',
      'subagent_message_delta',
      'subagent_tool_start',
      'subagent_tool_result',
      'subagent_tool_error',
    ];
    for (const eventType of eventTypes) {
      const description = describeStreamEvent({ eventType, metadata: { subagent_name: 'researcher' } });
      expect(description.badge).toBe('Subagent');
    }
  });

  it('subagent_start titles the entry with the subagent name', () => {
    const description = describeStreamEvent({
      eventType: 'subagent_start',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    });
    expect(description.title).toBe('→ researcher');
  });

  it('subagent_result reports success with duration and result preview', () => {
    const description = describeStreamEvent({
      eventType: 'subagent_result',
      metadata: {
        subagent_name: 'researcher',
        tool_call_id: 'call_1',
        success: true,
        duration_ms: 1500,
        result_preview: 'Found 3 matches',
      },
    });
    expect(description.title).toBe('✓ researcher');
    expect(description.metaDetail).toBe('1.50s • Found 3 matches');
  });

  it('subagent_result reports failure with the error message', () => {
    const description = describeStreamEvent({
      eventType: 'subagent_result',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1', success: false, error: 'boom' },
    });
    expect(description.title).toBe('✗ researcher');
    expect(description.metaDetail).toBe('boom');
  });

  // bodyHtml for non-empty content goes through formatMessage() (markdown ->
  // HTML via a real DOM), which this suite's node test environment doesn't
  // provide -- same scope limitation as the untested reasoning bodyHtml
  // branch elsewhere in describeStreamEvent. Covered instead by the
  // SubagentCard manual verification.
  it('subagent_message_delta titles the entry with the subagent name and skips formatMessage for empty content', () => {
    const description = describeStreamEvent({
      eventType: 'subagent_message_delta',
      content: '',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    });
    expect(description.title).toBe('researcher');
    expect(description.bodyHtml).toBe('');
  });

  it('subagent_tool_start/result/error use the nested tool name, not the parent task', () => {
    const start = describeStreamEvent({
      eventType: 'subagent_tool_start',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1', nested_tool_name: 'read_file' },
    });
    expect(start.title).toBe('researcher → Read File');

    const result = describeStreamEvent({
      eventType: 'subagent_tool_result',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1', nested_tool_name: 'read_file', duration_ms: 5 },
    });
    expect(result.title).toBe('researcher ✓ Read File');

    const error = describeStreamEvent({
      eventType: 'subagent_tool_error',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1', nested_tool_name: 'read_file', error: 'not found' },
    });
    expect(error.title).toBe('researcher ✗ Read File');
    expect(error.metaDetail).toBe('not found');
  });

  it('toolDetailsForEvent returns null for subagent_tool_* despite containing "tool" in the type', () => {
    expect(toolDetailsForEvent(
      { eventType: 'subagent_tool_start', metadata: { nested_tool_name: 'read_file' } },
      describeStreamEvent({ eventType: 'subagent_tool_start', metadata: { nested_tool_name: 'read_file' } }),
    )).toBeNull();
  });
});
