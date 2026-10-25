import { describe, expect, it } from 'vitest';
import { isToolEventFailure } from '@frontend/features/chat/models/toolFailureModel';

describe('isToolEventFailure', () => {
  it.each([
    { eventType: 'tool_error' },
    { eventType: 'subagent_tool_error' },
    { eventType: 'tool_result', metadata: { error: 'denied' } },
    { eventType: 'tool_result', metadata: { success: false } },
    { eventType: 'tool_result', metadata: { status: 'failure' } },
    { eventType: 'tool_result', metadata: { state: 'rejected' } },
    {
      eventType: 'tool_result',
      metadata: { tool_name: 'execute', result_preview: 'boom\n[Command failed with exit code 2]' },
    },
    {
      eventType: 'tool_result',
      metadata: { tool_name: 'javascript', result_preview: '<error type="TypeError">boom</error>' },
    },
  ])('recognizes failure event %#', (event) => {
    expect(isToolEventFailure(event)).toBe(true);
  });

  it.each([
    undefined,
    { eventType: 'tool_result' },
    { eventType: 'tool_result', metadata: { success: true, status: 'completed' } },
    {
      eventType: 'tool_result',
      metadata: { tool_name: 'execute', result_preview: 'done\n[Command succeeded with exit code 0]' },
    },
    {
      eventType: 'tool_result',
      metadata: { tool_name: 'javascript', result_preview: '<result kind="text">done</result>' },
    },
  ])('leaves successful event %# alone', (event) => {
    expect(isToolEventFailure(event)).toBe(false);
  });
});
