// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import useIdleTimeout from './useIdleTimeout';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const MINUTE = 60_000;
let root;
let container;
let latest;

function Probe({ ms }) {
  latest = useIdleTimeout(ms);
  return null;
}

function mount(ms) {
  root = createRoot(container);
  act(() => root.render(<Probe ms={ms} />));
}

const advance = (ms) => act(() => vi.advanceTimersByTime(ms));
const input = (type) => act(() => window.dispatchEvent(new Event(type)));

beforeEach(() => {
  vi.useFakeTimers();
  container = document.createElement('div');
});
afterEach(() => {
  act(() => root.unmount());
  vi.useRealTimers();
});

describe('useIdleTimeout', () => {
  it('goes idle after the timeout and stays idle until resumed', () => {
    mount(5 * MINUTE);
    advance(5 * MINUTE - 2000);
    expect(latest[0]).toBe(false);
    advance(3000);
    expect(latest[0]).toBe(true);

    // Input while idle must not resume; only the explicit action does.
    input('pointermove');
    advance(2000);
    expect(latest[0]).toBe(true);

    act(() => latest[1]());
    expect(latest[0]).toBe(false);
    advance(5 * MINUTE - 2000);
    expect(latest[0]).toBe(false);
    advance(3000);
    expect(latest[0]).toBe(true);
  });

  it('restarts the countdown on user input', () => {
    mount(5 * MINUTE);
    advance(4 * MINUTE);
    input('keydown');
    advance(4 * MINUTE);
    expect(latest[0]).toBe(false);
    advance(2 * MINUTE);
    expect(latest[0]).toBe(true);
  });

  it('applies a changed timeout to the running countdown without restarting it', () => {
    mount(30 * MINUTE);
    advance(4 * MINUTE);
    act(() => root.render(<Probe ms={5 * MINUTE} />));
    advance(2 * MINUTE);
    expect(latest[0]).toBe(true);
  });
});
