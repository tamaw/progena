import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { buildVirtualPaneLayout, findVirtualPaneRange, shouldCompensateVirtualScroll } from './PaneListVirtualModel';
import { createPortal } from 'react-dom';
import { usePaneCollapse, NARROW_MQ } from '../../hooks/usePaneCollapse';
import { useMenuDismiss } from '../../hooks/useMenuDismiss';

/** Provided by PaneSection; consumed by pane list rows to auto-collapse on selection. */
const PaneAutoCollapseCtx = createContext(null);

/**
 * Shell-aware pane wrapper. On narrow viewports (≤ 980px) it portals the
 * element into document.body so that position:fixed children are never
 * clipped by app-shell__content's stacking context.
 *
 * PaneListRows can auto-collapse when a row is selected on mobile.
 */
export function PaneSection({ as = 'section', className, onItemSelect, children, ...props }) {
  // Capitalised local variable required for JSX to treat it as a component ref.
  const Tag = as;
  const [narrow, setNarrow] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(NARROW_MQ).matches,
  );

  useEffect(() => {
    const mq = window.matchMedia(NARROW_MQ);
    const handler = (e) => setNarrow(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  const el = (
    <Tag className={className} {...props}>
      <PaneAutoCollapseCtx.Provider value={narrow ? onItemSelect ?? null : null}>
        {children}
      </PaneAutoCollapseCtx.Provider>
    </Tag>
  );

  if (narrow) {
    const mount = typeof document !== 'undefined' ? document.body : null;
    if (mount) return createPortal(el, mount);
  }
  return el;
}

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
  const collapseIfNarrow = useCallback(() => setCollapsed(true), [setCollapsed]);

  return (
    <PaneSection className={paneClassName} aria-label={label} onItemSelect={collapseIfNarrow}>
      {collapsed ? (
        <PaneExpandRail label={label} onExpand={() => setCollapsed(false)} />
      ) : (
        <>
          <PaneCollapseSeam label={label} onCollapse={() => setCollapsed(true)} />
          <button type="button" className="pane-mobile-scrim" aria-label={`Close ${label} list`} onClick={() => setCollapsed(true)} />
          {children}
        </>
      )}
    </PaneSection>
  );
}

export function PaneEmptyState({ children, detail = false, className = '', ...props }) {
  return (
    <div
      className={`pane-empty-state pane-empty-state--centered ${detail ? 'pane-empty-state--detail' : ''} ${className}`.trim()}
      {...props}
    >
      {children}
    </div>
  );
}

// `filters` items may be plain strings (value === label) or `{ value, label }`
// objects when the stored filter value differs from its display text. The
// optional `renderFilter(item)` lets a page decorate a tab (e.g. an unread dot).
export function PaneListFilters({ filters, activeFilter, onFilterChange, renderFilter, actionLabel, onAction, actionDisabled = false }) {
  // Mirror PaneListScroll: on narrow viewports the action (e.g. "+ New") also
  // collapses the pane so the new item's content is immediately visible.
  const collapseIfNarrow = useContext(PaneAutoCollapseCtx);
  const handleAction = () => { onAction?.(); collapseIfNarrow?.(); };
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
        <button type="button" className="chat-pane-action new-task sess-list__filters-action" onClick={handleAction} disabled={actionDisabled}>
          {actionLabel}
        </button>
      ) : null}
    </div>
  );
}

export function PaneListToolbar({ children, actionLabel, onAction }) {
  // See PaneListFilters: collapse the pane on narrow viewports when the action fires.
  const collapseIfNarrow = useContext(PaneAutoCollapseCtx);
  const handleAction = () => { onAction?.(); collapseIfNarrow?.(); };
  return (
    <div className="sess-list__tools">
      {children}
      {actionLabel ? (
        <button type="button" className="chat-pane-action new-task sess-list__tools-action" onClick={handleAction}>
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
      <input value={value} type="text" aria-label={placeholder} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
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

  useMenuDismiss(open, wrapRef, () => setOpen(false));

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

function PaneListGroupHeader({ title, count }) {
  return (
    <div className="sess-group__head">
      {title} <span className="muted">· {count}</span>
    </div>
  );
}


/**
 * Render grouped or ungrouped pane rows. Callers that provide `virtual` get a
 * fixed-height positional layout; every other pane remains in normal flow.
 */
export function PaneListRows({
  groups,
  items,
  getGroupItems = (group) => group.items || [],
  getGroupKey = (group, index) => group.key || group.id || index,
  getGroupTitle = (group) => group.title ?? group.label ?? null,
  getItemKey = (item, index) => item.id || item.path || index,
  renderItem,
  renderGroupHeader = (group, groupItems) => (
    <PaneListGroupHeader title={getGroupTitle(group)} count={groupItems.length} />
  ),
  className = '',
  onScroll,
  virtual = null,
}) {
  const collapseIfNarrow = useContext(PaneAutoCollapseCtx);
  const scrollRef = useRef(null);
  const appliedRowDeltaSequenceRef = useRef(null);
  const rangeTimerRef = useRef(null);
  const [viewport, setViewport] = useState({ top: 0, height: 0 });
  const rows = useMemo(() => {
    if (Array.isArray(groups)) {
      return groups.flatMap((group, groupIndex) => {
        const groupItems = getGroupItems(group);
        const groupKey = String(getGroupKey(group, groupIndex));
        const title = getGroupTitle(group);
        const header = title == null
          ? []
          : [{
            key: `group:${groupKey}`,
            type: 'group',
            group,
            groupItems,
          }];
        return [
          ...header,
          ...groupItems.map((item, itemIndex) => ({
            key: `item:${groupKey}:${String(getItemKey(item, itemIndex))}`,
            type: 'item',
            item,
            itemIndex,
          })),
        ];
      });
    }
    return (Array.isArray(items) ? items : []).map((item, index) => ({
      key: `item:${String(getItemKey(item, index))}`,
      type: 'item',
      item,
      itemIndex: index,
    }));
  }, [getGroupItems, getGroupKey, getGroupTitle, getItemKey, groups, items]);
  const virtualLayout = useMemo(() => (
    virtual
      ? buildVirtualPaneLayout({
        total: virtual.total,
        rowHeight: virtual.rowHeight,
        headerHeight: virtual.headerHeight,
        groups: virtual.groups,
      })
      : null
  ), [virtual]);
  const virtualRange = useMemo(() => (
    virtualLayout
      ? findVirtualPaneRange(virtualLayout, viewport.top, viewport.height, virtual.overscan ?? virtual.rowHeight * 6)
      : { start: 0, end: 0 }
  ), [viewport.height, viewport.top, virtual, virtualLayout]);
  const onRangeNeeded = virtual?.onRangeNeeded;
  const rowDelta = virtual?.rowDelta;
  const virtualRowHeight = virtual?.rowHeight;
  const refreshKey = virtual?.refreshKey;


  const syncViewport = useCallback((element) => {
    if (!element) return;
    setViewport({ top: element.scrollTop, height: element.clientHeight });
  }, []);

  useEffect(() => {
    if (!virtual || !scrollRef.current) return undefined;
    const element = scrollRef.current;
    syncViewport(element);
    const observer = new ResizeObserver(() => syncViewport(element));
    observer.observe(element);
    return () => observer.disconnect();
  }, [syncViewport, virtual]);

  useEffect(() => {
    if (!onRangeNeeded || virtualRange.end <= virtualRange.start) return undefined;
    window.clearTimeout(rangeTimerRef.current);
    rangeTimerRef.current = window.setTimeout(() => {
      onRangeNeeded(virtualRange.start, virtualRange.end);
    }, 25);
    return () => window.clearTimeout(rangeTimerRef.current);
  }, [onRangeNeeded, virtualRange.end, virtualRange.start]);
  // A refresh (e.g. after a reconnect) re-requests the visible rows once per
  // key change. Reading the callback and range through a ref keeps a new
  // callback identity, which follows every load-state change, from forcing
  // another refresh and looping.
  const latestRangeRef = useRef({ onRangeNeeded, range: virtualRange });
  const previousRefreshRef = useRef(refreshKey);
  useEffect(() => {
    latestRangeRef.current = { onRangeNeeded, range: virtualRange };
  }, [onRangeNeeded, virtualRange]);
  useEffect(() => {
    const previous = previousRefreshRef.current;
    previousRefreshRef.current = refreshKey;
    if (previous === undefined || previous === refreshKey) return;
    const { onRangeNeeded: request, range } = latestRangeRef.current;
    if (!refreshKey || !request || range.end <= range.start) return;
    request(range.start, range.end, true);
  }, [refreshKey]);

  useEffect(() => {
    const element = scrollRef.current;
    if (!rowDelta || rowDelta.sequence === appliedRowDeltaSequenceRef.current) return;
    const shouldCompensate = shouldCompensateVirtualScroll({
      rowDelta,
      lastSequence: appliedRowDeltaSequenceRef.current,
      scrollTop: element?.scrollTop ?? 0,
      rangeStart: virtualRange.start,
    });
    appliedRowDeltaSequenceRef.current = rowDelta.sequence;
    if (!element || !shouldCompensate) return;
    element.scrollTop = Math.max(0, element.scrollTop + (rowDelta.amount * virtualRowHeight));
  }, [rowDelta, virtualRange.start, virtualRowHeight]);

  const handleScroll = useCallback((event) => {
    if (virtual) syncViewport(event.currentTarget);
    onScroll?.(event);
  }, [onScroll, syncViewport, virtual]);

  if (virtual && virtualLayout) {
    const visibleRows = [];
    for (let index = virtualRange.start; index < virtualRange.end; index += 1) {
      const group = virtualLayout.groups.find((entry) => index >= entry.rowStart && index < entry.rowEnd);
      if (!group) continue;
      const top = group.rowsTop + ((index - group.rowStart) * virtual.rowHeight);
      const item = virtual.rowAt(index);
      visibleRows.push(
        <div
          key={`item:${index}:${item ? getItemKey(item, index) : 'placeholder'}`}
          className="pane-list-row pane-list-row--virtual"
          style={{ top, height: virtual.rowHeight }}
        >
          {item
            ? renderItem(item, index)
            : virtual.renderPlaceholder?.(index) ?? <div className="pane-list-row__placeholder" aria-hidden="true" />}
        </div>,
      );
    }
    const visibleHeaders = virtualLayout.groups
      .filter((group) => group.hasHeader && group.headerTop < viewport.top + viewport.height + (virtual.overscan ?? 0) && group.rowsTop > viewport.top - (virtual.overscan ?? 0))
      .map((group) => (
        <div
          key={`group:${group.key}`}
          className="pane-list-row pane-list-row--virtual pane-list-row--group"
          style={{ top: group.headerTop, height: virtual.headerHeight }}
        >
          {renderGroupHeader({ key: group.key, title: group.title, count: group.count }, { length: group.count })}
        </div>
      ));
    return (
      <div
        ref={scrollRef}
        className={`sess-list__scroll ${className}`.trim()}
        onClick={collapseIfNarrow ?? undefined}
        onScroll={handleScroll}
      >
        <div className="pane-list-rows pane-list-rows--virtual" style={{ height: virtualLayout.height }}>
          {visibleHeaders}
          {visibleRows}
        </div>
      </div>
    );
  }

  return (
    <div
      className={`sess-list__scroll ${className}`.trim()}
      onClick={collapseIfNarrow ?? undefined}
      onScroll={handleScroll}
    >
      <div className="pane-list-rows">
        {rows.map((row) => (
          <div key={row.key} className="pane-list-row">
            {row.type === 'group'
              ? renderGroupHeader(row.group, row.groupItems)
              : renderItem(row.item, row.itemIndex)}
          </div>
        ))}
      </div>
    </div>
  );
}

export function PaneListFooter({ children }) {
  return <div className="sess-list__foot">{children}</div>;
}
