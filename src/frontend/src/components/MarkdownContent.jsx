import { useMemo, useRef } from 'react';
import { useMermaidRender } from '../hooks/useMermaidRender';
import { renderMarkdownToHtml } from '../lib/markdown';
import './MarkdownContent.css';

/**
 * Read-only Markdown renderer for document-style content. Callers can retain
 * their existing HTML policy by supplying a renderer or post-render transform.
 */
export default function MarkdownContent({
  content,
  className = 'markdown-content',
  hiddenWhenEmpty = false,
  onClick,
  renderMarkdown = renderMarkdownToHtml,
  transformHtml,
  renderMermaid = false,
}) {
  const html = useMemo(() => {
    const rendered = renderMarkdown(content);
    return transformHtml ? transformHtml(rendered) : rendered;
  }, [content, renderMarkdown, transformHtml]);
  const containerRef = useRef(null);
  useMermaidRender(containerRef, renderMermaid ? content : null);

  if (hiddenWhenEmpty && !content) return null;

  return (
    <div
      ref={containerRef}
      className={`app-markdown-content ${className}`}
      onClick={onClick}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
