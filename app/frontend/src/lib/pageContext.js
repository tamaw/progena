// Derives {page, id} from the current URL for quick chat's automatic page
// context (see AppRuntimeShell.jsx's QuickChatPanel). Every detail page in
// this app follows one URL convention -- /<basePath>/:id/:slug?, the same
// convention buildEntityPath/useEntityUrlSync in routes.js enforce (id is
// always the first path segment after the base path; the slug is
// display-only and never read back) -- so "what page, what id" is fully
// recoverable from the pathname alone.
const PAGE_ROUTES = [
  { basePath: '/chat', page: 'chat' },
  { basePath: '/inbox', page: 'inbox' },
  { basePath: '/dashboard', page: 'dashboard' },
  { basePath: '/tasks', page: 'tasks' },
  { basePath: '/workflows', page: 'workflows' },
  { basePath: '/sequences', page: 'sequences' },
  { basePath: '/agents', page: 'agents' },
  { basePath: '/plugins', page: 'plugins', idSegmentIndex: 1 }, // /plugins/:marketplace/:plugin
  { basePath: '/tools', page: 'tools' },
  { basePath: '/knowledge', page: 'knowledge' },
  { basePath: '/schedules', page: 'schedules' },
  { basePath: '/channels', page: 'channels' },
  { basePath: '/repositories', page: 'repositories' },
];

function matchRoute(pathname) {
  return PAGE_ROUTES.find(
    (route) => pathname === route.basePath || pathname.startsWith(`${route.basePath}/`),
  ) || null;
}

/** Returns {page, id} for the current pathname, or null if it matches no known page. */
export function derivePageContext(pathname) {
  const route = matchRoute(typeof pathname === 'string' ? pathname : '');
  if (!route) {
    return null;
  }
  const segments = pathname.slice(route.basePath.length).split('/').filter(Boolean);
  const rawId = segments[route.idSegmentIndex ?? 0];
  return { page: route.page, id: rawId ? decodeURIComponent(rawId) : null };
}

/** Renders {page, id} as the fenced JSON block sent ahead of the user's message. */
export function formatPageContextBlock({ page, id }) {
  const payload = id ? { page, id } : { page };
  return `\`\`\`page-context\n${JSON.stringify(payload)}\n\`\`\``;
}

// The backend persists whatever text was actually sent -- context block
// included -- as the canonical message content. Local optimistic state never
// includes the block (see AppRuntimeShell's submit()), but anything fetched
// back from the backend (e.g. quick chat rehydrating its history after a
// page reload) does, so it must be stripped again at display time.
const PAGE_CONTEXT_BLOCK_PATTERN = /^```page-context\n\{[^\n]*\}\n```\n\n/;

export function stripPageContextBlock(content) {
  return typeof content === 'string' ? content.replace(PAGE_CONTEXT_BLOCK_PATTERN, '') : content;
}
