// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Select from '@frontend/components/Select';

let host;
let root;
let triggerTop;
let scrollHeightDescriptor;
let scrollWidthDescriptor;
let innerHeightDescriptor;
let innerWidthDescriptor;
let scrollIntoViewDescriptor;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('React', React);
  vi.stubGlobal('requestAnimationFrame', (callback) => {
    callback();
    return 1;
  });
  innerHeightDescriptor = Object.getOwnPropertyDescriptor(window, 'innerHeight');
  innerWidthDescriptor = Object.getOwnPropertyDescriptor(window, 'innerWidth');
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 400 });
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 600 });
  triggerTop = 180;
  scrollHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight');
  scrollWidthDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollWidth');
  scrollIntoViewDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView');
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
  Object.defineProperties(HTMLElement.prototype, {
    scrollHeight: {
      configurable: true,
      get() {
        if (!this.classList.contains('app-listbox__panel')) return 0;
        return this.style.maxHeight ? Number.parseFloat(this.style.maxHeight) : 274;
      },
    },
    scrollWidth: {
      configurable: true,
      get() {
        return this.classList.contains('app-listbox__panel') ? 180 : 0;
      },
    },
  });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  host.remove();
  if (scrollHeightDescriptor) Object.defineProperty(HTMLElement.prototype, 'scrollHeight', scrollHeightDescriptor);
  else delete HTMLElement.prototype.scrollHeight;
  if (scrollWidthDescriptor) Object.defineProperty(HTMLElement.prototype, 'scrollWidth', scrollWidthDescriptor);
  else delete HTMLElement.prototype.scrollWidth;
  if (scrollIntoViewDescriptor) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', scrollIntoViewDescriptor);
  else delete HTMLElement.prototype.scrollIntoView;
  if (innerHeightDescriptor) Object.defineProperty(window, 'innerHeight', innerHeightDescriptor);
  else delete window.innerHeight;
  if (innerWidthDescriptor) Object.defineProperty(window, 'innerWidth', innerWidthDescriptor);
  else delete window.innerWidth;
  vi.unstubAllGlobals();
});

describe('Select portal positioning', () => {
  it('remeasures unconstrained height after closing before reopening above its trigger', async () => {
    await act(async () => root.render(
      <Select
        portal
        ariaLabel="Viewport-bound select"
        value="one"
        onChange={() => {}}
        options={Array.from({ length: 20 }, (_, index) => ({ value: String(index), label: `Option ${index}` }))}
      />,
    ));
    const trigger = host.querySelector('button');
    trigger.getBoundingClientRect = () => ({
      top: triggerTop,
      bottom: triggerTop + 20,
      left: 20,
      width: 180,
    });

    await act(async () => trigger.click());
    expect(document.querySelector('.app-listbox__panel').style.maxHeight).toBe('186px');

    await act(async () => trigger.click());
    triggerTop = 330;
    await act(async () => trigger.click());

    const panel = document.querySelector('.app-listbox__panel');
    expect(panel.style.top).toBe('50px');
    expect(panel.style.maxHeight).toBe('316px');
  });
});

describe('Select commits', () => {
  const options = [{ value: 'send', label: 'Send' }, { value: 'agent', label: 'Agent' }];
  const listKey = (key) => document.querySelector('[role="listbox"]')
    .dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));

  it('treats choosing the current option as no change, like a native select', async () => {
    const onChange = vi.fn();
    await act(async () => root.render(<Select portal ariaLabel="Mode" value="send" onChange={onChange} options={options} />));
    const trigger = host.querySelector('button');

    await act(async () => trigger.click());
    await act(async () => listKey('Enter'));
    await act(async () => trigger.click());
    await act(async () => document.querySelector('.app-listbox__option.is-selected').click());
    expect(onChange).not.toHaveBeenCalled();

    await act(async () => trigger.click());
    await act(async () => listKey('ArrowDown'));
    await act(async () => listKey('Enter'));
    expect(onChange).toHaveBeenCalledExactlyOnceWith('agent');
  });

  it('returns focus to its trigger when the menu closes from the keyboard', async () => {
    await act(async () => root.render(<Select portal ariaLabel="Mode" value="send" onChange={() => {}} options={options} />));
    const trigger = host.querySelector('button');

    await act(async () => trigger.click());
    expect(document.activeElement.getAttribute('role')).toBe('listbox');
    await act(async () => listKey('Escape'));
    expect(document.activeElement).toBe(trigger);

    await act(async () => trigger.click());
    await act(async () => listKey('ArrowDown'));
    await act(async () => listKey('Enter'));
    expect(document.activeElement).toBe(trigger);
  });
});
