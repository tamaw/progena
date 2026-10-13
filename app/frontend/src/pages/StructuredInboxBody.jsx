import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useDispatch } from 'react-redux';
import { useMermaidRender } from '../hooks/useMermaidRender';
import { renderMarkdownToHtml } from '../lib/markdown';
import { parseInboxContent } from '../lib/inboxContent';
import { patchInboxSection } from '../store/uiDataSlice';
import { OpenChatIcon, ProgenitorAiIcon, QuickChatIcon } from '../components/layout/navIcons';
import { DeleteActionIcon, EditActionIcon } from '../components/layout/DetailHeader';

function Markdown({ content }) {
  const ref = useRef(null);
  const html = useMemo(() => renderMarkdownToHtml(content), [content]);
  useMermaidRender(ref, content);
  return <div ref={ref} className="markdown-content" dangerouslySetInnerHTML={{ __html: html }} />;
}

function openAgent(message, instruction, { draft = false } = {}) {
  const prompt = `For the inbox file /inbox/${message.id}.md: ${instruction}`;
  window.dispatchEvent(new CustomEvent('progenitor:open-quick-chat', {
    detail: {
      mode: 'inbox-edit',
      subtitle: message.title || message.id,
      [draft ? 'draft' : 'prompt']: prompt,
    },
  }));
}

function insertFormatting(value, selectionStart, selectionEnd, prefix, suffix) {
  return `${value.slice(0, selectionStart)}${prefix}${value.slice(selectionStart, selectionEnd)}${suffix}${value.slice(selectionEnd)}`;
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

export default function StructuredInboxBody({ message, inboxUrl }) {
  const dispatch = useDispatch();
  const content = message.body || '';
  const [selected, setSelected] = useState(null);
  const [mode, setMode] = useState(null);
  const displayContent = selected && mode === 'edit' && selected.source !== content
    ? selected.source
    : content;
  const nodes = useMemo(() => parseInboxContent(displayContent), [displayContent]);
  const [draft, setDraft] = useState('');
  const [instruction, setInstruction] = useState('');
  const [popoverPosition, setPopoverPosition] = useState(null);
  const popoverRef = useRef(null);
  const aiTriggerRef = useRef(null);
  const [error, setError] = useState('');
  const [openAnswers, setOpenAnswers] = useState({});
  const [answerDrafts, setAnswerDrafts] = useState({});
  const inputRef = useRef(null);

  const previousContent = useRef(content);
  useEffect(() => {
    if (previousContent.current === content || mode === 'edit') return;
    previousContent.current = content;
    setSelected(null);
    setMode(null);
  }, [content, mode, nodes]);

  useEffect(() => {
    if (mode !== 'ai') return undefined;
    const dismiss = (event) => {
      if (popoverRef.current?.contains(event.target) || aiTriggerRef.current?.contains(event.target)) return;
      setMode(null);
      setSelected(null);
    };
    const escape = (event) => {
      if (event.key === 'Escape') {
        setMode(null);
        setSelected(null);
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
  }, [mode]);

  function select(node) {
    if (selected?.start === node.start && selected?.end === node.end) {
      setSelected(null);
      setMode(null);
      return;
    }
    previousContent.current = content;
    setSelected({ start: node.start, end: node.end, openEnd: node.openEnd, closeStart: node.closeStart, tag: node.tag, source: content });
    setDraft(content.slice(node.openEnd, node.closeStart));
    setInstruction('');
    setMode(null);
    setError('');
  }

  async function save(replacement, target = selected) {
    if (!target) return;
    setError('');
    try {
      await dispatch(patchInboxSection({
        inboxUrl, messageId: message.id, expectedBody: target.source,
        start: target.start, end: target.end, replacement,
      })).unwrap();
      setSelected(null);
      setMode(null);
    } catch (failure) {
      setError(failure?.message || 'This message changed. Review the latest version and try again.');
    }
  }

  function submitAgent(node, action, options) {
    const exact = content.slice(node.start, node.end);
    openAgent(message, `Find this exact <${node.tag}> section (including its tags):\n${exact}\n\n${action} Keep other sections intact. If the section changed, ask before editing.`, options);
  }

  function submitAnswer(question, choice) {
    const value = choice.trim();
    if (!value) return;
    const block = content.slice(question.start, question.end);
    const id = question.attributes?.id;
    openAgent(message, `Read the latest /inbox/${message.id}.md and locate this exact question section${id ? ` (id ${JSON.stringify(id)})` : ''}:\n${block}\n\nThe user answered ${JSON.stringify(value)}. Rewrite this <questions> section in your own words to include the question and its answer naturally in the document. Keep its <questions> wrapper, id, and <options> list so the user can change their answer later. Do not add an <answer> tag or simply append the selected text. Preserve all other sections and user-owned frontmatter. If this question has changed, ask before editing.`);
  }

  function renderNode(node) {
    if (node.type === 'text') {
      if (!node.content.trim()) return null;
      const rendered = node.content.replace(/^(?:[ \t]*\r?\n)+|(?:\r?\n[ \t]*)+$/g, '');
      return <Markdown key={`text-${node.start}`} content={rendered} />;
    }
    const key = `${node.start}-${node.end}`;
    const active = selected?.start === node.start && selected?.end === node.end;
    const isQuestion = node.tag === 'questions';
    const questionKey = node.attributes?.id ? `question:${node.attributes.id}` : key;
    const questions = node.children.filter((child) => child.type === 'section' && child.tag === 'questions');
    const ordinary = node.children.filter((child) => !questions.includes(child));
    const questionOptions = isQuestion ? node.children.find((child) => child.type === 'section' && child.tag === 'options') : null;
    const questionText = isQuestion ? ordinary.filter((child) => child !== questionOptions) : ordinary;
    return (
      <section key={key} className={`inbox-section${active ? ' is-selected' : ''}`}>
        <div className="inbox-section__head">
          <button type="button" className="inbox-section__select" aria-pressed={active} onClick={() => select(node)} aria-label={`Select <${node.tag}> section`}>{node.tag}</button>
          <div className="inbox-section__actions" role="toolbar" aria-label={`${node.tag} section actions`}>
            <button
              type="button"
              ref={active && mode === 'ai' ? aiTriggerRef : null}
              className={`msg-action-btn${active && mode === 'ai' ? ' is-active' : ''}`}
              aria-label="Edit with AI"
              aria-expanded={active && mode === 'ai'}
              onClick={(event) => {
                if (active && mode === 'ai') { setMode(null); setSelected(null); return; }
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
            <button type="button" className={`msg-action-btn${active && mode === 'edit' ? ' is-active' : ''}`} aria-label="Edit" aria-pressed={active && mode === 'edit'} onClick={() => {
              if (active && mode === 'edit') setMode(null);
              else { if (!active) select(node); setDraft(content.slice(node.openEnd, node.closeStart)); setMode('edit'); }
            }}>
              <EditActionIcon /><span className="msg-action-btn__label">Edit</span>
            </button>
            <button type="button" className="msg-action-btn" aria-label="Remove" onClick={() => { if (window.confirm(`Remove the entire <${node.tag}> section and its contents?`)) save('', { ...node, source: content }); }}>
              <DeleteActionIcon /><span className="msg-action-btn__label">Remove</span>
            </button>
          </div>
        </div>
        {active && mode === 'edit' ? (
          <div className="inbox-section__editor">
            <div className="inbox-section__format" role="toolbar" aria-label="Markdown formatting">
              {[['Bold', '**', '**'], ['Italic', '*', '*'], ['Link', '[', '](url)'], ['Code', '`', '`']].map(([label, prefix, suffix]) => (
                <button key={label} type="button" onClick={() => {
                  const input = inputRef.current;
                  if (!input) return;
                  const { selectionStart, selectionEnd } = input;
                  setDraft(insertFormatting(draft, selectionStart, selectionEnd, prefix, suffix));
                  requestAnimationFrame(() => { input.focus(); input.setSelectionRange(selectionStart + prefix.length, selectionEnd + prefix.length); });
                }}>{label}</button>
              ))}
            </div>
            <textarea ref={inputRef} aria-label="Section Markdown" value={draft} onChange={(event) => setDraft(event.target.value)} />
            <div className="inbox-section__preview"><strong>Preview</strong><Markdown content={draft} /></div>
            <button type="button" onClick={() => save(`${selected.source.slice(node.start, node.openEnd)}${draft}${selected.source.slice(node.closeStart, node.end)}`)}>Save</button>
            <button type="button" onClick={() => setMode(null)}>Cancel</button>
          </div>
        ) : (
          <>
            <div className="inbox-section__content" onClick={(event) => {
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
            ) : questions.map(renderNode)}
          </>
        )}
      </section>
    );
  }

  return (
    <div className="inbox-structured-body">
      {(error || (selected && selected.source !== content)) ? (
        <p role="alert" className="inbox-section__error">
          {error || 'This message changed while you were editing. Select the latest section to review it.'}
        </p>
      ) : null}
      {nodes.map(renderNode)}
      {selected && mode === 'ai' && popoverPosition ? createPortal(
        <form
          ref={popoverRef}
          className="inbox-ai-popover"
          role="dialog"
          aria-label="Edit section with AI"
          style={popoverPosition}
          onSubmit={(event) => {
            event.preventDefault();
            if (!instruction.trim()) return;
            submitAgent(selected, instruction.trim());
            setMode(null);
            setSelected(null);
          }}
        >
          <ProgenitorAiIcon />
          <input aria-label="AI edit instruction" autoFocus value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="How should this section change?" />
          <button type="button" aria-label="Open in quick chat" title="Open in quick chat" onClick={() => {
            submitAgent(selected, instruction.trim() || 'Please update this section as follows:', { draft: true });
            setMode(null);
            setSelected(null);
          }}><OpenChatIcon /></button>
          <button type="submit" className="btn-send" aria-label="Send AI edit" title="Send AI edit" disabled={!instruction.trim()}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="12" y1="19" x2="12" y2="5" />
              <polyline points="5 12 12 5 19 12" />
            </svg>
          </button>
        </form>,
        document.body,
      ) : null}
    </div>
  );
}
