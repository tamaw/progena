// Shared helper for id + human-readable-slug detail routes (e.g. /chat/:id/:slug,
// /inbox/:id/:slug). The slug is a length-capped, url-safe label derived from a
// title -- display context only, never read back to select an entity; selection
// is always driven by the id segment alone.
const MAX_SLUG_LENGTH = 60;

export function slugifyTitle(title, maxLength = MAX_SLUG_LENGTH) {
  const slug = String(title || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug.slice(0, maxLength).replace(/-+$/g, '');
}

export function buildEntityPath(basePath, id, title, maxLength = MAX_SLUG_LENGTH) {
  if (!id) {
    return basePath;
  }
  const encodedId = encodeURIComponent(id);
  const slug = slugifyTitle(title, maxLength);
  return slug ? `${basePath}/${encodedId}/${encodeURIComponent(slug)}` : `${basePath}/${encodedId}`;
}
