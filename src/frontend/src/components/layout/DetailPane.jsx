// Shared right-hand detail pane: a scrollable column that holds a DetailHeader
// plus arbitrary body content. Replaces the per-page `*-detail` / `chat-
// container` wrappers, which all repeated the same flex/scroll/background. Pass
// a page-specific modifier via `className` for bespoke descendant rules, and
// `flush` for canvas/graph/chat bodies that manage their own scrolling
// (`overflow: hidden`).
//
// Rendered as a labelled <section> (a region landmark), not <main>: the app
// shell already provides the single <main> (AppRuntimeShell), so a nested one
// would be invalid landmark semantics.
export default function DetailPane({ children, className = '', label, flush = false, ...props }) {
  return (
    <section
      className={`detail-pane ${flush ? 'detail-pane--flush' : ''} ${className}`.trim()}
      aria-label={label}
      {...props}
    >
      {children}
    </section>
  );
}
