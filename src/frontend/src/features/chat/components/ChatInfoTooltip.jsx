import { useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import styles from './ChatInfoTooltip.module.css';

/** Chat metrics share the design-guide bubble, fixed at the page's top edge. */
export default function ChatInfoTooltip({ children, content, className = '', ...props }) {
  const id = useId();
  const trigger = useRef(null);
  const bubble = useRef(null);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const visible = (hovered || focused) && !dismissed;

  useLayoutEffect(() => {
    if (!visible) return undefined;
    const position = () => {
      if (!trigger.current || !bubble.current) return;
      const target = trigger.current.getBoundingClientRect();
      const width = bubble.current.getBoundingClientRect().width;
      bubble.current.style.left = `${Math.max(8, Math.min(window.innerWidth - width - 8, target.left + target.width / 2 - width / 2))}px`;
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
      onPointerEnter={() => { setHovered(true); setDismissed(false); }}
      onPointerLeave={() => setHovered(false)}
      onFocus={() => { setFocused(true); setDismissed(false); }}
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
