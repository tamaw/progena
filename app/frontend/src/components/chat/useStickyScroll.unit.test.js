import { afterEach, describe, expect, it, vi } from 'vitest';

const reactHarness = vi.hoisted(() => {
  let refs = [];
  let effects = [];
  let hookIndex = 0;
  let nextRefValues = [];

  return {
    beginRender(initialRefValues = []) {
      hookIndex = 0;
      effects = [];
      nextRefValues = [...initialRefValues];
    },
    flushEffects() {
      const cleanups = [];
      effects.forEach((effect) => {
        const cleanup = effect();
        if (typeof cleanup === 'function') {
          cleanups.push(cleanup);
        }
      });
      return cleanups;
    },
    reset() {
      refs = [];
      effects = [];
      hookIndex = 0;
      nextRefValues = [];
    },
    useEffect(effect) {
      effects.push(effect);
    },
    useRef(initialValue) {
      const index = hookIndex;
      hookIndex += 1;
      if (!refs[index]) {
        refs[index] = {
          current: nextRefValues.length > 0 ? nextRefValues.shift() : initialValue,
        };
      }
      return refs[index];
    },
  };
});

vi.mock('react', () => ({
  useEffect: reactHarness.useEffect,
  useRef: reactHarness.useRef,
}));

import useStickyScroll from './useStickyScroll';

function TestHarness({ dep, onResult }) {
  const hookResult = useStickyScroll(dep);
  onResult(hookResult);
  return null;
}

function renderHook(dep, element) {
  reactHarness.beginRender([element]);
  let result;
  TestHarness({
    dep,
    onResult: (value) => {
      result = value;
    },
  });
  const cleanups = reactHarness.flushEffects();
  return { result, cleanups };
}

afterEach(() => {
  reactHarness.reset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('useStickyScroll', () => {
  it('clears the pending RAF id during StrictMode cleanup replay so mount can reschedule', () => {
    const rafCallbacks = [];
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback) => {
      rafCallbacks.push(callback);
      return rafCallbacks.length;
    }));
    const cancelAnimationFrameSpy = vi.fn();
    vi.stubGlobal('cancelAnimationFrame', cancelAnimationFrameSpy);

    const element = { scrollTop: 0, scrollHeight: 400, clientHeight: 100 };
    const firstMount = renderHook('messages:1', element);

    expect(requestAnimationFrame).toHaveBeenCalledTimes(1);

    firstMount.cleanups.forEach((cleanup) => cleanup());
    expect(cancelAnimationFrameSpy).toHaveBeenCalledWith(1);

    const secondMount = renderHook('messages:1', element);
    expect(requestAnimationFrame).toHaveBeenCalledTimes(2);

    rafCallbacks[1]();
    expect(element.scrollTop).toBe(400);

    secondMount.cleanups.forEach((cleanup) => cleanup());
    expect(cancelAnimationFrameSpy).toHaveBeenCalledTimes(1);
  });
});
