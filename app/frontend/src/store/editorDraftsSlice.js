import { createSlice } from '@reduxjs/toolkit';
import { applyUiEvent } from './uiDataSlice';
import { failPendingOp } from './realtimeSlice';
import { failureMessage } from '../lib/failures';

const initialState = { entries: {}, locations: {}, nextRevision: 0 };

export function entityDraftKey(kind, id, editor = 'editor') {
  return id == null || id === '' ? null : JSON.stringify([kind, String(id), editor]);
}

function settle(state, ticket, error = null) {
  const entry = state.entries[ticket.key];
  if (!entry || entry.save?.token !== ticket.token) return;
  if (!error && entry.revision === ticket.revision) {
    delete state.entries[ticket.key];
    return;
  }
  entry.save = null;
  entry.error = error;
}

function receiveEvent(state, event) {
  const requestId = event?.payload?.request_id;
  if (!requestId) return;
  for (const entry of Object.values(state.entries)) {
    const save = entry.save;
    if (save?.requestId !== requestId) continue;
    if (event.type === 'stream.error' || event.type.endsWith('.error')) {
      settle(state, save, failureMessage(event.payload?.message || event.payload?.error || 'Save failed. Please try again.', 'Save changes').message);
    } else if (save.events.includes(event.type)) {
      settle(state, save);
    }
  }
}

const slice = createSlice({
  name: 'editorDrafts',
  initialState,
  reducers: {
    draftChanged(state, { payload: { key, value } }) {
      if (!key) return;
      const previous = state.entries[key];
      state.entries[key] = {
        value,
        revision: ++state.nextRevision,
        save: previous?.save || null,
        error: null,
      };
    },
    draftDiscarded(state, { payload: key }) {
      delete state.entries[key];
    },
    draftSaveStarted(state, { payload: save }) {
      const entry = state.entries[save.key];
      if (!entry || entry.revision !== save.revision || entry.save) return;
      entry.save = { ...save, events: save.events || [], requestId: save.requestId || null };
      entry.error = null;
    },
    draftSaveTracked(state, { payload: { ticket, requestId, events } }) {
      const entry = state.entries[ticket.key];
      if (entry?.save?.token !== ticket.token) return;
      entry.save.requestId = requestId;
      entry.save.events = events;
    },
    draftSaveCompleted(state, { payload: ticket }) {
      settle(state, ticket);
    },
    draftSaveFailed(state, { payload: { ticket, error } }) {
      const entry = state.entries[ticket.key];
      if (entry?.save?.token !== ticket.token) return;
      entry.save = null;
      entry.error = error || null;
    },
    editorLocationRemembered(state, { payload: { page, location } }) {
      state.locations[page] = location;
    },
    editorLocationForgotten(state, { payload: page }) {
      delete state.locations[page];
    },
  },
  extraReducers: (builder) => {
    builder.addCase(applyUiEvent, (state, action) => receiveEvent(state, action.payload));
    builder.addCase(failPendingOp, (state, { payload }) => {
      for (const entry of Object.values(state.entries)) {
        if (entry.save?.requestId === payload.requestId) {
          entry.error = typeof payload.error === 'string' ? payload.error : payload.error?.message || 'Save interrupted. Please try again.';
          entry.save = null;
        }
      }
    });
    builder.addMatcher(
      (action) => action.type.endsWith('/rejected') && Boolean(action.meta?.requestId),
      (state, action) => {
        for (const entry of Object.values(state.entries)) {
          if (entry.save?.requestId === action.meta.requestId) {
            entry.error = action.meta.aborted ? null : action.error?.message || 'Save failed. Please try again.';
            entry.save = null;
          }
        }
      },
    );
  },
});

export const {
  draftChanged, draftDiscarded, draftSaveStarted, draftSaveTracked,
  draftSaveCompleted, draftSaveFailed, editorLocationRemembered, editorLocationForgotten,
} = slice.actions;
export default slice.reducer;
