import { describe, expect, it } from 'vitest';
import { appendOlderPageRows, mergeRunPages } from '@frontend/features/runs/pageModel';

const row = (runId, name = runId) => ({ kind: 'workflow', run_id: runId, name });

describe('cursor run page model', () => {
  it('accumulates cursor pages in order and removes boundary duplicates', () => {
    expect(mergeRunPages([
      { runs: [row('newest'), row('shared')] },
      { runs: [row('shared', 'stale duplicate'), row('older')] },
    ])).toEqual([row('newest'), row('shared'), row('older')]);
  });

  it('retains the loaded cursor boundary when a live arrival refreshes the head page', () => {
    expect(mergeRunPages([
      { runs: [row('NEW'), row('A'), row('B')] },
      { runs: [row('C'), row('D')] },
    ])).toEqual([row('NEW'), row('A'), row('B'), row('C'), row('D')]);
  });

  it('adds only the newly fetched older page to an inspection held for live updates', () => {
    const held = [row('newest'), row('existing', 'updated')];
    const pages = [
      { runs: [row('live-new'), row('newest'), row('existing', 'updated again')] },
      { runs: [row('existing', 'cursor duplicate'), row('older')] },
    ];
    const currentRows = mergeRunPages(pages);

    expect(appendOlderPageRows(held, pages, 1, currentRows)).toEqual([
      row('newest'), row('existing', 'updated again'), row('older'),
    ]);
  });
});
