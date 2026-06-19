import { useEffect, useRef, useState } from 'react';
import { usePaneCollapse } from './usePaneCollapse';

function SearchIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <circle cx="7" cy="7" r="4.8" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M10.8 10.8 14 14" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.4" />
    </svg>
  );
}

function ChevronDownIcon() {
  return (
    <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
      <path d="m4.2 6.2 3.8 3.6 3.8-3.6" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

function ChevronLeftIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <path d="m9.8 4.2-3.6 3.8 3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <path d="m6.2 4.2 3.6 3.8-3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

export function PaneCollapseSeam({ label, onCollapse }) {
  return (
    <button
      type="button"
      className="sess-list__collapse"
      onClick={onCollapse}
      title={`Collapse ${label}`}
      aria-label={`Collapse ${label} list`}
    >
      <ChevronLeftIcon />
    </button>
  );
}

export function PaneExpandRail({ label, onExpand }) {
  return (
    <button
      type="button"
      className="sess-list__expand"
      onClick={onExpand}
      title={`Expand ${label}`}
      aria-label={`Expand ${label} list`}
    >
      <ChevronRightIcon />
      <span className="sess-list__expand-label">{label}</span>
    </button>
  );
}

export function PaneList({ label, className = '', children }) {
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse(`${className} sess-list`.trim());

  return (
    <section className={paneClassName} aria-label={label}>
      {collapsed ? (
        <PaneExpandRail label={label} onExpand={() => setCollapsed(false)} />
      ) : (
        <>
          <PaneCollapseSeam label={label} onCollapse={() => setCollapsed(true)} />
          {children}
        </>
      )}
    </section>
  );
}

// `filters` items may be plain strings (value === label) or `{ value, label }`
// objects when the stored filter value differs from its display text. The
// optional `renderFilter(item)` lets a page decorate a tab (e.g. an unread dot).
export function PaneListFilters({ filters, activeFilter, onFilterChange, renderFilter, actionLabel, onAction, actionDisabled = false }) {
  return (
    <div className="chat-pane-tabs sess-list__filters">
      {filters.map((item) => {
        const value = typeof item === 'string' ? item : item.value;
        const label = typeof item === 'string' ? item : item.label;
        return (
          <button
            key={value}
            type="button"
            className={`chat-pane-tab sess-filter ${activeFilter === value ? 'is-active' : ''}`}
            onClick={() => onFilterChange(value)}
          >
            {renderFilter ? renderFilter(item) : label}
          </button>
        );
      })}
      {actionLabel ? (
        <button type="button" className="chat-pane-action new-task sess-list__filters-action" onClick={onAction} disabled={actionDisabled}>
          {actionLabel}
        </button>
      ) : null}
    </div>
  );
}

export function PaneListToolbar({ children, actionLabel, onAction }) {
  return (
    <div className="sess-list__tools">
      {children}
      {actionLabel ? (
        <button type="button" className="chat-pane-action new-task sess-list__tools-action" onClick={onAction}>
          {actionLabel}
        </button>
      ) : null}
    </div>
  );
}

export function PaneListSearch({ value, placeholder, onChange }) {
  return (
    <label className="sess-search">
      <SearchIcon />
      <input value={value} type="text" placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

// Sort control. With `options`/`value`/`onSelect` it renders a dropdown menu of
// selectable sorts; otherwise it falls back to the plain click button other
// panes still use.
export function PaneListSortButton({ children, title = 'Sort', onClick, options, value, onSelect }) {
  if (options) {
    return <PaneListSortMenu title={title} options={options} value={value} onSelect={onSelect} />;
  }
  return (
    <button type="button" className="sess-sort" title={title} onClick={onClick}>
      {children}
      <ChevronDownIcon />
    </button>
  );
}

function PaneListSortMenu({ title, options, value, onSelect }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const items = options.map((option) => (typeof option === 'string' ? { value: option, label: option } : option));
  const current = items.find((item) => item.value === value) || items[0];

  useEffect(() => {
    if (!open) return undefined;
    const handlePointer = (event) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) {
        setOpen(false);
      }
    };
    const handleKey = (event) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointer);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handlePointer);
      document.removeEventListener('keydown', handleKey);
    };
  }, [open]);

  return (
    <div className="sess-sort-wrap" ref={wrapRef}>
      <button
        type="button"
        className="sess-sort"
        title={title}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((value_) => !value_)}
      >
        {current?.label}
        <ChevronDownIcon />
      </button>
      {open ? (
        <ul className="sess-sort-menu" role="listbox" aria-label={title}>
          {items.map((item) => (
            <li key={item.value}>
              <button
                type="button"
                role="option"
                aria-selected={item.value === current?.value}
                className={`sess-sort-menu__item ${item.value === current?.value ? 'is-active' : ''}`}
                onClick={() => { onSelect?.(item.value); setOpen(false); }}
              >
                {item.label}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function PaneListScroll({ children, className = '' }) {
  return <div className={`sess-list__scroll ${className}`.trim()}>{children}</div>;
}

export function PaneListGroup({ title, count, children, className = '' }) {
  return (
    <div className={`sess-group ${className}`.trim()}>
      <div className="sess-group__head">{title} <span className="muted">· {count}</span></div>
      {children}
    </div>
  );
}

export function PaneListFooter({ children }) {
  return <div className="sess-list__foot">{children}</div>;
}
