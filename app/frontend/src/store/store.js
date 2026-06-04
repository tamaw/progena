import { configureStore } from '@reduxjs/toolkit';
import realtimeReducer from './realtimeSlice';
import uiDataReducer from './uiDataSlice';
import { realtimeListenerMiddleware } from './realtimeListener';

export const store = configureStore({
  reducer: {
    realtime: realtimeReducer,
    uiData: uiDataReducer,
  },
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware().prepend(realtimeListenerMiddleware.middleware),
});

export default store;
