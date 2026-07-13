import { describe, it, expect } from 'vitest';
import { describeStreamEvent } from './messageRenderUtils';

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
