import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { NODE_MENU_CATEGORIES } from '../models/nodePalette';

// Chevrons for the right tools dock / collapse seam (point toward the action:
// left = expand the panel leftward, right = collapse it away). Ported from the
// Agents/Routines canvas.

// Chevrons for the right tools dock / collapse seam (point toward the action:
// left = expand the panel leftward, right = collapse it away). Ported from the
// Agents/Routines canvas.
function ChevronLeftSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m9.8 4.2-3.6 3.8 3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

function CanvasToolIcon({ tool }) {
  if (tool === 'grab') {
    return <svg viewBox="0 0 16 16" width="17" height="17" aria-hidden="true"><path d="M4.5 8V4.2a1 1 0 0 1 2 0V7m0-3.6a1 1 0 0 1 2 0V7m0-2.8a1 1 0 0 1 2 0V7m0-1.7a1 1 0 0 1 2 0v3.2a4.5 4.5 0 0 1-9 0V8a1 1 0 0 1 2 0Z" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.2" /></svg>;
  }
  if (tool === 'cut') {
    return <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><circle cx="4" cy="12" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.3" /><circle cx="12" cy="12" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.3" /><path d="m5.3 10.7 5.4-7.4M10.7 10.7 5.3 3.3" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.3" /></svg>;
  }
  return <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path d="m3 2 9.5 5.6-5 1.3L6.2 14Z" fill="none" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.3" /></svg>;
}

export function ChevronRightSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m6.2 4.2 3.6 3.8-3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

// Bottom-center floating dock for adding nodes to the canvas — ported directly
// from the Agents/Routines canvas's GraphDock: drag onto the canvas or click
// to add at the next slot. The event node is the fixed start and isn't
// dock-addable.

export function WorkflowDock({ menuAnchor, onOpenMenu, onCloseMenu, onAdd, tool, onToolChange }) {
  const [filter, setFilter] = useState('');
  const filterRef = useRef(null);
  const dockRef = useRef(null);
  const addButtonRef = useRef(null);
  const menuRef = useRef(null);
  const menuOpen = Boolean(menuAnchor);
  const normalizedFilter = filter.trim().toLowerCase();
  const categories = NODE_MENU_CATEGORIES
    .map((category) => ({
      ...category,
      items: category.items.filter((item) => !normalizedFilter || item.label.toLowerCase().includes(normalizedFilter)),
    }))
    .filter((category) => category.items.length);

  useEffect(() => {
    if (menuOpen) {
      filterRef.current?.focus();
    }
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const closeFromOutside = (event) => {
      if (!dockRef.current?.contains(event.target)) {
        setFilter('');
        onCloseMenu();
      }
    };
    const closeFromEscape = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setFilter('');
        onCloseMenu();
        addButtonRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', closeFromOutside);
    document.addEventListener('keydown', closeFromEscape);
    return () => {
      document.removeEventListener('pointerdown', closeFromOutside);
      document.removeEventListener('keydown', closeFromEscape);
    };
  }, [menuOpen, onCloseMenu]);

  const handleAdd = (kind) => {
    onAdd(kind, menuAnchor?.flowPosition);
    setFilter('');
    onCloseMenu();
  };

  const screenPosition = menuAnchor?.screenPosition;
  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    if (!screenPosition) {
      menu.style.left = '';
      menu.style.top = '';
      return;
    }
    const dockRect = dockRef.current?.getBoundingClientRect();
    const left = Math.max(16, Math.min(screenPosition.x, window.innerWidth - 346));
    const top = Math.max(16, Math.min(screenPosition.y, window.innerHeight - 510));
    menu.style.left = `${left - (dockRect?.left || 0)}px`;
    menu.style.top = `${top - (dockRect?.top || 0)}px`;
  }, [menuOpen, screenPosition]);

  return (
    <div ref={dockRef} className="canvas-dock workflow-canvas-dock">
      {menuOpen ? (
        <div ref={menuRef} className={`workflow-node-menu${screenPosition ? ' workflow-node-menu--cursor' : ''}`} role="menu" aria-label="Add workflow node">
          <label className="workflow-node-menu__filter">
            <span className="sr-only">Filter nodes</span>
            <input ref={filterRef} type="search" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter nodes…" />
          </label>
          <div className="workflow-node-menu__categories">
            {categories.map((category) => (
              <section key={category.id} className="workflow-node-menu__category" aria-labelledby={`node-menu-${category.id}`}>
                <h3 id={`node-menu-${category.id}`}>{category.label}</h3>
                {category.items.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className="workflow-node-menu__item"
                    role="menuitem"
                    disabled={item.disabled}
                    title={item.detail}
                    onClick={() => handleAdd(item.kind)}
                  >
                    <span className={`canvas-dock-icon ${item.kind || 'event'}`}>{item.icon}</span>
                    <span>{item.label}</span>
                    {item.detail ? <small>{item.detail}</small> : null}
                  </button>
                ))}
              </section>
            ))}
            {!categories.length ? <p className="workflow-node-menu__empty">No matching nodes.</p> : null}
          </div>
        </div>
      ) : null}
      <div className="canvas-dock-shell" aria-label="Canvas toolbar">
        <button type="button" className={`canvas-toolbar-button canvas-toolbar-button--tool ${tool === 'pointer' ? 'is-active' : ''}`} onClick={() => onToolChange('pointer')} aria-pressed={tool === 'pointer'} title="Pointer: select, move, and connect">
          <CanvasToolIcon tool="pointer" />
          <span className="canvas-dock-tooltip">Pointer</span>
        </button>
        <button type="button" className={`canvas-toolbar-button canvas-toolbar-button--tool ${tool === 'grab' ? 'is-active' : ''}`} onClick={() => onToolChange('grab')} aria-pressed={tool === 'grab'} title="Grab: pan with the primary button">
          <CanvasToolIcon tool="grab" />
          <span className="canvas-dock-tooltip">Grab</span>
        </button>
        <button type="button" className={`canvas-toolbar-button canvas-toolbar-button--tool ${tool === 'cut' ? 'is-active' : ''}`} onClick={() => onToolChange('cut')} aria-pressed={tool === 'cut'} title="Cut: click a connection to remove it">
          <CanvasToolIcon tool="cut" />
          <span className="canvas-dock-tooltip">Cut connection</span>
        </button>
        <button ref={addButtonRef} type="button" className={`canvas-toolbar-button canvas-toolbar-button--add ${menuOpen && !menuAnchor?.screenPosition ? 'is-active' : ''}`} onClick={() => onOpenMenu({})} aria-expanded={menuOpen} aria-haspopup="menu" title="Add workflow node">
          <span aria-hidden="true">+</span>
          <span className="canvas-dock-tooltip">Add node</span>
        </button>
      </div>
    </div>
  );
}

// Collapsed right-edge dock holding the workflow's auxiliary views (details
// form, JSON editor, run history) — ported from the Agents/Routines canvas's
// GraphSideDock so the canvas itself can occupy the full page instead of
// sharing space with a permanent header/form.
const WORKFLOW_SIDE_ITEMS = [
  { id: 'details', label: 'Details', title: 'Workflow details' },
  { id: 'json', label: 'JSON', title: 'Edit as JSON' },
  { id: 'runs', label: 'Runs', title: 'Run history' },
];

const DOCK_ITEM_META = {
  prompt: { icon: 'P', label: 'Prompt' },
  'call-agent': { icon: 'A', label: 'Agent' },
  'knowledge-search': { icon: 'K', label: 'Knowledge' },
  'kanban-task': { icon: 'T', label: 'Kanban' },
  extract: { icon: 'X', label: 'Extract' },
  'ask-user': { icon: 'U', label: 'Ask' },
  'inbox-message': { icon: 'I', label: 'Inbox' },
  routine: { icon: 'R', label: 'Routine' },
  condition: { icon: '◇', label: 'Condition' },
  approval: { icon: '✓', label: 'Approval' },
};

export function WorkflowSideDock({ onSelect }) {
  return (
    <aside className="workflow-side-dock" aria-label="Workflow tools">
      {WORKFLOW_SIDE_ITEMS.map((item) => (
        <button
          key={item.id}
          type="button"
          className="workflow-side-dock__item"
          title={item.title}
          aria-label={item.title}
          onClick={() => onSelect(item.id)}
        >
          <ChevronLeftSmall />
          <span className="workflow-side-dock__label">{item.label}</span>
        </button>
      ))}
    </aside>
  );
}
