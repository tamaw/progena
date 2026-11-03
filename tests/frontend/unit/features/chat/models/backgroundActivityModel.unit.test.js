import { describe, expect, it } from 'vitest';
import { backgroundRunIds, deriveBackgroundActivities } from '@frontend/features/chat/models/backgroundActivityModel';

function event(eventType, metadata, timestamp = 1000) {
  return { eventType, metadata, timestamp };
}

function workflowResult(runId = 'run-1') {
  return event('tool_result', {
    tool_name: 'run_workflow',
    result_preview: JSON.stringify({ success: true, queued: true, workflow_id: 'workflow-1', request_id: 'request-1', run_id: runId, path: '/workflows/nightly' }),
  });
}

describe('deriveBackgroundActivities', () => {
  it('hydrates a workflow from durable run identity and moves it out of sticky activity on a lifecycle terminal', () => {
    const messages = [{ id: 'answer-1', timestamp: 1000, streamEvents: [workflowResult()] }];
    const queued = deriveBackgroundActivities({ messages, threadId: 'chat-1', runRows: new Map() });
    expect(backgroundRunIds(messages)).toEqual(['run-1']);
    expect(queued.active).toMatchObject([{ key: 'workflow:run-1', status: 'checking', name: 'nightly' }]);

    const completed = deriveBackgroundActivities({
      messages,
      threadId: 'chat-1',
      runRows: new Map([['run-1', {
        kind: 'workflow', run_id: 'run-1', name: 'Nightly workflow', status: 'completed', started_at: '2026-11-03T00:00:00Z', completed_at: '2026-11-03T00:00:03Z',
      }]]),
    });
    expect(completed.active).toEqual([]);
    expect(completed.history).toMatchObject([{ key: 'workflow:run-1', status: 'completed', name: 'Nightly workflow' }]);
  });

  it('surfaces a failed run hydration instead of leaving an acknowledgement queued forever', () => {
    const messages = [{ id: 'answer-1', timestamp: 1000, streamEvents: [workflowResult('missing-run')] }];
    const unavailable = deriveBackgroundActivities({
      messages,
      runRequests: new Map([['missing-run', { error: 'Runs service disconnected' }]]),
    });
    expect(unavailable.active).toMatchObject([{
      key: 'workflow:missing-run',
      status: 'unavailable',
      error: 'Runs service disconnected',
    }]);
  });

  it('uses an authoritative durable task record to settle work after its root turn ended', () => {
    const settled = deriveBackgroundActivities({
      messages: [],
      threadId: 'chat-1',
      asyncTaskRecords: [{
        task_id: 'task-owned',
        parent_thread_id: 'chat-1',
        agent_name: 'researcher',
        status: 'success',
        created_at: '2026-11-03T00:00:00Z',
        updated_at: '2026-11-03T00:00:05Z',
        completed_at: '2026-11-03T00:00:05Z',
        summary: 'Completed independently',
      }],
    });
    expect(settled.active).toEqual([]);
    expect(settled.history).toMatchObject([{ key: 'async:task-owned', status: 'success', summary: 'Completed independently' }]);
  });

  it('rehydrates only the selected chat async task and retains a terminal error as history', () => {
    const messages = [{
      id: 'answer-1', timestamp: 1000,
      streamEvents: [
        event('async_subagent_start', { task_id: 'task-owned', thread_id: 'chat-1', subagent_name: 'researcher', status: 'running' }),
      ],
    }];
    const active = deriveBackgroundActivities({ messages, threadId: 'chat-1' });
    expect(active.active).toMatchObject([{ key: 'async:task-owned', status: 'running', name: 'researcher' }]);
    expect(active.active).toHaveLength(1);

    const reconnected = deriveBackgroundActivities({
      messages: [{ id: 'answer-1', timestamp: 1000, streamEvents: [event('tool_result', {
        tool_name: 'check_async_task',
        result_preview: JSON.stringify({ tasks: [
          { task_id: 'task-owned', parent_thread_id: 'chat-1', agent_name: 'researcher', status: 'error', created_at: '2026-11-03T00:00:00Z', completed_at: '2026-11-03T00:00:04Z', last_error: 'delegate failed' },
          { task_id: 'task-other', parent_thread_id: 'chat-2', agent_name: 'other', status: 'running' },
        ] }),
      })] }],
      threadId: 'chat-1',
    });
    expect(reconnected.active).toEqual([]);
    expect(reconnected.history).toMatchObject([{ key: 'async:task-owned', status: 'error', error: 'delegate failed' }]);
    expect(reconnected.history).toHaveLength(1);
  });

  it('does not fabricate a heartbeat background queue acceptance as running work', () => {
    const activities = deriveBackgroundActivities({
      messages: [{ id: 'answer-1', streamEvents: [event('tool_result', { tool_name: 'enqueue_background_task', result_preview: JSON.stringify({ id: 'bg-1', execution_thread_id: 'thread-1' }) })] }],
      threadId: 'chat-1',
    });
    expect(activities).toEqual({ active: [], history: [] });
  });
});
