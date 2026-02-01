import { useState, useRef, useEffect } from 'react';

function ChatInput({ onSend, onStop, isGenerating, disabled }) {
  const [message, setMessage] = useState('');
  const textareaRef = useRef(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + 'px';
    }
  }, [message]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    } else if (e.key === 'Escape' && isGenerating) {
      onStop?.();
    }
  };

  const handleSend = () => {
    if (message.trim() && !isGenerating && !disabled) {
      onSend(message.trim());
      setMessage('');
    }
  };

  return (
    <div className="chat-input-container">
      <div className="input-wrapper">
        <textarea
          ref={textareaRef}
          className="chat-input"
          placeholder="Type your message... (Enter to send, Shift+Enter for new line)"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
          disabled={disabled}
        />
        <div className="input-actions">
          {isGenerating ? (
            <button className="btn-stop" title="Stop Generation (Esc)" onClick={onStop}>
              ⏹
            </button>
          ) : (
            <button 
              className="btn-send" 
              title="Send Message (Enter)" 
              onClick={handleSend}
              disabled={!message.trim() || disabled}
            >
              <span className="send-icon">➤</span>
            </button>
          )}
        </div>
      </div>
      <div className="input-hints">
        <span className="hint">Press <kbd>Enter</kbd> to send</span>
        <span className="hint">Press <kbd>Shift+Enter</kbd> for new line</span>
        <span className="hint">Press <kbd>Esc</kbd> to stop generation</span>
      </div>
    </div>
  );
}

export default ChatInput;
