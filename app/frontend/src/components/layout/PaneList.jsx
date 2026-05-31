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

export function PaneList({ label, className = '', children }) {
  return (
    <section className={`${className} sess-list`.trim()} aria-label={label}>
      {children}
    </section>
  );
}

export function PaneListHeader({ title, count, actionLabel, onAction }) {
  return (
    <div className="chat-sessions-pane__head sess-list__head">
      <div className="chat-sessions-pane__title sess-list__title">{title} <span className="muted">{count}</span></div>
      {actionLabel ? <button type="button" className="chat-pane-action new-task" onClick={onAction}>{actionLabel}</button> : null}
    </div>
  );
}

export function PaneListFilters({ filters, activeFilter, onFilterChange, renderFilter }) {
  return (
    <div className="chat-pane-tabs sess-list__filters">
      {filters.map((filter) => (
        <button
          key={filter}
          type="button"
          className={`chat-pane-tab sess-filter ${activeFilter === filter ? 'is-active' : ''}`}
          onClick={() => onFilterChange(filter)}
        >
          {renderFilter ? renderFilter(filter) : filter}
        </button>
      ))}
    </div>
  );
}

export function PaneListToolbar({ children }) {
  return <div className="sess-list__tools">{children}</div>;
}

export function PaneListSearch({ value, placeholder, onChange }) {
  return (
    <label className="sess-search">
      <SearchIcon />
      <input value={value} type="text" placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

export function PaneListSortButton({ children, title = 'Sort', onClick }) {
  return (
    <button type="button" className="sess-sort" title={title} onClick={onClick}>
      {children}
      <ChevronDownIcon />
    </button>
  );
}

export function PaneListScroll({ children }) {
  return <div className="sess-list__scroll">{children}</div>;
}

export function PaneListGroup({ title, count, children }) {
  return (
    <div className="sess-group">
      <div className="sess-group__head">{title} <span className="muted">· {count}</span></div>
      {children}
    </div>
  );
}

export function PaneListFooter({ children }) {
  return <div className="sess-list__foot">{children}</div>;
}
