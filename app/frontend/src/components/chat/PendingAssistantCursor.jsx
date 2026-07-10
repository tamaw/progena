/**
 * A bare blinking cursor shown in transcript order before the assistant's
 * first stream event or text chunk arrives. Deliberately has no badge, tag,
 * role label, or timestamp -- we don't yet know whether this turn will open
 * with a tool call, reasoning, or a text reply, and showing a guess here
 * just means swapping it out (and looking like the entry "vanished") once
 * the real entry type is known. The gutter/main grid columns are kept so
 * the cursor lines up with wherever that real entry lands, avoiding a
 * layout jump on the swap.
 *
 * While waiting, a single word from PENDING_WORDS (plus a trailing "...")
 * is typed out one character at a time next to the cursor, purely to
 * signal "still working": blink on empty, type forward (through the dots
 * too), hold, delete backward, blink on empty again, then repeat with a
 * new word. The animation is entirely local (a setTimeout loop cleaned up
 * on unmount), so cancelling the turn or the real entry arriving unmounts
 * this component and the animation stops immediately with no extra
 * teardown.
 */

import { useEffect, useState } from 'react';

const PENDING_WORDS = [
  'Thinking',
  'Ideating',
  'Strategising',
  'Synergising',
  'Orchestrating',
  'Calibrating',
  'Percolating',
  'Formulating',
  'Cogitating',
  'Noodling',
  'Workshopping',
  'Optimising',
];

const TYPE_INTERVAL_MS = 90;
const PAUSE_MS = 1000;
const WORD_SUFFIX = '...';

function pickWord(exclude) {
  if (PENDING_WORDS.length <= 1) return PENDING_WORDS[0];
  let word = exclude;
  while (word === exclude) {
    word = PENDING_WORDS[Math.floor(Math.random() * PENDING_WORDS.length)];
  }
  return word;
}

export default function PendingAssistantCursor() {
  const [word, setWord] = useState('');
  const [typedLength, setTypedLength] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timeoutId;
    let bareWord = pickWord(null);
    let currentWord = bareWord + WORD_SUFFIX;

    const typeForward = (charIndex) => {
      if (cancelled) return;
      setWord(currentWord);
      setTypedLength(charIndex);
      if (charIndex < currentWord.length) {
        timeoutId = setTimeout(() => typeForward(charIndex + 1), TYPE_INTERVAL_MS);
      } else {
        timeoutId = setTimeout(() => deleteBackward(charIndex), PAUSE_MS);
      }
    };

    const deleteBackward = (charIndex) => {
      if (cancelled) return;
      setTypedLength(charIndex);
      if (charIndex > 0) {
        timeoutId = setTimeout(() => deleteBackward(charIndex - 1), TYPE_INTERVAL_MS);
      } else {
        timeoutId = setTimeout(() => {
          if (cancelled) return;
          bareWord = pickWord(bareWord);
          currentWord = bareWord + WORD_SUFFIX;
          typeForward(1);
        }, PAUSE_MS);
      }
    };

    timeoutId = setTimeout(() => typeForward(1), PAUSE_MS);

    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, []);

  return (
    <article
      className="entry entry--standalone pending-cursor"
      aria-live="polite"
      aria-label="Assistant is responding"
    >
      <div className="entry__gutter" aria-hidden="true" />
      <div className="entry__main">
        <div className="pending-cursor__blink" aria-hidden="true">
          <span className="pending-cursor__word">{word.slice(0, typedLength)}</span>
          <span className="stream-cursor">▋</span>
        </div>
      </div>
    </article>
  );
}
