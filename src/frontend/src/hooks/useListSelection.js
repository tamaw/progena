import { useMemo, useState } from 'react';

/**
 * Centralizes the filter + search + sort + selection state that every
 * "list + detail" page repeats (Agents, Channels, Models, Tasks, Knowledge…).
 *
 * Given the full `items` array and a few pure descriptor callbacks, it returns
 * the control state/setters plus the derived `visible` list, an optional
 * `groups` map, and the resolved `selected` item (selected id, or the first
 * visible item as a fallback).
 *
 * Pass STABLE callbacks (module-scope functions or memoized) for `match`,
 * `sort`, `groupBy`, and `getId` so the memoized derivations are not recomputed
 * on every render.
 *
 * @template T
 * @param {T[]} items
 * @param {{
 *   initialFilter?: string,
 *   initialSort?: string,
 *   initialSelectedId?: string | null,
 *   getId?: (item: T) => string,
 *   match?: (item: T, ctx: { filter: string, search: string }) => boolean,
 *   sort?: (a: T, b: T, sortMode: string) => number,
 *   groupBy?: (item: T) => string,
 * }} [options]
 */
export function useListSelection(items, {
  initialFilter = 'All',
  initialSort,
  initialSelectedId,
  getId = (item) => item?.id,
  match,
  sort,
  groupBy,
} = {}) {
  const [filter, setFilter] = useState(initialFilter);
  const [search, setSearch] = useState('');
  const [sortMode, setSortMode] = useState(initialSort);
  const [selectedId, setSelectedId] = useState(
    initialSelectedId ?? (items[0] ? getId(items[0]) : null),
  );

  const visible = useMemo(() => {
    let next = match ? items.filter((item) => match(item, { filter, search })) : items.slice();
    if (sort) {
      next = [...next].sort((a, b) => sort(a, b, sortMode));
    }
    return next;
  }, [items, match, sort, filter, search, sortMode]);

  const groups = useMemo(() => {
    if (!groupBy) {
      return null;
    }
    const order = [];
    const byKey = {};
    for (const item of visible) {
      const key = groupBy(item);
      if (!byKey[key]) {
        byKey[key] = [];
        order.push(key);
      }
      byKey[key].push(item);
    }
    return { order, byKey };
  }, [visible, groupBy]);

  const selected = useMemo(
    () => items.find((item) => getId(item) === selectedId) || visible[0] || null,
    [items, visible, selectedId, getId],
  );

  return {
    filter,
    setFilter,
    search,
    setSearch,
    sortMode,
    setSortMode,
    selectedId,
    setSelectedId,
    visible,
    groups,
    selected,
  };
}
