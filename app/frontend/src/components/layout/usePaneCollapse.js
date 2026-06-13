import { useState } from 'react';

// Collapse state + class names for a collapsible list pane. Pair the returned
// `paneClassName` with <PaneCollapseSeam> / <PaneExpandRail> from PaneList so any
// pane (the shared PaneList or a hand-rolled one) collapses to a slim rail the
// same way.
export function usePaneCollapse(baseClassName = '') {
  const [collapsed, setCollapsed] = useState(false);
  const paneClassName = `${baseClassName} sess-list--collapsible ${collapsed ? 'is-collapsed' : ''}`.trim();
  return { collapsed, setCollapsed, paneClassName };
}
