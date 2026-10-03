import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { GraphEditContext } from './graphEditorContext';
import { NodeInspector } from './NodeInspector';
import { RoutineChain } from './RoutineChain';

function renderWithGraph(component, nodes, edges, overrides = {}) {
  return renderToStaticMarkup(
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
    </GraphEditContext.Provider>,
  );
}

describe('extracted routine components', () => {
  it('renders inspector config and marks the required field on an incomplete prompt', () => {
    const node = { id: 'prompt', kind: 'prompt', label: 'Compose response', config: {} };
    const markup = renderWithGraph(<NodeInspector node={node} isNew />, [node], []);

    expect(markup).toContain('Compose response');
    expect(markup).toContain('System prompt (optional)');
    expect(markup).toContain('aria-required="true"');
    expect(markup).toContain('One field is required before this step can be saved.');
    expect(markup).toContain('Fill it in, or close to discard this step.');
  });

  it('renders a complete inspector expression and cached run details', () => {
    const node = { id: 'calculate', kind: 'eval', label: 'Calculate', config: { expression: 'request.count + 1' } };
    const markup = renderWithGraph(<NodeInspector node={node} />, [node], [], {
      runMeta: { calculate: [{ duration_ms: 1250, output: { calculate: 3 } }] },
    });

    expect(markup).toContain('Calculate');
    expect(markup).toContain('request.count + 1');
    expect(markup).toContain('1.3s');
    expect(markup).not.toContain('aria-required="true"');
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

    for (const node of nodes) expect(markup.match(new RegExp(`data-node-id="${node.id}"`, 'g'))).toHaveLength(1);
    expect(markup.indexOf('data-node-id="a"')).toBeLessThan(markup.indexOf('data-node-id="b"'));
    expect(markup.indexOf('data-node-id="inner"')).toBeLessThan(markup.indexOf('data-node-id="c"'));
    expect(markup).toContain('routine-fork--joined');
    expect(markup).toContain('aria-label="Insert step here"');
    expect(markup).not.toContain('Unreachable steps');

    const readOnlyMarkup = renderWithGraph(<RoutineChain nodes={nodes} edges={edges} entry="outer" />, nodes, edges, { readOnly: true });
    expect(readOnlyMarkup).toContain('data-node-id="joined"');
    expect(readOnlyMarkup).not.toContain('aria-label="Insert step here"');
    expect(readOnlyMarkup).not.toContain('aria-label="Remove branch"');
  });
});
