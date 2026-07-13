/**
 * Skills domain: the DeepAgent skill library, kept live by `skills.snapshot`
 * events on /api/ui/stream and seeded via REST when the Skills page mounts.
 * Each skill is the raw API shape so edits PUT the file content straight back.
 */
import { createAsyncThunk } from '@reduxjs/toolkit';
import { createOperationRequestId, requestAcknowledged, withQuery } from '../http';

export const initialState = {
  skills: {
    items: [],
    loaded: false,
    loading: false,
    error: null,
    snapshotRequestId: null,
    requestId: null,
  },
};

function applySkills(state, items, requestId = null) {
  state.skills.items = Array.isArray(items) ? items : [];
  state.skills.loaded = true;
  state.skills.snapshotRequestId = requestId;
  if (!state.skills.loading || requestId === state.skills.requestId) {
    state.skills.loading = false;
    state.skills.error = null;
    state.skills.requestId = null;
  }
}


// Load the skill catalogue. The GET triggers a `skills.snapshot` broadcast
// that hydrates state from the realtime stream.
export const fetchSkills = createAsyncThunk('uiData/fetchSkills', async ({ skillsUrl, agent } = {}, { requestId }) => {
  return requestAcknowledged(
    agent ? withQuery(skillsUrl, { agent }) : skillsUrl,
    { requestId },
  );
}, { idGenerator: createOperationRequestId });

// Persist edits to one skill file. The backend saves it and broadcasts
// `skills.snapshot`; the acknowledgement triggers the snapshot event
// which reconciles state.
export const saveSkill = createAsyncThunk('uiData/saveSkill', async ({ skillsUrl, path, content }, { requestId }) => {
  return requestAcknowledged(`${skillsUrl}/content`, {
    method: 'PUT',
    body: { path, content },
    requestId,
  });
}, { idGenerator: createOperationRequestId });

// Create a new skill file under <agent>/<name>/SKILL.md. The backend broadcasts
// `skills.snapshot`; the acknowledgement triggers the snapshot event
// which reconciles state.
export const createSkill = createAsyncThunk('uiData/createSkill', async ({ skillsUrl, agent, name, content }, { requestId }) => {
  return requestAcknowledged(skillsUrl, {
    method: 'POST',
    body: { agent, name, content },
    requestId,
  });
}, { idGenerator: createOperationRequestId });

export const eventHandlers = {
  'skills.snapshot': (state, payload) => {
    applySkills(state, payload.skills, payload.request_id || null);
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchSkills.pending, (state, action) => {
      state.skills.loading = true;
      state.skills.error = null;
      state.skills.requestId = action.meta.requestId;
    })
    .addCase(fetchSkills.rejected, (state, action) => {
      if (state.skills.requestId !== action.meta.requestId) {
        return;
      }
      state.skills.loading = false;
      state.skills.error = action.error?.message || 'Failed to load skills';
      state.skills.requestId = null;
    });
}

export const selectSkills = (state) => state.uiData.skills.items;
export const selectSkillsState = (state) => state.uiData.skills;
