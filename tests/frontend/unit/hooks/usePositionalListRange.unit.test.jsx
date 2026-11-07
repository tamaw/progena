// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import usePositionalListRange from '@frontend/hooks/usePositionalListRange';

const VIEW = { q: '', sort: 'recent' };
const sameView = (left, right) => left.q === right.q && left.sort === right.sort;

// The hook's return value is handed to the test through a callback from an effect.
function Probe({ list, view = VIEW, streamReady, fetchPage, onRange }) {
  const requestRange = usePositionalListRange({ list, view, sameView, streamReady, pageSize: 10, fetchPage });
  React.useEffect(() => {
    onRange(requestRange);
  }, [onRange, requestRange]);
  return null;
}

const freshList = (extra = {}) => ({
  view: VIEW, loadedPages: {}, inFlightRequests: {}, rangeVersion: 0, ...extra,
});

describe('usePositionalListRange', () => {
  let host;
  let root;
  let fetchPage;
  let latestRange;
  const onRange = (requestRange) => {
    latestRange = requestRange;
  };
  const render = (props) => act(async () => root.render(<Probe fetchPage={fetchPage} onRange={onRange} {...props} />));
  const api = (...args) => latestRange(...args);
  const offsets = () => fetchPage.mock.calls.map(([request]) => request.offset);

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.useFakeTimers();
    fetchPage = vi.fn();
    latestRange = undefined;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('requests page zero when the stream is ready even if the store already holds it', async () => {
    await render({ list: freshList({ loadedPages: { 0: true } }), streamReady: true });
    expect(offsets()).toEqual([0]);
  });

  it('reloads a retained nonzero page when it is scrolled into after a fresh mount', async () => {
    await render({ list: freshList({ loadedPages: { 0: true, 10: true } }), streamReady: true });
    fetchPage.mockClear();

    act(() => api(10, 20));
    expect(offsets()).toEqual([10]);
  });

  it('waits for the stream before requesting anything', async () => {
    await render({ list: freshList(), streamReady: false });
    expect(fetchPage).not.toHaveBeenCalled();
    await render({ list: freshList(), streamReady: true });
    expect(offsets()).toEqual([0]);
  });

  it('asks only for page zero of a new view, not the old scroll position', async () => {
    await render({ list: freshList({ loadedPages: { 0: true, 50: true } }), streamReady: true });
    fetchPage.mockClear();

    await render({ list: freshList({ loadedPages: { 0: true, 50: true } }), view: { q: '', sort: 'name' }, streamReady: true });
    act(() => api(50, 70));
    expect(offsets()).toEqual([]);
    act(() => vi.advanceTimersByTime(300));
    expect(offsets()).toEqual([0]);
  });

  it('requests only the pages a scroll range needs that are neither loaded nor in flight', async () => {
    const list = freshList({
      loadedPages: { 0: true, 10: true },
      inFlightRequests: { r: { offset: 20, rangeVersion: 0 } },
    });
    await render({ list, streamReady: true });
    fetchPage.mockClear();

    act(() => api(0, 45));
    expect(offsets()).toEqual([10, 30, 40]);
  });

  it('ignores an in-flight request from an older range version', async () => {
    const list = freshList({ inFlightRequests: { r: { offset: 10, rangeVersion: -1 } }, loadedPages: { 0: true } });
    await render({ list, streamReady: true });
    fetchPage.mockClear();

    act(() => api(10, 11));
    expect(offsets()).toEqual([10]);
  });

  it('refetches the range when forced, and after a view change requests page zero once the debounce passes', async () => {
    const list = freshList({ loadedPages: { 0: true, 10: true } });
    await render({ list, streamReady: true });
    fetchPage.mockClear();
    act(() => api(0, 20, true));
    expect(offsets()).toEqual([0, 10]);

    fetchPage.mockClear();
    await render({ list, view: { q: 'invoice', sort: 'recent' }, streamReady: true });
    expect(fetchPage).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTime(250));
    expect(fetchPage).toHaveBeenCalledTimes(1);
    expect(fetchPage.mock.calls[0][0]).toMatchObject({ offset: 0, view: { q: 'invoice' } });
  });

  it('after a reconnect requests page zero once and reloads a stale visible page once', async () => {
    const list = freshList({ loadedPages: { 0: true, 10: true, 30: true } });
    await render({ list, streamReady: true });
    expect(offsets()).toEqual([0]);

    await render({ list, streamReady: false });
    fetchPage.mockClear();
    await render({ list, streamReady: true });
    act(() => api(10, 20));

    expect(offsets()).toEqual([0, 10]);
    expect(fetchPage.mock.calls.every(([request]) => request.view === VIEW)).toBe(true);
  });
});
