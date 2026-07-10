// The chat detail route is /chat/:threadId/:titleSlug -- the id alone drives
// which conversation loads. The slug is a human-readable label appended for
// the user's benefit (bookmarks, browser history, sharing) and is never read
// back to select a session.
import { slugifyTitle, buildEntityPath } from '../../lib/entityUrl';

export const slugifyChatTitle = slugifyTitle;

export function buildChatPath(threadId, title) {
  return buildEntityPath('/chat', threadId, title);
}
