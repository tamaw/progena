export function buildPageTree(pages = []) {
  const root = { name: '', path: '', directories: [], pages: [] };
  const dirs = new Map();
  for (const page of pages) {
    const parts = String(page.path || '').split('/').filter(Boolean);
    if (!parts.length) continue;
    let node = root;
    let prefix = '';
    for (const part of parts.slice(0, -1)) {
      prefix = prefix ? `${prefix}/${part}` : part;
      if (!dirs.has(prefix)) {
        const child = { name: part, path: prefix, directories: [], pages: [] };
        dirs.set(prefix, child); node.directories.push(child);
      }
      node = dirs.get(prefix);
    }
    node.pages.push({ ...page, name: parts.at(-1) });
  }
  // Add synthetic index.md at each directory level if missing.
  const addSyntheticIndexes = (node, prefix = '') => {
    const hasIndex = node.pages.some((p) => p.name === 'index.md');
    if (!hasIndex) {
      node.pages.unshift({
        path: prefix ? `${prefix}/index.md` : 'index.md',
        kind: 'index',
        title: 'Index',
        name: 'index.md',
      });
    }
    for (const dir of node.directories) {
      addSyntheticIndexes(dir, dir.path);
    }
  };
  addSyntheticIndexes(root);
  const sort = (node) => {
    node.directories.sort((a, b) => a.name.localeCompare(b.name));
    node.pages.sort((a, b) => {
      const order = { index: 0, log: 1, concept: 2 };
      const aOrder = order[a.kind] ?? 2;
      const bOrder = order[b.kind] ?? 2;
      if (aOrder !== bOrder) return aOrder - bOrder;
      return String(a.title || a.name).localeCompare(String(b.title || b.name));
    });
    node.directories.forEach(sort);
    return node;
  };
  return sort(root);
}

export const dirOf = (path) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '');

// Resolve a relative markdown link against the page it appears on; null when it
// isn't a page in this base (external URL, anchor, absolute path, non-.md).
export function resolvePageLink(fromPath, href) {
  if (!href || /^[a-z][a-z0-9+.-]*:|^[/#]/i.test(href)) return null;
  const target = href.split('#')[0].split('?')[0];
  if (!target.endsWith('.md') && !target.endsWith('/')) return null;
  const parts = dirOf(fromPath).split('/').filter(Boolean);
  for (const part of target.split('/')) {
    if (part === '..') { if (!parts.length) return null; parts.pop(); } else if (part && part !== '.') parts.push(part);
  }
  const resolved = parts.join('/');
  return target.endsWith('/') ? `${resolved ? `${resolved}/` : ''}index.md` : resolved;
}
