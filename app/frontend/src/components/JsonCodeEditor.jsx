import { useMemo, useRef } from 'react';
import hljs from 'highlight.js/lib/common';
import './JsonCodeEditor.css';

// The highlight and textarea layers share box metrics so text and caret stay aligned.
export default function JsonCodeEditor({ value, onChange, ariaLabel = 'JSON source' }) {
  const highlightRef = useRef(null);
  const highlighted = useMemo(() => {
    try {
      return hljs.highlight(value, { language: 'json' }).value;
    } catch {
      return null;
    }
  }, [value]);

  function syncScroll(event) {
    if (highlightRef.current) {
      highlightRef.current.scrollTop = event.target.scrollTop;
      highlightRef.current.scrollLeft = event.target.scrollLeft;
    }
  }

  return (
    <div className="app-json-editor">
      <pre ref={highlightRef} className="app-json-editor__highlight" aria-hidden="true">
        {highlighted === null ? (
          <code className="hljs language-json">{`${value}\n`}</code>
        ) : (
          <code className="hljs language-json" dangerouslySetInnerHTML={{ __html: `${highlighted}\n` }} />
        )}
      </pre>
      <textarea
        className="app-json-editor__textarea"
        aria-label={ariaLabel}
        spellCheck={false}
        value={value}
        onChange={onChange}
        onScroll={syncScroll}
      />
    </div>
  );
}
