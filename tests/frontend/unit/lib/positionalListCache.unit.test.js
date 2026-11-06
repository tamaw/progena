import { describe, expect, it } from 'vitest';
import { createPositionalListOps } from '@frontend/lib/positionalListCache';

const ops = createPositionalListOps(10);

function cache(rows, extra = {}) {
  return {
    rowsByIndex: Object.fromEntries(rows.map((id, index) => [index, id])),
    loadedPages: {},
    inFlightRequests: {},
    rangeVersion: 0,
    loading: false,
    rowDelta: null,
    ...extra,
  };
}

describe('positional list cache', () => {
  it('finds a loaded row by id and reports an unloaded one as null', () => {
    const list = cache(['a', 'b', 'c']);
    expect(ops.indexOfRow(list, 'b')).toBe('1');
    expect(ops.indexOfRow(list, 'zzz')).toBeNull();
  });

  it('shifts rows forward and backward without overwriting, and drops rows pushed below zero', () => {
    const forward = cache(['a', 'b', 'c']);
    ops.shiftRows(forward, 1, 1);
    expect(forward.rowsByIndex).toEqual({ 0: 'a', 2: 'b', 3: 'c' });

    const backward = cache(['a', 'b', 'c']);
    ops.shiftRows(backward, 1, -1);
    expect(backward.rowsByIndex).toEqual({ 0: 'b', 1: 'c' });

    const underflow = cache(['a', 'b']);
    ops.shiftRows(underflow, 0, -1);
    expect(underflow.rowsByIndex).toEqual({ 0: 'b' });
  });

  it('invalidates from the containing page onward, so every outstanding response becomes stale', () => {
    const list = cache(['a'], {
      loadedPages: { 0: true, 10: true, 20: true },
      inFlightRequests: { r1: { offset: 0, rangeVersion: 0 }, r2: { offset: 20, rangeVersion: 0 } },
      loading: true,
    });
    ops.invalidatePagesFrom(list, 14);

    expect(list.loadedPages).toEqual({ 0: true });
    expect(list.inFlightRequests).toEqual({});
    expect(list.loading).toBe(false);
    expect(list.rangeVersion).toBe(1);
  });

  it('removing a row closes the gap, invalidates from its page and numbers the scroll delta', () => {
    const list = cache(['a', 'b', 'c'], { loadedPages: { 0: true } });
    ops.removeRowAt(list, 1);
    ops.removeRowAt(list, 0);

    expect(list.rowsByIndex).toEqual({ 0: 'c' });
    expect(list.rowDelta).toEqual({ sequence: 2, index: 0, amount: -1 });
    expect(list.loadedPages).toEqual({});
  });

  it('inserting a row opens a slot at its position', () => {
    const list = cache(['a', 'b']);
    ops.insertRowAt(list, 1, 'new');

    expect(list.rowsByIndex).toEqual({ 0: 'a', 1: 'new', 2: 'b' });
    expect(list.rowDelta).toMatchObject({ index: 1, amount: 1 });
  });
});
