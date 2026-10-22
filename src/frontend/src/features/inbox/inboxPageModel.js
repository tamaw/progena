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

export function matchesInboxFilter(message, filter) {
  if (filter === 'archived') return Boolean(message.archived);
  if (message.archived) return false;
  if (filter === 'unread') return getInboxMessageStatus(message) === 'unread';
  if (filter === 'pending') return inboxMessageNeedsDecision(message);
  return true;
}

export function matchesInboxQuery(message, query) {
  const needle = String(query || '').trim().toLowerCase();
  if (!needle) return true;
  return (
    String(message.title || '').toLowerCase().includes(needle)
    || String(message.body || '').toLowerCase().includes(needle)
  );
}

export function filterInboxMessages(messages, filter, query) {
  if (!Array.isArray(messages)) return [];
  return messages.filter((message) => matchesInboxFilter(message, filter) && matchesInboxQuery(message, query));
}

export function sortInboxMessages(messages, sort) {
  const list = Array.isArray(messages) ? messages : [];
  if (sort === 'Name') {
    return [...list].sort((a, b) => String(a.title || '').localeCompare(String(b.title || '')));
  }
  // Server order is already created_at desc ("Recent").
  return list;
}
