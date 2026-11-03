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

  it('preserves transcript workflow identity when also discovered from durable ownership', () => {
    const activities = deriveBackgroundActivities({
      messages: [{ id: 'launch', timestamp: 1000, streamEvents: [workflowResult()] }],
      workflowRunIds: ['run-1'],
    });
    expect(activities.active).toMatchObject([{
      key: 'workflow:run-1', name: 'nightly', startedAt: 1000,
    }]);
  });

  it('rehydrates a workflow omitted by newest-50 pagination and retains it after terminal trace update', () => {
    const newestPage = Array.from({ length: 50 }, (_, index) => ({
      id: `message-${index + 1}`,
      timestamp: index + 1,
      streamEvents: [],
    }));
    const runningRow = {
      kind: 'workflow',
      run_id: 'run-before-page',
      parent_thread_id: 'chat-1',
      name: 'Long-running workflow',
      status: 'running',
      started_at: '2026-11-03T00:00:00Z',
    };
    const checking = deriveBackgroundActivities({
      messages: newestPage,
      threadId: 'chat-1',
      workflowRunIds: ['run-before-page'],
      runRows: new Map([['run-before-page', runningRow]]),
    });
    expect(backgroundRunIds(newestPage, undefined, ['run-before-page'])).toEqual(['run-before-page']);
    expect(checking.active).toMatchObject([{
      key: 'workflow:run-before-page',
      status: 'running',
      name: 'Long-running workflow',
    }]);

    const terminal = deriveBackgroundActivities({
      messages: newestPage,
      threadId: 'chat-1',
      workflowRunIds: ['run-before-page'],
      runRows: new Map([['run-before-page', {
        ...runningRow,
        status: 'completed',
        completed_at: '2026-11-03T00:00:04Z',
      }]]),
    });
    expect(terminal.active).toEqual([]);
    expect(terminal.history).toMatchObject([{ key: 'workflow:run-before-page', status: 'completed' }]);
  });

  it('removes a workflow row that belongs to another chat', () => {
    const activities = deriveBackgroundActivities({
      messages: [{ id: 'answer-1', timestamp: 1000, streamEvents: [workflowResult('run-other-chat')] }],
      threadId: 'chat-1',
      runRows: new Map([['run-other-chat', {
        run_id: 'run-other-chat',
        parent_thread_id: 'chat-2',
        name: 'Other workflow',
        status: 'running',
      }]]),
    });
    expect(activities).toEqual({ active: [], history: [] });
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

  for (const status of ['completed', 'failed', 'cancelled', 'stopped', 'interrupted', 'error']) {
    it(`settles a ${status} workflow into history`, () => {
      const activities = deriveBackgroundActivities({
        messages: [{ id: 'answer-1', timestamp: 1000, streamEvents: [workflowResult()] }],
        threadId: 'chat-1',
        runRows: new Map([['run-1', { run_id: 'run-1', parent_thread_id: 'chat-1', status }]]),
      });
      expect(activities.active).toEqual([]);
      expect(activities.history).toMatchObject([{ key: 'workflow:run-1', status }]);
    });
  }

  for (const status of ['success', 'error', 'cancelled']) {
    it(`settles a ${status} async task into history`, () => {
      const activities = deriveBackgroundActivities({
        threadId: 'chat-1',
        asyncTaskRecords: [{ task_id: 'task-1', parent_thread_id: 'chat-1', status }],
      });
      expect(activities.active).toEqual([]);
      expect(activities.history).toMatchObject([{ key: 'async:task-1', status }]);
    });
  }

  for (const status of ['interrupted', 'paused', 'waiting', 'running']) {
    it(`keeps a nonterminal ${status} async task active`, () => {
      const activities = deriveBackgroundActivities({
        threadId: 'chat-1',
        asyncTaskRecords: [{ task_id: 'task-1', parent_thread_id: 'chat-1', status }],
      });
      expect(activities.active).toMatchObject([{ key: 'async:task-1', status }]);
      expect(activities.history).toEqual([]);
    });
  }

  for (const status of ['paused', 'waiting', 'waiting_approval', 'running']) {
    it(`keeps a nonterminal ${status} workflow active`, () => {
      const activities = deriveBackgroundActivities({
        workflowRunIds: ['run-1'],
        runRows: new Map([['run-1', { run_id: 'run-1', status }]]),
      });
      expect(activities.active).toMatchObject([{ key: 'workflow:run-1', status }]);
    });
  }

  it('settles only the terminal identity among mixed active tasks and workflows', () => {
    const activities = deriveBackgroundActivities({
      messages: [{ id: 'answer-1', timestamp: 1000, streamEvents: [workflowResult('run-done'), workflowResult('run-live')] }],
      threadId: 'chat-1',
      workflowRunIds: ['run-done', 'run-live', 'run-done'],
      runRows: new Map([
        ['run-done', { run_id: 'run-done', parent_thread_id: 'chat-1', status: 'completed' }],
        ['run-live', { run_id: 'run-live', parent_thread_id: 'chat-1', status: 'running' }],
      ]),
      asyncTaskRecords: [
        { task_id: 'task-done', parent_thread_id: 'chat-1', status: 'success' },
        { task_id: 'task-live', parent_thread_id: 'chat-1', status: 'running' },
        { task_id: 'task-other', parent_thread_id: 'chat-2', status: 'running' },
      ],
    });
    expect(activities.active.map((activity) => activity.key).sort())
      .toEqual(['async:task-live', 'workflow:run-live']);
    expect(activities.history.map((activity) => activity.key).sort())
      .toEqual(['async:task-done', 'workflow:run-done']);
  });

  it('keeps canonical terminal workflow status despite a failed ancillary refresh', () => {
    const messages = [{ id: 'answer-1', timestamp: 1000, streamEvents: [workflowResult()] }];
    const terminalRow = { run_id: 'run-1', parent_thread_id: 'chat-1', status: 'completed', completed_at: '2026-11-03T00:00:03Z' };
    for (const request of [{ error: 'Runs service disconnected' }, { notFound: true }]) {
      const activities = deriveBackgroundActivities({
        messages,
        threadId: 'chat-1',
        runRows: new Map([['run-1', terminalRow]]),
        runRequests: new Map([['run-1', request]]),
      });
      expect(activities.active).toEqual([]);
      expect(activities.history).toMatchObject([{ key: 'workflow:run-1', status: 'completed', error: '' }]);
    }
  });

  it('still surfaces a refresh failure for unresolved nonterminal workflow rows', () => {
    const activities = deriveBackgroundActivities({
      workflowRunIds: ['run-1'],
      runRows: new Map([['run-1', { run_id: 'run-1', status: 'running' }]]),
      runRequests: new Map([['run-1', { error: 'Runs service disconnected' }]]),
    });
    expect(activities.active).toMatchObject([{
      key: 'workflow:run-1', status: 'unavailable', error: 'Runs service disconnected',
    }]);
  });

  it('does not fabricate a heartbeat background queue acceptance as running work', () => {
    const activities = deriveBackgroundActivities({
      messages: [{ id: 'answer-1', streamEvents: [event('tool_result', { tool_name: 'enqueue_background_task', result_preview: JSON.stringify({ id: 'bg-1', execution_thread_id: 'thread-1' }) })] }],
      threadId: 'chat-1',
    });
    expect(activities).toEqual({ active: [], history: [] });
  });
});
