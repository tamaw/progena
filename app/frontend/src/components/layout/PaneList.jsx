import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { usePaneCollapse, NARROW_MQ } from './usePaneCollapse';

/** Provided by PaneSection; consumed by PaneListVirtualized to auto-collapse on item selection. */
const PaneAutoCollapseCtx = createContext(null);

/**
 * Shell-aware pane wrapper. On narrow viewports (≤ 980px) it portals the
 * element into document.body so that position:fixed children are never
 * clipped by app-shell__content's stacking context.
 *
 * PaneListVirtualized can auto-collapse when an item is selected on mobile.
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

const DEFAULT_VIRTUAL_ITEM_HEIGHT = 72;
const DEFAULT_VIRTUAL_GROUP_HEIGHT = 30;
const DEFAULT_VIRTUAL_OVERSCAN = 8;

function VirtualGroupHeader({ title, count }) {
  return (
    <div className="sess-group__head">
      {title} <span className="muted">· {count}</span>
    </div>
  );
}

/**
 * Render only the rows near the scroll viewport. `groups` keeps the existing
 * master-detail page grouping model; `items` handles an ungrouped card list.
 * Row heights are measured after mount so wrapped cards remain safe while the
 * initial estimate keeps the scrollbar stable before measurement.
 */
export function PaneListVirtualized({
  groups,
  items,
  getGroupItems = (group) => group.items || [],
  getGroupKey = (group, index) => group.key || group.id || index,
  getGroupTitle = (group) => group.title ?? group.label ?? null,
  getItemKey = (item, index) => item.id || item.path || index,
  renderItem,
  renderGroupHeader = (group, groupItems) => (
    <VirtualGroupHeader title={getGroupTitle(group)} count={groupItems.length} />
  ),
  estimatedItemHeight = DEFAULT_VIRTUAL_ITEM_HEIGHT,
  estimatedGroupHeight = DEFAULT_VIRTUAL_GROUP_HEIGHT,
  overscan = DEFAULT_VIRTUAL_OVERSCAN,
  className = '',
}) {
  const collapseIfNarrow = useContext(PaneAutoCollapseCtx);
  const scrollRef = useRef(null);
  const rowElementsRef = useRef(new Map());
  const heightsRef = useRef(new Map());
  const [viewport, setViewport] = useState({ height: 640, top: 0 });
  const [, setHeightRevision] = useState(0);

  const rows = useMemo(() => {
    if (Array.isArray(groups)) {
      return groups.flatMap((group, groupIndex) => {
        const groupItems = getGroupItems(group);
        const groupKey = String(getGroupKey(group, groupIndex));
        const title = getGroupTitle(group);
        const header = title == null ? [] : [{
          key: `group:${groupKey}`,
          type: 'group',
          group,
          groupItems,
          estimatedHeight: estimatedGroupHeight,
        }];
        return [
          ...header,
          ...groupItems.map((item, itemIndex) => ({
            key: `item:${groupKey}:${String(getItemKey(item, itemIndex))}`,
            type: 'item',
            item,
            itemIndex,
            estimatedHeight: estimatedItemHeight,
          })),
        ];
      });
    }
    return (Array.isArray(items) ? items : []).map((item, index) => ({
      key: `item:${String(getItemKey(item, index))}`,
      type: 'item',
      item,
      itemIndex: index,
      estimatedHeight: estimatedItemHeight,
    }));
  }, [
    estimatedGroupHeight,
    estimatedItemHeight,
    getGroupItems,
    getGroupKey,
    getGroupTitle,
    getItemKey,
    groups,
    items,
  ]);

  const layout = (() => {
    let top = 0;
    const positioned = rows.map((row) => {
      const height = heightsRef.current.get(row.key) || row.estimatedHeight;
      const positionedRow = { ...row, top, height };
      top += height;
      return positionedRow;
    });
    return { rows: positioned, totalHeight: top };
  })();

  const visibleRows = useMemo(() => {
    const min = Math.max(0, viewport.top - (estimatedItemHeight * overscan));
    const max = viewport.top + viewport.height + (estimatedItemHeight * overscan);
    return layout.rows.filter((row) => row.top + row.height >= min && row.top <= max);
  }, [estimatedItemHeight, layout.rows, overscan, viewport.height, viewport.top]);

  useEffect(() => {
    const scrollArea = scrollRef.current;
    if (!scrollArea) return undefined;

    const syncViewport = () => {
      setViewport({
        height: scrollArea.clientHeight || 640,
        top: scrollArea.scrollTop || 0,
      });
    };
    syncViewport();
    window.addEventListener('resize', syncViewport);
    return () => window.removeEventListener('resize', syncViewport);
  }, []);

  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver((entries) => {
      let changed = false;
      entries.forEach((entry) => {
        // Observe the row's rendered content, not the wrapper: the wrapper
        // carries an inline `minHeight` floor (the current estimate), so
        // measuring it directly can only ever grow a row, never shrink it
        // back down when the real content is shorter than the estimate.
        const key = entry.target.closest('[data-virtual-row-key]')?.dataset.virtualRowKey;
        const nextHeight = Math.ceil(entry.borderBoxSize?.[0]?.blockSize || entry.contentRect.height);
        if (key && nextHeight > 0 && heightsRef.current.get(key) !== nextHeight) {
          heightsRef.current.set(key, nextHeight);
          changed = true;
        }
      });
      if (changed) setHeightRevision((value) => value + 1);
    });
    visibleRows.forEach((row) => {
      const element = rowElementsRef.current.get(row.key);
      const target = element?.firstElementChild || element;
      if (target) observer.observe(target);
    });
    return () => observer.disconnect();
  }, [visibleRows]);

  const handleScroll = (event) => {
    setViewport({
      height: event.currentTarget.clientHeight || 640,
      top: event.currentTarget.scrollTop || 0,
    });
  };

  return (
    <div
      ref={scrollRef}
      className={`sess-list__scroll ${className}`.trim()}
      onClick={collapseIfNarrow ?? undefined}
      onScroll={handleScroll}
    >
      <div className="pane-list-virtualized" style={{ height: `${layout.totalHeight}px` }}>
        {visibleRows.map((row) => (
          <div
            key={row.key}
            ref={(element) => {
              if (element) rowElementsRef.current.set(row.key, element);
              else rowElementsRef.current.delete(row.key);
            }}
            data-virtual-row-key={row.key}
            className="pane-list-virtualized__row"
            style={{ top: `${row.top}px`, minHeight: `${row.height}px` }}
          >
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
