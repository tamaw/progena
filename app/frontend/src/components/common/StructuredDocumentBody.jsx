import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useMermaidRender } from '../../hooks/useMermaidRender';
import { renderMarkdownToHtml } from '../../lib/markdown';
import { parseStructuredContent } from '../../lib/structuredContent';
import { OpenChatIcon, ProgenitorAiIcon, QuickChatIcon } from '../layout/navIcons';
import { DeleteActionIcon } from '../layout/DetailHeader';

function Markdown({ content }) {
  const ref = useRef(null);
  const html = useMemo(() => renderMarkdownToHtml(content), [content]);
  useMermaidRender(ref, content);
  return <div ref={ref} className="markdown-content" dangerouslySetInnerHTML={{ __html: html }} />;
}


function positionAiPopover(button) {
  const rect = button.getBoundingClientRect();
  const width = Math.min(390, window.innerWidth - 16);
  const top = rect.bottom + 62 > window.innerHeight
    ? Math.max(8, rect.top - 56)
    : rect.bottom + 6;
  return {
    top,
    left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
    width,
  };
}

export default function StructuredDocumentBody({ content = '', onAskAgent, onAnswerQuestion }) {
  const [selected, setSelected] = useState(null);
  const [mode, setMode] = useState(null);
  const nodes = useMemo(() => parseStructuredContent(content), [content]);
  const [instruction, setInstruction] = useState('');
  const [popoverPosition, setPopoverPosition] = useState(null);
  const [aiJob, setAiJob] = useState(null);
  const popoverRef = useRef(null);
  const aiTriggerRef = useRef(null);
  const [openAnswers, setOpenAnswers] = useState({});
  const [answerDrafts, setAnswerDrafts] = useState({});

  const selectionCurrent = selected?.source === content;
  const aiPopoverOpen = mode === 'ai' && aiJob?.phase !== 'completed'
    && (selectionCurrent || aiJob?.phase === 'working');

  useEffect(() => {
    if (!aiPopoverOpen) return undefined;
    const dismiss = (event) => {
      if (popoverRef.current?.contains(event.target) || aiTriggerRef.current?.contains(event.target)) return;
      setMode(null);
      setSelected(null);
      setAiJob(null);
    };
    const escape = (event) => {
      if (event.key === 'Escape') {
        setMode(null);
        setSelected(null);
        setAiJob(null);
        aiTriggerRef.current?.focus();
      }
    };
    const reposition = () => {
      if (aiTriggerRef.current) setPopoverPosition(positionAiPopover(aiTriggerRef.current));
    };
    document.addEventListener('pointerdown', dismiss, true);
    document.addEventListener('keydown', escape);
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    return () => {
      document.removeEventListener('pointerdown', dismiss, true);
      document.removeEventListener('keydown', escape);
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
    };
  }, [aiPopoverOpen]);

  useEffect(() => {
    const handleStatus = (event) => {
      setAiJob((current) => {
        if (!current || current.id !== event.detail?.clientId) return current;
        return event.detail.status === 'completed'
          ? { ...current, phase: 'completed' }
          : { ...current, phase: 'error', error: event.detail.error || 'The quick-chat response did not complete.' };
      });
    };
    window.addEventListener('progenitor:quick-chat-inline-status', handleStatus);
    return () => window.removeEventListener('progenitor:quick-chat-inline-status', handleStatus);
  }, []);


  function select(node) {
    setAiJob(null);
    if (selected?.source === content && selected.start === node.start && selected.end === node.end) {
      setSelected(null);
      setMode(null);
      return;
    }
    setSelected({ start: node.start, end: node.end, tag: node.tag, title: node.title, type: node.type, source: content });
    setInstruction('');
    setMode(null);
  }

  function submitAgent(node, action, options) {
    if (node.source !== undefined && node.source !== content) return false;
    const exact = content.slice(node.start, node.end);
    const label = node.type === 'heading' ? `${'#'.repeat(node.level)} ${node.title}` : `<${node.tag}>`;
    onAskAgent?.(`Find this exact ${label} section (including its content):\n${exact}\n\n${action} Keep other sections intact. If the section changed, ask before editing.`, options);
    return true;
  }

  function submitAnswer(question, choice) {
    const value = choice.trim();
    if (value) onAnswerQuestion?.({ question, choice: value, content });
  }


  function renderNode(node) {
    if (node.type === 'text') {
      if (!node.content.trim()) return null;
      const rendered = node.content.replace(/^(?:[ \t]*\r?\n)+|(?:\r?\n[ \t]*)+$/g, '');
      return <Markdown key={`text-${node.start}`} content={rendered} />;
    }

    if (node.type === 'heading') {
      const key = `${node.start}-${node.end}`;
      const active = selectionCurrent && selected.start === node.start && selected.end === node.end && aiJob?.phase !== 'completed';
      const levelPrefix = '#'.repeat(node.level);
      const HeadingTag = `h${node.level}`;
      return (
        <section key={key} className={`inbox-section${active ? ' is-selected' : ''}`}>
          <div className="inbox-section__head">
            <HeadingTag className="inbox-section__title"><button type="button" className="inbox-section__select" aria-pressed={active} disabled={!onAskAgent} onClick={() => select(node)} aria-label={`Select ${levelPrefix} ${node.title} section`}>{node.title}</button></HeadingTag>
            {onAskAgent && (
              <div className="inbox-section__actions" role="toolbar" aria-label={`${levelPrefix} ${node.title} section actions`}>
                <button
                  type="button"
                  ref={active && aiPopoverOpen ? aiTriggerRef : null}
                  className={`msg-action-btn${active && aiPopoverOpen ? ' is-active' : ''}`}
                  aria-label="Edit with AI"
                  aria-expanded={active && aiPopoverOpen}
                  onClick={(event) => {
                    if (active && aiPopoverOpen) { setMode(null); setSelected(null); setAiJob(null); return; }
                    if (!active) select(node);
                    setPopoverPosition(positionAiPopover(event.currentTarget));
                    setMode('ai');
                  }}
                >
                  <ProgenitorAiIcon /><span className="msg-action-btn__label">Edit with AI</span>
                </button>
                <button type="button" className="msg-action-btn" aria-label="Explain" onClick={() => submitAgent(node, 'Explain this section to me in chat. Do not modify the file.')}> 
                  <QuickChatIcon /><span className="msg-action-btn__label">Explain</span>
                </button>
                <button type="button" className="msg-action-btn" aria-label="Remove" onClick={() => {
                  if (window.confirm(`Ask Progenitor to remove the entire ${levelPrefix} ${node.title} section and its contents?`)) {
                    submitAgent(node, 'Remove this entire section and its contents from the inbox file.');
                  }
                }}>
                  <DeleteActionIcon /><span className="msg-action-btn__label">Remove</span>
                </button>
              </div>
            )}
          </div>
          <div className="inbox-section__content" onClick={(event) => {
            if (!onAskAgent) return;
            if (event.target.closest('.inbox-section') !== event.currentTarget.closest('.inbox-section')) return;
            if (event.target.closest('a, button, input, textarea, select, [contenteditable="true"]')) return;
            select(node);
          }}>{node.children.map(renderNode)}</div>
        </section>
      );
    }

    // node.type === 'section' — only questions/options/option remain as sections
    const key = `${node.start}-${node.end}`;
    const active = selectionCurrent && selected.start === node.start && selected.end === node.end && aiJob?.phase !== 'completed';
    const isQuestion = node.tag === 'questions' && Boolean(onAnswerQuestion);
    const questionKey = node.attributes?.id ? `question:${node.attributes.id}` : key;
    const questionOptions = isQuestion ? node.children.find((child) => child.type === 'section' && child.tag === 'options') : null;
    const questionText = isQuestion ? node.children.filter((child) => child !== questionOptions) : node.children;
    return (
      <section key={key} className={`inbox-section${active ? ' is-selected' : ''}`}>
        <div className="inbox-section__head">
          <button type="button" className="inbox-section__select" aria-pressed={active} disabled={!onAskAgent} onClick={() => select(node)} aria-label={`Select <${node.tag}> section`}>{node.tag}</button>
          {onAskAgent && (
            <div className="inbox-section__actions" role="toolbar" aria-label={`${node.tag} section actions`}>
              <button
                type="button"
                ref={active && aiPopoverOpen ? aiTriggerRef : null}
                className={`msg-action-btn${active && aiPopoverOpen ? ' is-active' : ''}`}
                aria-label="Edit with AI"
                aria-expanded={active && aiPopoverOpen}
                onClick={(event) => {
                  if (active && aiPopoverOpen) { setMode(null); setSelected(null); setAiJob(null); return; }
                  if (!active) select(node);
                  setPopoverPosition(positionAiPopover(event.currentTarget));
                  setMode('ai');
                }}
              >
                <ProgenitorAiIcon /><span className="msg-action-btn__label">Edit with AI</span>
              </button>
              <button type="button" className="msg-action-btn" aria-label="Explain" onClick={() => submitAgent(node, 'Explain this section to me in chat. Do not modify the file.')}> 
                <QuickChatIcon /><span className="msg-action-btn__label">Explain</span>
              </button>
              <button type="button" className="msg-action-btn" aria-label="Remove" onClick={() => {
                if (window.confirm(`Ask Progenitor to remove the entire <${node.tag}> section and its contents?`)) {
                  submitAgent(node, 'Remove this entire section and its contents from the inbox file.');
                }
              }}>
                <DeleteActionIcon /><span className="msg-action-btn__label">Remove</span>
              </button>
            </div>
          )}
        </div>
        <div className="inbox-section__content" onClick={(event) => {
          if (!onAskAgent) return;
          if (event.target.closest('.inbox-section') !== event.currentTarget.closest('.inbox-section')) return;
          if (event.target.closest('a, button, input, textarea, select, [contenteditable="true"]')) return;
          select(node);
        }}>{questionText.map(renderNode)}</div>
        {isQuestion ? (
          <div className="inbox-section__question">
            <div className="inbox-section__options">
              {(questionOptions?.children || []).filter((child) => child.type === 'section' && child.tag === 'option').map((option) => (
                <button type="button" key={option.start} onClick={() => submitAnswer(node, content.slice(option.openEnd, option.closeStart))}>{content.slice(option.openEnd, option.closeStart)}</button>
              ))}
              <button
                type="button"
                className={openAnswers[questionKey] ? 'is-active' : ''}
                aria-expanded={Boolean(openAnswers[questionKey])}
                onClick={() => setOpenAnswers((current) => ({ ...current, [questionKey]: !current[questionKey] }))}
              >Enter your own</button>
              {openAnswers[questionKey] ? (
                <form onSubmit={(event) => {
                  event.preventDefault();
                  if (answerDrafts[questionKey]?.trim()) {
                    submitAnswer(node, answerDrafts[questionKey]);
                    setOpenAnswers((current) => ({ ...current, [questionKey]: false }));
                  }
                }}>
                  <input aria-label="Your own answer" autoFocus value={answerDrafts[questionKey] || ''} onChange={(event) => setAnswerDrafts((current) => ({ ...current, [questionKey]: event.target.value }))} placeholder="Your own answer" />
                  <button type="submit" disabled={!answerDrafts[questionKey]?.trim()}>Send answer</button>
                </form>
              ) : null}
            </div>
          </div>
        ) : null}
      </section>
    );
  }

  return (
    <div className="structured-document-body">
      {nodes.map(renderNode)}
      {selected && aiPopoverOpen && popoverPosition ? createPortal(
        <form
          ref={popoverRef}
          className="inbox-ai-popover"
          role="dialog"
          aria-label="Edit section with AI"
          style={popoverPosition}
          onSubmit={(event) => {
            event.preventDefault();
            if (!instruction.trim() || (aiJob && aiJob.phase !== 'error')) return;
            if (selected.source !== content) return;
            const id = crypto.randomUUID();
            setAiJob({ id, phase: 'working', error: '' });
            submitAgent(selected, instruction.trim(), { inline: true, clientId: id });
          }}
        >
          <ProgenitorAiIcon />
          <input
            aria-label={aiJob && aiJob.phase !== 'error' ? 'AI edit status' : 'AI edit instruction'}
            autoFocus
            readOnly={Boolean(aiJob && aiJob.phase !== 'error')}
            value={aiJob?.phase === 'working' ? 'Working…' : instruction}
            onChange={(event) => setInstruction(event.target.value)}
            placeholder="How should this section change?"
          />
          <button type="button" aria-label="Open in quick chat" title="Open in quick chat" disabled={Boolean(aiJob && aiJob.phase !== 'error')} onClick={() => {
            submitAgent(selected, instruction.trim() || 'Please update this section as follows:', { draft: true });
            setMode(null);
            setSelected(null);
          }}><OpenChatIcon /></button>
          <button type="submit" className="btn-send" aria-label="Send AI edit" title="Send AI edit" disabled={!instruction.trim() || Boolean(aiJob && aiJob.phase !== 'error')}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="12" y1="19" x2="12" y2="5" />
              <polyline points="5 12 12 5 19 12" />
            </svg>
          </button>
          {aiJob?.phase === 'error' ? <span role="alert" className="inbox-ai-popover__error">{aiJob.error}</span> : null}
        </form>,
        document.body,
      ) : null}
    </div>
  );
}
