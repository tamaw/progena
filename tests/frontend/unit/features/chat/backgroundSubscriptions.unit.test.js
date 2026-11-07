import { beforeEach, describe, expect, it, vi } from 'vitest';
import { configureStore } from '@reduxjs/toolkit';

vi.mock('@frontend/realtime/realtimeClient', () => ({
  realtimeClientId: 'browser',
  waitForRealtimeClient: async () => 'browser',
}));
vi.mock('@frontend/store/http', async (importOriginal) => ({
  ...await importOriginal(),
  requestAcknowledged: vi.fn(),
}));

beforeEach(() => vi.resetModules());

const cases = [
  {
    name: 'task completion',
    load: () => import('@frontend/features/chat/backgroundTaskSubscription'),
    watch: (module) => module.watchBackgroundTasks('/api/ui/chats/chat/background-tasks'),
    completed: 'success',
    event: (status) => ({
      type: 'chat.background_tasks.snapshot',
      payload: { thread_id: 'chat', revision: status === 'running' ? 0 : 1, tasks: [{ task_id: 'task', status }] },
    }),
    status: (state) => state.uiData.chatBackgroundTasks.byThread.chat?.tasks[0]?.status,
  },
  {
    name: 'workflow trace completion',
    load: () => import('@frontend/features/chat/backgroundRunTraceSubscription'),
    watch: (module) => module.watchBackgroundRunTrace('/api/ui/runs/subscription', 'workflow', 'run'),
    completed: 'completed',
    event: (status) => ({
      type: 'run.trace.updated',
      payload: { trace: { kind: 'workflow', run_id: 'run', status, spans: [] } },
    }),
    status: (state) => state.uiData.runs.traces['workflow:run']?.status,
  },
];

describe('background subscriptions after reconnect', () => {
  for (const scenario of cases) {
    it(`receives ${scenario.name} after reconnecting while the earlier acknowledgement is pending`, async () => {
      const { default: reducer } = await import('@frontend/store/uiDataSlice');
      const { applyUiEvent } = await import('@frontend/store/uiEvents');
      const { publishUiEvent, subscribeUiEvent } = await import('@frontend/realtime/realtimeEvents');
      const { requestAcknowledged } = await import('@frontend/store/http');
      const store = configureStore({ reducer: { uiData: reducer } });
      const stopDelivery = subscribeUiEvent((event) => store.dispatch(applyUiEvent(event)));
      let durableStatus = 'running';
      let registered = false;
      let first = true;
      let releaseAcknowledgement;
      let reportRegistered;
      const acknowledgement = new Promise((resolve) => { releaseAcknowledgement = resolve; });
      const registration = new Promise((resolve) => { reportRegistered = resolve; });

      // The server installs the interest and emits its canonical snapshot
      // before the finite HTTP acknowledgement finishes crossing the network.
      requestAcknowledged.mockImplementation(async (_url, { body }) => {
        registered = body.subscribed === true || Boolean(body.kind);
        if (!registered) return {};
        publishUiEvent(scenario.event(durableStatus));
        if (first) {
          first = false;
          reportRegistered();
          await acknowledgement;
        }
        return {};
      });
      const module = await scenario.load();
      const stopWatching = scenario.watch(module);
      try {
        await registration;
        expect(scenario.status(store.getState())).toBe('running');
        // Disconnect cleanup removes server interests. The task can finish
        // while disconnected, without another root-agent reply or UI remount.
        registered = false;
        durableStatus = scenario.completed;
        publishUiEvent({ type: 'connection.ready', payload: { client_id: 'browser' } });
        releaseAcknowledgement();
        await vi.waitFor(() => {
          expect(scenario.status(store.getState())).toBe(scenario.completed);
        });
      } finally {
        releaseAcknowledgement();
        stopWatching();
        stopDelivery();
      }
    });
  }
});

describe('background task watch release', () => {
  it('adopts the opened chat interest, survives a re-render gap, and asks again after a release', async () => {
    vi.useFakeTimers();
    try {
      const { requestAcknowledged } = await import('@frontend/store/http');
      requestAcknowledged.mockReset().mockResolvedValue({});
      const { noteServerRegistration, watchBackgroundTasks } = await import('@frontend/features/chat/backgroundTaskSubscription');
      const url = '/api/ui/chats/open/background-tasks';
      const bodies = () => requestAcknowledged.mock.calls.map(([, { body }]) => body.subscribed);

      // Opening the chat registered the interest server-side.
      noteServerRegistration(url);
      const stop = watchBackgroundTasks(url);
      stop();
      // The open chat reloads: its view drops the watch and takes it back.
      const stopAgain = watchBackgroundTasks(url);
      await vi.advanceTimersByTimeAsync(5000);
      expect(bodies()).toEqual([]);

      stopAgain();
      await vi.advanceTimersByTimeAsync(5000);
      expect(bodies()).toEqual([false]);

      // Returning to the chat from its cached detail makes no detail request,
      // so the released interest must be asked for again.
      const stopRevisit = watchBackgroundTasks(url);
      await vi.advanceTimersByTimeAsync(0);
      expect(bodies()).toEqual([false, true]);
      stopRevisit();
    } finally {
      vi.useRealTimers();
    }
  });
});
