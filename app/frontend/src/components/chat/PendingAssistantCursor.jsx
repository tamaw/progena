/**
 * A bare blinking cursor shown in transcript order before the assistant's
 * first stream event or text chunk arrives. Deliberately has no badge, tag,
 * role label, or timestamp -- we don't yet know whether this turn will open
 * with a tool call, reasoning, or a text reply, and showing a guess here
 * just means swapping it out (and looking like the entry "vanished") once
 * the real entry type is known. The gutter/main grid columns are kept so
 * the cursor lines up with wherever that real entry lands, avoiding a
 * layout jump on the swap.
 */
export default function PendingAssistantCursor() {
  return (
    <article
      className="entry entry--standalone pending-cursor"
      aria-live="polite"
      aria-label="Assistant is responding"
    >
      <div className="entry__gutter" aria-hidden="true" />
      <div className="entry__main">
        <div className="pending-cursor__blink" aria-hidden="true" />
      </div>
    </article>
  );
}
