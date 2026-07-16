import { afterEach, beforeEach, afterAll, describe, expect, it, vi } from 'vitest';
import React from 'react';

const harness = vi.hoisted(() => ({
  effects: [],
  refs: [],
  image: { src: 'https://cdn.example.test/full.png', alt: 'Full image' },
  setState: null,
  listeners: new Map(),
}));

vi.mock('react', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useState: () => {
      harness.setState = vi.fn();
      return [harness.image, harness.setState];
    },
    useRef: () => harness.refs.shift() || { current: null },
    useEffect: (effect) => harness.effects.push(effect),
    useCallback: (callback) => callback,
  };
});

import MessageImageStrip from './MessageImageStrip';

function expand(node) {
  if (!node || typeof node !== 'object') return node;
  if (typeof node.type === 'function') return expand(node.type(node.props));
  if (Array.isArray(node)) return node.map(expand);
  if (node.props?.children) {
    return { ...node, props: { ...node.props, children: expand(node.props.children) } };
  }
  return node;
}

function findByRole(node, role) {
  if (!node || typeof node !== 'object') return null;
  if (node.props?.role === role) return node;
  const children = node.props?.children;
  if (Array.isArray(children)) {
    for (const child of children) {
      const match = findByRole(child, role);
      if (match) return match;
    }
  }
  return findByRole(children, role);
}

function findByClass(node, className) {
  if (!node || typeof node !== 'object') return null;
  if (node.props?.className?.split?.(' ').includes(className)) return node;
  const children = node.props?.children;
  if (Array.isArray(children)) {
    for (const child of children) {
      const match = findByClass(child, className);
      if (match) return match;
    }
  }
  return findByClass(children, className);
}

const previousReact = globalThis.React;
beforeEach(() => {
  globalThis.React = React;
});
afterAll(() => {
  globalThis.React = previousReact;
});

afterEach(() => {
  harness.effects = [];
  harness.refs = [];
  harness.setState = null;
  harness.listeners.clear();
  vi.unstubAllGlobals();
});

describe('MessageImageStrip modal accessibility', () => {
  it('focuses the close control, traps Tab, handles Escape, and restores the opener', () => {
    const opener = { isConnected: true, focus: vi.fn() };
    const closeButton = { focus: vi.fn() };
    const firstFocusable = { focus: vi.fn() };
    const lastFocusable = { focus: vi.fn() };
    const modal = {
      contains: () => true,
      querySelectorAll: () => [firstFocusable, lastFocusable],
    };
    harness.refs = [{ current: opener }, { current: closeButton }, { current: modal }];

    const documentStub = {
      activeElement: closeButton,
      addEventListener: vi.fn((type, listener) => harness.listeners.set(type, listener)),
      removeEventListener: vi.fn(),
    };
    vi.stubGlobal('document', documentStub);

    const tree = expand(MessageImageStrip({ images: [harness.image] }));
    const dialog = findByRole(tree, 'dialog');
    const close = findByClass(tree, 'img-modal-close');

    expect(dialog.props['aria-modal']).toBe('true');
    expect(dialog.props['aria-label']).toBe('Image preview');
    expect(close.props['aria-label']).toBe('Close');

    const cleanups = harness.effects.map((effect) => effect());
    expect(closeButton.focus).toHaveBeenCalledTimes(1);

    const onKeyDown = harness.listeners.get('keydown');
    const preventDefault = vi.fn();
    documentStub.activeElement = lastFocusable;
    onKeyDown({ key: 'Tab', shiftKey: false, preventDefault });
    expect(preventDefault).toHaveBeenCalledTimes(1);
    expect(firstFocusable.focus).toHaveBeenCalledTimes(1);

    documentStub.activeElement = firstFocusable;
    onKeyDown({ key: 'Tab', shiftKey: true, preventDefault });
    expect(lastFocusable.focus).toHaveBeenCalledTimes(1);

    onKeyDown({ key: 'Escape', preventDefault });
    expect(preventDefault).toHaveBeenCalledTimes(3);
    expect(harness.setState).toHaveBeenCalledWith(null);

    close.props.onClick();
    expect(opener.focus).toHaveBeenCalled();
    cleanups.forEach((cleanup) => cleanup?.());
    expect(documentStub.removeEventListener).toHaveBeenCalledWith('keydown', onKeyDown);
  });
});
