// @vitest-environment jsdom
import React, { act } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import MessageTokenUsage from '@frontend/features/chat/components/MessageTokenUsage';

let root;
let host;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  delete globalThis.IS_REACT_ACT_ENVIRONMENT;
});

async function tooltipNumbers(props) {
  await act(async () => root.render(<MessageTokenUsage {...props} />));
  await act(async () => host.querySelector('[tabindex]').focus());
  const tooltip = document.querySelector('[role="tooltip"]');
  expect(tooltip).not.toBeNull();
  return [...tooltip.textContent.matchAll(/[0-9][0-9,]*/g)]
    .map(([value]) => Number(value.replaceAll(',', '')));
}

describe('MessageTokenUsage', () => {
  it('retains reported zero without manufacturing a final-call count', async () => {
    expect(await tooltipNumbers({ tokenCount: 0, finalCallTokenCount: null })).toEqual([0]);
  });

  it('separates intermediate output from the final call without adding the final call twice', async () => {
    expect(await tooltipNumbers({ tokenCount: 125, finalCallTokenCount: 24 })).toEqual([125, 101, 24]);
  });

  it('preserves reported final-call usage when an intermediate call has unknown usage', async () => {
    expect(await tooltipNumbers({ tokenCount: null, finalCallTokenCount: 12 })).toEqual([12]);
  });

  it('does not fabricate counts when neither provider total is available', () => {
    expect(renderToStaticMarkup(React.createElement(MessageTokenUsage, {
      tokenCount: null, finalCallTokenCount: null,
    }))).toBe('');
  });
});
