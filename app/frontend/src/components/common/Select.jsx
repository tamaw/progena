import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { ChevronDownIcon } from '../layout/navIcons';

/**
 * Fully custom, themeable dropdown - the default going forward for any
 * select where the option list itself needs to be styled (a native
 * <select>'s popup is OS-rendered; see forms.css's .app-select comment,
 * which openly says as much: "the options popup is native"). Same
 * floating-panel visual language as the tooltip system and the existing
 * WorkspaceSwitcher menu (bg-secondary/border/shadow-md), generalized into
 * a reusable component with real keyboard nav and optional search - neither
 * of which WorkspaceSwitcher has today.
 *
 * `options`: [{ value, label, icon? }]. `searchable` shows a filter input
 * inside the panel - turn it on for any list long enough that scrolling
 * to find an entry is worse than typing a few letters (e.g. timezones).
 */
export default function Select({
  value,
  onChange,
  options,
  placeholder = 'Select…',
  searchable = false,
  ariaLabel,
  className = '',
  disabled = false,
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(-1);
  const rootRef = useRef(null);
  const searchRef = useRef(null);
  const listRef = useRef(null);
  const listboxId = useId();

  const filtered = useMemo(() => {
    if (!searchable || !query.trim()) {
      return options;
    }
    const needle = query.trim().toLowerCase();
    return options.filter((opt) => opt.label.toLowerCase().includes(needle));
  }, [options, query, searchable]);

  const selected = options.find((opt) => opt.value === value);

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }
    const handlePointer = (event) => {
      if (rootRef.current && !rootRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handlePointer);
    return () => document.removeEventListener('mousedown', handlePointer);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    setQuery('');
    const selectedIndex = options.findIndex((opt) => opt.value === value);
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0);
    const focusTarget = searchable ? searchRef.current : listRef.current;
    // Popup just mounted; move focus into it once the DOM node exists.
    requestAnimationFrame(() => focusTarget?.focus());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen || activeIndex < 0) {
      return;
    }
    listRef.current?.querySelector(`[data-index="${activeIndex}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [isOpen, activeIndex]);

  const commit = useCallback(
    (nextValue) => {
      onChange(nextValue);
      setIsOpen(false);
    },
    [onChange],
  );

  const handlePanelKeyDown = useCallback(
    (event) => {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setActiveIndex((current) => Math.min(current + 1, filtered.length - 1));
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        setActiveIndex((current) => Math.max(current - 1, 0));
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (filtered[activeIndex]) {
          commit(filtered[activeIndex].value);
        }
      } else if (event.key === 'Escape') {
        event.preventDefault();
        setIsOpen(false);
      }
    },
    [filtered, activeIndex, commit],
  );

  return (
    <div className={`app-listbox ${className}`} ref={rootRef}>
      <button
        type="button"
        className="app-listbox__trigger"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => setIsOpen((current) => !current)}
        onKeyDown={(event) => {
          if (!isOpen && (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault();
            setIsOpen(true);
          }
        }}
      >
        <span className="app-listbox__trigger-label">{selected ? selected.label : placeholder}</span>
        <span className={`app-listbox__chevron ${isOpen ? 'is-open' : ''}`} aria-hidden="true">
          <ChevronDownIcon />
        </span>
      </button>
      {isOpen ? (
        <div className="app-listbox__panel">
          {searchable ? (
            <input
              ref={searchRef}
              className="app-listbox__search"
              value={query}
              placeholder="Filter…"
              onChange={(event) => {
                setQuery(event.target.value);
                setActiveIndex(0);
              }}
              onKeyDown={handlePanelKeyDown}
            />
          ) : null}
          <ul
            ref={listRef}
            className="app-listbox__list"
            role="listbox"
            id={listboxId}
            aria-label={ariaLabel}
            tabIndex={searchable ? -1 : 0}
            onKeyDown={searchable ? undefined : handlePanelKeyDown}
          >
            {filtered.length === 0 ? <li className="app-listbox__empty">No matches</li> : null}
            {filtered.map((opt, index) => (
              <li key={opt.value} role="option" aria-selected={opt.value === value} data-index={index}>
                <button
                  type="button"
                  className={`app-listbox__option ${opt.value === value ? 'is-selected' : ''} ${index === activeIndex ? 'is-active' : ''}`}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => commit(opt.value)}
                >
                  {opt.icon ? <span className="app-listbox__option-icon" aria-hidden="true">{opt.icon}</span> : null}
                  <span className="app-listbox__option-label">{opt.label}</span>
                  {opt.value === value ? <span className="app-listbox__check" aria-hidden="true">✓</span> : null}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
