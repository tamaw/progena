import { renderMarkdownToHtml } from './markdown';

// ─── Markdown / HTML ──────────────────────────────────────────────────────

export function formatMessage(content) {
  return renderMarkdownToHtml(content);
}

// A foreground turn and its settled historical message mount under different
// owners. Keep a small LRU cache so that handoff or canonical reconciliation
// does not parse, sanitize, and highlight unchanged terminal Markdown twice.
const renderedMarkdownCache = new Map();

const MAX_RENDERED_MARKDOWN_ENTRIES = 16;

const MAX_RENDERED_MARKDOWN_CHARS = 2 * 1024 * 1024;

let renderedMarkdownChars = 0;

export function renderSafeMarkdown(content) {
  if (!content) return '';

  const cached = renderedMarkdownCache.get(content);
  if (cached !== undefined) {
    renderedMarkdownCache.delete(content);
    renderedMarkdownCache.set(content, cached);
    return cached;
  }

  const rendered = sanitizeHtmlFragment(formatMessage(content));
  const cacheCost = content.length + rendered.length;
  if (cacheCost > MAX_RENDERED_MARKDOWN_CHARS) {
    return rendered;
  }

  renderedMarkdownCache.set(content, rendered);
  renderedMarkdownChars += cacheCost;
  while (
    renderedMarkdownCache.size > MAX_RENDERED_MARKDOWN_ENTRIES
    || renderedMarkdownChars > MAX_RENDERED_MARKDOWN_CHARS
  ) {
    const [oldestContent, oldestRendered] = renderedMarkdownCache.entries().next().value;
    renderedMarkdownCache.delete(oldestContent);
    renderedMarkdownChars -= oldestContent.length + oldestRendered.length;
  }
  return rendered;
}

const ALLOWED_TAGS = new Set([
  'p', 'br', 'b', 'i', 'em', 'strong', 'code', 'pre', 'blockquote',
  'ul', 'ol', 'li', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr', 'span', 'div',
  'del', 'ins', 'sup', 'sub',
]);

const ALLOWED_ATTRS = new Set(['href', 'title', 'class', 'id', 'target', 'rel']);

function isSafeHref(rawValue) {
  const colonIdx = rawValue.trim().indexOf(':');
  if (colonIdx === -1) return true; // relative URL, no scheme
  // Validate scheme portion strictly — any non-alpha char (including control chars
  // browsers normalize away) makes this an unrecognised / potentially obfuscated scheme.
  const schemePart = rawValue.trim().slice(0, colonIdx);
  if (!/^[a-zA-Z][a-zA-Z0-9+.-]*$/.test(schemePart)) return false;
  const scheme = schemePart.toLowerCase();
  return scheme === 'http' || scheme === 'https' || scheme === 'mailto';
}

function sanitizeNode(node) {
  for (const child of Array.from(node.childNodes)) {
    if (child.nodeType === Node.ELEMENT_NODE) {
      const tag = child.tagName.toLowerCase();
      if (!ALLOWED_TAGS.has(tag)) {
        child.replaceWith(document.createTextNode(child.textContent || ''));
        continue;
      }
      for (const attr of Array.from(child.attributes)) {
        const name = attr.name.toLowerCase();
        if (!ALLOWED_ATTRS.has(name) || (name === 'href' && !isSafeHref(attr.value))) {
          child.removeAttribute(attr.name);
        }
      }
      sanitizeNode(child);
    }
  }
}

export function sanitizeHtmlFragment(html) {
  if (!html) return '';
  const template = document.createElement('template');
  template.innerHTML = html;
  sanitizeNode(template.content);
  const div = document.createElement('div');
  div.appendChild(template.content.cloneNode(true));
  return div.innerHTML;
}
