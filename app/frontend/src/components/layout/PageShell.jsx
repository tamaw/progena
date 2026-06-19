// Shared two-pane page skeleton: a left list pane (PaneList) and a right detail
// pane (DetailPane) in a CSS grid. Pages used to each hand-roll this grid under
// their own `*-page` class (and several borrowed `agents-page`); this lifts the
// shared layout into one generic `.pane-page` class. Add `pane-page--rail` via
// `className` for a third column (e.g. the Tasks board's right rail). Page-
// specific deltas can still be applied by passing an extra modifier class.
export default function PageShell({ children, className = '', label, ...props }) {
  return (
    <div className={`pane-page ${className}`.trim()} aria-label={label} {...props}>
      {children}
    </div>
  );
}
