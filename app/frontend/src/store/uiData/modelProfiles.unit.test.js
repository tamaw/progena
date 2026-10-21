import { describe, expect, it } from 'vitest';
import reducer, { applyUiEvent } from '../uiDataSlice';
import { fetchModelProfiles, patchModelProfile } from './modelProfiles';

const profile = { name: 'review', provider_id: 'foundry-1', deployment: 'review-v1' };
const snapshot = (profiles, requestId) => applyUiEvent({
  type: 'profiles.snapshot', payload: { profiles, request_id: requestId },
});

describe('model profile request lifecycle', () => {
  it('requires a causal snapshot, not the HTTP acknowledgement, to finish loading', () => {
    let state = reducer(undefined, fetchModelProfiles.pending('load-a', {}));
    state = reducer(state, fetchModelProfiles.fulfilled({ request_id: 'load-a' }, 'load-a', {}));
    expect(state.modelProfiles.loaded).toBe(false);
    expect(state.modelProfiles.loading).toBe(true);
    state = reducer(state, snapshot([profile], 'load-a'));
    expect(state.modelProfiles.loading).toBe(false);
    expect(state.modelProfiles.profiles).toEqual([profile]);
  });

  it('ignores rejection from an older disconnected request after recovery started', () => {
    let state = reducer(undefined, fetchModelProfiles.pending('old', {}));
    state = reducer(state, fetchModelProfiles.pending('new', {}));
    state = reducer(state, fetchModelProfiles.rejected(new Error('disconnected'), 'old', {}));
    expect(state.modelProfiles.loading).toBe(true);
    expect(state.modelProfiles.error).toBeNull();
    state = reducer(state, snapshot([profile], 'new'));
    state = reducer(state, fetchModelProfiles.rejected(new Error('late'), 'new', {}));
    expect(state.modelProfiles.loading).toBe(false);
    expect(state.modelProfiles.error).toBeNull();
  });

  it('preserves save completion when NOTIFY snapshots follow before the acknowledgement', () => {
    let state = reducer(undefined, patchModelProfile.pending('save-a', {}));
    state = reducer(state, snapshot([{ ...profile, deployment: 'review-v2' }], 'save-a'));
    state = reducer(state, snapshot([{ ...profile, deployment: 'review-v2' }]));
    state = reducer(state, patchModelProfile.fulfilled({ request_id: 'save-a' }, 'save-a', {}));
    expect(state.modelProfiles.saveRequest.completedRequestId).toBe('save-a');
    expect(state.modelProfiles.saveRequest.loading).toBe(false);
    expect(state.modelProfiles.saveRequest.error).toBeNull();
  });

  it('does not complete another save from an unrelated snapshot and resets completion for the next save', () => {
    let state = reducer(undefined, patchModelProfile.pending('save-a', {}));
    state = reducer(state, snapshot([profile], 'other-client'));
    expect(state.modelProfiles.saveRequest.loading).toBe(true);
    expect(state.modelProfiles.saveRequest.completedRequestId).toBeNull();
    state = reducer(state, snapshot([profile], 'save-a'));
    state = reducer(state, patchModelProfile.pending('save-b', {}));
    expect(state.modelProfiles.saveRequest.completedRequestId).toBeNull();
    expect(state.modelProfiles.saveRequest.loading).toBe(true);
  });

  it('removes deleted profiles and clears stale errors on an authoritative empty snapshot', () => {
    let state = reducer(undefined, snapshot([profile]));
    state = reducer(state, fetchModelProfiles.pending('failed', {}));
    state = reducer(state, fetchModelProfiles.rejected(new Error('unavailable'), 'failed', {}));
    expect(state.modelProfiles.error).toBe('unavailable');
    state = reducer(state, snapshot([]));
    expect(state.modelProfiles.profiles).toEqual([]);
    expect(state.modelProfiles.loaded).toBe(true);
    expect(state.modelProfiles.error).toBeNull();
  });

  it('does not mark identical hydration snapshots as unseen profile changes', () => {
    let state = reducer(undefined, snapshot([profile], 'initial-hydration'));
    state = reducer(state, snapshot([profile], 'other-tab-hydration'));
    expect(state.navActivity.unseenRoutes['/profiles']).toBeUndefined();
    state = reducer(state, snapshot([{ ...profile, deployment: 'review-v2' }]));
    expect(state.navActivity.unseenRoutes['/profiles']).toBe(true);
  });
});
