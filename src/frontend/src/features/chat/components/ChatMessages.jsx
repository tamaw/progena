import { useSelector } from 'react-redux';
import { selectAgentName } from '../../settings/state/preferences';

import Message from './Message';

function ChatMessages({ messages }) {
  const agentName = useSelector(selectAgentName);
  if (messages.length === 0) {
    return (
      <div className="chat-messages">
        <div className="welcome-message">
          <h2>Welcome to Progena AI</h2>
          <p>Start a conversation with {agentName}.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-messages">
      {messages.map((msg, index) => (
        <Message key={msg.id || index} message={msg} />
      ))}
    </div>
  );
}

export default ChatMessages;
