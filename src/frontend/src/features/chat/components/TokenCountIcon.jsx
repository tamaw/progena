import { useState } from 'react';
import ChatInfoTooltip from './ChatInfoTooltip';
import styles from './TokenCountIcon.module.css';

function TokenIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" aria-hidden="true">
      <path d="M3 6h10.5M2.5 10H13M6.5 2.5l-1 11M10.5 2.5l-1 11" />
    </svg>
  );
}

/**
 * Token counts shown like Copy/Regenerate: an icon that widens to reveal the
 * already formatted `label`. Hover and focus reveal it; click, tap, Enter or
 * Space pin it open until Escape or blur, and `label` is the accessible name.
 * `details` adds the shared metrics tooltip. `passive` is for counts inside
 * another control (such as a work-group toggle), which owns interaction: the
 * count reveals with that control's hover, focus or expanded state.
 */
export default function TokenCountIcon({ label, details = null, passive = false }) {
  const [expanded, setExpanded] = useState(false);
  if (!label) return null;
  const content = (
    <>
      <TokenIcon />
      <span className={styles.label}>{label}</span>
    </>
  );
  const baseClassName = styles.count;
  if (passive) {
    return <span className={`${baseClassName} ${styles.passive}`}>{content}</span>;
  }

  const disclosureProps = {
    className: `${baseClassName}${expanded ? ` ${styles.expanded}` : ''}`,
    role: 'button',
    'aria-label': label,
    'aria-expanded': expanded,
    onClick: () => setExpanded((value) => !value),
    onKeyDown: (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        setExpanded((value) => !value);
      } else if (event.key === 'Escape') {
        setExpanded(false);
      }
    },
    onBlur: () => setExpanded(false),
  };
  return details
    ? <ChatInfoTooltip {...disclosureProps} content={details}>{content}</ChatInfoTooltip>
    : <span {...disclosureProps} tabIndex={0}>{content}</span>;
}
