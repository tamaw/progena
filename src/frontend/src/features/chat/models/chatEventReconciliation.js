import { UI_EVENT } from '../../../realtime/eventVocabulary';

export function reconcileIncomingChatEvent({ event, openThreadId, isGenerating, reloadOpenChat, show }) {
  if (!event?.data) return;
  if (
    event.type !== UI_EVENT.AGENT_MESSAGE
    && event.type !== UI_EVENT.AGENT_QUESTION
    && event.type !== UI_EVENT.SCHEDULED_RESPONSE
  ) {
    return;
  }
  const metadata = event.data.metadata || {};
  const destination = typeof metadata.thread_id === 'string' && metadata.thread_id
    ? metadata.thread_id
    : typeof metadata.conversation_id === 'string' && metadata.conversation_id
      ? metadata.conversation_id
      : typeof event.sessionId === 'string' && event.sessionId ? event.sessionId : null;
  if (destination && destination === openThreadId) {
    if (!isGenerating) reloadOpenChat(destination);
    return;
  }

  if (destination || event.type === UI_EVENT.SCHEDULED_RESPONSE) {
    show(
      'info',
      destination ? 'New chat activity' : 'Scheduled task completed',
      event.data.content ? event.data.content.slice(0, 100) : 'Open the destination chat to view the result.',
    );
  }
}
