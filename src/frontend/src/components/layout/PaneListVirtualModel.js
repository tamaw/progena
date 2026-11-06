export function buildVirtualPaneLayout({ total = 0, rowHeight, headerHeight = 0, groups }) {
  const normalizedTotal = Math.max(0, Number.isFinite(total) ? total : 0);
  const normalizedGroups = Array.isArray(groups) && groups.length
    ? groups
    : [{ key: 'items', title: null, count: normalizedTotal }];
  let rowStart = 0;
  let top = 0;
  const layoutGroups = normalizedGroups
    .map((group, index) => {
      const count = Math.max(0, Number.isFinite(group?.count) ? group.count : 0);
      const hasHeader = group?.title != null && count > 0;
      const entry = {
        key: String(group?.key ?? index),
        title: group?.title ?? null,
        count,
        rowStart,
        rowEnd: rowStart + count,
        headerTop: top,
        rowsTop: top + (hasHeader ? headerHeight : 0),
        hasHeader,
      };
      rowStart += count;
      top = entry.rowsTop + (count * rowHeight);
      return entry;
    })
    .filter((group) => group.count > 0);
  return { groups: layoutGroups, height: top, total: rowStart, rowHeight };
}

export function findVirtualPaneRange(layout, scrollTop, viewportHeight, overscan = 0) {
  const startY = Math.max(0, scrollTop - overscan);
  const endY = Math.max(startY, scrollTop + viewportHeight + overscan);
  let start = null;
  let end = null;
  for (const group of layout.groups) {
    const rowsEnd = group.rowsTop + ((group.rowEnd - group.rowStart) * layout.rowHeight);
    if (rowsEnd <= startY || group.rowsTop >= endY) continue;
    const first = Math.max(group.rowStart, group.rowStart + Math.floor((startY - group.rowsTop) / layout.rowHeight));
    const last = Math.min(group.rowEnd, group.rowStart + Math.ceil((endY - group.rowsTop) / layout.rowHeight));
    start = start == null ? first : Math.min(start, first);
    end = end == null ? last : Math.max(end, last);
  }
  return start == null ? { start: 0, end: 0 } : { start, end };
}

/** A positional change above the viewport preserves its visual anchor once. */
export function shouldCompensateVirtualScroll({ rowDelta, lastSequence, scrollTop, rangeStart }) {
  return Boolean(
    rowDelta
    && rowDelta.sequence !== lastSequence
    && scrollTop > 0
    && rowDelta.index < rangeStart
  );
}
