// @vitest-environment jsdom
import React from 'react';
import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import MessageTokenUsage from '@frontend/features/chat/components/MessageTokenUsage';

function tooltipNumbers(props) {
  const container = document.createElement('div');
  container.innerHTML = renderToStaticMarkup(React.createElement(MessageTokenUsage, props));
  const tooltip = container.querySelector('[role="tooltip"]');
  expect(tooltip).not.toBeNull();
  return [...tooltip.textContent.matchAll(/[0-9][0-9,]*/g)]
    .map(([value]) => Number(value.replaceAll(',', '')));
}

describe('MessageTokenUsage', () => {
  it('retains reported zero without manufacturing a final-call count', () => {
    expect(tooltipNumbers({ tokenCount: 0, finalCallTokenCount: null })).toEqual([0]);
  });

  it('separates intermediate output from the final call without adding the final call twice', () => {
    expect(tooltipNumbers({ tokenCount: 125, finalCallTokenCount: 24 })).toEqual([125, 101, 24]);
  });

  it('preserves reported final-call usage when an intermediate call has unknown usage', () => {
    expect(tooltipNumbers({ tokenCount: null, finalCallTokenCount: 12 })).toEqual([12]);
  });

  it('does not fabricate counts when neither provider total is available', () => {
    expect(renderToStaticMarkup(React.createElement(MessageTokenUsage, {
      tokenCount: null, finalCallTokenCount: null,
    }))).toBe('');
  });
});
