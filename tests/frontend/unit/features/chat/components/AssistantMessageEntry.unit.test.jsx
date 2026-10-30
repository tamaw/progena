// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import AssistantMessageEntry from '@frontend/features/chat/components/AssistantMessageEntry';

// Isolate React's ownership of DOM that an external diagram renderer mutates.
// Native Mermaid rendering is exercised in the browser smoke.
vi.mock('@frontend/hooks/useMermaidRender', () => ({ useMermaidRender: () => {} }));

let host;
let root;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it('retains a rendered diagram through metadata updates but replaces it when source changes', async () => {
  const item = {
    id: 'answer', content: '```mermaid\ngraph LR\n A-->B\n```',
    isStreaming: false, responseStatus: 'completed', tokenCount: null,
  };
  await act(async () => root.render(<AssistantMessageEntry item={item} agentName="Progena" />));
  const pre = host.querySelector('pre.mermaid');
  const diagram = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  label.textContent = 'Rendered result';
  diagram.append(label);
  pre.replaceChildren(diagram);
  pre.dataset.processed = 'true';

  await act(async () => root.render(<AssistantMessageEntry item={{ ...item, tokenCount: 7, responseStatus: 'interrupted' }} agentName="Progena" />));
  expect(host.querySelector('pre.mermaid svg')).toBe(diagram);
  expect(host.querySelector('pre.mermaid text').textContent).toBe('Rendered result');
  expect(host.querySelector('[role="status"]').textContent.toLowerCase()).toBe('interrupted');

  await act(async () => root.render(<AssistantMessageEntry item={{ ...item, content: '```mermaid\ngraph LR\n A-->C\n```' }} agentName="Progena" />));
  expect(host.querySelector('pre.mermaid svg')).toBeNull();
  expect(host.querySelector('pre.mermaid').textContent).toContain('A-->C');
});
