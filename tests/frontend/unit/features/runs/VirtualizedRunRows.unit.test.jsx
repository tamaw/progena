// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import VirtualizedRunRows from '@frontend/features/runs/VirtualizedRunRows';

const rows = (expanded) => Array.from({ length: 20 }, (_, index) => ({
  key: `run-${index}`,
  expanded: index === 0 && expanded,
  label: index === 0 ? 'selected run' : `run ${index}`,
}));

describe('VirtualizedRunRows measurements', () => {
  let host;
  let root;
  let scrollElement;
  let originalRect;
  let frames;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('React', React);
    frames = [];
    vi.stubGlobal('requestAnimationFrame', (callback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.stubGlobal('cancelAnimationFrame', () => {});
    originalRect = HTMLElement.prototype.getBoundingClientRect;
    HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
      return { height: this.textContent === 'selected run' ? 1077 : 36 };
    };
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    scrollElement = document.createElement('div');
    Object.defineProperties(scrollElement, {
      clientHeight: { configurable: true, value: 100 },
      scrollTop: { configurable: true, writable: true, value: 0 },
    });
    document.body.append(scrollElement);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    scrollElement.remove();
    HTMLElement.prototype.getBoundingClientRect = originalRect;
    vi.unstubAllGlobals();
  });

  async function flushAnimationFrames() {
    await act(async () => {
      const pending = frames;
      frames = [];
      pending.forEach((callback) => callback());
    });
  }

  it('drops an offscreen expanded measurement when the row collapses', async () => {
    const render = async (expanded) => act(async () => root.render(
      <VirtualizedRunRows rows={rows(expanded)} scrollElement={scrollElement} renderRow={(row) => <span>{row.label}</span>} />,
    ));
    await render(true);
    await flushAnimationFrames();
    expect(host.querySelector('.runs-virtual-body').style.height).toBe('1761px');

    await act(async () => {
      scrollElement.scrollTop = 1800;
      scrollElement.dispatchEvent(new Event('scroll'));
    });
    await flushAnimationFrames();
    expect(host.textContent).not.toContain('selected run');

    await render(false);
    expect(host.querySelector('.runs-virtual-body').style.height).toBe('720px');
  });
});
