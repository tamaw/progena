// The chat detail route is /chat/:threadId/:titleSlug -- the id alone drives
// which conversation loads. The slug is a human-readable label appended for
// the user's benefit (bookmarks, browser history, sharing) and is never read
// back to select a session.
const MAX_SLUG_LENGTH = 60;

export function slugifyChatTitle(title, maxLength = MAX_SLUG_LENGTH) {
  const slug = String(title || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug.slice(0, maxLength).replace(/-+$/g, '');
}

export function buildChatPath(threadId, title) {
  if (!threadId) {
    return '/chat';
  }
  const encodedId = encodeURIComponent(threadId);
  const slug = slugifyChatTitle(title);
  return slug ? `/chat/${encodedId}/${encodeURIComponent(slug)}` : `/chat/${encodedId}`;
}
