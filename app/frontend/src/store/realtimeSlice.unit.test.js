import { describe, it, expect } from 'vitest';
import reducer, {
  realtimeConnected,
  realtimeErrored,
  uiEventReceived,
} from './realtimeSlice';

describe('realtimeSlice dedup across reconnects', () => {
  it('drops a duplicate id received twice on the same connection', () => {
    let s = reducer(undefined, uiEventReceived({ id: 'ui-1', type: 'widgets.updated' }));
    expect(s.receivedIds).toEqual(['ui-1']);
    s = reducer(s, uiEventReceived({ id: 'ui-1', type: 'widgets.updated' }));
    expect(s.receivedIds).toEqual(['ui-1']);
  });

  it('reconnecting clears receivedIds so a new process reusing low ids is not dropped', () => {
    let s = reducer(undefined, uiEventReceived({ id: 'ui-1', type: 'widgets.updated' }));
    s = reducer(s, uiEventReceived({ id: 'ui-2', type: 'inbox.snapshot' }));
    expect(s.receivedIds).toEqual(['ui-1', 'ui-2']);

    // Backend restarted; the tab's EventSource auto-reconnects to a fresh
    // process whose sequence counter starts over from "ui-1".
    s = reducer(s, realtimeErrored('disconnected'));
    s = reducer(s, realtimeConnected());
    expect(s.receivedIds).toEqual([]);

    // The new process's "ui-1" must be applied, not silently deduped away.
    s = reducer(s, uiEventReceived({ id: 'ui-1', type: 'inbox.snapshot' }));
    expect(s.receivedIds).toEqual(['ui-1']);
    expect(s.lastEvent).toEqual({ id: 'ui-1', type: 'inbox.snapshot' });
  });
});
