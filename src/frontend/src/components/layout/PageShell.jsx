// Shared two-pane page skeleton: a left list pane (PaneList) and a right detail
// pane (DetailPane) in a CSS flexbox row. Page-specific deltas can be applied
// by passing extra modifier classes.
export default function PageShell({ children, className = '', label, ...props }) {
  return (
    <div className={`pane-page ${className}`.trim()} aria-label={label} {...props}>
      {children}
    </div>
  );
}
