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

  // Images before plain links: `![alt](url)` would otherwise be matched by
  // the `[label](url)` pass below (it doesn't care about the leading `!`),
  // leaving a stray `!` in front of a link instead of rendering an image.
  formatted = formatted.replace(
    /!\[([^\]]*)\]\(([^)]+)\)/g,
    (match, alt, src) => renderMarkdownImage(alt, src),
  );

  formatted = formatted.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    (match, label, href) => renderMarkdownLink(label, href),
  );

  formatted = autolinkPlainUrls(formatted);

  formatted = formatted.replace(/^---$/gm, '<hr class="md-hr">');
  formatted = formatted.replace(/^\*\*\*$/gm, '<hr class="md-hr">');

  formatted = formatted.replace(/^&gt;\s*(.+)$/gm, '<blockquote>$1</blockquote>');
  formatted = formatted.replace(/<\/blockquote>\s*<blockquote>/g, '<br>');

  formatted = formatted.replace(/^[-*+]\s+(.+)$/gm, '<li class="md-li">$1</li>');
  formatted = formatted.replace(/^\d+\.\s+(.+)$/gm, '<li class="md-li-ordered">$1</li>');

  formatted = formatted.replace(/\[ \]\s+(.+)/g, '<span class="task-item">☐ $1</span>');
  formatted = formatted.replace(/\[x\]\s+(.+)/gi, '<span class="task-item completed">☑ $1</span>');

  // GFM tables. Runs after inline formatting (bold/links/code spans) so cell
  // content already carries its HTML, and after the list-item pass since
  // both operate on whole lines and table rows never collide with `- `/`1. `
  // list markers. Only the piped style (`| a | b |`) is guaranteed to be
  // detected -- a bare `a | b` / `- | -` table can be misread as a list
  // item by the pass above, same as any other line-based markdown here.
  formatted = convertMarkdownTables(formatted);

  formatted = formatted.replace(/\n\n/g, '</p><p>');
  formatted = formatted.replace(/\n/g, '<br>');

  if (!formatted.startsWith('<') || formatted.startsWith('<br>')) {
    formatted = `<p>${formatted}</p>`;
  }

  // Restore notepad blocks AFTER list normalization. The placeholder is inert
  // text at this point, so normalizeListMarkup never sees the (already
  // normalized) <li> nested inside the injected notepad and won't strip it.
  formatted = normalizeListMarkup(formatted);

  return restoreWritingBlocks(restoreCodeBlocks(formatted, codeBlocks), writingBlocks);
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

function restoreCodeBlocks(html, codeBlocks) {
  if (codeBlocks.length === 0) return html;
  // Keep fenced blocks opaque until every heading/list/table pass has finished.
  // Restoring a block earlier puts its literal `#`/`-` lines through markdown
  // normalization and can invalidate the complete surrounding document.
  return html
    .replace(
      /<p>\s*(?:<br>\s*)*@@CODEBLOCK_(\d+)@@(?:\s*<br>)*\s*<\/p>/g,
      (match, index) => codeBlocks[Number(index)] || '',
    )
    .replace(
      /@@CODEBLOCK_(\d+)@@/g,
      (match, index) => codeBlocks[Number(index)] || '',
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

const TABLE_SEPARATOR_ROW_RE = /^[ \t]*\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$/;

function isTableRowCandidate(line) {
  return typeof line === 'string' && line.includes('|') && line.trim() !== '';
}

function splitTableRow(line) {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  return trimmed.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, '|'));
}

function tableColumnAlignment(separatorCell) {
  const left = separatorCell.startsWith(':');
  const right = separatorCell.endsWith(':');
  if (left && right) return 'center';
  if (right) return 'right';
  if (left) return 'left';
  return null;
}

function tableCellAttr(align) {
  return align ? ` style="text-align:${align}"` : '';
}

function renderTable(headerCells, alignments, bodyRows) {
  const headHtml = headerCells
    .map((cell, index) => `<th${tableCellAttr(alignments[index])}>${cell}</th>`)
    .join('');
  const bodyHtml = bodyRows
    .map((row) => {
      const rowHtml = headerCells
        .map((_, index) => `<td${tableCellAttr(alignments[index])}>${row[index] ?? ''}</td>`)
        .join('');
      return `<tr>${rowHtml}</tr>`;
    })
    .join('');
  return `<table><thead><tr>${headHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`;
}

// Detects GFM-style tables: a header row immediately followed by a
// `| --- | --- |`-shaped separator row, then zero or more body rows. Consumes
// consecutive piped lines rather than a single regex since a table body can
// be any number of rows.
function convertMarkdownTables(text) {
  const lines = text.split('\n');
  const output = [];
  let i = 0;

  while (i < lines.length) {
    const headerLine = lines[i];
    const separatorLine = lines[i + 1];

    if (
      separatorLine !== undefined
      && isTableRowCandidate(headerLine)
      && TABLE_SEPARATOR_ROW_RE.test(separatorLine)
    ) {
      const headerCells = splitTableRow(headerLine);
      const alignments = splitTableRow(separatorLine).map(tableColumnAlignment);
      const bodyRows = [];
      let j = i + 2;
      while (j < lines.length && isTableRowCandidate(lines[j])) {
        bodyRows.push(splitTableRow(lines[j]));
        j += 1;
      }
      output.push(renderTable(headerCells, alignments, bodyRows));
      i = j;
      continue;
    }

    output.push(headerLine);
    i += 1;
  }

  return output.join('\n');
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  // textContent->innerHTML only escapes &, <, > -- quotes must be escaped
  // too since this output also lands inside attribute values (href, src,
  // alt), where a literal `"` breaks out of the attribute.
  return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
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
      decoded: decodedHref,
      external: false,
    };
  }

  if (/^[a-zA-Z][a-zA-Z\d+.-]*:/.test(decodedHref)) {
    try {
      const parsed = new URL(decodedHref);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
        return {
          href: escapeHtml(decodedHref),
          decoded: decodedHref,
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
    decoded: decodedHref,
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

// Chat renders markdown images as thumbnails in a bottom strip alongside
// tool-result images (see conversationModel.collectMessageImages(), which
// calls extractMarkdownImages() below), not inline -- so inline text just
// keeps a plain link to the image, the same as any other markdown link.
function renderMarkdownImage(alt, src) {
  return renderMarkdownLink(alt || src, src);
}

// Pulls `![alt](url)` images out of raw (pre-render) markdown so a caller
// can display them separately from the text, e.g. chat's bottom image strip.
// Skips fenced code blocks so a literal `![]()` shown as an example isn't
// mistaken for an image.
export function extractMarkdownImages(content) {
  if (!content) return [];
  const withoutCodeBlocks = content.replace(/```(?:\w*)\n[\s\S]*?```/g, '');
  const images = [];
  const imageRe = /!\[([^\]]*)\]\(([^)]+)\)/g;
  let match = imageRe.exec(withoutCodeBlocks);
  while (match !== null) {
    const [, alt, rawSrc] = match;
    const sanitized = sanitizeMarkdownHref(rawSrc.trim());
    if (sanitized) {
      images.push({ alt: alt.trim(), src: sanitized.decoded });
    }
    match = imageRe.exec(withoutCodeBlocks);
  }
  return images;
}

// Runs on already-escaped HTML text, after `[label](url)` conversion. Only
// bare `http(s)://` URLs are linked -- schemeless domains like "example.com"
// are left alone since they're too easy to confuse with ordinary text.
const BARE_URL_RE = /\bhttps?:\/\/[^\s<>"')\]]+/g;

function autolinkPlainUrls(text) {
  // `<a>` tags (from the `[label](url)` / `![alt](url)` passes) and inline-
  // code spans must not be touched -- otherwise a link's own label/href, or
  // a URL a user deliberately wrote as code, gets a second, broken link
  // wrapped around it. Pull them out into placeholders first, same
  // technique as code/writing blocks above, then restore once the bare-URL
  // pass has run.
  //
  // The placeholder token includes a per-call random component so that if a
  // message's own text happens to contain something that looks like
  // "@@AUTOLINKSKIP_0@@", it's left alone -- only placeholders this exact
  // call stashed (matching this call's random suffix) are restored.
  const skipped = [];
  const token = `@@AUTOLINKSKIP_${Math.random().toString(36).slice(2)}_`;
  const stash = (match) => {
    skipped.push(match);
    return `${token}${skipped.length - 1}@@`;
  };

  let working = text.replace(/<a\b[^>]*>[\s\S]*?<\/a>/g, stash);
  working = working.replace(/<code class="inline-code">[\s\S]*?<\/code>/g, stash);

  working = working.replace(BARE_URL_RE, (match) => {
    // Peel off trailing punctuation that reads as sentence punctuation
    // rather than part of the URL, e.g. "see https://example.com."
    const trailingMatch = match.match(/[).,!?;:]+$/);
    const trailing = trailingMatch ? trailingMatch[0] : '';
    const url = trailing ? match.slice(0, -trailing.length) : match;
    return url ? `${renderMarkdownLink(url, url)}${trailing}` : match;
  });

  const restoreRe = new RegExp(`${token}(\\d+)@@`, 'g');
  return working.replace(restoreRe, (match, index) => skipped[Number(index)] || '');
}

// Highlighting is synchronous and grammar-heavy. Large tool/file payloads
// must remain responsive at terminal handoff, so retain the visual code block
// but leave syntax coloring to reasonably sized snippets.
const MAX_HIGHLIGHTED_CODE_CHARS = 12 * 1024;

function renderCodeBlock(language, code) {
  const normalizedLanguage = typeof language === 'string' ? language.trim().toLowerCase() : '';
  const trimmedCode = code.replace(/\n$/, '');
  const safeLanguage = normalizedLanguage ? escapeHtml(normalizedLanguage) : '';

  let highlightedValue = escapeHtml(trimmedCode);
  let languageClass = '';

  if (trimmedCode && trimmedCode.length <= MAX_HIGHLIGHTED_CODE_CHARS) {
    if (normalizedLanguage && hljs.getLanguage(normalizedLanguage)) {
      highlightedValue = hljs.highlight(trimmedCode, { language: normalizedLanguage }).value;
      languageClass = ` language-${safeLanguage}`;
    } else {
      const autoResult = hljs.highlightAuto(trimmedCode);
      highlightedValue = autoResult.value;
      languageClass = autoResult.language ? ` language-${escapeHtml(autoResult.language)}` : '';
    }
  } else if (safeLanguage) {
    languageClass = ` language-${safeLanguage}`;
  }

  const langLabel = safeLanguage ? `<span class="code-lang">${safeLanguage}</span>` : '';
  const html = `<div class="code-block">${langLabel}<pre><code class="hljs${languageClass}">${highlightedValue}</code></pre></div>`;
  return html;
}
