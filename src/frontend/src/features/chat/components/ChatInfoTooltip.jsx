import { useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import styles from './ChatInfoTooltip.module.css';

const VIEWPORT_PADDING = 8;
const TOOLTIP_GAP = 8;

function positionTooltip(trigger, bubble, pointer) {
  const target = trigger.getBoundingClientRect();
  const box = bubble.getBoundingClientRect();
  const anchor = pointer || { x: target.left + target.width / 2, y: target.top };
  const preferredLeft = pointer && anchor.x + box.width + TOOLTIP_GAP <= window.innerWidth - VIEWPORT_PADDING
    ? anchor.x + TOOLTIP_GAP
    : anchor.x - box.width - TOOLTIP_GAP;
  const preferredTop = pointer
    ? (anchor.y + box.height + TOOLTIP_GAP <= window.innerHeight - VIEWPORT_PADDING
      ? anchor.y + TOOLTIP_GAP
      : anchor.y - box.height - TOOLTIP_GAP)
    : target.top - box.height - TOOLTIP_GAP;
  const left = Math.max(VIEWPORT_PADDING, Math.min(window.innerWidth - box.width - VIEWPORT_PADDING, preferredLeft));
  const top = Math.max(VIEWPORT_PADDING, Math.min(window.innerHeight - box.height - VIEWPORT_PADDING, preferredTop));
  bubble.style.left = `${left}px`;
  bubble.style.top = `${top}px`;
}

/** Chat metrics use a portal so scroll panes cannot clip their details. */
export default function ChatInfoTooltip({ children, content, className = '', ...props }) {
  const id = useId();
  const trigger = useRef(null);
  const bubble = useRef(null);
  const pointer = useRef(null);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const visible = (hovered || focused) && !dismissed;

  useLayoutEffect(() => {
    if (!visible) return undefined;
    const position = () => {
      if (!trigger.current || !bubble.current) return;
      positionTooltip(trigger.current, bubble.current, pointer.current);
    };
    const dismiss = () => setDismissed(true);
    position();
    window.addEventListener('resize', position);
    document.addEventListener('scroll', dismiss, true);
    return () => {
      window.removeEventListener('resize', position);
      document.removeEventListener('scroll', dismiss, true);
    };
  }, [visible, content]);

  return (
    <span
      {...props}
      ref={trigger}
      className={`chat-info-control ${styles.control} ${className}`}
      tabIndex={0}
      aria-describedby={visible ? id : undefined}
      onPointerEnter={(event) => {
        pointer.current = { x: event.clientX, y: event.clientY };
        setHovered(true);
        setDismissed(false);
      }}
      onPointerMove={(event) => {
        pointer.current = { x: event.clientX, y: event.clientY };
        if (trigger.current && bubble.current) positionTooltip(trigger.current, bubble.current, pointer.current);
      }}
      onPointerLeave={() => setHovered(false)}
      onFocus={() => {
        pointer.current = null;
        setFocused(true);
        setDismissed(false);
      }}
      onBlur={() => setFocused(false)}
      onKeyDown={(event) => { if (event.key === 'Escape') setDismissed(true); }}
    >
      {children}
      {visible && createPortal(
        <span id={id} ref={bubble} className={`chat-info-tooltip ${styles.tooltip}`} role="tooltip">{content}</span>,
        document.body,
      )}
    </span>
  );
}
