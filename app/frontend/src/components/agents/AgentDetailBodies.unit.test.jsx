import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterAll, describe, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { ObjectAgentDetailBody } from './AgentDetailBodies';

vi.stubGlobal('React', React);
afterAll(() => vi.unstubAllGlobals());

function parseHtml(html) {
  const { document } = new JSDOM(html).window;
  return document.body ?? document;
}

describe('object manifest ownership', () => {
  const agent = (source) => ({
    kind: 'object', prompt: 'Help.', skills: [], tools: [],
    card: { source, front_matter: { name: 'reviewer', version: '1.0.0' } },
  });

  it('keeps bundled manifests read-only, even with a stale edit state', () => {
    const doc = parseHtml(renderToStaticMarkup(<ObjectAgentDetailBody agent={agent('native')} editing />));
    expect(doc.textContent).toContain('Read-only');
    expect(doc.querySelector('textarea')).toBeNull();
    expect(Array.from(doc.querySelectorAll('button')).some((b) => b.textContent.trim() === 'Edit')).toBe(false);
    expect(Array.from(doc.querySelectorAll('button')).some((b) => b.textContent.trim() === 'Save')).toBe(false);
  });

  it.each(['user', 'marketplace'])('allows editing %s documents', (source) => {
    const doc = parseHtml(renderToStaticMarkup(<ObjectAgentDetailBody agent={agent(source)} />));
    expect(Array.from(doc.querySelectorAll('button')).some((b) => b.textContent.trim() === 'Edit')).toBe(true);
  });

  it('keeps a rejected manifest editable while showing its structured failure', () => {
    const draft = '---\nname: edited-agent\n---\nKeep this rejected draft.';
    const error = { title: 'Couldn’t save agent', message: 'Manifest name conflicts with another agent.' };
    const doc = parseHtml(renderToStaticMarkup(
      <ObjectAgentDetailBody agent={agent('user')} editing draft={draft} error={error} />,
    ));
    expect(doc.querySelector('[role="alert"]').textContent).toContain(error.message);
    expect(doc.querySelector('textarea').value).toBe(draft);
    expect(doc.querySelector('textarea').disabled).toBe(false);
  });
});
