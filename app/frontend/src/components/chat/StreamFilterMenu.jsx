import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { STREAM_EVENT_CATEGORIES } from './streamFilters';

function StreamFilterMenu({ storageKey, filters, onChange }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const handler = (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

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

  const applyPreset = useCallback(
    (preset) => {
      let next;
      if (preset === 'all') {
        next = STREAM_EVENT_CATEGORIES.reduce((acc, c) => ({ ...acc, [c.id]: true }), {});
      } else if (preset === 'basic') {
        next = STREAM_EVENT_CATEGORIES.reduce(
          (acc, c) => ({ ...acc, [c.id]: c.id === 'tools' || c.id === 'steps' }),
          {},
        );
      } else {
        next = STREAM_EVENT_CATEGORIES.reduce((acc, c) => ({ ...acc, [c.id]: false }), {});
      }
      updateFilters(next);
    },
    [updateFilters],
  );

  const activeCount = useMemo(
    () => STREAM_EVENT_CATEGORIES.filter((c) => filters[c.id]).length,
    [filters],
  );

  return (
    <div className="stream-filter-menu" ref={containerRef}>
      <button
        type="button"
        className="stream-filter-toggle"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="menu"
        title="Stream event visibility"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"></polygon>
        </svg>
        <span>Events ({activeCount}/{STREAM_EVENT_CATEGORIES.length})</span>
      </button>
      {open && (
        <div className="stream-filter-dropdown" role="menu">
          <div className="stream-filter-presets">
            <button type="button" onClick={() => applyPreset('all')}>All</button>
            <button type="button" onClick={() => applyPreset('basic')}>Basic</button>
            <button type="button" onClick={() => applyPreset('none')}>None</button>
          </div>
          {STREAM_EVENT_CATEGORIES.map((c) => (
            <label key={c.id} className="stream-filter-option">
              <input
                type="checkbox"
                checked={Boolean(filters[c.id])}
                onChange={() => toggleCategory(c.id)}
              />
              <span className="stream-filter-option-label">
                <span className="stream-filter-option-title">{c.label}</span>
                <span className="stream-filter-option-desc">{c.description}</span>
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

export default StreamFilterMenu;
