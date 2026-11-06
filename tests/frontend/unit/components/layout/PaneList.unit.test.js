import { describe, expect, it } from 'vitest';
import { buildVirtualPaneLayout, findVirtualPaneRange } from '@frontend/components/layout/PaneListVirtualModel';

describe('virtual pane layout', () => {
  it('accounts for headers in full-height grouped layout', () => {
    const layout = buildVirtualPaneLayout({
      total: 3,
      rowHeight: 10,
      headerHeight: 5,
      groups: [
        { key: 'first', title: 'First', count: 2 },
        { key: 'empty', title: 'Empty', count: 0 },
        { key: 'later', title: 'Later', count: 1 },
      ],
    });

    expect(layout.height).toBe(40);
    expect(layout.groups).toMatchObject([
      { key: 'first', rowStart: 0, rowEnd: 2, headerTop: 0, rowsTop: 5 },
      { key: 'later', rowStart: 2, rowEnd: 3, headerTop: 25, rowsTop: 30 },
    ]);
  });

  it('finds only data rows intersecting a scrolled viewport', () => {
    const layout = buildVirtualPaneLayout({
      total: 4,
      rowHeight: 10,
      headerHeight: 5,
      groups: [{ key: 'all', title: 'All', count: 4 }],
    });

    expect(findVirtualPaneRange(layout, 14, 12)).toEqual({ start: 0, end: 3 });
    expect(findVirtualPaneRange(layout, 26, 8)).toEqual({ start: 2, end: 3 });
  });
});
