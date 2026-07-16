import { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { RegenerateDisabledContext } from './RegenerateDisabledContext';

const FEEDBACK_DURATION_MS = 1600;


function CopyIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true">
      <rect x="4" y="4" width="9" height="9" rx="1.5" />
      <path d="M3 11V3.5A1.5 1.5 0 0 1 4.5 2H11" />
    </svg>
  );
}

function RegenerateIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M13.5 8a5.5 5.5 0 1 1-1.7-3.98M13.5 2.5v3h-3" />
    </svg>
  );
}

export default function MessageActions({ content, onRegenerate }) {
  const regenerateDisabledWhileGenerating = useContext(RegenerateDisabledContext);
  const [feedback, setFeedback] = useState('');
  const feedbackTimeoutRef = useRef(null);

  const showFeedback = useCallback((message) => {
    clearTimeout(feedbackTimeoutRef.current);
    setFeedback(message);
    feedbackTimeoutRef.current = setTimeout(() => {
      feedbackTimeoutRef.current = null;
      setFeedback('');
    }, FEEDBACK_DURATION_MS);
  }, []);

  useEffect(() => () => clearTimeout(feedbackTimeoutRef.current), []);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content);
      showFeedback('Copied');
    } catch (error) {
      console.error('Failed to copy:', error);
    }
  }, [content, showFeedback]);

  const handleRegenerate = useCallback(() => {
    if (onRegenerate?.() !== false) {
      showFeedback('Regenerated');
    }
  }, [onRegenerate, showFeedback]);

  return (
    <span className="message-actions msg__actions">
      <button
        type="button"
        className={`msg-action-btn copy-btn${feedback === 'Copied' ? ' is-feedback-visible' : ''}`}
        data-tooltip={feedback === 'Copied' ? feedback : undefined}
        onClick={handleCopy}
        aria-label="Copy message"
      >
        <CopyIcon />
        <span className="msg-action-btn__label">Copy</span>
      </button>
      {onRegenerate && (
        <button
          type="button"
          className={`msg-action-btn regen-btn${feedback === 'Regenerated' ? ' is-feedback-visible' : ''}`}
          data-tooltip={feedback === 'Regenerated' ? feedback : undefined}
          onClick={handleRegenerate}
          aria-label="Regenerate response"
          disabled={regenerateDisabledWhileGenerating}
        >
          <RegenerateIcon />
          <span className="msg-action-btn__label">Regenerate</span>
        </button>
      )}
    </span>
  );
}
