import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ ready: false }));

vi.mock('@frontend/store/async', () => ({
  createAcknowledgedThunk: (type) => (args) => ({ type, args }),
  requestAcknowledged: vi.fn(),
}));
vi.mock('@frontend/realtime/realtimeClient', () => ({
  isRealtimeClientReady: () => state.ready,
  realtimeClientId: 'tab',
  waitForRealtimeClient: () => Promise.resolve(),
}));

import { holdTopic, resetStreamTopicsForTest } from '@frontend/realtime/streamTopics';
import { notifyStreamReady } from '@frontend/realtime/streamLifecycle';

describe('stream topics', () => {
  let dispatch;
  const context = () => ({ dispatch, streamUrl: '/s' });
  const sent = () => dispatch.mock.calls.map(([action]) => `${action.args.topic}:${action.args.watching}`);

  beforeEach(() => {
    vi.useFakeTimers();
    resetStreamTopicsForTest();
    dispatch = vi.fn();
    state.ready = true;
  });

  afterEach(() => {
    resetStreamTopicsForTest();
    vi.useRealTimers();
  });

  it('registers a topic once however many pages hold it, and releases it after the last leaves', () => {
    const a = holdTopic('agents', context());
    const b = holdTopic('agents', context());
    expect(sent()).toEqual(['agents:true']);

    a();
    vi.advanceTimersByTime(1000);
    expect(sent()).toEqual(['agents:true']);

    b();
    vi.advanceTimersByTime(1000);
    expect(sent()).toEqual(['agents:true', 'agents:false']);
  });

  it('a page that leaves and returns at once keeps the registration and sends nothing', () => {
    const first = holdTopic('runs', context());
    first();
    const again = holdTopic('runs', context());
    vi.advanceTimersByTime(1000);

    expect(sent()).toEqual(['runs:true']);
    again();
    vi.advanceTimersByTime(1000);
    expect(sent()).toEqual(['runs:true', 'runs:false']);
  });

  it('only records a topic while the stream is not ready and registers it when it becomes ready', () => {
    state.ready = false;
    holdTopic('tasks', context());
    expect(dispatch).not.toHaveBeenCalled();

    state.ready = true;
    notifyStreamReady({ dispatch, streamUrl: '/s' });
    expect(sent()).toEqual(['tasks:true']);
  });

  it('registers every held topic again on a reconnect, but not ones already released', () => {
    holdTopic('chats', context());
    const gone = holdTopic('knowledge', context());
    gone();
    vi.advanceTimersByTime(1000);
    dispatch.mockClear();

    notifyStreamReady({ dispatch, streamUrl: '/s' });
    expect(sent()).toEqual(['chats:true']);
  });

  it('releasing the same hold twice is harmless', () => {
    const release = holdTopic('mcp', context());
    const other = holdTopic('mcp', context());
    release();
    release();
    vi.advanceTimersByTime(1000);
    expect(sent()).toEqual(['mcp:true']);
    other();
  });
});
