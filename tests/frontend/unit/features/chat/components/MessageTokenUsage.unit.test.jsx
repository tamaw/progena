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

describe('MessageTokenUsage expanding icon', () => {
  async function renderUsage(props = { tokenCount: 1234, finalCallTokenCount: 200 }) {
    await act(async () => root.render(<MessageTokenUsage {...props} />));
    return host.querySelector('[role="button"]');
  }
  const press = (control, key) => act(async () => {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
    control.dispatchEvent(event);
    return event;
  });

  it('renders a token icon whose accessible name is the provider-reported count', async () => {
    const control = await renderUsage();
    expect(control.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    expect(control.getAttribute('aria-label')).toBe('1,234 output tokens');
    expect(control.getAttribute('aria-expanded')).toBe('false');
    expect(control.tabIndex).toBe(0);
    expect(control.textContent).toBe('1,234 output tokens');
  });

  it('pins the count open on click or tap and collapses on a second click', async () => {
    const control = await renderUsage();
    await act(async () => control.click());
    expect(control.getAttribute('aria-expanded')).toBe('true');
    await act(async () => control.click());
    expect(control.getAttribute('aria-expanded')).toBe('false');
  });

  it('toggles with Enter and Space, and collapses on Escape or blur', async () => {
    const control = await renderUsage();
    await act(async () => control.focus());
    expect(document.querySelector('[role="tooltip"]')).not.toBeNull();

    await press(control, 'Enter');
    expect(control.getAttribute('aria-expanded')).toBe('true');
    await press(control, ' ');
    expect(control.getAttribute('aria-expanded')).toBe('false');
    await press(control, ' ');
    expect(control.getAttribute('aria-expanded')).toBe('true');

    await press(control, 'Escape');
    expect(control.getAttribute('aria-expanded')).toBe('false');
    expect(document.querySelector('[role="tooltip"]')).toBeNull();

    await press(control, 'Enter');
    await act(async () => control.blur());
    expect(control.getAttribute('aria-expanded')).toBe('false');
  });

  it('prevents page scroll for Space and keeps the breakdown tooltip on focus', async () => {
    const control = await renderUsage();
    await act(async () => control.focus());
    const event = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true });
    await act(async () => control.dispatchEvent(event));
    expect(event.defaultPrevented).toBe(true);
    expect(document.querySelector('[role="tooltip"]').textContent).toContain('Final call');
  });
});
