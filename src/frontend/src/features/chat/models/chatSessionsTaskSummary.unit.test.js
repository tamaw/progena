import { describe, it, expect } from 'vitest';
import { normalizeBackendChatMessage } from './chatSessionsModel';

function persistedTask(description) {
  return {
    id: 'message-task-summary',
    role: 'assistant',
    content: 'done',
    tool_calls: [{
      id: 'call-persisted-summary',
      name: 'task',
      args: {
        subagent_type: 'researcher',
        ...(description === undefined ? {} : { description }),
      },
      result: 'Final answer from the persisted subagent',
    }],
  };
}

describe('persisted task descriptions', () => {
  it('replays a normalized description into both subagent events and the card', () => {
    const message = normalizeBackendChatMessage(
      persistedTask('  Read   the\n README.md\tand summarize its purpose  '),
      1,
    );
    const start = message.streamEvents.find((event) => event.eventType === 'subagent_start');
    const result = message.streamEvents.find((event) => event.eventType === 'subagent_result');
    const card = message.timelineItems.find((item) => item.kind === 'subagent');

    expect(start.metadata.task_summary).toBe('Read the README.md and summarize its purpose');
    expect(result.metadata.task_summary).toBe('Read the README.md and summarize its purpose');
    expect(card.taskSummary).toBe('Read the README.md and summarize its purpose');
    expect(card.resultPreview).toBe('Final answer from the persisted subagent');
  });

  it('bounds a persisted description to 140 characters including the ellipsis', () => {
    const message = normalizeBackendChatMessage(persistedTask('x'.repeat(200)), 1);
    const card = message.timelineItems.find((item) => item.kind === 'subagent');

    expect(card.taskSummary).toBe(`${'x'.repeat(139)}…`);
    expect(card.taskSummary).toHaveLength(140);
  });

  it('keeps missing persisted descriptions compatible with the legacy card shape', () => {
    const message = normalizeBackendChatMessage(persistedTask(), 1);
    const start = message.streamEvents.find((event) => event.eventType === 'subagent_start');
    const card = message.timelineItems.find((item) => item.kind === 'subagent');

    expect(start.metadata).not.toHaveProperty('task_summary');
    expect(card.taskSummary).toBe('');
    expect(card.resultPreview).toBe('Final answer from the persisted subagent');
  });
});
