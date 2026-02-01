function TypingIndicator({ state = 'thinking', tokenCount = 0 }) {
  const getActionText = () => {
    switch (state) {
      case 'thinking': return 'is thinking...';
      case 'generating': return 'is responding...';
      case 'tool-use': return 'is using tools...';
      default: return 'is working...';
    }
  };

  return (
    <div className="typing-indicator" data-state={state}>
      <div className="typing-dots">
        <span></span>
        <span></span>
        <span></span>
      </div>
      <div className="typing-text">
        <span className="typing-status">Progenitor</span>
        <span className="typing-action">{getActionText()}</span>
      </div>
      {tokenCount > 0 && (
        <span className="token-counter">{tokenCount} tokens</span>
      )}
    </div>
  );
}

export default TypingIndicator;
