// Renders `<pre class="mermaid">` blocks (produced by lib/markdown.js's
// fenced ```mermaid``` handling) into inline SVG diagrams. Loaded lazily so
// pages that never show a diagram never pay for the mermaid bundle.

let mermaidModulePromise = null;

function loadMermaid() {
  if (!mermaidModulePromise) {
    mermaidModulePromise = import('mermaid').then(({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'default',
      });
      return mermaid;
    });
  }
  return mermaidModulePromise;
}

// Renders every not-yet-processed `pre.mermaid` element under `root`. Safe to
// call repeatedly (e.g. once per streamed chat token) -- mermaid marks each
// node `data-processed` once rendered, and this only ever selects nodes
// without that attribute. A malformed/incomplete diagram (mid-stream) throws
// from mermaid.run(); caught here so the raw diagram source stays visible as
// a fallback instead of leaving the rest of the message unrendered.
export async function renderMermaidDiagrams(root) {
  if (!root) return;
  const nodes = root.querySelectorAll('pre.mermaid:not([data-processed])');
  if (nodes.length === 0) return;

  try {
    const mermaid = await loadMermaid();
    await mermaid.run({ nodes: Array.from(nodes) });
  } catch (error) {
    console.error('Mermaid diagram render failed', error);
  }
}
