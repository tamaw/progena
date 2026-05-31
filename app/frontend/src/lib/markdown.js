import hljs from 'highlight.js/lib/common';

export function renderMarkdownToHtml(content) {
  if (!content) return '';

  const codeBlocks = [];
  let formatted = content.replace(/```(\w*)\n([\s\S]*?)```/g, (match, lang, code) => {
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
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
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

  return normalizeListMarkup(formatted);
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
