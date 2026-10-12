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
