import { Children, useRef, useState } from 'react';

import { useMenuDismiss } from '../../hooks/useMenuDismiss';

function normalizeTab(tab) {
  return typeof tab === 'string' ? { id: tab, label: tab } : tab;
}

function ActionSvg({ children }) {
  return (
    <svg className="detail-header-action__svg" viewBox="0 0 16 16" aria-hidden="true">
      {children}
    </svg>
  );
}

export function EditActionIcon() {
  return (
    <ActionSvg>
      <path d="M2.5 13.5h2.75l7-7-2.75-2.75-7 7z" />
      <path d="M8.75 3 11.5 5.75" />
    </ActionSvg>
  );
}

export function CopyActionIcon() {
  return (
    <ActionSvg>
      <rect x="5" y="5" width="8" height="8" rx="1.75" />
      <path d="M3.5 10.75V4.75A1.75 1.75 0 0 1 5.25 3h6" />
    </ActionSvg>
  );
}

export function ConfigureActionIcon() {
  return (
    <ActionSvg>
      <path d="M3 4.5h7.5" />
      <circle cx="11.25" cy="4.5" r="1.25" />
      <path d="M5.5 8h7.5" />
      <circle cx="4.25" cy="8" r="1.25" />
      <path d="M3 11.5h7.5" />
      <circle cx="11.25" cy="11.5" r="1.25" />
    </ActionSvg>
  );
}

export function DuplicateActionIcon() {
  return (
    <ActionSvg>
      <rect x="6" y="4.25" width="6.75" height="6.75" rx="1.5" />
      <path d="M4.25 10.5H4A1.75 1.75 0 0 1 2.25 8.75V4A1.75 1.75 0 0 1 4 2.25h4.75A1.75 1.75 0 0 1 10.5 4v.25" />
    </ActionSvg>
  );
}

export function PlayActionIcon() {
  return (
    <ActionSvg>
      <path d="M5.25 4.25v7.5L11.75 8z" fill="currentColor" stroke="none" />
    </ActionSvg>
  );
}

export function StopActionIcon() {
  return (
    <ActionSvg>
      <rect x="4.75" y="4.75" width="6.5" height="6.5" rx="1.25" fill="currentColor" stroke="none" />
    </ActionSvg>
  );
}

export function SendActionIcon() {
  return (
    <ActionSvg>
      <path d="M13.5 2.5 2.5 7l4.5 2 2 4.5z" />
      <path d="M13.5 2.5 7 9" />
    </ActionSvg>
  );
}

export function PauseActionIcon() {
  return (
    <ActionSvg>
      <rect x="4.5" y="3.75" width="2.5" height="8.5" rx="1" fill="currentColor" stroke="none" />
      <rect x="9" y="3.75" width="2.5" height="8.5" rx="1" fill="currentColor" stroke="none" />
    </ActionSvg>
  );
}

export function SaveActionIcon() {
  return (
    <ActionSvg>
      <path d="M3.75 2.75h6.5L13.25 5.75v7.5H3.75z" />
      <path d="M5.75 2.75v3.5h4.5v-3.5" />
      <path d="M5.75 13.25v-3.75h4.5v3.75" />
    </ActionSvg>
  );
}

export function InstallActionIcon() {
  return (
    <ActionSvg>
      <path d="M8 2.75v6.5" />
      <path d="M5.25 6.75 8 9.5l2.75-2.75" />
      <path d="M3.25 10.75v1.5a1 1 0 0 0 1 1h7.5a1 1 0 0 0 1-1v-1.5" />
    </ActionSvg>
  );
}

export function UninstallActionIcon() {
  return (
    <ActionSvg>
      <path d="M8 9.5V3" />
      <path d="M5.25 5.5 8 2.75l2.75 2.75" />
      <path d="M3.25 10.75v1.5a1 1 0 0 0 1 1h7.5a1 1 0 0 0 1-1v-1.5" />
    </ActionSvg>
  );
}

export function ApproveActionIcon() {
  return (
    <ActionSvg>
      <path d="M3.25 8.5 6.5 11.75 12.75 4.5" />
    </ActionSvg>
  );
}

export function RejectActionIcon() {
  return (
    <ActionSvg>
      <path d="M4 4l8 8" />
      <path d="M12 4l-8 8" />
    </ActionSvg>
  );
}

export function DeleteActionIcon() {
  return (
    <ActionSvg>
      <path d="M3.5 4.5h9" />
      <path d="M6.25 4.5V3.25h3.5V4.5" />
      <path d="M5 6.25v5.25M8 6.25v5.25M11 6.25v5.25" />
      <path d="M4.5 4.5l.6 8A1.75 1.75 0 0 0 6.85 14h2.3a1.75 1.75 0 0 0 1.75-1.5l.6-8" />
    </ActionSvg>
  );
}

export function ArchiveActionIcon() {
  return (
    <ActionSvg>
      <path d="M2.5 4h11v2.25h-11z" />
      <path d="M3.5 7.25h9v5.25h-9z" />
      <path d="M8 8.5v2.25M6.5 9.5 8 11l1.5-1.5" />
    </ActionSvg>
  );
}

export function LogsActionIcon() {
  return (
    <ActionSvg>
      <rect x="2.75" y="3.25" width="10.5" height="9.5" rx="1.75" />
      <path d="m5.25 6 1.75 1.75L5.25 9.5" />
      <path d="M8.5 9.5h2.25" />
    </ActionSvg>
  );
}

export function ReconnectActionIcon() {
  return (
    <ActionSvg>
      <path d="M12 6.25A4.5 4.5 0 0 0 4.5 4.3" />
      <path d="M4.5 4.3V2.75M4.5 4.3H6" />
      <path d="M4 9.75A4.5 4.5 0 0 0 11.5 11.7" />
      <path d="M11.5 11.7v1.55M11.5 11.7H10" />
    </ActionSvg>
  );
}

export function CloneActionIcon() {
  return (
    <ActionSvg>
      <circle cx="4.25" cy="4" r="1.25" />
      <circle cx="4.25" cy="12" r="1.25" />
      <circle cx="11.75" cy="8" r="1.25" />
      <path d="M5.5 4.75v6.5" />
      <path d="M5.5 5.25 10.5 7.25" />
      <path d="M5.5 10.75 10.5 8.75" />
    </ActionSvg>
  );
}

export function OpenActionIcon() {
  return (
    <ActionSvg>
      <path d="M6.75 3.25h6v6" />
      <path d="M6.25 9.75 12.75 3.25" />
      <path d="M10.25 8.5v2.75A1.75 1.75 0 0 1 8.5 13h-4A1.75 1.75 0 0 1 2.75 11.25v-4A1.75 1.75 0 0 1 4.5 5.5h2.75" />
    </ActionSvg>
  );
}

export function PullActionIcon() {
  return (
    <ActionSvg>
      <path d="M8 2.75v6.5" />
      <path d="m5.75 7.5 2.25 2.25 2.25-2.25" />
      <path d="M3.5 11.5h9" />
      <path d="M4.75 13.25h6.5" />
    </ActionSvg>
  );
}

export function MoreActionIcon() {
  return (
    <ActionSvg>
      <circle cx="8" cy="3.5" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="8" cy="8" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="8" cy="12.5" r="1.3" fill="currentColor" stroke="none" />
    </ActionSvg>
  );
}

// Overflow "⋮" menu rendered alongside the inline action row. CSS (a container
// query on .detail-header) hides the inline row and reveals this trigger when
// the header is too narrow, so every page collapses its actions the same way.
// Menu items are derived from the same DetailHeaderAction children, so pages
// only declare their actions once.
function DetailHeaderOverflowMenu({ items, label }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useMenuDismiss(open, wrapRef, () => setOpen(false));

  return (
    <div className="detail-header-menu" ref={wrapRef}>
      <button
        type="button"
        className="detail-header-action detail-header-menu__trigger is-icon-only"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        data-tooltip={label}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="detail-header-action__icon" aria-hidden="true"><MoreActionIcon /></span>
      </button>
      {open ? (
        <div className="detail-header-menu__dropdown" role="menu" aria-label={label}>
          {items.map((item, index) => {
            const props = item.props || {};
            const text = props.label || props.tooltip || props.ariaLabel || '';
            // Toggle actions carry `aria-pressed` inline; preserve that checked
            // state in the menu so screen readers still announce it when the
            // header collapses (menuitem has no checked semantics).
            const pressed = props['aria-pressed'];
            const isToggle = pressed !== undefined && pressed !== null;
            return (
              <button
                key={item.key ?? index}
                type="button"
                role={isToggle ? 'menuitemcheckbox' : 'menuitem'}
                aria-checked={isToggle ? Boolean(pressed) : undefined}
                className={`detail-header-menu__item ${props.variant === 'danger' ? 'is-danger' : ''}`.trim()}
                disabled={props.disabled}
                onClick={(event) => {
                  props.onClick?.(event);
                  setOpen(false);
                }}
              >
                <span className="detail-header-menu__item-icon" aria-hidden="true">{props.icon}</span>
                <span>{text}</span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

export function DetailHeaderActions({ children, className = '', menuLabel = 'More actions' }) {
  const items = Children.toArray(children).filter(Boolean);
  return (
    <div className={`detail-header-actions ${className}`.trim()}>
      <div className="detail-header-actions__inline">{children}</div>
      {items.length ? <DetailHeaderOverflowMenu items={items} label={menuLabel} /> : null}
    </div>
  );
}

export function DetailHeaderAction({
  icon,
  label,
  tooltip,
  ariaLabel,
  variant = 'default',
  iconOnly = true,
  className = '',
  type = 'button',
  ...props
}) {
  const description = tooltip || label || ariaLabel;

  return (
    <button
      type={type}
      className={`detail-header-action detail-header-action--${variant} ${iconOnly ? 'is-icon-only' : ''} ${className}`.trim()}
      aria-label={iconOnly ? ariaLabel || description : ariaLabel}
      data-tooltip={description}
      {...props}
    >
      {icon ? <span className="detail-header-action__icon" aria-hidden="true">{icon}</span> : null}
      {!iconOnly && label ? <span className="detail-header-action__label">{label}</span> : null}
    </button>
  );
}

export function DetailTabs({ tabs = [], activeTab, onTabChange, className = '', label = 'Detail tabs' }) {
  if (!tabs.length) {
    return null;
  }

  return (
    <nav className={`detail-tabs ${className}`.trim()} aria-label={label}>
      {tabs.map((tab) => {
        const item = normalizeTab(tab);
        const selected = activeTab === item.id;
        return (
          <button
            key={item.id}
            type="button"
            className={`dtab detail-tab ${selected ? 'is-active' : ''}`}
            aria-pressed={selected}
            onClick={() => onTabChange?.(item.id)}
          >
            {item.label}
          </button>
        );
      })}
    </nav>
  );
}

// Status indicator for a detail-header meta row: a coloured dot + coloured
// label, no surrounding bubble. `tone`: idle (healthy, at rest; hollow ring),
// ok (working / new data), info (waiting), warning (unknown / degraded),
// error (failed), muted (off by choice). List rows use sess-row__dot--<tone>.
export function DetailStatus({ tone = 'muted', children, className = '' }) {
  return (
    <span className={`detail-status detail-status--${tone} ${className}`.trim()}>
      <span className="detail-status__dot" aria-hidden="true" />
      {children}
    </span>
  );
}

export function DetailMeta({ items = [], className = '' }) {
  const visibleItems = items.filter(Boolean);
  if (!visibleItems.length) {
    return null;
  }

  return (
    <div className={`detail-header__meta ${className}`.trim()}>
      {visibleItems.map((item, index) => (
        <span className="detail-header__meta-group" key={item.key || index}>
          {index > 0 ? <span className="detail-header__separator">|</span> : null}
          <span className="detail-header__meta-item">{item}</span>
        </span>
      ))}
    </div>
  );
}

export default function DetailHeader({
  title,
  eyebrow,
  subtitle,
  avatar,
  actions,
  meta = [],
  tabs = [],
  activeTab,
  onTabChange,
  tabLabel,
  className = '',
  titleClassName = '',
}) {
  return (
    <header className={`detail-header ${className}`.trim()}>
      {eyebrow ? <p className="detail-header__eyebrow">{eyebrow}</p> : null}
      <div className="detail-header__title-row">
        {avatar ? <span className="detail-header__avatar">{avatar}</span> : null}
        <div className="detail-header__title-stack">
          <h1 className={`detail-header__title ${titleClassName}`.trim()}>{title}</h1>
          {subtitle ? <p className="detail-header__subtitle">{subtitle}</p> : null}
        </div>
        <span className="detail-header__spacer" />
        {actions ? <div className="detail-header__actions">{actions}</div> : null}
      </div>
      <DetailMeta items={meta} />
      <DetailTabs tabs={tabs} activeTab={activeTab} onTabChange={onTabChange} label={tabLabel} />
    </header>
  );
}
