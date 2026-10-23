import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { watchRoutineRuns } from '@frontend/features/routines/state/routineSubscriptions';
import { publishUiEvent } from '@frontend/realtime/realtimeEvents';
import { setRealtimeClientReady } from '@frontend/realtime/realtimeClient';

let watch;
beforeEach(() => {
  setRealtimeClientReady(true);
  vi.stubGlobal('fetch', vi.fn(async (_url, options) => new Response(JSON.stringify({
    request_id: options.headers['X-Request-ID'], event_types: ['agent.routine.subscription.updated'],
  }), { status: 202 })));
});
afterEach(async () => {
  watch?.close();
  await vi.waitFor(() => expect(JSON.parse(fetch.mock.calls.at(-1)[1].body).subscribed).toBe(false));
  watch = null;
  vi.unstubAllGlobals();
});

describe('routine detail interests', () => {
  it('registers the new session before the caller can enqueue execution and unsubscribes on leaving', async () => {
    watch = watchRoutineRuns('/api/ui/agent-profiles', 'routine');
    await watch.select({ session_id: 'new-run' });
    expect(JSON.parse(fetch.mock.calls.at(-1)[1].body)).toMatchObject({ subscribed: true, session_id: 'new-run' });
    watch.close();
    await vi.waitFor(() => expect(JSON.parse(fetch.mock.calls.at(-1)[1].body).subscribed).toBe(false));
    const count = fetch.mock.calls.length;
    publishUiEvent({ type: 'connection.ready' });
    await Promise.resolve();
    expect(fetch.mock.calls).toHaveLength(count);
  });

  it('restores the viewed historical thread after reconnect', async () => {
    watch = watchRoutineRuns('/api/ui/agent-profiles', 'routine');
    await watch.select({ thread_id: 'routine:routine:rev:old' });
    const count = fetch.mock.calls.length;
    setRealtimeClientReady(false);
    setRealtimeClientReady(true);
    publishUiEvent({ type: 'connection.ready' });
    await vi.waitFor(() => expect(fetch.mock.calls).toHaveLength(count + 1));
    expect(JSON.parse(fetch.mock.calls.at(-1)[1].body)).toMatchObject({ subscribed: true, thread_id: 'routine:routine:rev:old' });
  });

  it('sends one request for repeated identical selections but a new one when the selection changes', async () => {
    watch = watchRoutineRuns('/api/ui/agent-profiles', 'routine');
    await Promise.all([watch.select({}), watch.select({ session_id: 'run-1' }), watch.select({ session_id: 'run-1' })]);
    await watch.select({ session_id: 'run-1' });
    expect(fetch.mock.calls.map(([, options]) => JSON.parse(options.body).session_id)).toEqual([undefined, 'run-1']);
    await watch.select({ session_id: 'run-2' });
    await watch.select({ session_id: 'run-1' });
    expect(fetch.mock.calls.map(([, options]) => JSON.parse(options.body).session_id))
      .toEqual([undefined, 'run-1', 'run-2', 'run-1']);
  });

  it('retries a selection whose request failed and resubmits the same selection after reconnect', async () => {
    watch = watchRoutineRuns('/api/ui/agent-profiles', 'routine');
    await watch.select({});
    fetch.mockImplementationOnce(async () => new Response('{}', { status: 500 }));
    await expect(watch.select({ session_id: 'flaky' })).rejects.toBeDefined();
    await watch.select({ session_id: 'flaky' });
    expect(fetch.mock.calls.map(([, options]) => JSON.parse(options.body).session_id))
      .toEqual([undefined, 'flaky', 'flaky']);
    const count = fetch.mock.calls.length;
    publishUiEvent({ type: 'connection.ready' });
    await vi.waitFor(() => expect(fetch.mock.calls).toHaveLength(count + 1));
    expect(JSON.parse(fetch.mock.calls.at(-1)[1].body)).toMatchObject({ subscribed: true, session_id: 'flaky' });
  });
});
