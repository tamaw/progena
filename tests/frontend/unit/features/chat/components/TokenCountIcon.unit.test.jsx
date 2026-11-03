// @vitest-environment jsdom
import React, { act } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import TokenCountIcon from '@frontend/features/chat/components/TokenCountIcon';

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

const press = (control, key) => act(async () => {
  control.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
});

describe('TokenCountIcon', () => {
  it('discloses a per-event count by click, Enter and Space without a tooltip', async () => {
    await act(async () => root.render(<TokenCountIcon label="~120 tokens" />));
    const control = host.querySelector('[role="button"]');
    expect(control.tabIndex).toBe(0);
    expect(control.getAttribute('aria-label')).toBe('~120 tokens');
    expect(control.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    expect(control.getAttribute('aria-expanded')).toBe('false');

    await act(async () => control.click());
    expect(control.getAttribute('aria-expanded')).toBe('true');
    await act(async () => control.focus());
    await press(control, 'Escape');
    expect(control.getAttribute('aria-expanded')).toBe('false');
    await press(control, 'Enter');
    expect(control.getAttribute('aria-expanded')).toBe('true');
    await press(control, ' ');
    expect(control.getAttribute('aria-expanded')).toBe('false');
    await press(control, ' ');
    await act(async () => control.blur());
    expect(control.getAttribute('aria-expanded')).toBe('false');
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
  });

  it('adds the shared metrics tooltip only when details are supplied', async () => {
    await act(async () => root.render(<TokenCountIcon label="12 output tokens" details={<span>Breakdown</span>} />));
    await act(async () => host.querySelector('[role="button"]').focus());
    expect(document.querySelector('[role="tooltip"]').textContent).toBe('Breakdown');
  });

  it('renders a passive count inside another control without a nested control', () => {
    const markup = renderToStaticMarkup(<button type="button"><TokenCountIcon label="42 reasoning tokens" passive /></button>);
    expect(markup).toContain('42 reasoning tokens');
    expect(markup).toContain('<svg width="12" height="12" viewBox="0 0 16 16"');
    expect(markup).toContain('aria-hidden="true"');
    expect(markup).not.toContain('role="button"');
    expect(markup).not.toContain('tabindex');
  });

  it('renders nothing without a label', () => {
    expect(renderToStaticMarkup(<TokenCountIcon label="" />)).toBe('');
  });
});
