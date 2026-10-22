import { describe, expect, it } from 'vitest';
import reducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';

describe('search results event', () => {
  it('stores sections by query', () => {
    let state = reducer(undefined, { type: 'init' });
    state = reducer(state, applyUiEvent({
      type: 'search.results',
      payload: {
        query: 'raft',
        sections: [
          { id: 'inbox', label: 'Inbox', results: [{ id: 'm1', title: 'Raft consensus', to: '/inbox/m1' }] },
        ],
      },
    }));
    expect(state.search.resultsByQuery.raft.sections).toHaveLength(1);
    expect(state.search.resultsByQuery.raft.sections[0].label).toBe('Inbox');
  });

  it('stores empty sections', () => {
    let state = reducer(undefined, { type: 'init' });
    state = reducer(state, applyUiEvent({
      type: 'search.results',
      payload: { query: 'xyz', sections: [] },
    }));
    expect(state.search.resultsByQuery.xyz.sections).toHaveLength(0);
  });
});
