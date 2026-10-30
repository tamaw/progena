import { UI_EVENT } from '../../realtime/eventVocabulary';
/**
 * Repos domain: declared repositories (repo:<id> documents) merged with the
 * heartbeat-derived git scan, kept live by `repositories.snapshot` and
 * `repo.detail.updated` events on /api/ui/stream. The list carries each
 * entry's declared fields (name/local_path/access/...) plus its latest scan
 * (branch/dirty_count/commits/...); full branch/commit/tree detail for the
 * selected repo arrives on demand via `repo.detail.updated`.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from '../../store/async';

export const initialState = {
  repos: {
    items: [],
    workspaceSignal: {},
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
    detailById: {},
  },
};

// Acknowledge a repos-list request; the snapshot arrives on the realtime
// stream as `repositories.snapshot` (the GET just triggers the broadcast).
export const fetchRepos = createAcknowledgedThunk('uiData/fetchRepos', async ({ reposUrl }, { requestId }) => {
  return requestAcknowledged(reposUrl, { requestId });
});

// Fetch one repo's full detail (branches/commits/tree) on demand.
export const fetchRepoDetail = createAcknowledgedThunk('uiData/fetchRepoDetail', async ({ reposUrl, repoId }, { requestId }) => {
  return requestAcknowledged(`${reposUrl}/${encodeURIComponent(repoId)}/detail`, { requestId });
});

export const updateRepo = createAcknowledgedThunk('uiData/updateRepo', async ({ reposUrl, repoId, patch }, { requestId }) => {
  return requestAcknowledged(`${reposUrl}/${encodeURIComponent(repoId)}`, {
    method: 'PATCH',
    body: patch,
    requestId,
  });
});

export const deleteRepo = createAcknowledgedThunk('uiData/deleteRepo', async ({ reposUrl, repoId }, { requestId }) => {
  return requestAcknowledged(`${reposUrl}/${encodeURIComponent(repoId)}`, {
    method: 'DELETE',
    requestId,
  });
});

// On-demand "Fetch": runs `git fetch` against the repo's remote and merges a
// fresh scan into the repo_scan document server-side (see
// src/backend/repositories/service.py:fetch_and_rescan_repo), instead of waiting out
// the heartbeat's own scan interval. Updated branch/upstream state arrives
// on `repositories.snapshot` + `repo.detail.updated`, same as every other
// write here.
export const fetchRepoUpstream = createAcknowledgedThunk('uiData/fetchRepoUpstream', async ({ reposUrl, repoId }, { requestId }) => {
  return requestAcknowledged(`${reposUrl}/${encodeURIComponent(repoId)}/fetch`, {
    method: 'POST',
    requestId,
  });
});

export const eventHandlers = {
  [UI_EVENT.REPOSITORIES_SNAPSHOT]: (state, payload) => {
    state.repos.items = Array.isArray(payload.items) ? payload.items : [];
    state.repos.workspaceSignal = payload.workspace_signal && typeof payload.workspace_signal === 'object'
      ? payload.workspace_signal
      : {};
    state.repos.loaded = true;
    if (!state.repos.loading || payload.request_id === state.repos.requestId) {
      state.repos.loading = false;
      state.repos.error = null;
      state.repos.requestId = null;
    }
  },
  [UI_EVENT.REPO_DETAIL_UPDATED]: (state, payload) => {
    if (typeof payload?.id === 'string' && payload.id) {
      state.repos.detailById[payload.id] = payload;
    }
  },
};

export function buildExtraReducers(builder) {
  return addRequestLifecycle(builder, fetchRepos, {
    select: (state) => state.repos,
    errorMessage: 'Failed to load repositories',
  });
}

export const selectRepos = (state) => state.uiData.repos.items;
export const selectReposState = (state) => state.uiData.repos;
export const selectWorkspaceSignal = (state) => state.uiData.repos.workspaceSignal;
export const selectRepoDetail = (state, repoId) => (
  repoId ? state.uiData.repos.detailById[repoId] || null : null
);
