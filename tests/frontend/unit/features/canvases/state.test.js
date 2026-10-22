import { describe, expect, it } from 'vitest';
import reducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';

describe('canvases stream reducers', () => {
  it('hydrates the catalogue from a canvases snapshot', () => {
    const state = reducer(undefined, applyUiEvent({ type: 'canvases.snapshot', payload: {
      items: [{ name: 'task-graph', title: 'Task Graph', revision: 'abc123' }], request_id: null,
    } }));
    expect(state.canvases.loaded).toBe(true);
    expect(state.canvases.items).toEqual([{ name: 'task-graph', title: 'Task Graph', revision: 'abc123' }]);
  });

  it('removes deleted canvas caches and its catalogue item', () => {
    let state = reducer(undefined, applyUiEvent({ type: 'canvases.snapshot', payload: {
      items: [{ name: 'one' }, { name: 'two' }],
    } }));
    state = JSON.parse(JSON.stringify(state));
    state.canvases.filesByName.one = { revision: 'r1', files: {} };
    state = reducer(state, applyUiEvent({ type: 'canvas.deleted', payload: { name: 'one' } }));
    expect(state.canvases.items).toEqual([{ name: 'two' }]);
    expect(state.canvases.filesByName.one).toBeUndefined();
  });
});
