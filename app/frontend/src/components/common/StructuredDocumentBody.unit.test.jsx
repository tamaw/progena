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

describe('StructuredDocumentBody optional actions', () => {
  it('renders nested content without offering unavailable edit, AI, or answer actions', () => {
    const content = '<plan>Review **this**.\n<questions>Proceed?<options><option>Yes</option></options></questions></plan>';
    const readonly = documentFor({ content });
    expect(readonly.querySelector('.inbox-section__content strong')?.textContent).toBe('this');
    expect(readonly.querySelectorAll('.inbox-section')).toHaveLength(4);
    expect(readonly.querySelectorAll('[role="toolbar"], .inbox-section__question')).toHaveLength(0);
    expect(readonly.querySelector('.inbox-section__select')?.disabled).toBe(true);

    const editable = documentFor({ content, onSaveSection: async () => {} });
    expect([...editable.querySelectorAll('[role="toolbar"] button')].map((button) => button.getAttribute('aria-label'))).toEqual(['Edit', 'Remove', 'Edit', 'Remove', 'Edit', 'Remove', 'Edit', 'Remove']);
    expect(editable.querySelector('[aria-label="Edit with AI"]')).toBeNull();
  });
});
