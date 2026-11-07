import { afterEach, describe, expect, it, vi } from 'vitest';
import { configureStore } from '@reduxjs/toolkit';
import uiData from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';
import realtime, { uiEventReceived } from '@frontend/realtime/realtimeSlice';
import { realtimeListenerMiddleware } from '@frontend/realtime/realtimeListener';
import { updateKanbanCard } from '@frontend/features/tasks/state';
import { updateWidget } from '@frontend/features/schedules/widgetsState';
vi.mock('@frontend/realtime/realtimeClient', () => ({
  realtimeClientId: 'composition-tab',
  waitForRealtimeClient: () => Promise.resolve(),
}));


const requestId = 'f0e1d2c3-b4a5-4678-9abc-def012345678';
let store;

function createStore() {
  store = configureStore({
    reducer: { uiData, realtime },
    middleware: (defaults) => defaults().prepend(realtimeListenerMiddleware.middleware),
  });
  return store;
}

function acknowledgement(eventType) {
  return {
    status: 202,
    text: async () => JSON.stringify({ request_id: requestId, event_types: [eventType] }),
  };
}

afterEach(() => {
  store = undefined;
  vi.unstubAllGlobals();
});

describe('feature composition', () => {
  it('hydrates alerts from the connection envelope, which no longer carries runtime status', () => {
    const alerts = [{ id: 'alert-1', message: 'Ready' }];
    const state = uiData(undefined, applyUiEvent({
      type: 'connection.ready',
      payload: { stream: 'ui', alerts: { alerts } },
    }));
    expect(state.alerts).toEqual(alerts);
    expect(state.status).toBeNull();
  });

  it('merges a runtime-status snapshot with metric-only ticks and leaves alerts and widgets alone', () => {
    const alerts = [{ id: 'alert-2' }];
    const widgets = [{ id: 'schedule:weekly' }];
    let state = uiData(undefined, applyUiEvent({ type: 'alerts.updated', payload: { alerts } }));
    state = uiData(state, applyUiEvent({ type: 'widgets.updated', payload: { widgets } }));
    state = uiData(state, applyUiEvent({
      type: 'status.updated', payload: { cycle_count: 4, loops: [{ name: 'main' }] },
    }));
    state = uiData(state, applyUiEvent({ type: 'status.updated', payload: { memory_mb: 120 } }));
    expect(state.status).toMatchObject({ cycle_count: 4, memory_mb: 120, loops: [{ name: 'main' }] });
    expect(state.alerts).toEqual(alerts);
    expect(state.widgets).toEqual(widgets);
  });

  it('retains a draft request ID and handles its task snapshot before the acknowledgement', async () => {
    createStore();
    let acknowledge;
    const fetch = vi.fn(() => new Promise((resolve) => { acknowledge = resolve; }));
    vi.stubGlobal('fetch', fetch);
    const operation = store.dispatch(updateKanbanCard({
      kanbanUrl: '/w/example/api/ui/kanban', cardId: 'card/1', payload: { title: 'Updated' }, requestId,
    }));
    expect(operation.requestId).toBe(requestId);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith(
      '/w/example/api/ui/kanban/cards/card%2F1?client_id=composition-tab',
      expect.objectContaining({
        method: 'PATCH',
        headers: expect.objectContaining({ 'X-Operation-Id': requestId }),
      }),
    ));
    expect(store.getState().realtime.pendingOps[requestId].status).toBe('awaiting_acknowledgement');
    const event = {
      id: 'ui-test-1', type: 'tasks.snapshot',
      payload: { cards: [{ id: 'card/1', title: 'Updated' }], request_id: requestId },
    };
    store.dispatch(applyUiEvent(event));
    store.dispatch(uiEventReceived(event));
    acknowledge(acknowledgement(event.type));
    await operation.unwrap();
    expect(store.getState().uiData.tasks.board.cards[0].title).toBe('Updated');
    expect(store.getState().realtime.pendingOps[requestId]).toBeUndefined();
  });

  it('keeps acknowledged widget changes pending until their causal stream event', async () => {
    createStore();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(acknowledgement('widgets.updated')));
    await store.dispatch(updateWidget({
      widgetsUrl: '/api/ui/widgets', widgetId: 'mcp:example', updates: { enabled: false }, requestId,
    })).unwrap();
    expect(store.getState().realtime.pendingOps[requestId].status).toBe('pending');
    expect(store.getState().uiData.widgets).toEqual([]);
    store.dispatch(uiEventReceived({
      id: 'widget-change', type: 'widgets.updated', payload: { request_id: requestId },
    }));
    expect(store.getState().realtime.pendingOps[requestId]).toBeUndefined();
  });
});
