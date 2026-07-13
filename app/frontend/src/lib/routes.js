// Shared helpers for id + human-readable-slug detail routes (e.g. /chat/:id/:slug,
// /inbox/:id/:slug, /tasks/:id/:slug). The slug is a length-capped, url-safe label
// derived from a title -- display context only, never read back to select an
// entity; selection is always driven by the id segment alone.
import { useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

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

// Keeps the address bar in sync with whichever entity is actually selected on
// a list/detail page (Chat, Inbox, Tasks, Tools, Knowledge, Schedules). Pass
// `basePath: null` to disable the sync (e.g. Tasks embedded in a project's
// kanban board, which doesn't own the /tasks URL). Always navigates with
// `replace: true` so selecting items doesn't pollute browser history.
export function useEntityUrlSync(basePath, id, title) {
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(() => {
    if (!basePath) {
      return;
    }
    const nextPath = buildEntityPath(basePath, id, title || id);
    if (nextPath !== location.pathname) {
      navigate(nextPath, { replace: true });
    }
  }, [basePath, id, title, location.pathname, navigate]);
}
