

// Mirrors the server-side placeholder grammar: {root} or {root.a.b} where each
// segment matches [A-Za-z_][A-Za-z0-9_]*. JSON literals like {"a": 1} are ignored.
// thread_id is reserved and never treated as a placeholder root.
// `excludeThreadId` preserves prompt nodes' implicit thread behavior while
// letting format nodes expose every placeholder root as a real input port.
export function parsePromptPlaceholders(text, excludeThreadId = true) {
  const roots = [];
  const placeholders = [];
  const seen = new Set();
  const regex = /\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}/g;
  let match;
  while ((match = regex.exec(text)) !== null) {
    const full = match[1];
    const root = full.split('.')[0];
    if (root === 'thread_id' && excludeThreadId) continue;
    placeholders.push(full);
    if (!seen.has(root)) {
      seen.add(root);
      roots.push(root);
    }
  }
  return { roots, placeholders };
}

export function derivePromptInputSchema(text) {
  const { roots } = parsePromptPlaceholders(text);
  const properties = { thread_id: { type: 'string' } };
  roots.forEach((root) => {
    properties[root] = {};
  });
  return {
    type: 'object',
    ...(roots.length ? { required: roots } : {}),
    properties,
  };
}

export function deriveFormatInputSchema(text) {
  const { roots } = parsePromptPlaceholders(text, false);
  return {
    type: 'object',
    ...(roots.length ? { required: roots } : {}),
    properties: Object.fromEntries(roots.map((root) => [root, {}])),
  };
}

export function formatTemplatePatch(text) {
  return { template: text, inputSchema: deriveFormatInputSchema(text) };
}

export function promptTemplatePatch(text) {
  return { template: text, inputSchema: derivePromptInputSchema(text) };
}

export function instructionPatch(path, text) {
  return {
    instruction: path || null,
    template: null,
    inputSchema: derivePromptInputSchema(text),
  };
}
