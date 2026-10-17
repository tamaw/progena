import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
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
 *
 * `portal`: renders the panel into document.body, fixed-positioned from the
 * trigger's live getBoundingClientRect() instead of CSS position:absolute.
 * Required inside any CSS-transformed ancestor (React Flow's canvas pans/
 * zooms nodes via `transform`) - an absolutely-positioned child inherits
 * that same transform/scale and renders tiny, huge, or clipped. A native
 * <select>'s popup doesn't have this problem (OS-rendered, ignores CSS
 * transforms entirely), which is why it silently worked before. Not the
 * default because it costs a rect measurement + doesn't live-track canvas
 * panning while open (closes safely instead - see the pan/zoom effect).
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
  portal = false,
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(-1);
  const [panelRect, setPanelRect] = useState(null);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const panelRef = useRef(null);
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
      const inRoot = rootRef.current && rootRef.current.contains(event.target);
      const inPanel = panelRef.current && panelRef.current.contains(event.target);
      if (!inRoot && !inPanel) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handlePointer);
    return () => document.removeEventListener('mousedown', handlePointer);
  }, [isOpen]);

  // Portal mode only: the trigger may sit inside a CSS-transformed ancestor
  // (e.g. a React Flow node) whose pan/zoom doesn't fire scroll/resize
  // events. Rather than silently drifting out of place, close on any wheel/
  // pointer activity outside the panel while open - cheap and correct.
  useEffect(() => {
    if (!isOpen || !portal) {
      return undefined;
    }
    const handleReposition = (event) => {
      if (panelRef.current && panelRef.current.contains(event.target)) {
        return;
      }
      setIsOpen(false);
    };
    window.addEventListener('wheel', handleReposition, { passive: true });
    window.addEventListener('resize', handleReposition);
    return () => {
      window.removeEventListener('wheel', handleReposition);
      window.removeEventListener('resize', handleReposition);
    };
  }, [isOpen, portal]);

  useLayoutEffect(() => {
    if (!isOpen) {
      // A portal panel is remounted on every open. Clear its previous
      // constrained geometry before the next mount is measured.
      setPanelRect(null);
      return;
    }
    if (!portal || !triggerRef.current || !panelRef.current) {
      return;
    }
    const rect = triggerRef.current.getBoundingClientRect();
    const margin = 8;
    const gap = 6;
    const below = Math.max(0, window.innerHeight - rect.bottom - gap - margin);
    const above = Math.max(0, rect.top - gap - margin);
    const height = panelRef.current.scrollHeight;
    const opensAbove = height > below && above > below;
    const maxHeight = opensAbove ? above : below;
    const width = Math.min(Math.max(rect.width, panelRef.current.scrollWidth), window.innerWidth - margin * 2);
    setPanelRect({
      top: opensAbove ? Math.max(margin, rect.top - gap - Math.min(height, maxHeight)) : rect.bottom + gap,
      left: Math.max(margin, Math.min(rect.left, window.innerWidth - width - margin)),
      width,
      maxHeight,
    });
  }, [isOpen, portal, filtered.length]);

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

  const panel = isOpen ? (
    <div
      className="app-listbox__panel"
      ref={panelRef}
      style={portal ? {
        position: 'fixed',
        minWidth: 0,
        top: panelRect?.top,
        left: panelRect?.left,
        width: panelRect?.width,
        maxHeight: panelRect?.maxHeight,
        overflowY: 'auto',
      } : undefined}
    >
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
  ) : null;

  return (
    <div className={`app-listbox ${className}`} ref={rootRef}>
      <button
        type="button"
        className="app-listbox__trigger"
        ref={triggerRef}
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
      {portal ? (isOpen ? createPortal(panel, document.body) : null) : panel}
    </div>
  );
}
