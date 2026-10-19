// @vitest-environment jsdom
import React, { act } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { afterAll, describe, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import realtimeReducer from '../../store/realtimeSlice';
import uiDataReducer, { applyUiEvent } from '../../store/uiDataSlice';
import editorDraftsReducer from '../../store/editorDraftsSlice';
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

  it('restores raw SKILL.md drafts by agent, source, and path', async () => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    const skillPath = '/skills/marketplace__quality/review';
    const store = configureStore({
      reducer: {
        realtime: realtimeReducer,
        uiData: uiDataReducer,
        editorDrafts: editorDraftsReducer,
      },
    });
    store.dispatch(applyUiEvent({
      type: 'agent.skill.content.updated',
      payload: {
        path: skillPath,
        name: 'review',
        source: 'marketplace',
        editable: true,
        front_matter: { name: 'review' },
        raw_content: '---\nname: review\n---\nCanonical skill.',
      },
    }));

    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    const agentWithSkill = (id) => ({
      ...agent('user'),
      id,
      skills: [skillPath],
    });
    const render = async (currentAgent) => {
      await act(async () => root.render(
        <Provider store={store}>
          <ObjectAgentDetailBody agent={currentAgent} agentsUrl="/api/agent-profiles" />
        </Provider>,
      ));
    };
    const click = async (label) => {
      const card = host.querySelector('.agents-skill-card');
      const button = Array.from(card.querySelectorAll('button'))
        .find((candidate) => candidate.textContent.trim() === label);
      await act(async () => button.click());
    };
    const enterDraft = async (value) => {
      const textarea = host.querySelector('.agents-skill-card textarea');
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(textarea, value);
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
      });
    };

    try {
      await render(agentWithSkill('object-a'));
      await click('review');
      await click('Edit');
      await enterDraft('A raw invalid draft');

      await render(agentWithSkill('object-b'));
      await click('review');
      await click('Edit');
      await enterDraft('B raw invalid draft');

      await render(agentWithSkill('object-a'));
      expect(host.querySelector('.agents-skill-card textarea').value).toBe('A raw invalid draft');
      await render(agentWithSkill('object-b'));
      expect(host.querySelector('.agents-skill-card textarea').value).toBe('B raw invalid draft');
    } finally {
      await act(async () => root.unmount());
      host.remove();
      vi.unstubAllGlobals();
      vi.stubGlobal('React', React);
    }
  });
});
