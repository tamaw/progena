import { afterEach, describe, expect, it, vi } from 'vitest';
import { configureStore } from '@reduxjs/toolkit';
import uiData from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';
import realtime, { uiEventReceived } from '@frontend/realtime/realtimeSlice';
import { realtimeListenerMiddleware, stopRealtimeStream } from '@frontend/realtime/realtimeListener';
import { updateKanbanCard } from '@frontend/features/tasks/state';
import { updateWidget } from '@frontend/features/schedules/widgetsState';

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
  store?.dispatch(stopRealtimeStream());
  vi.unstubAllGlobals();
});

describe('feature composition', () => {
  it('hydrates status, alerts and widgets from the same connection envelope', () => {
    const alerts = [{ id: 'alert-1', message: 'Ready' }];
    const widgets = [{ id: 'schedule:daily', enabled: true }];
    const state = uiData(undefined, applyUiEvent({
      type: 'connection.ready',
      payload: { status: { status: 'healthy', widgets }, alerts: { alerts } },
    }));
    expect(state.status.status).toBe('healthy');
    expect(state.alerts).toEqual(alerts);
    expect(state.widgets).toEqual(widgets);
  });

  it('fans out status changes and retains feature data on metric-only ticks', () => {
    const alerts = [{ id: 'alert-2' }];
    const widgets = [{ id: 'schedule:weekly' }];
    let state = uiData(undefined, applyUiEvent({
      type: 'status.updated', payload: { cycle_count: 4, alerts, widgets },
    }));
    state = uiData(state, applyUiEvent({ type: 'status.updated', payload: { memory_mb: 120 } }));
    expect(state.status).toMatchObject({ cycle_count: 4, memory_mb: 120 });
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
    expect(fetch).toHaveBeenCalledWith('/w/example/api/ui/kanban/cards/card%2F1', expect.objectContaining({
      method: 'PATCH',
      headers: expect.objectContaining({ 'X-Operation-Id': requestId }),
    }));
    expect(store.getState().realtime.pendingOps[requestId].status).toBe('awaiting_acknowledgement');
    const event = {
      id: 'ui-test-1', type: 'kanban.snapshot',
      payload: { cards: [{ id: 'card/1', title: 'Updated' }], request_id: requestId },
    };
    store.dispatch(applyUiEvent(event));
    store.dispatch(uiEventReceived(event));
    acknowledge(acknowledgement(event.type));
    await operation.unwrap();
    expect(store.getState().uiData.tasks.board.cards[0].title).toBe('Updated');
    expect(store.getState().realtime.pendingOps[requestId]).toBeUndefined();
  });

  it('keeps acknowledged widget changes pending until their stream event or disconnect', async () => {
    createStore();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(acknowledgement('widgets.updated')));
    await store.dispatch(updateWidget({
      widgetsUrl: '/api/ui/widgets', widgetId: 'mcp:example', updates: { enabled: false }, requestId,
    })).unwrap();
    expect(store.getState().realtime.pendingOps[requestId].status).toBe('pending');
    expect(store.getState().uiData.widgets).toEqual([]);
    store.dispatch(stopRealtimeStream());
    expect(store.getState().realtime.pendingOps[requestId]).toBeUndefined();
    expect(store.getState().realtime.error).toBe('Stream disconnected');
  });
});
