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
