import { useEffect, useState } from 'react';

export const NARROW_MQ = '(max-width: 980px)';

// Collapse state + class names for a collapsible list pane. Pair the returned
// `paneClassName` with <PaneCollapseSeam> / <PaneExpandRail> from PaneList so any
// pane (the shared PaneList or a hand-rolled one) collapses to a slim rail the
// same way. On narrow viewports the pane starts collapsed and auto-collapses
// if the viewport shrinks back down.
export function usePaneCollapse(baseClassName = '') {
  const [collapsed, setCollapsed] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(NARROW_MQ).matches,
  );

  useEffect(() => {
    const mq = window.matchMedia(NARROW_MQ);
    const handler = (e) => { if (e.matches) setCollapsed(true); };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  const paneClassName = `${baseClassName} sess-list--collapsible ${collapsed ? 'is-collapsed' : ''}`.trim();
  return { collapsed, setCollapsed, paneClassName };
}
