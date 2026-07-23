/**
 * Skills domain: the DeepAgent skill library, kept live by `skills.snapshot`
 * events on /api/ui/stream and seeded via REST when the Skills page mounts.
 * Each skill is the raw API shape so edits PUT the file content straight back.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from './async';
import { withQuery } from '../http';

export const initialState = {
  skills: {
    items: [],
    loaded: false,
    loading: false,
    error: null,
    snapshotRequestId: null,
    requestId: null,
    contentByPath: {},
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
export const fetchSkills = createAcknowledgedThunk('uiData/fetchSkills', async ({ skillsUrl, agent } = {}, { requestId }) => {
  return requestAcknowledged(
    agent ? withQuery(skillsUrl, { agent }) : skillsUrl,
    { requestId },
  );
});

// Persist edits to one skill file. The backend saves it and broadcasts
// `skills.snapshot`; the acknowledgement triggers the snapshot event
// which reconciles state.
export const saveSkill = createAcknowledgedThunk('uiData/saveSkill', async ({ skillsUrl, path, content }, { requestId }) => {
  return requestAcknowledged(`${skillsUrl}/content`, {
    method: 'PUT',
    body: { path, content },
    requestId,
  });
});

// Fetch one skill's full body on demand (list items carry metadata only).
export const fetchSkillContent = createAcknowledgedThunk('uiData/fetchSkillContent', async ({ skillsUrl, path }, { requestId }) => {
  const acknowledgement = await requestAcknowledged(
    withQuery(`${skillsUrl}/content`, { path }),
    { requestId },
  );
  return { ...acknowledgement, path };
});

// Create a new skill file under <agent>/<name>/SKILL.md. The backend broadcasts
// `skills.snapshot`; the acknowledgement triggers the snapshot event
// which reconciles state.
export const createSkill = createAcknowledgedThunk('uiData/createSkill', async ({ skillsUrl, agent, name, content }, { requestId }) => {
  return requestAcknowledged(skillsUrl, {
    method: 'POST',
    body: { agent, name, content },
    requestId,
  });
});

export const eventHandlers = {
  'skills.snapshot': (state, payload) => {
    applySkills(state, payload.skills, payload.request_id || null);
  },
  'skill.content.updated': (state, payload) => {
    if (typeof payload?.path === 'string' && payload.path) {
      state.skills.contentByPath[payload.path] = payload;
    }
  },
};

export function buildExtraReducers(builder) {
  return addRequestLifecycle(builder, fetchSkills, {
    select: (state) => state.skills,
    errorMessage: 'Failed to load skills',
  });
}

export const selectSkills = (state) => state.uiData.skills.items;
export const selectSkillsState = (state) => state.uiData.skills;
export const selectSkillContentEntry = (state, path) => (
  path ? state.uiData.skills.contentByPath[path] || null : null
);
