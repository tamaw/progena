import { useCallback, useState } from 'react';
import styles from './ChatQueue.module.css';

const EMPTY_MESSAGES = Object.freeze([]);

function attachmentLabel(attachments) {
  if (!Array.isArray(attachments) || attachments.length === 0) {
    return '';
  }
  return attachments
    .map((attachment) => attachment?.name || attachment?.filename || '')
    .filter(Boolean)
    .join(', ');
}

function reorderedIds(messages, messageId, destinationId) {
  const ids = messages.filter((message) => message.status === 'queued').map((message) => message.id);
  const sourceIndex = ids.indexOf(messageId);
  const destinationIndex = ids.indexOf(destinationId);
  if (sourceIndex < 0 || destinationIndex < 0 || sourceIndex === destinationIndex) {
    return null;
  }
  ids.splice(sourceIndex, 1);
  ids.splice(destinationIndex, 0, messageId);
  return ids;
}

function QueueButton({ label, children, ...props }) {
  return (
    <button type="button" className={styles.action} aria-label={label} title={label} {...props}>
      {children}
    </button>
  );
}

/** Server-owned queued user messages. No optimistic item/order state is kept here. */
export default function ChatQueue({ snapshot, isMutating, onDelete, onReorder, onSendNow }) {
  const [draggingId, setDraggingId] = useState(null);
  const messages = snapshot?.messages ?? EMPTY_MESSAGES;
  const queuedMessages = messages.filter((message) => message.status === 'queued');
  const move = useCallback((messageId, destinationId) => {
    const order = reorderedIds(messages, messageId, destinationId);
    if (order) {
      void onReorder(order);
    }
  }, [messages, onReorder]);

  if (messages.length === 0) {
    return null;
  }

  return (
    <section className={styles.queue} aria-label="Queued messages">
      <div className={styles.heading}>Queued messages <span>{messages.length}</span></div>
      <ol className={styles.list}>
        {messages.map((message, index) => {
          const mutable = message.status === 'queued' && !isMutating;
          const attachments = attachmentLabel(message.attachments);
          return (
            <li
              key={message.id}
              className={styles.item}
              draggable={mutable}
              onDragStart={(event) => {
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', message.id);
                setDraggingId(message.id);
              }}
              onDragOver={(event) => {
                if (mutable && draggingId && draggingId !== message.id) {
                  event.preventDefault();
                  event.dataTransfer.dropEffect = 'move';
                }
              }}
              onDrop={(event) => {
                event.preventDefault();
                const sourceId = event.dataTransfer.getData('text/plain') || draggingId;
                if (mutable && sourceId && sourceId !== message.id) {
                  move(sourceId, message.id);
                }
                setDraggingId(null);
              }}
              onDragEnd={() => setDraggingId(null)}
            >
              <div className={styles.message}>
                <span className={styles.status}>
                  {message.status === 'sending' ? 'Sending' : (message.send_now ? 'Sending next' : `Queued ${index + 1}`)}
                </span>
                <span className={styles.text}>{message.text}</span>
                {attachments ? <span className={styles.attachments}>{attachments}</span> : null}
              </div>
              <div className={styles.actions}>
                {message.status === 'queued' ? (
                  <>
                    <QueueButton
                      label="Move queued message earlier"
                      disabled={!mutable || queuedMessages.indexOf(message) === 0}
                      onClick={() => move(message.id, queuedMessages[queuedMessages.indexOf(message) - 1]?.id)}
                    >↑</QueueButton>
                    <QueueButton
                      label="Move queued message later"
                      disabled={!mutable || queuedMessages.indexOf(message) === queuedMessages.length - 1}
                      onClick={() => move(message.id, queuedMessages[queuedMessages.indexOf(message) + 1]?.id)}
                    >↓</QueueButton>
                    <QueueButton label="Send queued message now" disabled={!mutable} onClick={() => { void onSendNow(message.id); }}>Send now</QueueButton>
                    <QueueButton label="Delete queued message" disabled={!mutable} onClick={() => { void onDelete(message.id); }}>×</QueueButton>
                  </>
                ) : (
                  <span className={styles.sending} aria-label="Message is sending">Sending…</span>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
