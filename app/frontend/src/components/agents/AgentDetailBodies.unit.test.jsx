import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterAll, describe, expect, it, vi } from 'vitest';
import { ObjectAgentDetailBody } from './AgentDetailBodies';

vi.stubGlobal('React', React);
afterAll(() => vi.unstubAllGlobals());

describe('object manifest ownership', () => {
  const agent = (source) => ({
    kind: 'object', prompt: 'Help.', skills: [], tools: [],
    card: { source, front_matter: { name: 'reviewer', version: '1.0.0' } },
  });

  it('keeps bundled manifests read-only, even with a stale edit state', () => {
    const html = renderToStaticMarkup(<ObjectAgentDetailBody agent={agent('native')} editing />);
    expect(html).toContain('Read-only');
    expect(html).not.toContain('<textarea');
    expect(html).not.toContain('>Edit<');
    expect(html).not.toContain('>Save<');
  });

  it.each(['user', 'marketplace'])('allows editing %s documents', (source) => {
    const html = renderToStaticMarkup(<ObjectAgentDetailBody agent={agent(source)} />);
    expect(html).toContain('>Edit<');
  });
});
