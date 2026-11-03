/** Native one-line conversation disclosure, shared by transcript and sticky entries. */
export function EntryLine({ title, text, trailing, children, summaryLabel, defaultOpen = false, open, onToggle }) {
  const line = <>
    <span className="entry__role tool-call-entry__title">{title}</span>
    {text}
    <span className="msg__head-trailing">{trailing}</span>
  </>;
  if (!children) return <div className="entry__head tool-call-entry__line" aria-label={summaryLabel}>{line}</div>;
  return <details className="tool-event-disclosure tool-call-entry__disclosure"
    open={open ?? (defaultOpen || undefined)} onToggle={onToggle}>
    <summary className="entry__head tool-call-entry__line" aria-label={summaryLabel}>
      {line}
      <span className="tool-event-disclosure__chevron" aria-hidden="true" />
    </summary>
    {children}
  </details>;
}

export function Trailing({ duration, time }) {
  return <>
    {duration && <span className="entry__meta">{duration}</span>}
    {time && <span className="msg__time entry__time">{time}</span>}
  </>;
}
