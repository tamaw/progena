import useTypingWord, { LONGEST_PENDING_WORD } from '../hooks/useTypingWord';

/**
 * The "still working" typed word plus blinking ▋ cursor (useTypingWord).
 * With `reserveWidth`, an invisible copy of the longest word holds the
 * width so text after it stays put while letters are typed and deleted.
 */
export default function TypingWord({ className = '', reserveWidth = false }) {
  const typed = useTypingWord();
  const text = (
    <span className="typing-word__text">
      <span className={className}>{typed}</span>
      <span className="stream-cursor">▋</span>
    </span>
  );
  if (!reserveWidth) return text;
  return (
    <span className="typing-word typing-word--reserved">
      <span className="typing-word__sizer" aria-hidden="true">{LONGEST_PENDING_WORD}▋</span>
      {text}
    </span>
  );
}
