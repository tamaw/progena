import { describe, expect, it } from 'vitest';
import reducer from '../../store/uiDataSlice';
import { applyUiEvent } from '../../store/uiEvents';

describe('modules stream reducers', () => {
  it('hydrates the catalogue from a modules snapshot', () => {
    const state = reducer(undefined, applyUiEvent({ type: 'modules.snapshot', payload: {
      items: [{ name: 'task-graph', title: 'Task Graph', revision: 'abc123' }], request_id: null,
    } }));
    expect(state.modules.loaded).toBe(true);
    expect(state.modules.items).toEqual([{ name: 'task-graph', title: 'Task Graph', revision: 'abc123' }]);
  });

  it('removes deleted module caches and its catalogue item', () => {
    let state = reducer(undefined, applyUiEvent({ type: 'modules.snapshot', payload: {
      items: [{ name: 'one' }, { name: 'two' }],
    } }));
    state = JSON.parse(JSON.stringify(state));
    state.modules.filesByName.one = { revision: 'r1', files: {} };
    state.modules.revisionsByName.one = [{ id: '1' }];
    state = reducer(state, applyUiEvent({ type: 'module.deleted', payload: { name: 'one' } }));
    expect(state.modules.items).toEqual([{ name: 'two' }]);
    expect(state.modules.filesByName.one).toBeUndefined();
    expect(state.modules.revisionsByName.one).toBeUndefined();
  });
});
