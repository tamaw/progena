import { describe, expect, it } from 'vitest';
import reducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';
import { selectBackgroundTasks } from '@frontend/features/chat/state/backgroundTasks';

describe('chat background task state', () => {
  it('keeps task snapshots isolated by parent chat and accepts autonomous updates', () => {
    let state = reducer(undefined, { type: 'init' });
    state = reducer(state, applyUiEvent({
      type: 'chat.background_tasks.snapshot',
      payload: { thread_id: 'chat-1', revision: 4, tasks: [{ task_id: 'task-1', status: 'running' }] },
    }));
    state = reducer(state, applyUiEvent({
      type: 'chat.background_tasks.updated',
      payload: { thread_id: 'chat-1', revision: 5, tasks: [{ task_id: 'task-1', status: 'success' }] },
    }));
    state = reducer(state, applyUiEvent({
      type: 'chat.background_tasks.snapshot',
      payload: { thread_id: 'chat-1', revision: 4, tasks: [{ task_id: 'task-1', status: 'running' }] },
    }));
    state = reducer(state, applyUiEvent({
      type: 'chat.background_tasks.updated',
      payload: { thread_id: 'chat-2', revision: 1, tasks: [{ task_id: 'task-2', status: 'running' }] },
    }));

    expect(selectBackgroundTasks({ uiData: state }, 'chat-1')).toEqual([{ task_id: 'task-1', status: 'success' }]);
    expect(selectBackgroundTasks({ uiData: state }, 'chat-2')).toEqual([{ task_id: 'task-2', status: 'running' }]);
  });

  it('accepts the authoritative reconnect snapshot after backend revision counters restart', () => {
    let state = reducer(undefined, { type: 'init' });
    state = reducer(state, applyUiEvent({
      type: 'chat.background_tasks.updated',
      payload: { thread_id: 'chat', revision: 80, tasks: [{ task_id: 'task', status: 'running' }] },
    }));
    state = reducer(state, applyUiEvent({ type: 'connection.ready', payload: {} }));
    state = reducer(state, applyUiEvent({
      type: 'chat.background_tasks.snapshot',
      payload: { thread_id: 'chat', revision: 0, tasks: [{ task_id: 'task', status: 'success' }] },
    }));
    expect(selectBackgroundTasks({ uiData: state }, 'chat')).toEqual([{ task_id: 'task', status: 'success' }]);
  });
});
