/**
 * A chat-row shell with a blinking cursor that shows before any visible
 * assistant output has arrived. Rendered in transcript order, not inside
 * the composer, so the user sees a clear "the assistant is thinking" state
 * without an agent header, timestamp, or gutter badge.
 */
export default function PendingAssistantCursor() {
  return (
    <article className="message-wrapper assistant msg msg--assistant entry entry--assistant entry--standalone pending-cursor">
      <div className="entry__gutter" aria-hidden="true">
        <span className="entry__badge entry__badge--assistant">A</span>
      </div>
      <div className="entry__main msg__entry-main">
        <div className="msg__head entry__head">
          <span className="entry__tag entry__tag--assistant">agent</span>
          <span className="msg__role thinking-label">Thinking…</span>
        </div>
        <div className="message assistant streaming pending-cursor__body">
          <div className="message-content pending-cursor__blink" />
        </div>
      </div>
    </article>
  );
}
