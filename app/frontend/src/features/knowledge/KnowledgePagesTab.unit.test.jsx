// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { fetchRevision } = vi.hoisted(() => ({
  fetchRevision: vi.fn((args) => ({ type: 'revision/load', payload: args })),
}));
vi.mock('./state', async () => {
  const actual = await vi.importActual('./state');
  return { ...actual, fetchKnowledgePageRevision: fetchRevision };
});
vi.mock('../../hooks/useMermaidRender', () => ({ useMermaidRender: () => {} }));

import KnowledgePagesTab from './KnowledgePagesTab';

let host;
let root;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  host?.remove();
  host = null;
  root = null;
  fetchRevision.mockClear();
  vi.unstubAllGlobals();
});

function stateFor({ revision = null, revisionError = null }) {
  return {
    uiData: {
      knowledge: {
        pagesByBaseId: { base: [] },
        pageByKey: {
          'base/index.md': {
            body: 'live body that must not leak',
            front_matter: { sources: ['https://live.example/source'] },
          },
        },
        revisionsByKey: { 'base/index.md': [] },
        revisionByKey: revision ? { 'base/index.md@r1': revision } : {},
        pageRequestByKey: {},
        revisionsRequestByKey: {},
        revisionRequestByKey: {
          'base/index.md@r1': { loading: false, error: revisionError, requestId: null },
        },
      },
    },
  };
}

async function renderReader(state) {
  const dispatch = vi.fn();
  const store = { getState: () => state, subscribe: () => () => {}, dispatch };
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root.render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/?revision=r1']}>
        <KnowledgePagesTab baseId="base" baseName="Base" knowledgeUrl="/knowledge" />
      </MemoryRouter>
    </Provider>,
  ));
  return dispatch;
}

describe('KnowledgePagesTab historical reader', () => {
  it('hides cached live content and sources when the requested revision fails, and retries that revision', async () => {
    const dispatch = await renderReader(stateFor({ revisionError: 'historical load failed' }));

    expect(host.querySelector('.app-markdown-content')).toBeNull();
    expect(host.textContent).not.toContain('live body that must not leak');
    expect(host.querySelector('.knowledge-page-sources')).toBeNull();
    expect(host.querySelector('[role="alert"]')).not.toBeNull();

    await act(async () => host.querySelector('[role="alert"] button').click());
    expect(dispatch).toHaveBeenLastCalledWith({
      type: 'revision/load',
      payload: { knowledgeUrl: '/knowledge', baseId: 'base', path: 'index.md', revisionId: 'r1' },
    });
  });

  it('renders the historical document and its sources instead of the cached live page', async () => {
    await renderReader(stateFor({
      revision: {
        content: 'historical body',
        front_matter: { sources: ['https://history.example/source'] },
      },
    }));

    expect(host.querySelector('.app-markdown-content').textContent).toContain('historical body');
    expect(host.textContent).not.toContain('live body that must not leak');
    expect(host.querySelector('.knowledge-page-sources').textContent).toContain('https://history.example/source');
    expect(host.textContent).not.toContain('https://live.example/source');
  });
});
