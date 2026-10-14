// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import React, { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';
import StructuredDocumentBody from './StructuredDocumentBody';

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

describe('StructuredDocumentBody', () => {
  it('keeps nested questions between the surrounding paragraphs', () => {
    const content = '<plan>Before.\n<questions>Proceed?<options><option>Yes</option></options></questions>\nAfter.</plan>';
    const document = documentFor({ content, onAskAgent: () => {}, onAnswerQuestion: () => {} });
    const plan = document.querySelector('.inbox-section');
    const rendered = plan.querySelector('.inbox-section__content');
    expect(rendered.textContent.indexOf('Before.')).toBeLessThan(rendered.textContent.indexOf('Proceed?'));
    expect(rendered.textContent.indexOf('Proceed?')).toBeLessThan(rendered.textContent.indexOf('After.'));
    expect(plan.querySelector('[aria-label="Edit"]')).toBeNull();
    expect(plan.querySelector('[aria-label="Remove"]')).not.toBeNull();
  });
});
