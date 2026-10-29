function rowKey(row) {
  return row?.kind && row?.run_id ? `${row.kind}:${row.run_id}` : '';
}

/** Keep cursor pages in server order while removing cursor-boundary duplicates. */
export function mergeRunPages(pages) {
  const rows = [];
  const seen = new Set();
  for (const page of pages) {
    for (const row of page?.runs || []) {
      const key = rowKey(row);
      if (key && !seen.has(key)) {
        seen.add(key);
        rows.push(row);
      }
    }
  }
  return rows;
}

/** Add only older, newly fetched cursor pages to an inspection held at its position. */
export function appendOlderPageRows(heldRows, pages, firstNewPage, currentRows) {
  const currentByKey = new Map(currentRows.map((row) => [rowKey(row), row]));
  const normalizedHeldRows = heldRows.map((row) => currentByKey.get(rowKey(row)) || row);
  const known = new Set(normalizedHeldRows.map(rowKey));
  const appended = [];
  for (const page of pages.slice(firstNewPage)) {
    for (const row of page?.runs || []) {
      const key = rowKey(row);
      if (key && !known.has(key)) {
        known.add(key);
        appended.push(currentByKey.get(key) || row);
      }
    }
  }
  return [...normalizedHeldRows, ...appended];
}
