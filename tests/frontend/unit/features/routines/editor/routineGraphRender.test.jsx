import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Provider } from 'react-redux';
import { describe, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { store } from '@frontend/store/store';
import { GraphEditContext } from '@frontend/features/routines/editor/graphEditorContext';
import { NodeInspector } from '@frontend/features/routines/editor/NodeInspector';
import { RoutineChain } from '@frontend/features/routines/editor/RoutineChain';

vi.mock('@frontend/context/ConfigContext', () => ({
  useConfig: () => ({ getApiUrl: (name) => `/api/ui/${name}` }),
}));

function renderWithGraph(component, nodes, edges, overrides = {}) {
  return renderToStaticMarkup(
    <Provider store={store}>
      <GraphEditContext.Provider value={{
        nodes,
        edges,
        runMeta: {},
        runData: {},
        runStatus: 'idle',
        loopMembership: new Map(),
        loopIterations: {},
        ...overrides,
      }}>
        {component}
      </GraphEditContext.Provider>
    </Provider>,
  );
}

function parseMarkup(markup) {
  const { document } = new JSDOM(markup).window;
  return document.body ?? document;
}

describe('extracted routine components', () => {
  it('renders inspector config and marks the required field on an incomplete prompt', () => {
    const node = { id: 'prompt', kind: 'prompt', label: 'Compose response', config: {} };
    const markup = renderWithGraph(<NodeInspector node={node} isNew />, [node], []);
    const doc = parseMarkup(markup);

    expect(doc.textContent).toContain('Compose response');
    expect(doc.querySelector('[aria-required="true"]')).not.toBeNull();
  });

  it('renders a complete inspector expression and cached run details', () => {
    const node = { id: 'calculate', kind: 'eval', label: 'Calculate', config: { expression: 'request.count + 1' } };
    const markup = renderWithGraph(<NodeInspector node={node} />, [node], [], {
      runMeta: { calculate: [{ duration_ms: 1250, output: { calculate: 3 } }] },
    });
    const doc = parseMarkup(markup);

    expect(doc.textContent).toContain('Calculate');
    expect(doc.textContent).toContain('request.count + 1');
    expect(doc.textContent).toContain('1.3s');
    expect(doc.querySelector('[aria-required="true"]')).toBeNull();
  });

  it('renders editable Code source with filesystem permissions', () => {
    const node = { id: 'transform', kind: 'code', label: 'Transform', config: { language: 'javascript', code: 'return input.items;' } };
    const doc = parseMarkup(renderWithGraph(<NodeInspector node={node} />, [node], []));

    expect(doc.textContent).toContain('JavaScript');
    expect(doc.querySelector('textarea')?.textContent).toBe('return input.items;');
    expect(doc.textContent).toContain('Filesystem access');
  });

  it('renders nested forks and their shared join once with connector controls', () => {
    const nodes = [
      { id: 'outer', kind: 'condition', config: { expression: 'True' } },
      { id: 'inner', kind: 'condition', config: { expression: 'True' } },
      ...['a', 'b', 'c', 'joined'].map(id => ({ id, kind: 'noop', config: {} })),
    ];
    const edges = [
      { from: 'outer', level: 'route', router: true, branches: { false: 'c', true: 'inner' } },
      { from: 'inner', level: 'route', router: true, branches: { false: 'b', true: 'a' } },
      { from: ['a', 'b', 'c'], to: 'joined', level: 'fan_in' },
    ];
    const markup = renderWithGraph(<RoutineChain nodes={nodes} edges={edges} entry="outer" />, nodes, edges);
    const doc = parseMarkup(markup);

    for (const node of nodes) expect(markup.match(new RegExp(`data-node-id="${node.id}"`, 'g'))).toHaveLength(1);
    expect(markup.indexOf('data-node-id="a"')).toBeLessThan(markup.indexOf('data-node-id="b"'));
    expect(markup.indexOf('data-node-id="inner"')).toBeLessThan(markup.indexOf('data-node-id="c"'));
    expect(doc.querySelector('.routine-fork--joined')).not.toBeNull();
    expect(doc.querySelector('[aria-label="Insert step here"]')).not.toBeNull();
    expect(doc.textContent).not.toContain('Unreachable steps');

    const readOnlyDoc = parseMarkup(renderWithGraph(<RoutineChain nodes={nodes} edges={edges} entry="outer" />, nodes, edges, { readOnly: true }));
    expect(readOnlyDoc.querySelector('[data-node-id="joined"]')).not.toBeNull();
    expect(readOnlyDoc.querySelector('[aria-label="Insert step here"]')).toBeNull();
    expect(readOnlyDoc.querySelector('[aria-label="Remove branch"]')).toBeNull();
  });
});
