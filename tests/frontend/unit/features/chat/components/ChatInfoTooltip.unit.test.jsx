// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import ChatInfoTooltip from '@frontend/features/chat/components/ChatInfoTooltip';

let host;
let root;
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

describe('chat metric tooltip', () => {
  it('exposes keyboard details outside the scroll pane and dismisses without moving focus', async () => {
    await act(async () => root.render(<ChatInfoTooltip content="125 / 1000">Metric</ChatInfoTooltip>));
    const trigger = host.querySelector('[tabindex]');
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
    await act(async () => trigger.focus());
    const tooltip = document.getElementById(trigger.getAttribute('aria-describedby'));
    expect(tooltip.textContent).toBe('125 / 1000');
    expect(tooltip.parentElement).toBe(document.body);
    await act(async () => trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    await act(async () => { trigger.blur(); trigger.focus(); });
    expect(document.querySelector('[role="tooltip"]')).not.toBeNull();
    await act(async () => document.dispatchEvent(new Event('scroll')));
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
  });
});
