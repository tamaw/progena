import { useCallback, useState } from 'react';
import { formatTimestamp } from '../models/messageRenderUtils';
import { EntryLine } from './ChatEntryLine';
import StickyChatEntry from './StickyChatEntry';
import entryStyles from './StickyChatEntry.module.css';
import styles from './ChatQueue.module.css';

const EMPTY_MESSAGES = Object.freeze([]);

function attachmentLabel(attachments) {
  if (!Array.isArray(attachments) || attachments.length === 0) return '';
  return attachments.map((attachment) => attachment?.name || attachment?.filename || '').filter(Boolean).join(', ');
}

function reorderedIds(messages, messageId, destinationId) {
  const ids = messages.filter((message) => message.status === 'queued').map((message) => message.id);
  const sourceIndex = ids.indexOf(messageId);
  const destinationIndex = ids.indexOf(destinationId);
  if (sourceIndex < 0 || destinationIndex < 0 || sourceIndex === destinationIndex) return null;
  ids.splice(sourceIndex, 1);
  ids.splice(destinationIndex, 0, messageId);
  return ids;
}

function QueueButton({ label, caption, children, onClick, ...props }) {
  return (
    <button
      type="button"
      className="msg-action-btn"
      aria-label={label}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onClick(event);
      }}
      {...props}
    >
      {children}
      <span className="msg-action-btn__label">{caption}</span>
    </button>
  );
}

function SendNowIcon() {
  return <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m2.5 7.7 10.7-4.6-3.9 10.1-2-4.1-4.8-1.4Z" /><path d="m7.3 9.1 2.5-2.4" /></svg>;
}

function CancelIcon() {
  return <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true"><path d="m4.5 4.5 7 7m0-7-7 7" /></svg>;
}

function MoveEarlierIcon() {
  return <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m8 12V4m-3 3 3-3 3 3" /></svg>;
}

function MoveLaterIcon() {
  return <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M8 4v8m-3-3 3 3 3-3" /></svg>;
}

export default function ChatQueue({ snapshot, isMutating, onDelete, onReorder, onSendNow }) {
  const [draggingId, setDraggingId] = useState(null);
  const messages = snapshot?.messages ?? EMPTY_MESSAGES;
  const queuedMessages = messages.filter((message) => message.status === 'queued');
  const move = useCallback((messageId, destinationId) => {
    const order = reorderedIds(messages, messageId, destinationId);
    if (order) void onReorder(order);
  }, [messages, onReorder]);
  if (messages.length === 0) return null;
  return (
    <section className={`chat-transcript ${entryStyles.region} ${styles.queue}`} aria-label="Queued messages">
      {messages.map((message, index) => {
        const mutable = message.status === 'queued' && !isMutating;
        const queuedIndex = queuedMessages.indexOf(message);
        const attachments = attachmentLabel(message.attachments);
        const status = message.status === 'sending' ? 'Sending' : 'Queued';
        const summary = message.status === 'sending'
          ? 'Message is sending'
          : (message.send_now ? 'Sending next' : `Message ${index + 1}`);
        const time = formatTimestamp(message.created_at);
        return (
          <StickyChatEntry
            key={message.id}
            badgeClassName="entry__badge--user"
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
              if (mutable && sourceId && sourceId !== message.id) move(sourceId, message.id);
              setDraggingId(null);
            }}
            onDragEnd={() => setDraggingId(null)}
          >
            <EntryLine
              title={status}
              text={<span className="tool-call-entry__summary">{summary}</span>}
              summaryLabel={`${status}: ${summary}`}
              defaultOpen
              trailing={
                <>
                  {message.status === 'queued' ? (
                    <span className="message-actions msg__actions">
                      <QueueButton label="Move queued message earlier" caption="Earlier" disabled={!mutable || queuedIndex === 0} onClick={() => move(message.id, queuedMessages[queuedIndex - 1]?.id)}><MoveEarlierIcon /></QueueButton>
                      <QueueButton label="Move queued message later" caption="Later" disabled={!mutable || queuedIndex === queuedMessages.length - 1} onClick={() => move(message.id, queuedMessages[queuedIndex + 1]?.id)}><MoveLaterIcon /></QueueButton>
                      <QueueButton label="Send queued message now" caption="Send now" disabled={!mutable} onClick={() => { void onSendNow(message.id); }}><SendNowIcon /></QueueButton>
                      <QueueButton label="Cancel queued message" caption="Cancel" disabled={!mutable} onClick={() => { void onDelete(message.id); }}><CancelIcon /></QueueButton>
                    </span>
                  ) : null}
                  {time && <span className="msg__time entry__time">{time}</span>}
                </>
              }
            >
              <div className="message-content">
                <span>{message.text}</span>
                {attachments ? <div className="muted">{attachments}</div> : null}
              </div>
            </EntryLine>
          </StickyChatEntry>
        );
      })}
    </section>
  );
}
