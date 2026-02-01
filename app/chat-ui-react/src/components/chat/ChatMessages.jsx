import Message from './Message';
import TypingIndicator from './TypingIndicator';

function ChatMessages({ messages, isGenerating, typingState, tokenCount }) {
  if (messages.length === 0) {
    return (
      <div className="chat-messages">
        <div className="welcome-message">
          <h2>Welcome to Progenitor Chat</h2>
          <p>Start a conversation with the Progenitor AI agent.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-messages">
      {messages.map((msg, index) => (
        <Message key={msg.id || index} message={msg} />
      ))}
      {isGenerating && (
        <TypingIndicator state={typingState} tokenCount={tokenCount} />
      )}
    </div>
  );
}

export default ChatMessages;
