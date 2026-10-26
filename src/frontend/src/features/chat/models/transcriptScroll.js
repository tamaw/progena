/**
 * Follows the tail of the chat transcript (a DOM helper shared by
 * ConversationPage and LiveAssistantTurn).
 *
 * Normally keeps the transcript scrolled to the bottom while the reader is
 * pinned there. When the newest assistant answer is the last thing in the
 * transcript and grows taller than the viewport, it instead puts the
 * answer's start at the top and switches to "reading" that answer: following
 * stops, and the answer's start is held in place through later transcript
 * updates (the live answer is replaced by its saved copy on completion, and
 * rows can appear above it, which would otherwise shift it away).
 *
 * Reading ends when the reader scrolls themselves (noteTranscriptScroll) or
 * sends a message (pinTranscriptToTail).
 */

const ANSWER_TOP_OFFSET_PX = 8;
// Node.DOCUMENT_POSITION_FOLLOWING: compareDocumentPosition's "other comes after".
const DOCUMENT_POSITION_FOLLOWING = 4;

// Per container: the offset of the answer being read from the viewport top,
// and when the user last gave scroll input (wheel, touch, pointer, keys).
// Only scrolls shortly after real input end reading -- the browser's own
// scroll anchoring also fires scroll events when content above changes.
const state = new WeakMap();
const USER_INPUT_WINDOW_MS = 1000;
// Within this distance of the bottom counts as "at the bottom".
const PINNED_SLACK_PX = 24;

function stateFor(container) {
  let entry = state.get(container);
  if (!entry) {
    entry = { readingOffset: null, lastUserInputAt: -Infinity, lastAnswer: undefined };
    state.set(container, entry);
  }
  return entry;
}

function setScrollTop(container, top) {
  container.scrollTop = top;
}

// The newest assistant answer, unless the user has written since -- then the
// conversation's tail is their message and the reply still to come.
function newestAnswer(container) {
  const answers = container.querySelectorAll('.msg--assistant');
  const answer = answers[answers.length - 1];
  if (!answer) return null;
  const users = container.querySelectorAll('.msg--user');
  const lastUser = users[users.length - 1];
  if (lastUser && (answer.compareDocumentPosition(lastUser) & DOCUMENT_POSITION_FOLLOWING)) return null;
  return answer;
}

function contentTop(container, element) {
  // The transcript is positioned, so offsetParent coordinates end here.
  // Unlike client rects, these ignore the entry's slide-in transform.
  let top = 0;
  for (let node = element; node && node !== container; node = node.offsetParent) {
    top += node.offsetTop;
  }
  return top;
}

/**
 * Keep the transcript where the reader expects it after content changed:
 * hold the answer being read in place, else follow the tail while pinned.
 */
export function followTranscript(container, isPinnedToBottomRef) {
  if (!container) return;
  const entry = stateFor(container);

  if (entry.readingOffset !== null) {
    const answer = newestAnswer(container);
    if (answer) setScrollTop(container, contentTop(container, answer) - entry.readingOffset);
    return;
  }
  if (!isPinnedToBottomRef.current) return;

  const tail = container.scrollHeight - container.clientHeight;
  const answer = newestAnswer(container);
  // Collapsing a tall work group can clamp scrollTop past a newly arrived
  // answer. That is not the reader choosing to skip the answer's beginning.
  const answerJustAppeared = entry.lastAnswer === null && answer !== null;
  entry.lastAnswer = answer;
  if (answer) {
    const answerTop = contentTop(container, answer);
    const startTarget = Math.max(0, answerTop - ANSWER_TOP_OFFSET_PX);
    // Only while the answer's start is still at or below the viewport top:
    // once the reader has scrolled past it, plain tail-following resumes.
    if (startTarget < tail && (container.scrollTop <= answerTop || answerJustAppeared)) {
      setScrollTop(container, startTarget);
      entry.readingOffset = answerTop - container.scrollTop;
      isPinnedToBottomRef.current = false;
      return;
    }
  }
  setScrollTop(container, container.scrollHeight);
}

/** Call from the transcript's wheel/touch/pointer/key handlers: the user is scrolling. */
export function noteTranscriptUserInput(container, now = Date.now()) {
  if (container) stateFor(container).lastUserInputAt = now;
}

/**
 * Call from the transcript's scroll handler. A scroll right after real user
 * input ends reading and sets pinned from where it landed; any scroll that
 * reaches the bottom re-pins. Other scrolls never unpin -- the browser also
 * scrolls on its own when content is added or re-anchored, which must not
 * stop the transcript from following.
 */
export function noteTranscriptScroll(container, isPinnedToBottomRef, now = Date.now()) {
  if (!container) return;
  const entry = stateFor(container);
  const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < PINNED_SLACK_PX;
  if (now - entry.lastUserInputAt <= USER_INPUT_WINDOW_MS) {
    entry.readingOffset = null;
    isPinnedToBottomRef.current = atBottom;
  } else if (atBottom) {
    isPinnedToBottomRef.current = true;
  }
  if (atBottom) {
    // End the old gesture here: subsequent work-collapse scroll events are
    // browser layout adjustments, not fresh input from that gesture.
    entry.lastUserInputAt = -Infinity;
  }
}

/** Sending a message: drop any reading anchor and follow the tail again. */
export function pinTranscriptToTail(container, isPinnedToBottomRef) {
  if (container) stateFor(container).readingOffset = null;
  isPinnedToBottomRef.current = true;
}
