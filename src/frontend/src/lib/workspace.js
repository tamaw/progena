import { BROWSER_STORAGE_KEY } from './browserContracts';

// Multi-backend "workspace" routing. Each workspace is identified purely by
// the backend's host[:port] -- there is no separate id/label indirection.
// The active workspace lives in the URL itself: /w/<host>/<rest-of-app>.
// Switching workspaces happens in place (see context/WorkspaceContext.jsx's
// `switchWorkspace`) -- this module only owns URL parsing/building and the
// localStorage host registry, no navigation.

const WORKSPACE_PATH_RE = /^\/w\/([^/]+)(\/.*)?$/;
// A DNS label per RFC 1035 (alnum, internal hyphens, 1-63 chars), one or more
// dot-separated, optionally followed by :port. No scheme, path, or userinfo --
// those get stripped by normalizeWorkspaceHost before this ever runs.
const HOST_RE = /^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*(:[0-9]{1,5})?$/;
const SCHEME_RE = /^[a-z][a-z0-9+.-]*:\/\//i;
// Loopback hosts: switching between these during local dev testing (a
// second local backend on another port) never crosses onto the open
// internet, so there is nothing for TLS to protect there -- forcing https
// just breaks a plain-http local dev server. Gated to `import.meta.env.DEV`
// (a real Vite build flag, not a runtime guess) so a production build never
// carries this allowance. RFC 1918 private/LAN hosts get NO such exemption:
// a private IP is not inherently a dev backend -- a TLS-enforced LAN
// deployment still needs HTTPS -- and the host string alone can't say
// whether it actually speaks plain http, so those always resolve to https
// like any other switched-to host.
const LOCALHOST_RE = /^(localhost|127(\.\d{1,3}){3}|\[?::1\]?)(:\d{1,5})?$/i;
const REGISTRY_KEY = BROWSER_STORAGE_KEY.WORKSPACE_REGISTRY;

/** Parse a pathname into `{ host, rest }`, or `null` if it isn't workspace-scoped. */
export function parseWorkspacePath(pathname) {
  const match = WORKSPACE_PATH_RE.exec(pathname);
  if (!match) {
    return null;
  }
  return { host: match[1], rest: match[2] || '/' };
}

/** The host[:port] the frontend itself was served from -- the default workspace. */
export function currentPageHost() {
  return window.location.host;
}

/**
 * The backend origin a workspace host resolves to. The frontend's own host
 * (the default workspace -- see `currentPageHost`) reuses whatever scheme
 * the page itself was served over: it's the same origin, so there's no
 * trust boundary being crossed. A bare `localhost`/loopback host does the
 * same, but ONLY in a Vite dev build (`import.meta.env.DEV`) -- that's
 * exclusively the "second local backend on another port" dev workflow.
 * Every other host, including private/LAN IPs, is an explicit cross-origin
 * workspace switch and always resolves to `https://` -- no insecure
 * fallback for a backend the user typed in or switched to.
 */
export function resolveWorkspaceOrigin(host) {
  if (host === currentPageHost()) {
    return `${window.location.protocol}//${host}`;
  }
  if (import.meta.env.DEV && LOCALHOST_RE.test(host)) {
    return `${window.location.protocol}//${host}`;
  }
  return `https://${host}`;
}

/**
 * Build the path for `host`. When `host` is the page's own host (the
 * default workspace), the /w/ prefix is redundant -- it's the host every
 * unprefixed URL already resolves to -- so this returns the plain `rest`
 * path instead. Any other host needs the explicit /w/<host> prefix to
 * disambiguate which backend the URL targets.
 */
export function buildWorkspacePath(host, rest = '/') {
  const suffix = rest.startsWith('/') ? rest : `/${rest}`;
  if (host === currentPageHost()) {
    return suffix;
  }
  return `/w/${host}${suffix}`;
}

function readRegistry() {
  try {
    const raw = localStorage.getItem(REGISTRY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((host) => typeof host === 'string' && host) : [];
  } catch {
    return [];
  }
}

function writeRegistry(hosts) {
  localStorage.setItem(REGISTRY_KEY, JSON.stringify(hosts));
}

/** Known workspace hosts, most-recently-added last. */
export function getWorkspaceRegistry() {
  return readRegistry();
}

/** Registers `host` if it isn't already known. Safe to call on every workspace visit. */
export function ensureWorkspaceRegistered(host) {
  const hosts = readRegistry();
  if (!hosts.includes(host)) {
    writeRegistry([...hosts, host]);
  }
}

const DEFAULT_WORKSPACE_KEY = BROWSER_STORAGE_KEY.DEFAULT_WORKSPACE;

/**
 * The host to land on for a bare `/` visit -- the last workspace the user
 * *deliberately switched to* (see WorkspaceContext's `switchWorkspace`).
 * Never set from just loading a URL, so opening someone else's shared
 * workspace link never silently hijacks your default.
 */
export function getDefaultWorkspaceHost() {
  const host = localStorage.getItem(DEFAULT_WORKSPACE_KEY);
  return typeof host === 'string' && host ? host : null;
}

export function setDefaultWorkspaceHost(host) {
  localStorage.setItem(DEFAULT_WORKSPACE_KEY, host);
}

/**
 * Normalizes free-form user input (which may include a pasted scheme, path,
 * or mixed case) down to a bare `hostname` or `hostname:port`. Returns `null`
 * if it isn't a valid host after stripping. The port is dev/test convenience
 * only -- production workspaces are expected to be bare hostnames on 443.
 */
export function normalizeWorkspaceHost(input) {
  if (typeof input !== 'string') {
    return null;
  }
  const value = input
    .trim()
    .replace(SCHEME_RE, '')
    .split(/[/?#]/)[0]
    .toLowerCase();
  return HOST_RE.test(value) ? value : null;
}

/** Validates + registers `host`. Returns `false` (and registers nothing) if invalid. */
export function addWorkspaceHost(host) {
  const normalized = normalizeWorkspaceHost(host);
  if (!normalized) {
    return false;
  }
  ensureWorkspaceRegistered(normalized);
  return true;
}

export function removeWorkspaceHost(host) {
  writeRegistry(readRegistry().filter((entry) => entry !== host));
}
