import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { watchRoutineRuns } from './routineSubscriptions';
import { publishUiEvent } from './realtimeEvents';
import { setRealtimeClientReady } from './realtimeClient';

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
});
