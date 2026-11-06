// Pure helpers for the Inbox page: a plain read/unread state machine, kept
// free of React so it's unit-testable in isolation, mirroring
// knowledgePageModel.js / tasksPageModel.js. Approve/reject is a separate,
// uncolored signal (requires_approval + decision) layered on top -- it does
// not get its own status/color, since red/orange read as "error"/"warning"
// for something that's neither.

export const INBOX_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'unread', label: 'Unread' },
  { value: 'pending', label: 'Pending' },
  { value: 'archived', label: 'Archived' },
];

export const INBOX_STATUS_META = {
  unread: { label: 'Unread', tone: 'ok', dotClass: 'chat-session-card__dot--inbox-unread' },
  read: { label: 'Read', tone: 'idle', dotClass: 'chat-session-card__dot--inbox-read' },
};

export function getInboxMessageStatus(message) {
  return message?.read ? 'read' : 'unread';
}

// A message needs a decision as long as it requires approval and hasn't
// gotten one -- independent of read/unread, and not colored.
export function inboxMessageNeedsDecision(message) {
  return Boolean(message?.requires_approval) && !message?.decision;
}

