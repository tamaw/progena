import { useCallback, useEffect, useRef } from 'react';
const SEARCH_DEBOUNCE_MS = 250;

function hasInFlightPage(inFlightRequests, rangeVersion, offset) {
  return Object.values(inFlightRequests)
    .some((request) => request.offset === offset && request.rangeVersion === rangeVersion);
}

/**
 * Page requests for a lazily scrolled `PaneList` over a server-ordered list.
 * The list's slice (see `lib/positionalListCache`) keeps `view`, `loadedPages`,
 * `inFlightRequests` and `rangeVersion`; this hook decides which pages to ask for.
 *
 * - `requestRange(start, end, force)` is the pane's `onRangeNeeded`: it requests
 *   every page overlapping [start, end) that is neither loaded nor in flight.
 * - Page zero is requested whenever the stream becomes ready (a mount or a
 *   reconnect), even if the store already holds it, because row changes made
 *   while this page was not watching were never delivered. Other retained pages
 *   become stale; the pane's ordinary visible-range request reloads them.
 * - A changed `view` (search text, filter, sort) requests page zero after a
 *   short debounce.
 *
 * `fetchPage({ view, offset, limit })` dispatches the page request; keep it
 * referentially stable.
 */
export default function usePositionalListRange({
  list,
  view,
  sameView,
  streamReady,
  pageSize,
  fetchPage,
}) {
  const { view: listView, loadedPages, inFlightRequests, rangeVersion } = list;
  const stalePagesRef = useRef(new Set());
  const requestRange = useCallback((start, end, force = false) => {
    if (!streamReady || end <= start) return;
    const sameListView = sameView(listView, view);
    const first = Math.floor(Math.max(0, start) / pageSize) * pageSize;
    const last = Math.floor(Math.max(first, end - 1) / pageSize) * pageSize;
    for (let offset = first; offset <= last; offset += pageSize) {
      const inFlight = hasInFlightPage(inFlightRequests, rangeVersion, offset);
      const stale = stalePagesRef.current.has(offset);
      // A new view (filter, search, sort) starts at page 0; deeper ranges are
      // the old view's scroll position and wait for the new total.
      if (!sameListView && offset !== 0) continue;
      if (sameListView && (inFlight || (!force && !stale && loadedPages[offset]))) continue;
      stalePagesRef.current.delete(offset);
      fetchPage({ view, offset, limit: pageSize });
    }
  }, [
    fetchPage,
    inFlightRequests,
    listView,
    loadedPages,
    pageSize,
    rangeVersion,
    sameView,
    streamReady,
    view,
  ]);

  const hydratedRef = useRef(false);
  useEffect(() => {
    if (!streamReady) {
      hydratedRef.current = false;
      stalePagesRef.current.clear();
      return undefined;
    }
    if (!hydratedRef.current) {
      hydratedRef.current = true;
      stalePagesRef.current = new Set(
        Object.keys(loadedPages).map(Number).filter((offset) => offset !== 0),
      );
      requestRange(0, pageSize, true);
      return undefined;
    }
    if (sameView(listView, view) && (loadedPages[0] || hasInFlightPage(inFlightRequests, rangeVersion, 0))) {
      return undefined;
    }
    const timer = window.setTimeout(() => requestRange(0, pageSize), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [
    inFlightRequests,
    listView,
    loadedPages,
    pageSize,
    rangeVersion,
    requestRange,
    sameView,
    streamReady,
    view,
  ]);

  return requestRange;
}
