import { CloseIcon, PinIcon } from '../navIcons';

export function PanelIconButton({ label, children, onClick, type = 'button', disabled = false }) {
  return (
    <button type={type} className="topnav-pane__icon-btn" aria-label={label} data-tooltip={label} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

export function SegmentedControl({ items, value, onChange, ariaLabel, className = '' }) {
  return (
    <div className={`topnav-seg ${className}`.trim()} aria-label={ariaLabel}>
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          className={`topnav-seg__btn ${value === item.value ? 'is-active' : ''}`}
          onClick={() => onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

export function TopNavPane({
  title,
  subtitle,
  badge,
  icon,
  actions,
  children,
  onClose,
  onTogglePin,
  isPinned,
  isOpen = true,
  isBackground = false,
  className = '',
  bodyClassName = 'topnav-pane__body',
}) {
  const panelId = title.replace(/\s+/g, '-').toLowerCase();
  return (
    <>
      <aside
        className={`topnav-pane${isOpen ? ' is-open' : ''}${isPinned ? ' is-pinned' : ''}${isBackground ? ' is-background' : ''} ${className}`.trim()}
        aria-label={title}
        aria-labelledby={`${panelId}-title`}
        aria-hidden={isBackground || undefined}
        inert={isBackground || undefined}
        role={isBackground ? undefined : isPinned ? undefined : 'dialog'}
        aria-modal={isBackground || isPinned ? undefined : true}
      >
        <header className="topnav-pane__head">
          <div className="topnav-pane__heading">
            <h2 className="topnav-pane__title" id={`${panelId}-title`}>
              {icon ? <span className="topnav-pane__title-icon">{icon}</span> : null}
              {title}
              {badge ? <span className="topnav-pane__badge">{badge}</span> : null}
            </h2>
            {subtitle ? <p className="topnav-pane__sub">{subtitle}</p> : null}
          </div>
          <div className="topnav-pane__actions">
            {actions}
            {onTogglePin ? (
              <PanelIconButton label={isPinned ? 'Unpin panel' : 'Pin panel'} onClick={onTogglePin}>
                <PinIcon isPinned={isPinned} />
              </PanelIconButton>
            ) : null}
            <PanelIconButton label="Close" onClick={onClose}><CloseIcon /></PanelIconButton>
          </div>
        </header>
        <div className={bodyClassName}>{children}</div>
      </aside>
    </>
  );
}

export function ChromeIconButton({ label, icon, active, badge, onClick }) {
  return (
    <button
      type="button"
      className={`chrome-icon-btn ${active ? 'is-active' : ''} ${badge ? 'has-badge' : ''}`}
      aria-label={label}
      data-tooltip={label}
      onClick={onClick}
    >
      <span aria-hidden="true">{icon}</span>
      {badge ? <span className="chrome-icon-btn__badge">{badge}</span> : null}
    </button>
  );
}
