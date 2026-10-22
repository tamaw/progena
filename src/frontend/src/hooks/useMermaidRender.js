import { useEffect } from 'react';
import { renderMermaidDiagrams } from '../lib/mermaid';

// Renders any `<pre class="mermaid">` blocks inside `ref`'s element after the
// markdown HTML they came from is mounted/updated. Pass the same value that
// produced the HTML (e.g. the raw markdown or message content) as `content`
// so the effect re-runs when it changes, including on each streamed chat
// token -- renderMermaidDiagrams() is cheap to call repeatedly since it only
// ever touches not-yet-processed nodes.
export function useMermaidRender(ref, content) {
  useEffect(() => {
    if (!content) return;
    renderMermaidDiagrams(ref.current);
  }, [ref, content]);
}
