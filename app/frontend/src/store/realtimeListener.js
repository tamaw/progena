import { createAction, createListenerMiddleware } from '@reduxjs/toolkit';
import {
  realtimeConnected,
  realtimeConnecting,
  realtimeDisconnected,
  realtimeErrored,
  uiEventReceived,
} from './realtimeSlice';
import { applyUiEvent } from './uiDataSlice';

export const startRealtimeStream = createAction('realtime/startStream');
export const stopRealtimeStream = createAction('realtime/stopStream');

export const realtimeListenerMiddleware = createListenerMiddleware();

let eventSource = null;
let activeStreamUrl = null;

function closeCurrentStream(dispatch) {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
    activeStreamUrl = null;
    dispatch(realtimeDisconnected());
  }
}

realtimeListenerMiddleware.startListening({
  actionCreator: startRealtimeStream,
  effect: async (action, listenerApi) => {
    const { streamUrl } = action.payload || {};
    if (!streamUrl || activeStreamUrl === streamUrl) {
      return;
    }

    closeCurrentStream(listenerApi.dispatch);
    listenerApi.dispatch(realtimeConnecting());

    const source = new EventSource(streamUrl);
    eventSource = source;
    activeStreamUrl = streamUrl;

    source.onopen = () => {
      listenerApi.dispatch(realtimeConnected());
    };

    source.onerror = () => {
      listenerApi.dispatch(realtimeErrored('Realtime stream disconnected'));
    };

    source.addEventListener('ui.event', (messageEvent) => {
      try {
        const event = JSON.parse(messageEvent.data);
        const { realtime } = listenerApi.getState();
        if (event?.id && realtime.receivedIds.includes(event.id)) {
          return;
        }
        listenerApi.dispatch(uiEventReceived(event));
        listenerApi.dispatch(applyUiEvent(event));
      } catch (error) {
        listenerApi.dispatch(realtimeErrored(error.message));
      }
    });
  },
});

realtimeListenerMiddleware.startListening({
  actionCreator: stopRealtimeStream,
  effect: async (_action, listenerApi) => {
    closeCurrentStream(listenerApi.dispatch);
  },
});
