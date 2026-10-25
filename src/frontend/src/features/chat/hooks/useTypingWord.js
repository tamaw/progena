import { useEffect, useState } from 'react';

// Mirrors the nav group headings in AppRuntimeShell (Orchestrate, Spectate,
// Operate, Automate, Delegate, Facilitate, Integrate), gerund-ified.
export const PENDING_WORDS = [
  'Orchestrating',
  'Spectating',
  'Operating',
  'Automating',
  'Delegating',
  'Facilitating',
  'Integrating',
];

export const WORD_SUFFIX = '...';

/** The longest word plus suffix: reserving its width keeps what follows from shifting while typing. */
export const LONGEST_PENDING_WORD = PENDING_WORDS.reduce((a, b) => (b.length > a.length ? b : a)) + WORD_SUFFIX;

const TYPE_INTERVAL_MS = 90;
const PAUSE_MS = 1000;

function pickWord(exclude) {
  if (PENDING_WORDS.length <= 1) return PENDING_WORDS[0];
  let word = exclude;
  while (word === exclude) {
    word = PENDING_WORDS[Math.floor(Math.random() * PENDING_WORDS.length)];
  }
  return word;
}

/**
 * "Still working" text: a random word from PENDING_WORDS (plus "...") typed
 * out one character at a time -- blink on empty, type forward (through the
 * dots too), hold, delete backward, blink on empty again, then repeat with a
 * new word. Returns the currently typed text. The loop is a local
 * setTimeout chain cleared on unmount, so callers stop it by unmounting.
 */
export default function useTypingWord() {
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

  return word.slice(0, typedLength);
}
