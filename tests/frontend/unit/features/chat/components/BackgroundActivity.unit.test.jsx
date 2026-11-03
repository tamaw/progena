// @vitest-environment jsdom
import React, { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot } from 'react-dom/client';
import { combineReducers, configureStore } from '@reduxjs/toolkit';
import { Provider } from 'react-redux';
import { MemoryRouter } from 'react-router-dom';
import realtimeReducer, { realtimeStreamReady } from '@frontend/realtime/realtimeSlice';
import uiDataReducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';
import { runsQueryKey } from '@frontend/features/runs/state';
import { publishUiEvent, subscribeUiEvent } from '@frontend/realtime/realtimeEvents';
import { requestAcknowledged } from '@frontend/store/http';
import { BackgroundActivity, BackgroundActivityHistory } from '@frontend/features/chat/components/BackgroundActivity';
import ChatQueue from '@frontend/features/chat/components/ChatQueue';

vi.mock('@frontend/realtime/realtimeClient', () => ({
  realtimeClientId: 'browser',
  waitForRealtimeClient: async () => 'browser',
}));
vi.mock('@frontend/store/http', async (importOriginal) => ({
  ...await importOriginal(),
  requestAcknowledged: vi.fn(),
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const THREAD = 'chat-1';
const queueSnapshot = {
  revision: 1,
  messages: [
    { id: 'queued', text: 'Queued follow-up', attachments: [], status: 'queued', created_at: '2026-11-03T00:00:00Z' },
    { id: 'sending', text: 'Claimed follow-up', attachments: [], status: 'sending', created_at: '2026-11-03T00:00:01Z' },
  ],
};

function workflowLaunch(runId, name) {
  return {
    eventType: 'tool_result',
    timestamp: 1000,
    metadata: {
      tool_name: 'run_workflow',
      result_preview: JSON.stringify({ success: true, run_id: runId, path: `/workflows/${name}` }),
    },
  };
}

// The newest message page names run-done and run-live; run-paged is only known
// through the chat's durable workflow ownership.
const messages = [
  { id: 'reply', role: 'assistant', timestamp: 1000, streamEvents: [workflowLaunch('run-done', 'deploy'), workflowLaunch('run-live', 'nightly')] },
];
const workflowRunIds = ['run-done', 'run-live', 'run-paged'];

let host;
let root;
let store;
let stopDelivery;
const queueActions = { onDelete: vi.fn(), onReorder: vi.fn(), onSendNow: vi.fn() };

function emit(type, payload) {
  act(() => publishUiEvent({ type, payload }));
}

function tasks(revision, statuses) {
  emit('chat.background_tasks.updated', {
    thread_id: THREAD,
    revision,
    tasks: Object.entries(statuses).map(([taskId, status]) => ({
      task_id: taskId, parent_thread_id: THREAD, agent_name: taskId, status,
    })),
  });
}

function trace(runId, status) {
  emit('run.trace.updated', {
    trace: { kind: 'workflow', run_id: runId, name: runId, status, started_at: '2026-11-03T00:00:00Z', spans: [] },
  });
}

async function mount() {
  await act(async () => root.render(
    <Provider store={store}>
      <MemoryRouter>
        <BackgroundActivityHistory messages={messages} threadId={THREAD} workflowRunIds={workflowRunIds}
          runsUrl="/api/ui/runs" chatsUrl="/api/ui/chats" />
        <BackgroundActivity messages={messages} threadId={THREAD} workflowRunIds={workflowRunIds}
          runsUrl="/api/ui/runs" chatsUrl="/api/ui/chats" />
        <ChatQueue snapshot={queueSnapshot} isMutating={false} {...queueActions} />
      </MemoryRouter>
    </Provider>,
  ));
}

async function unmount() {
  await act(async () => root.unmount());
  root = createRoot(host);
}

function rowNames(label) {
  const region = host.querySelector(`[aria-label="${label}"]`);
  return region ? [...region.querySelectorAll('summary')].map((summary) => summary.getAttribute('aria-label')).sort() : [];
}

beforeEach(() => {
  requestAcknowledged.mockReset();
  requestAcknowledged.mockImplementation(async (url) => {
    // The ancillary list refresh for the completed run fails; its canonical
    // terminal trace must still settle it.
    if (url.startsWith('/api/ui/runs?') && url.includes('run-done')) throw new Error('Runs service disconnected');
    return { accepted: true };
  });
  store = configureStore({ reducer: combineReducers({ realtime: realtimeReducer, uiData: uiDataReducer }) });
  store.dispatch(realtimeStreamReady());
  stopDelivery = subscribeUiEvent((event) => store.dispatch(applyUiEvent(event)));
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root?.unmount());
  stopDelivery?.();
  host?.remove();
});

describe('BackgroundActivity', () => {
  it('removes only settled work from the sticky area on live updates and after remount', async () => {
    await mount();
    tasks(1, { 'task-done': 'running', 'task-live': 'running', 'task-paused': 'interrupted' });
    trace('run-done', 'running');
    trace('run-live', 'running');
    trace('run-paged', 'running');
    // An unresolved run whose list refresh failed still offers Retry.
    expect(rowNames('Background activity')).toEqual([
      'run-done, Unable to refresh', 'run-live, Running', 'run-paged, Running',
      'task-done, Running', 'task-live, Running', 'task-paused, Waiting',
    ]);
    expect(rowNames('Background activity history')).toEqual([]);

    // Canonical lifecycle updates arrive after the root reply already ended.
    tasks(2, { 'task-done': 'success', 'task-live': 'running', 'task-paused': 'interrupted' });
    trace('run-done', 'completed');
    trace('run-paged', 'failed');
    expect(rowNames('Background activity')).toEqual([
      'run-live, Running', 'task-live, Running', 'task-paused, Waiting',
    ]);
    expect(rowNames('Background activity history')).toEqual([
      'run-done, Completed', 'run-paged, Failed', 'task-done, Completed',
    ]);
    expect(store.getState().uiData.runs.requests[
      runsQueryKey({ kind: 'workflow', q: 'run-done', sort: 'newest' })
    ]?.error).toBeTruthy();

    // Reopen the chat after a backend restart reset task revisions.
    await unmount();
    emit('connection.ready', {});
    tasks(0, { 'task-done': 'success', 'task-live': 'cancelled', 'task-paused': 'interrupted' });
    await mount();
    await act(async () => {});
    expect(rowNames('Background activity')).toEqual(['run-live, Running', 'task-paused, Waiting']);
    expect(rowNames('Background activity history')).toEqual([
      'run-done, Completed', 'run-paged, Failed', 'task-done, Completed', 'task-live, Cancelled',
    ]);
    expect(host.querySelector('[aria-label="Open run-done in Runs"]').getAttribute('href'))
      .toBe('/runs?run=workflow:run-done');

    // Ordinary queued messages are unaffected and cleanup sent no execution commands.
    const queued = [...host.querySelectorAll('article')].map((article) => article.textContent);
    expect(queued.some((text) => text.includes('Queued follow-up'))).toBe(true);
    expect(queued.some((text) => text.includes('Claimed follow-up') && text.includes('Sending'))).toBe(true);
    expect(Object.values(queueActions).every((action) => action.mock.calls.length === 0)).toBe(true);
    for (const [url, options] of requestAcknowledged.mock.calls) {
      expect(url).toMatch(/^\/api\/ui\/(runs\?|runs\/subscription$|chats\/chat-1\/background-tasks\/subscription$)/);
      expect(['GET', 'PUT', undefined]).toContain(options?.method);
      expect(options?.method === 'DELETE' || /cancel/i.test(url)).toBe(false);
    }
  });
});
