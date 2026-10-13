import { describe, expect, it } from 'vitest';
import reducer, { applyUiEvent } from '../uiDataSlice';

function populated() {
  let state = reducer(undefined, { type: 'init' });
  state = reducer(state, applyUiEvent({ type: 'knowledge.snapshot', payload: { bases: [{ id: 'one', index_status: 'indexing' }, { id: 'two', index_status: 'indexed' }] } }));
  return state;
}

describe('knowledge index events', () => {
  it('patches the matching base status on index events', () => {
    let state = populated();
    state = reducer(state, applyUiEvent({ type: 'knowledge.index.progress', payload: { base_id: 'one', status: 'indexing', pages: 10, indexed_pages: 3, error: '' } }));
    expect(state.knowledge.bases[0].index_status).toBe('indexing');
    expect(state.knowledge.bases[0].index_pages).toBe(10);
    expect(state.knowledge.bases[0].indexed_pages).toBe(3);
    expect(state.knowledge.bases[1].index_status).toBe('indexed');
  });

  it('marks a base as failed on knowledge.index.failed', () => {
    let state = populated();
    state = reducer(state, applyUiEvent({ type: 'knowledge.index.failed', payload: { base_id: 'one', status: 'failed', error: 'embedder missing' } }));
    expect(state.knowledge.bases[0].index_status).toBe('failed');
    expect(state.knowledge.bases[0].index_error).toBe('embedder missing');
  });

  it('stores search results by query', () => {
    let state = populated();
    state = reducer(state, applyUiEvent({ type: 'knowledge.search.results', payload: { query: 'raft', results: [{ base_id: 'two', path: 'raft.md', title: 'Raft' }], error: null } }));
    expect(state.knowledge.searchResultsByQuery.raft.results).toHaveLength(1);
    expect(state.knowledge.searchResultsByQuery.raft.error).toBeNull();
  });

  it('stores search error when present', () => {
    let state = populated();
    state = reducer(state, applyUiEvent({ type: 'knowledge.search.results', payload: { query: 'x', results: [], error: 'not configured' } }));
    expect(state.knowledge.searchResultsByQuery.x.error).toBe('not configured');
  });
});
