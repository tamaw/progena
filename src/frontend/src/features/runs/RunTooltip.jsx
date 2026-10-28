import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

// Preserve the app's data-tooltip contract, but portal outside the chart's
// scroll viewport so bars at its top and right edges do not clip the bubble.
export default function RunTooltip({ rootRef }) {
  const [active, setActive] = useState(null);
  const bubble = useRef(null);
  const activeTarget = useRef(null);
  const id = useId();
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return undefined;
    const show = (event) => {
      const target = event.target.closest('[data-tooltip]');
      if (!target || !root.contains(target) || !target.dataset.tooltip) return;
      if (event.type === 'pointerover' && activeTarget.current === document.activeElement) return;
      activeTarget.current = target;
      setActive((current) => current?.target === target ? current : { target, text: target.dataset.tooltip });
    };
    const hide = (event) => {
      if (event?.type === 'keydown' && !['Escape', 'Enter', ' '].includes(event.key)) return;
      if (event?.relatedTarget && event.target.closest('[data-tooltip]')?.contains(event.relatedTarget)) return;
      const target = activeTarget.current;
      if (event?.type === 'pointerout' && target && target === document.activeElement) return;
      if (event?.type === 'scroll' && target && target === document.activeElement) {
        const rect = target.getBoundingClientRect();
        if (rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth) {
          setActive({ target, text: target.dataset.tooltip });
          return;
        }
      }
      activeTarget.current = null;
      setActive(null);
    };
    root.addEventListener('pointerover', show);
    root.addEventListener('pointermove', show);
    root.addEventListener('focusin', show);
    root.addEventListener('pointerout', hide);
    root.addEventListener('focusout', hide);
    root.addEventListener('pointerdown', hide);
    root.addEventListener('click', hide);
    root.addEventListener('keydown', hide);
    document.addEventListener('scroll', hide, true);
    window.addEventListener('resize', hide);
    return () => {
      root.removeEventListener('pointerover', show);
      root.removeEventListener('pointermove', show);
      root.removeEventListener('focusin', show);
      root.removeEventListener('pointerout', hide);
      root.removeEventListener('focusout', hide);
      root.removeEventListener('keydown', hide);
      root.removeEventListener('pointerdown', hide);
      root.removeEventListener('click', hide);
      document.removeEventListener('scroll', hide, true);
      window.removeEventListener('resize', hide);
    };
  }, [rootRef]);
  useLayoutEffect(() => {
    if (!active || !bubble.current || !active.target.isConnected) return undefined;
    const target = active.target;
    const previous = target.getAttribute('aria-describedby');
    target.setAttribute('aria-describedby', [previous, id].filter(Boolean).join(' '));
    const rect = target.getBoundingClientRect();
    const box = bubble.current.getBoundingClientRect();
    const left = Math.max(8, Math.min(window.innerWidth - box.width - 8, rect.left + rect.width / 2 - box.width / 2));
    const above = rect.top >= box.height + 12;
    bubble.current.style.left = `${left}px`;
    bubble.current.style.top = `${above ? rect.top - box.height - 8 : Math.min(window.innerHeight - box.height - 8, rect.bottom + 8)}px`;
    return () => {
      if (previous) target.setAttribute('aria-describedby', previous);
      else target.removeAttribute('aria-describedby');
    };
  }, [active, id]);
  return active?.target.isConnected ? createPortal(<div id={id} ref={bubble} role="tooltip" className="run-tooltip">{active.text}</div>, document.body) : null;
}
