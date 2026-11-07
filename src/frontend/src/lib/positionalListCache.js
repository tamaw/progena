/**
 * Cache operations for a lazily paged, server-ordered list (the Chat sidebar,
 * the Inbox). `PaneList` renders `total` rows and asks for the pages it scrolls
 * to; the owning slice keeps those pages in a position-indexed cache and applies
 * live row changes to it with these operations.
 *
 * `cache` is the slice's list state and must hold:
 *   rowsByIndex       {number: id}  loaded row ids by absolute position
 *   loadedPages       {offset: true} pages already fetched
 *   inFlightRequests  {requestId: {offset, rangeVersion}}
 *   rangeVersion      number; a response from an older version is discarded
 *   loading           boolean
 *   rowDelta          {sequence, index, amount, end?} | null, for scroll compensation
 * The operations mutate it in place, so call them from reducers (immer drafts).
 */

/** Bind the operations to a list's page size. */
export function createPositionalListOps(pageSize) {
  const pageOffset = (index) => Math.floor(Math.max(0, index) / pageSize) * pageSize;

  /** Absolute position of a loaded row, or `null` when it is not loaded. */
  function indexOfRow(cache, id) {
    return Object.entries(cache.rowsByIndex).find(([, rowId]) => rowId === id)?.[0] ?? null;
  }

  /** Move every loaded row at or after `start` by `amount` positions. */
  function shiftRows(cache, start, amount) {
    const indexes = Object.keys(cache.rowsByIndex).map(Number)
      .filter((index) => index >= start)
      .sort((left, right) => (amount > 0 ? right - left : left - right));
    for (const index of indexes) {
      const next = index + amount;
      if (next >= 0) cache.rowsByIndex[next] = cache.rowsByIndex[index];
      delete cache.rowsByIndex[index];
    }
  }

  /**
   * Forget the pages from `index`'s page onward so they are fetched again.
   * Bumping `rangeVersion` makes every response still in flight stale, including
   * lower pages that would otherwise be kept; none of them can settle, so the
   * in-flight table is cleared with them.
   */
  function invalidatePagesFrom(cache, index) {
    const start = pageOffset(index);
    for (const page of Object.keys(cache.loadedPages)) {
      if (Number(page) >= start) delete cache.loadedPages[page];
    }
    cache.inFlightRequests = {};
    cache.loading = false;
    cache.rangeVersion += 1;
  }

  /**
   * Record a positional change so a scrolled pane can keep its place: rows
   * after `index` (up to and including `end`, when given) moved by `amount`.
   * An insertion or removal has no `end`; a move from one index to another
   * shifts only the rows between them.
   */
  function noteRowDelta(cache, index, amount, end = null) {
    cache.rowDelta = {
      sequence: (cache.rowDelta?.sequence ?? 0) + 1,
      index,
      amount,
      end,
    };
  }

  function removeRowAt(cache, index) {
    delete cache.rowsByIndex[index];
    shiftRows(cache, index + 1, -1);
    invalidatePagesFrom(cache, index);
    noteRowDelta(cache, index, -1);
  }

  function insertRowAt(cache, index, id) {
    shiftRows(cache, index, 1);
    cache.rowsByIndex[index] = id;
    invalidatePagesFrom(cache, index);
    noteRowDelta(cache, index, 1);
  }

  /** Settle a failed acknowledged request so the page can be requested again. */
  function settleInFlightRequest(cache, requestId) {
    if (!cache.inFlightRequests[requestId]) return false;
    delete cache.inFlightRequests[requestId];
    cache.loading = Object.keys(cache.inFlightRequests).length > 0 && !cache.loaded;
    return true;
  }

  return {
    pageOffset,
    indexOfRow,
    shiftRows,
    invalidatePagesFrom,
    noteRowDelta,
    removeRowAt,
    insertRowAt,
    settleInFlightRequest,
  };
}
