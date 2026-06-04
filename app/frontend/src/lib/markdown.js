import hljs from 'highlight.js/lib/common';

export function renderMarkdownToHtml(content) {
  if (!content) return '';

  // Pull `:::writing … :::` directives out first so their inner content renders
  // as its own markdown document inside a notepad container (see chat-panel.css).
  const writingBlocks = [];
  const pushWritingBlock = (inner) => {
    const index = writingBlocks.length;
    writingBlocks.push(renderWritingBlock(inner));
    return `@@WRITINGBLOCK_${index}@@`;
  };
  let formatted = content.replace(
    /^[ \t]*:::writing[ \t]*\n([\s\S]*?)\n[ \t]*:::[ \t]*$/gm,
    (match, inner) => pushWritingBlock(inner),
  );
  // An unterminated block (still streaming) renders as a notepad anyway so the
  // styling appears as the text arrives rather than snapping in at the close.
  formatted = formatted.replace(
    /^[ \t]*:::writing[ \t]*\n([\s\S]*)$/m,
    (match, inner) => pushWritingBlock(inner),
  );

  const codeBlocks = [];
  formatted = formatted.replace(/```(\w*)\n([\s\S]*?)```/g, (match, lang, code) => {
    const codeBlockIndex = codeBlocks.length;
    codeBlocks.push(renderCodeBlock(lang, code));
    return `@@CODEBLOCK_${codeBlockIndex}@@`;
  });

  formatted = escapeHtml(formatted);

  formatted = formatted.replace(/@@CODEBLOCK_(\d+)@@/g, (match, index) => codeBlocks[Number(index)] || '');

  formatted = formatted.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

  formatted = formatted.replace(/^(#{1,6})\s+(.+)$/gm, (match, hashes, text) => {
    const level = hashes.length;
    return `<h${level} class="md-heading">${text}</h${level}>`;
  });

  formatted = formatted.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  formatted = formatted.replace(
    /(^|[\s([{>])__([^_\n]+?)__(?=$|[\s)\]}<.,;:!?])/g,
    '$1<strong>$2</strong>',
  );

  formatted = formatted.replace(
    /(^|[\s([{>])\*([^*\n]+?)\*(?=$|[\s)\]}<.,;:!?])/g,
    '$1<em>$2</em>',
  );
  formatted = formatted.replace(
    /(^|[\s([{>])_([^_\n]+?)_(?=$|[\s)\]}<.,;:!?])/g,
    '$1<em>$2</em>',
  );

  formatted = formatted.replace(/~~([^~]+)~~/g, '<del>$1</del>');

  formatted = formatted.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    (match, label, href) => renderMarkdownLink(label, href),
  );

  formatted = formatted.replace(/^---$/gm, '<hr class="md-hr">');
  formatted = formatted.replace(/^\*\*\*$/gm, '<hr class="md-hr">');

  formatted = formatted.replace(/^&gt;\s*(.+)$/gm, '<blockquote>$1</blockquote>');
  formatted = formatted.replace(/<\/blockquote>\s*<blockquote>/g, '<br>');

  formatted = formatted.replace(/^[-*+]\s+(.+)$/gm, '<li class="md-li">$1</li>');
  formatted = formatted.replace(/^\d+\.\s+(.+)$/gm, '<li class="md-li-ordered">$1</li>');

  formatted = formatted.replace(/\[ \]\s+(.+)/g, '<span class="task-item">☐ $1</span>');
  formatted = formatted.replace(/\[x\]\s+(.+)/gi, '<span class="task-item completed">☑ $1</span>');

  formatted = formatted.replace(/\n\n/g, '</p><p>');
  formatted = formatted.replace(/\n/g, '<br>');

  if (!formatted.startsWith('<') || formatted.startsWith('<br>')) {
    formatted = `<p>${formatted}</p>`;
  }

  // Restore notepad blocks AFTER list normalization. The placeholder is inert
  // text at this point, so normalizeListMarkup never sees the (already
  // normalized) <li> nested inside the injected notepad and won't strip it.
  formatted = normalizeListMarkup(formatted);

  return restoreWritingBlocks(formatted, writingBlocks);
}

function renderWritingBlock(inner) {
  const body = renderMarkdownToHtml(inner.replace(/^\n+/, '').replace(/\s+$/, ''));
  return (
    '<div class="writing-block" role="note">'
    + '<div class="writing-block__tab">Notes</div>'
    + `<div class="writing-block__sheet">${body}</div>`
    + '</div>'
  );
}

function restoreWritingBlocks(html, writingBlocks) {
  if (writingBlocks.length === 0) return html;
  // Drop the wrapping <p>…</p> the paragraph pass put around a lone placeholder
  // so the block-level notepad isn't nested inside an inline paragraph.
  return html
    .replace(
      /<p>\s*(?:<br>\s*)*@@WRITINGBLOCK_(\d+)@@(?:\s*<br>)*\s*<\/p>/g,
      (match, index) => writingBlocks[Number(index)] || '',
    )
    .replace(
      /@@WRITINGBLOCK_(\d+)@@/g,
      (match, index) => writingBlocks[Number(index)] || '',
    );
}

function normalizeListMarkup(html) {
  const container = document.createElement('div');
  container.innerHTML = html;

  if (!container.querySelector('li.md-li, li.md-li-ordered')) {
    return html;
  }

  const children = Array.from(container.childNodes);
  const fragment = document.createDocumentFragment();
  let activeList = null;
  let mutated = false;

  const isListItem = (node) =>
    node instanceof HTMLElement && (node.matches('li.md-li') || node.matches('li.md-li-ordered'));

  const isBreak = (node) => node instanceof HTMLElement && node.tagName === 'BR';

  const trimTrailingBreaks = (node) => {
    while (isBreak(node.lastChild)) {
      node.lastChild.remove();
      mutated = true;
    }
  };

  children.forEach((node, index) => {
    const previous = children[index - 1];
    const next = children[index + 1];

    if (node instanceof HTMLParagraphElement) {
      trimTrailingBreaks(node);
      if (!node.textContent?.trim() && (isListItem(previous) || isListItem(next))) {
        mutated = true;
        return;
      }

      activeList = null;
      fragment.appendChild(node);
      return;
    }

    if (isListItem(node)) {
      const tagName = node.matches('li.md-li-ordered') ? 'ol' : 'ul';
      if (!activeList || activeList.tagName.toLowerCase() !== tagName) {
        activeList = document.createElement(tagName);
        activeList.className = tagName === 'ol' ? 'md-ol' : 'md-ul';
        fragment.appendChild(activeList);
      }

      activeList.appendChild(node);
      mutated = true;
      return;
    }

    if (isBreak(node) && (isListItem(previous) || isListItem(next))) {
      mutated = true;
      return;
    }

    activeList = null;
    fragment.appendChild(node);
  });

  if (mutated) {
    container.replaceChildren(fragment);
  }

  return container.innerHTML;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function decodeHtmlEntities(text) {
  const div = document.createElement('div');
  div.innerHTML = text;
  return div.textContent || '';
}

function sanitizeMarkdownHref(href) {
  const decodedHref = decodeHtmlEntities(href).trim();
  if (!decodedHref || decodedHref.startsWith('//')) {
    return null;
  }

  if (
    decodedHref.startsWith('#')
    || decodedHref.startsWith('/')
    || decodedHref.startsWith('./')
    || decodedHref.startsWith('../')
    || decodedHref.startsWith('?')
  ) {
    return {
      href: escapeHtml(decodedHref),
      external: false,
    };
  }

  if (/^[a-zA-Z][a-zA-Z\d+.-]*:/.test(decodedHref)) {
    try {
      const parsed = new URL(decodedHref);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
        return {
          href: escapeHtml(decodedHref),
          external: true,
        };
      }
    } catch {
      return null;
    }

    return null;
  }

  return {
    href: escapeHtml(decodedHref),
    external: false,
  };
}

function renderMarkdownLink(label, href) {
  const sanitizedHref = sanitizeMarkdownHref(href);
  if (!sanitizedHref) {
    return label;
  }

  const externalAttrs = sanitizedHref.external ? ' target="_blank" rel="noopener noreferrer"' : '';
  return `<a href="${sanitizedHref.href}"${externalAttrs}>${label}</a>`;
}

function renderCodeBlock(language, code) {
  const normalizedLanguage = typeof language === 'string' ? language.trim().toLowerCase() : '';
  const trimmedCode = code.replace(/\n$/, '');
  const safeLanguage = normalizedLanguage ? escapeHtml(normalizedLanguage) : '';

  let highlightedValue = escapeHtml(trimmedCode);
  let languageClass = '';

  if (trimmedCode) {
    if (normalizedLanguage && hljs.getLanguage(normalizedLanguage)) {
      highlightedValue = hljs.highlight(trimmedCode, { language: normalizedLanguage }).value;
      languageClass = ` language-${safeLanguage}`;
    } else {
      const autoResult = hljs.highlightAuto(trimmedCode);
      highlightedValue = autoResult.value;
      languageClass = autoResult.language ? ` language-${escapeHtml(autoResult.language)}` : '';
    }
  }

  const langLabel = safeLanguage ? `<span class="code-lang">${safeLanguage}</span>` : '';
  return `<div class="code-block">${langLabel}<pre><code class="hljs${languageClass}">${highlightedValue}</code></pre></div>`;
}
