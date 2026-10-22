// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import React, { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';
import StructuredDocumentBody from './StructuredDocumentBody';
import { parseStructuredContent } from '../lib/structuredContent';

vi.mock('../lib/structuredContent', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    parseStructuredContent: vi.fn(actual.parseStructuredContent),
  };
});

function documentFor(props) {
  // Vitest compiles the shared icon modules with the classic JSX runtime.
  const previous = globalThis.React;
  try {
    globalThis.React = React;
    return new JSDOM(renderToStaticMarkup(createElement(StructuredDocumentBody, props))).window.document;
  } finally {
    if (previous === undefined) delete globalThis.React;
    else globalThis.React = previous;
  }
}

function nText(content, start, end) {
  return { type: 'text', content, start, end };
}

function nHeading(level, title, start, end, children = []) {
  return { type: 'heading', level, title, start, end, children };
}

function nSection(tag, attributes, start, end, openEnd, closeStart, children = []) {
  return { type: 'section', tag, attributes, start, end, openEnd, closeStart, children };
}

describe('StructuredDocumentBody', () => {
  afterEach(() => {
    vi.mocked(parseStructuredContent).mockRestore();
  });

  it('renders heading sections as selectable inbox sections', () => {
    parseStructuredContent.mockReturnValue([
      nHeading(2, 'Plan', 0, 20, [nText('Do the thing.', 9, 20)]),
    ]);
    const document = documentFor({ content: '## Plan\nDo the thing.', onAskAgent: () => {}, onAnswerQuestion: () => {} });
    const sections = document.querySelectorAll('.inbox-section');
    expect(sections.length).toBe(1);
    const head = sections[0].querySelector('.inbox-section__head');
    expect(head.querySelector('h2.inbox-section__title .inbox-section__select').textContent).toBe('Plan');
    expect(head.querySelector('[aria-label="Edit with AI"]')).not.toBeNull();
    expect(head.querySelector('[aria-label="Explain"]')).not.toBeNull();
    expect(head.querySelector('[aria-label="Remove"]')).not.toBeNull();
  });

  it('renders nested headings as nested sections', () => {
    parseStructuredContent.mockReturnValue([
      nHeading(2, 'Plan', 0, 40, [
        nText('Overview.\n', 9, 19),
        nHeading(3, 'Detail', 19, 40, [nText('More info.', 28, 40)]),
      ]),
    ]);
    const document = documentFor({ content: '## Plan\nOverview.\n### Detail\nMore info.', onAskAgent: () => {}, onAnswerQuestion: () => {} });
    const outer = document.querySelectorAll('.inbox-section');
    expect(outer.length).toBe(2);
    expect(outer[0].querySelector('h2.inbox-section__title .inbox-section__select').textContent).toBe('Plan');
    expect(outer[1].querySelector('h3.inbox-section__title .inbox-section__select').textContent).toBe('Detail');
    expect(outer[0].contains(outer[1])).toBe(true);
  });

  it('renders questions inside heading content', () => {
    parseStructuredContent.mockReturnValue([
      nHeading(2, 'Plan', 0, 60, [
        nText('Before.\n', 9, 17),
        nSection('questions', {}, 17, 50, 28, 39, [
          nText('Proceed?', 28, 39),
          nSection('options', {}, 39, 50, 48, 48, [
            nSection('option', {}, 48, 48, 48, 48, []),
          ]),
        ]),
        nText('\nAfter.', 50, 60),
      ]),
    ]);
    const document = documentFor({ content: '## Plan\nBefore.\n<questions>Proceed?<options><option>Yes</option></options></questions>\nAfter.', onAskAgent: () => {}, onAnswerQuestion: () => {} });
    const heading = document.querySelector('.inbox-section');
    const rendered = heading.querySelector('.inbox-section__content');
    expect(rendered.textContent.indexOf('Before.')).toBeLessThan(rendered.textContent.indexOf('Proceed?'));
    expect(rendered.textContent.indexOf('Proceed?')).toBeLessThan(rendered.textContent.indexOf('After.'));
    expect(heading.querySelectorAll('.inbox-section').length).toBe(1); // nested questions only (querySelectorAll excludes self)
  });

  it('renders generic XML tags as markdown text, not sections', () => {
    parseStructuredContent.mockReturnValue([
      nText('<plan>Before.</plan>', 0, 21),
    ]);
    const document = documentFor({ content: '<plan>Before.</plan>', onAskAgent: () => {}, onAnswerQuestion: () => {} });
    expect(document.querySelector('.inbox-section:not(.inbox-section--default)')).toBeNull();
    expect(document.querySelector('.markdown-content')).not.toBeNull();
  });

  it('wraps text outside any heading or question in a default group with actions', () => {
    parseStructuredContent.mockReturnValue([
      nText('Just a plain message.', 0, 21),
    ]);
    const document = documentFor({ content: 'Just a plain message.', onAskAgent: () => {} });
    const groups = document.querySelectorAll('.inbox-section--default');
    expect(groups).toHaveLength(1);
    expect(groups[0].querySelector('.inbox-section__content .markdown-content').textContent).toBe('Just a plain message.');
    expect(groups[0].querySelector('.inbox-section__title')).toBeNull();
    expect(groups[0].querySelector('[role="toolbar"]')).not.toBeNull();
  });

  it('keeps a leading H1 title outside the default group', () => {
    const source = '# Title\n\nIntro paragraph.';
    parseStructuredContent.mockReturnValue([nText(source, 0, source.length)]);
    const document = documentFor({ content: source, onAskAgent: () => {} });
    const group = document.querySelector('.inbox-section--default');
    expect(group.textContent).toContain('Intro paragraph.');
    expect(group.textContent).not.toContain('Title');
    expect(document.querySelector('.structured-document-body > .markdown-content').textContent).toContain('Title');
  });

  it('splits a tab-separated H1 title out of the group', () => {
    const source = '#\tTab title\n\nBody.';
    parseStructuredContent.mockReturnValue([nText(source, 0, source.length)]);
    const group = documentFor({ content: source, onAskAgent: () => {} }).querySelector('.inbox-section--default');
    expect(group.textContent).toContain('Body.');
    expect(group.textContent).not.toContain('Tab title');
  });

  it('keeps indented-code lookalikes in the group instead of treating them as a title', () => {
    const source = '    # Not a title\n\nBody.';
    parseStructuredContent.mockReturnValue([nText(source, 0, source.length)]);
    const group = documentFor({ content: source, onAskAgent: () => {} }).querySelector('.inbox-section--default');
    expect(group.textContent).toContain('Not a title');
  });

  it('gives a default group a focusable select control for keyboard users', () => {
    parseStructuredContent.mockReturnValue([nText('Plain.', 0, 6)]);
    const withAgent = documentFor({ content: 'Plain.', onAskAgent: () => {} });
    expect(withAgent.querySelector('.inbox-section--default button[aria-label="Select text section"]')).not.toBeNull();
    const readOnly = documentFor({ content: 'Plain.' });
    expect(readOnly.querySelector('button')).toBeNull();
  });

  it('groups preamble text but not whitespace or text already inside a heading', () => {
    parseStructuredContent.mockReturnValue([
      nText('Intro.\n', 0, 7),
      nHeading(2, 'Plan', 7, 30, [nText('Body.', 15, 30)]),
      nText('\n\n', 30, 32),
    ]);
    const document = documentFor({ content: 'Intro.\n## Plan\nBody.\n\n', onAskAgent: () => {} });
    expect(document.querySelectorAll('.inbox-section--default')).toHaveLength(1);
    expect(document.querySelectorAll('.inbox-section')).toHaveLength(2);
  });

  it('hides actions when onAskAgent is absent', () => {
    parseStructuredContent.mockReturnValue([
      nHeading(2, 'Plan', 0, 20, [nText('Do the thing.', 9, 20)]),
    ]);
    const document = documentFor({ content: '## Plan\nDo the thing.', onAnswerQuestion: () => {} });
    const section = document.querySelector('.inbox-section');
    expect(section.querySelector('.inbox-section__select').disabled).toBe(true);
    expect(section.querySelector('.inbox-section__actions')).toBeNull();
  });

  it('renders question options as clickable buttons', () => {
    parseStructuredContent.mockReturnValue([
      nSection('questions', { id: 'q1' }, 0, 115, 19, 103, [
        nText('Pick one:', 19, 28),
        nSection('options', {}, 28, 103, 37, 93, [
          nSection('option', { value: 'a' }, 37, 65, 55, 56, [nText('A', 55, 56)]),
          nSection('option', { value: 'b' }, 65, 93, 83, 84, [nText('B', 83, 84)]),
        ]),
      ]),
    ]);
    const document = documentFor({ content: '<questions id="q1">Pick one:<options><option value="a">A</option><option value="b">B</option></options></questions>', onAskAgent: () => {}, onAnswerQuestion: () => {} });
    const bracket = document.querySelector('.inbox-section > .inbox-section__content');
    expect(bracket.querySelector('.markdown-content').textContent).toContain('Pick one:');
    expect(bracket.querySelector('.inbox-section__options')).not.toBeNull();
    const options = document.querySelectorAll('.inbox-section__options button');
    expect(options.length).toBe(3); // A, B, Enter your own
    expect(options[0].textContent).toBe('A');
    expect(options[1].textContent).toBe('B');
    expect(options[2].textContent).toBe('Enter your own');
  });

  it('uses semantic heading levels without Markdown markers in the UI', () => {
    parseStructuredContent.mockReturnValue([
      nHeading(2, 'Two', 0, 10, []),
      nHeading(3, 'Three', 10, 22, []),
      nHeading(4, 'Four', 22, 34, []),
      nHeading(5, 'Five', 34, 46, []),
      nHeading(6, 'Six', 46, 56, []),
    ]);
    const document = documentFor({ content: '## Two\n### Three\n#### Four\n##### Five\n###### Six', onAskAgent: () => {}, onAnswerQuestion: () => {} });
    for (const [index, title] of ['Two', 'Three', 'Four', 'Five', 'Six'].entries()) {
      const heading = document.querySelector(`h${index + 2}.inbox-section__title`);
      expect(heading.querySelector('button').textContent).toBe(title);
    }
  });

  it('does not render H1 as a selectable section', () => {
    parseStructuredContent.mockReturnValue([
      nText('# Title\n\n## Plan\nContent.', 0, 28),
    ]);
    const document = documentFor({ content: '# Title\n\n## Plan\nContent.', onAskAgent: () => {}, onAnswerQuestion: () => {} });
    expect(document.querySelector('.inbox-section__title')).toBeNull();
    const rendered = [...document.querySelectorAll('.markdown-content')].map((node) => node.textContent).join(' ');
    expect(rendered).toContain('Title');
    expect(rendered).toContain('Plan');
  });
});
