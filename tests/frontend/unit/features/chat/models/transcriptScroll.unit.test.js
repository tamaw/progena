import { describe, expect, it } from 'vitest';
import { followTranscript, noteTranscriptScroll, noteTranscriptUserInput, pinTranscriptToTail } from '@frontend/features/chat/models/transcriptScroll';

// Minimal scroll container: entries are laid out top to bottom at `top`
// (content coordinates); their viewport position follows container.scrollTop.
function transcript(entries, { clientHeight = 500 } = {}) {
  let scrollTop = 0;
  const container = {
    clientHeight,
    get scrollHeight() {
      return Math.max(...entries.map((entry) => entry.top + entry.height)) + 24;
    },
    // Browsers clamp scrollTop to the scrollable range.
    get scrollTop() {
      return scrollTop;
    },
    set scrollTop(value) {
      scrollTop = Math.max(0, Math.min(value, container.scrollHeight - clientHeight));
    },
    getBoundingClientRect: () => ({ top: 0 }),
    querySelectorAll: (selector) => entries.filter((entry) => selector === `.msg--${entry.role}`),
  };
  entries.forEach((entry, order) => {
    entry.offsetParent = container;
    Object.defineProperty(entry, 'offsetTop', { get: () => entry.top });
    entry.offsetHeight = entry.height;
    entry.getBoundingClientRect = () => ({ top: entry.top - container.scrollTop });
    entry.compareDocumentPosition = (other) => (entries.indexOf(other) > order ? 4 : 2);
  });
  return container;
}

const bottom = (container) => container.scrollHeight - container.clientHeight;

describe('followTranscript', () => {
  it('follows the bottom while pinned and the newest answer fits', () => {
    const container = transcript([
      { role: 'user', top: 0, height: 60 },
      { role: 'assistant', top: 600, height: 200 },
    ]);
    const pinned = { current: true };
    followTranscript(container, pinned);
    expect(container.scrollTop).toBe(bottom(container));
    expect(pinned.current).toBe(true);
  });

  it('puts a long newest answer\'s start at the top, stops following, and holds it as content shifts above', () => {
    const answer = { role: 'assistant', top: 600, height: 1200 };
    const above = { role: 'user', top: 0, height: 60 };
    const container = transcript([above, answer]);
    container.scrollTop = 400;
    const pinned = { current: true };

    followTranscript(container, pinned);
    expect(container.scrollTop).toBe(592);
    expect(pinned.current).toBe(false);

    // Completion re-renders: rows appear above the answer, moving it down.
    answer.top = 900;
    followTranscript(container, pinned);
    expect(container.scrollTop).toBe(892);
  });

  it('holds the layout position when a saved answer slides in after streaming', () => {
    const answer = { role: 'assistant', top: 600, height: 1200 };
    const container = transcript([{ role: 'user', top: 0, height: 60 }, answer]);
    const pinned = { current: true };
    followTranscript(container, pinned);

    answer.getBoundingClientRect = () => ({ top: answer.top - container.scrollTop + 10 });
    followTranscript(container, pinned);
    expect(container.scrollTop).toBe(592);

    answer.getBoundingClientRect = () => ({ top: answer.top - container.scrollTop });
    followTranscript(container, pinned);
    expect(container.scrollTop).toBe(592);
  });

  it('stops holding the answer once the reader scrolls, and then leaves them alone', () => {
    const answer = { role: 'assistant', top: 600, height: 1200 };
    const container = transcript([{ role: 'user', top: 0, height: 60 }, answer]);
    container.scrollTop = 400;
    const pinned = { current: true };
    followTranscript(container, pinned);

    noteTranscriptUserInput(container, 1000);
    container.scrollTop = 1000;
    noteTranscriptScroll(container, pinned, 1100);
    expect(pinned.current).toBe(false);
    answer.top = 900;
    followTranscript(container, pinned);
    expect(container.scrollTop).toBe(1000);
  });

  it('keeps following when the browser scrolls on its own (content added or re-anchored)', () => {
    const container = transcript([{ role: 'user', top: 0, height: 60 }, { role: 'assistant', top: 600, height: 200 }]);
    const pinned = { current: true };
    followTranscript(container, pinned);

    container.scrollTop -= 200;
    noteTranscriptScroll(container, pinned, 5000);
    expect(pinned.current).toBe(true);
  });

  it('keeps holding the answer through scrolls the reader did not make (browser scroll anchoring)', () => {
    const answer = { role: 'assistant', top: 600, height: 1200 };
    const container = transcript([{ role: 'user', top: 0, height: 60 }, answer]);
    container.scrollTop = 400;
    const pinned = { current: true };
    followTranscript(container, pinned);

    container.scrollTop = 450;
    noteTranscriptScroll(container, pinned, 5000);
    answer.top = 900;
    followTranscript(container, pinned);
    expect(container.scrollTop).toBe(892);
  });

  it('follows the bottom past an old answer once the user has written after it', () => {
    const container = transcript([
      { role: 'assistant', top: 0, height: 1200 },
      { role: 'user', top: 1210, height: 60 },
    ]);
    const pinned = { current: true };
    followTranscript(container, pinned);
    expect(container.scrollTop).toBe(bottom(container));
  });

  it('resumes plain tail-following when the reader is already past the answer start', () => {
    const container = transcript([{ role: 'user', top: 0, height: 60 }, { role: 'assistant', top: 600, height: 1200 }]);
    container.scrollTop = 900;
    const pinned = { current: true };
    followTranscript(container, pinned);
    expect(container.scrollTop).toBe(bottom(container));
  });

  it('sending drops the reading hold and follows the tail again', () => {
    const answer = { role: 'assistant', top: 600, height: 1200 };
    const container = transcript([{ role: 'user', top: 0, height: 60 }, answer]);
    container.scrollTop = 400;
    const pinned = { current: true };
    followTranscript(container, pinned);

    pinTranscriptToTail(container, pinned);
    container.scrollTop = 1700;
    followTranscript(container, pinned);
    expect(pinned.current).toBe(true);
    expect(container.scrollTop).toBe(bottom(container));
  });
});
