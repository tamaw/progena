/**
 * Skills domain: the DeepAgent skill library, kept live by `skills.snapshot`
 * events on /api/ui/stream and seeded via REST when the Skills page mounts.
 * Each skill is the raw API shape so edits PUT the file content straight back.
 */
import { createAsyncThunk } from '@reduxjs/toolkit';
import { requestJson, withQuery } from '../http';

export const initialState = {
  skills: {
    items: [],
    loaded: false,
    loading: false,
    error: null,
  },
};

function applySkills(state, items) {
  state.skills.items = Array.isArray(items) ? items : [];
  state.skills.loaded = true;
  state.skills.loading = false;
  state.skills.error = null;
}

function upsertSkill(state, skill) {
  if (!skill?.path) {
    return;
  }
  const index = state.skills.items.findIndex((item) => item.path === skill.path);
  if (index >= 0) {
    state.skills.items[index] = skill;
  } else {
    state.skills.items.push(skill);
  }
}

// Load the skill catalogue. The response carries the list (so the page renders
// immediately) and the GET also triggers a `skills.snapshot` broadcast that
// keeps other open tabs in sync.
export const fetchSkills = createAsyncThunk(
  'uiData/fetchSkills',
  ({ skillsUrl, agent } = {}) => requestJson(
    agent ? withQuery(skillsUrl, { agent }) : skillsUrl,
    { errorLabel: 'Failed to load skills' },
  ),
);

// Persist edits to one skill file. The backend saves it and broadcasts
// `skills.snapshot`; the response carries the saved record so we upsert it
// immediately (the snapshot then reconciles).
export const saveSkill = createAsyncThunk(
  'uiData/saveSkill',
  ({ skillsUrl, path, content }) => requestJson(`${skillsUrl}/content`, {
    method: 'PUT',
    body: { path, content },
    errorLabel: 'Failed to save skill:',
  }),
);

// Create a new skill file under <agent>/<name>/SKILL.md.
export const createSkill = createAsyncThunk(
  'uiData/createSkill',
  ({ skillsUrl, agent, name, content }) => requestJson(skillsUrl, {
    method: 'POST',
    body: { agent, name, content },
    errorLabel: 'Failed to create skill:',
  }),
);

export const eventHandlers = {
  'skills.snapshot': (state, payload) => {
    applySkills(state, payload.skills);
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchSkills.pending, (state) => {
      state.skills.loading = true;
      state.skills.error = null;
    })
    .addCase(fetchSkills.fulfilled, (state, action) => {
      applySkills(state, action.payload?.skills);
    })
    .addCase(fetchSkills.rejected, (state, action) => {
      state.skills.loading = false;
      state.skills.error = action.error?.message || 'Failed to load skills';
    })
    .addCase(saveSkill.fulfilled, (state, action) => {
      upsertSkill(state, action.payload);
    })
    .addCase(createSkill.fulfilled, (state, action) => {
      upsertSkill(state, action.payload);
    });
}

export const selectSkills = (state) => state.uiData.skills.items;
export const selectSkillsState = (state) => state.uiData.skills;
