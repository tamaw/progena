/**
 * Pending turn in transcript order before its first stream event or text.
 * Keep an active gutter dot while waiting, in the same position as the work
 * group or assistant reply that replaces it.
 *
 * While waiting, TypingWord types out a "still working" word next to the
 * cursor (see useTypingWord). Cancelling the turn or the real entry
 * arriving unmounts this component and the animation stops with it.
 */

import TypingWord from './TypingWord';

export default function PendingAssistantCursor() {
  return (
    <article
      className="entry entry--standalone pending-cursor"
      aria-live="polite"
      aria-label="Assistant is responding"
    >
      <div className="entry__gutter" aria-hidden="true">
        <span className="entry__badge entry__badge--assistant activity-dot" />
      </div>
      <div className="entry__main">
        <div className="pending-cursor__blink" aria-hidden="true">
          <TypingWord className="pending-cursor__word" />
        </div>
      </div>
    </article>
  );
}
