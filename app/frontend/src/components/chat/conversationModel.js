export const CHAT_AGENT_NAME = 'Progenitor';

export function formatClockTime(timestamp) {
  if (!timestamp) {
    return '';
  }
  return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function summarizeMessageContent(content) {
  if (typeof content !== 'string' || content.trim().length === 0) {
    return 'No messages yet. Start a conversation with the Progenitor agent.';
  }
  const compact = content.replace(/\s+/g, ' ').trim();
  return compact.length > 128 ? `${compact.slice(0, 125)}...` : compact;
}

export function deriveChatTitle(messages) {
  const firstUserMessage = messages.find((message) => message.role === 'user' && message.content);
  if (!firstUserMessage) {
    return 'New Progenitor conversation';
  }
  const title = firstUserMessage.content.replace(/\s+/g, ' ').trim();
  return title.length > 72 ? `${title.slice(0, 69)}...` : title;
}

function formatSessionAge(timestamp) {
  if (!timestamp) return 'now';
  const elapsedMs = Date.now() - timestamp;
  const elapsedMinutes = Math.max(0, Math.round(elapsedMs / 60000));
  if (elapsedMinutes < 1) return 'now';
  if (elapsedMinutes < 60) return `${elapsedMinutes}m`;
  const elapsedHours = Math.round(elapsedMinutes / 60);
  if (elapsedHours < 24) return `${elapsedHours}h`;
  return `${Math.round(elapsedHours / 24)}d`;
}

export function buildSessionItems(messages, sessionId, chatTitle, totalTokenCount) {
  const lastMessage = messages[messages.length - 1];
  const currentSession = {
    id: sessionId || 'current-session',
    title: chatTitle,
    age: formatSessionAge(lastMessage?.timestamp),
    preview: summarizeMessageContent(lastMessage?.content),
    tokenCount: totalTokenCount,
    selected: true,
  };

  return [currentSession];
}
