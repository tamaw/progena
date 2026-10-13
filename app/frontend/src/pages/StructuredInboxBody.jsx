import { useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch } from 'react-redux';
import { useMermaidRender } from '../hooks/useMermaidRender';
import { renderMarkdownToHtml } from '../lib/markdown';
import { parseInboxContent } from '../lib/inboxContent';
import { patchInboxSection } from '../store/uiDataSlice';
import { ProgenitorAiIcon } from '../components/layout/navIcons';
import { DeleteActionIcon } from '../components/layout/DetailHeader';

function Markdown({ content }) {
  const ref = useRef(null);
  const html = useMemo(() => renderMarkdownToHtml(content), [content]);
  useMermaidRender(ref, content);
  return <div ref={ref} className="markdown-content" dangerouslySetInnerHTML={{ __html: html }} />;
}

function openAgent(message, instruction) {
  window.dispatchEvent(new CustomEvent('progenitor:open-quick-chat', {
    detail: {
      mode: 'inbox-edit',
      subtitle: message.title || message.id,
      prompt: `For the inbox file /inbox/${message.id}.md: ${instruction}`,
    },
  }));
}

function insertFormatting(value, selectionStart, selectionEnd, prefix, suffix) {
  return `${value.slice(0, selectionStart)}${prefix}${value.slice(selectionStart, selectionEnd)}${suffix}${value.slice(selectionEnd)}`;
}

function firstSectionSelection(nodes, content) {
  const first = nodes.find((node) => node.type === 'section');
  return first ? {
    start: first.start, end: first.end, openEnd: first.openEnd,
    closeStart: first.closeStart, tag: first.tag, source: content,
  } : null;
}

export default function StructuredInboxBody({ message, inboxUrl }) {
  const dispatch = useDispatch();
  const content = message.body || '';
  const [selected, setSelected] = useState(() => firstSectionSelection(parseInboxContent(content), content));
  const [mode, setMode] = useState(null);
  const displayContent = selected && mode === 'edit' && selected.source !== content
    ? selected.source
    : content;
  const nodes = useMemo(() => parseInboxContent(displayContent), [displayContent]);
  const [draft, setDraft] = useState('');
  const [instruction, setInstruction] = useState('');
  const [error, setError] = useState('');
  const [openAnswers, setOpenAnswers] = useState({});
  const [answerDrafts, setAnswerDrafts] = useState({});
  const inputRef = useRef(null);

  const previousContent = useRef(content);
  useEffect(() => {
    if (previousContent.current === content || mode === 'edit') return;
    previousContent.current = content;
    setSelected(firstSectionSelection(nodes, content));
    setMode(null);
  }, [content, mode, nodes]);

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

  async function save(replacement) {
    if (!selected) return;
    setError('');
    try {
      const updatedBody = `${selected.source.slice(0, selected.start)}${replacement}${selected.source.slice(selected.end)}`;
      await dispatch(patchInboxSection({
        inboxUrl, messageId: message.id, expectedBody: selected.source,
        start: selected.start, end: selected.end, replacement,
      })).unwrap();
      setSelected(firstSectionSelection(parseInboxContent(updatedBody), updatedBody));
      setMode(null);
    } catch (failure) {
      setError(failure?.message || 'This message changed. Review the latest version and try again.');
    }
  }

  function submitAgent(node, action) {
    const exact = content.slice(node.start, node.end);
    openAgent(message, `Find this exact <${node.tag}> section (including its tags):\n${exact}\n\n${action} Keep other sections intact. If the section changed, ask before editing.`);
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
          {active ? (
            <div className="inbox-section__actions" role="toolbar" aria-label={`${node.tag} section actions`}>
              {mode === 'ai' ? (
                <form onSubmit={(event) => { event.preventDefault(); if (instruction.trim()) { submitAgent(node, instruction.trim()); setMode(null); } }}>
                  <input aria-label="AI edit instruction" autoFocus value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="How should this section change?" />
                  <button type="submit">Send</button><button type="button" onClick={() => setMode(null)}>Cancel</button>
                </form>
              ) : (
                <>
                  <button type="button" className="inbox-section__ai-action" onClick={() => setMode('ai')}>
                    <ProgenitorAiIcon /> Edit with AI
                  </button>
                  <button type="button" className="inbox-section__ai-action" onClick={() => submitAgent(node, 'Explain this section to me in chat. Do not modify the file.')}>
                    <ProgenitorAiIcon /> Explain
                  </button>
                  <button type="button" className={mode === 'edit' ? 'inbox-section__edit-action is-active' : 'inbox-section__edit-action'} aria-pressed={mode === 'edit'} onClick={() => {
                    if (mode === 'edit') setMode(null);
                    else { setDraft(content.slice(node.openEnd, node.closeStart)); setMode('edit'); }
                  }}>✎ Edit</button>
                  <button type="button" className="inbox-section__remove-action" onClick={() => { if (window.confirm(`Remove the entire <${node.tag}> section and its contents?`)) save(''); }}><DeleteActionIcon /> Remove</button>
                </>
              )}
            </div>
          ) : null}
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
    </div>
  );
}
