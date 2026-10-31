// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ChatInfoTooltip from '@frontend/features/chat/components/ChatInfoTooltip';

let host;
const triggerRect = { top: 200, right: 310, bottom: 220, left: 280, width: 30, height: 20 };
const tooltipRect = { top: 0, right: 120, bottom: 40, left: 0, width: 120, height: 40 };

let root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal('innerWidth', 320);
  vi.stubGlobal('innerHeight', 240);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function getBoundingClientRect() {
    return this.getAttribute('role') === 'tooltip' ? tooltipRect : triggerRect;
  });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  delete globalThis.IS_REACT_ACT_ENVIRONMENT;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('chat metric tooltip', () => {
  it('anchors keyboard details beside the trigger, clips them to the viewport, and dismisses without moving focus', async () => {
    await act(async () => root.render(<ChatInfoTooltip content="125 / 1000">Metric</ChatInfoTooltip>));
    const trigger = host.querySelector('[tabindex]');
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
    await act(async () => trigger.focus());
    const tooltip = document.getElementById(trigger.getAttribute('aria-describedby'));
    expect(tooltip.textContent).toBe('125 / 1000');
    expect(tooltip.parentElement).toBe(document.body);
    const left = Number.parseFloat(tooltip.style.left);
    const top = Number.parseFloat(tooltip.style.top);
    expect(left).toBeGreaterThanOrEqual(8);
    expect(left + tooltipRect.width).toBeLessThanOrEqual(window.innerWidth - 8);
    expect(top).toBeGreaterThanOrEqual(8);
    expect(top + tooltipRect.height).toBeLessThanOrEqual(window.innerHeight - 8);
    expect(top).toBeLessThan(triggerRect.top);
    await act(async () => trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    await act(async () => { trigger.blur(); trigger.focus(); });
    expect(document.querySelector('[role="tooltip"]')).not.toBeNull();
    await act(async () => trigger.blur());
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
    await act(async () => trigger.focus());
    await act(async () => document.dispatchEvent(new Event('scroll')));
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
  });

  it('follows the hovered pointer while retaining the portal', async () => {
    await act(async () => root.render(<ChatInfoTooltip content="125 / 1000">Metric</ChatInfoTooltip>));
    const trigger = host.querySelector('[tabindex]');
    await act(async () => trigger.dispatchEvent(new MouseEvent('pointerover', {
      bubbles: true,
      clientX: 280,
      clientY: 220,
    })));
    const tooltip = document.querySelector('[role="tooltip"]');
    const left = Number.parseFloat(tooltip.style.left);
    const top = Number.parseFloat(tooltip.style.top);
    expect(left).toBeGreaterThanOrEqual(8);
    expect(left + tooltipRect.width).toBeLessThanOrEqual(window.innerWidth - 8);
    expect(top).toBeGreaterThanOrEqual(8);
    expect(top + tooltipRect.height).toBeLessThanOrEqual(window.innerHeight - 8);
    expect(Math.abs(left - 280)).toBeLessThanOrEqual(tooltipRect.width + 8);
    expect(Math.abs(top - 220)).toBeLessThanOrEqual(tooltipRect.height + 8);
  });
});
