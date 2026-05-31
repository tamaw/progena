import { memo } from 'react';

function DashboardCanvasNode({
  title,
  size = 'small',
  badge,
  children,
  onRemove,
  isDragging = false,
  isDropTarget = false,
  ...dragProps
}) {
  return (
    <article
      className={`dashboard-widget-shell dashboard-widget-shell--${size}${isDragging ? ' is-dragging' : ''}${isDropTarget ? ' is-drop-target' : ''}`}
      {...dragProps}
    >
      <header className="dashboard-widget-shell__head dashboard-canvas__tile-head">
        <div className="dashboard-canvas__tile-title">
          <span className="dashboard-canvas__tile-handle" aria-hidden="true">⋮⋮</span>
          <h3>{title}</h3>
        </div>
        <div className="dashboard-widget-shell__actions">
          {badge !== undefined && badge !== null ? <span className="badge">{badge}</span> : null}
          <button
            type="button"
            className="dashboard-widget-shell__remove"
            aria-label={`Remove ${title}`}
            onClick={(event) => {
              event.stopPropagation();
              onRemove?.();
            }}
          >
            ×
          </button>
        </div>
      </header>
      <div className="dashboard-widget-shell__body dashboard-canvas__tile-body">
        {children}
      </div>
    </article>
  );
}

export default memo(DashboardCanvasNode);
