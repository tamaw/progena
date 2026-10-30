// @vitest-environment jsdom
import React, { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot } from 'react-dom/client';
import CacheExpiryIndicator from '@frontend/features/chat/components/CacheExpiryIndicator';

let host;
let root;
const now = Date.UTC(2026, 0, 1);
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(now);
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.useRealTimers();
  delete globalThis.IS_REACT_ACT_ENVIRONMENT;
});

function text() { return host.querySelector('[role="timer"] > span')?.textContent; }

describe('CacheExpiryIndicator', () => {
  it('counts down at minute boundaries, expires at zero, and resets for a new response', async () => {
    await act(async () => root.render(<CacheExpiryIndicator expiry={{ expiresAt: now + 120000, ttlSeconds: 120 }} />));
    expect(text()).toBe('2m');
    await act(async () => vi.advanceTimersByTime(60000));
    expect(text()).toBe('1m');
    await act(async () => vi.advanceTimersByTime(60000));
    expect(text()).toBe('0m');
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => root.render(<CacheExpiryIndicator expiry={{ expiresAt: Date.now() + 3600000, ttlSeconds: 3600 }} />));
    expect(text()).toBe('60m');
  });

  it('uses wall-clock time when a backgrounded tab becomes visible', async () => {
    await act(async () => root.render(<CacheExpiryIndicator expiry={{ expiresAt: now + 3600000, ttlSeconds: 3600 }} />));
    vi.setSystemTime(now + 48 * 60000);
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    await act(async () => document.dispatchEvent(new Event('visibilitychange')));
    expect(text()).toBe('12m');
    delete document.hidden;
  });

  it('does not invent a timer when retention is missing', async () => {
    await act(async () => root.render(<CacheExpiryIndicator expiry={null} />));
    expect(host.querySelector('[role="timer"]')).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });
});
