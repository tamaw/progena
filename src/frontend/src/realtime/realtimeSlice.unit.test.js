import { describe, it, expect } from 'vitest';
import reducer, {
  realtimeConnected,
  realtimeErrored,
  registerPendingOp,
  uiEventReceived,
} from './realtimeSlice';

describe('realtimeSlice dedup across reconnects', () => {
  it('drops a duplicate envelope received twice on the same connection', () => {
    const event = { id: 'ui-1', type: 'widgets.updated', payload: {} };
    let s = reducer(undefined, uiEventReceived(event));
    expect(s.receivedIds).toEqual(['ui-1']);
    s = reducer(s, uiEventReceived(event));
    expect(s.receivedIds).toEqual(['ui-1']);
  });

  it('reconnecting clears receivedIds so a new process reusing low ids is not dropped', () => {
    let s = reducer(undefined, uiEventReceived({ id: 'ui-1', type: 'widgets.updated', payload: {} }));
    s = reducer(s, uiEventReceived({ id: 'ui-2', type: 'inbox.snapshot', payload: {} }));
    expect(s.receivedIds).toEqual(['ui-1', 'ui-2']);

    // Backend restarted; the tab's EventSource auto-reconnects to a fresh
    // process whose sequence counter starts over from 'ui-1'.
    s = reducer(s, realtimeErrored('disconnected'));
    s = reducer(s, realtimeConnected());
    expect(s.receivedIds).toEqual([]);

    // The new process's "ui-1" must be applied, not silently deduped away.
    const reusedEvent = { id: 'ui-1', type: 'inbox.snapshot', payload: {} };
    s = reducer(s, uiEventReceived(reusedEvent));
    expect(s.receivedIds).toEqual(['ui-1']);
    expect(s.lastEvent).toEqual(reusedEvent);
  });
describe('realtimeSlice failure, correlation, and bounds', () => {
  it('retains an error state while preserving no stale connection claim', () => {
    const s = reducer(undefined, realtimeErrored('stream unavailable'));
    expect(s.connected).toBe(false);
    expect(s.status).toBe('error');
    expect(s.error).toBe('stream unavailable');
  });

  it('resolves only the pending operation matching the causal request id', () => {
    const event = {
      id: 'ui-chat-1',
      type: 'chat.response.completed',
      payload: { request_id: 'request-1' },
    };
    let s = reducer(undefined, registerPendingOp({ requestId: 'request-1', eventTypes: ['chat.response.completed'] }));
    s = reducer(s, registerPendingOp({ requestId: 'request-2', eventTypes: ['chat.response.completed'] }));
    s = reducer(s, uiEventReceived(event));

    expect(s.pendingOps['request-1']).toBeUndefined();
    expect(s.pendingOps['request-2']).toBeDefined();
  });

  it('bounds replay queues and dedup history under a high-rate event burst', () => {
    let s = reducer(undefined, { type: '@@init' });
    for (let index = 0; index < 501; index += 1) {
      s = reducer(s, uiEventReceived({
        id: `ui-${index}`,
        type: 'widgets.updated',
        payload: {},
      }));
    }

    expect(s.eventQueue).toHaveLength(500);
    expect(s.eventQueue[0].event.id).toBe('ui-1');
    expect(s.eventQueue.at(-1).event.id).toBe('ui-500');
    expect(s.receivedIds).toHaveLength(300);
    expect(s.receivedIds[0]).toBe('ui-201');
  });
  it('bounds retained request-correlated events while keeping the newest causal frames', () => {
    let s = reducer(undefined, { type: '@@init' });
    for (let index = 0; index < 101; index += 1) {
      s = reducer(s, uiEventReceived({
        id: `request-event-${index}`,
        type: 'widgets.updated',
        payload: { request_id: 'request-burst' },
      }));
    }

    expect(s.receivedRequestEvents['request-burst']).toHaveLength(100);
    expect(s.receivedRequestEvents['request-burst'][0].id).toBe('request-event-1');
    expect(s.receivedRequestEvents['request-burst'].at(-1).id).toBe('request-event-100');
  });

});

});
