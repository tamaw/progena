import { stripPageContextBlock } from '../../../lib/pageContext';
import { normalizeBackendChatMessage } from './chatSessionsModel';

/**
 * Normalize persisted Quick Chat history through the same path as the main
 * conversation, then hide page context only from a user's rendered text.
 */
export function normalizeQuickChatHistoryMessage(message, index) {
  const normalized = normalizeBackendChatMessage(message, index);
  if (!normalized) return null;
  if (normalized.role !== 'user') return normalized;
  return {
    ...normalized,
    inputContent: normalized.content,
    content: stripPageContextBlock(normalized.content),
  };
}

/** Preserve hidden page context when resending local Quick Chat history. */
export function toQuickChatInputHistory(history) {
  return history.map((turn) => ({
    role: turn.role,
    content: turn.inputContent ?? turn.content,
  }));
}

/** Load the full transcript, discarding a refresh superseded by a new turn. */
export async function loadQuickChatHistory(fetchPage, isCurrent) {
  const pages = [];
  let beforeMessageId;
  let hasMore;
  do {
    if (!isCurrent()) return null;
    const page = await fetchPage(beforeMessageId);
    if (!isCurrent()) return null;
    pages.push(page.messages);
    hasMore = page.has_more;
    if (hasMore) beforeMessageId = page.messages[0].id;
  } while (hasMore);
  return pages.reverse().flat()
    .map(normalizeQuickChatHistoryMessage)
    .filter(Boolean);
}
