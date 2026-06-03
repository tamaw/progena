import { useCallback, useMemo } from 'react';
import { STREAM_EVENT_CATEGORIES } from './streamFilters';

function StreamFilterMenu({ storageKey, filters, onChange, eventCounts }) {
  const updateFilters = useCallback(
    (next) => {
      onChange(next);
      try {
        localStorage.setItem(storageKey, JSON.stringify(next));
      } catch {
        // ignore quota / private mode failures
      }
    },
    [onChange, storageKey],
  );

  const toggleCategory = useCallback(
    (id) => {
      updateFilters({ ...filters, [id]: !filters[id] });
    },
    [filters, updateFilters],
  );

  const activeCount = useMemo(
    () => STREAM_EVENT_CATEGORIES.filter((c) => filters[c.id]).length,
    [filters],
  );
  const allActive = activeCount === STREAM_EVENT_CATEGORIES.length;

  const enableAll = useCallback(() => {
    updateFilters(STREAM_EVENT_CATEGORIES.reduce((acc, category) => ({ ...acc, [category.id]: true }), {}));
  }, [updateFilters]);

  return (
    <div className="sess-filters chat-events-filters" role="toolbar" aria-label="Event filters">
      <button
        type="button"
        className={`sess-fchip ${allActive ? 'is-active' : ''}`}
        aria-pressed={allActive}
        onClick={enableAll}
        title="Show every event category"
      >
        All
        <span className="count">{activeCount}/{STREAM_EVENT_CATEGORIES.length}</span>
      </button>
      {STREAM_EVENT_CATEGORIES.map((category) => (
        <button
          key={category.id}
          type="button"
          className={`sess-fchip ${filters[category.id] ? 'is-active' : ''}`}
          aria-pressed={Boolean(filters[category.id])}
          onClick={() => toggleCategory(category.id)}
          title={category.description}
        >
          {category.color ? <span className="adot" style={{ background: category.color }} /> : null}
          {category.label}
          {eventCounts && eventCounts[category.id] > 0 && (
            <span className="count">{eventCounts[category.id]}</span>
          )}
        </button>
      ))}
    </div>
  );
}

export default StreamFilterMenu;
