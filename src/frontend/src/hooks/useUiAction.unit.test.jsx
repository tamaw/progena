// @vitest-environment jsdom
import React, { act, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import useUiAction from './useUiAction';
import useConfirmAction from './useConfirmAction';

let host;
let root;
let current;
function Probe({ scope, confirm = false }) {
  const action = useUiAction(scope);
  const confirmation = useConfirmAction(scope);
  useEffect(() => { current = confirm ? confirmation : action; });
  return null;
}
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('React', React);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
const render = (scope, confirm = false) => act(async () => root.render(<Probe scope={scope} confirm={confirm} />));

it('blocks duplicate submissions and allows retry after failure', async () => {
  await render('a');
  const pending = deferred();
  const duplicate = vi.fn();
  let result;
  await act(async () => {
    result = current.run('Save record', () => pending.promise);
    expect(await current.run('Save record', duplicate)).toEqual({ ok: false, busy: true });
  });
  expect(current.busy).toBe(true);
  expect(duplicate).not.toHaveBeenCalled();
  await act(async () => { pending.reject(new Error('Rejected by policy')); await result; });
  expect(current.busy).toBe(false);
  expect(current.error.message).toBe('Rejected by policy');
  await act(async () => expect(await current.run('Save record', async () => 7)).toEqual({ ok: true, value: 7 }));
  expect(current.error).toBeNull();
});

it('does not let old entity results clear a newer action or expose a stale failure', async () => {
  await render('a');
  const old = deferred();
  let oldResult;
  let oldIsCurrent;
  const oldRun = current.run;
  await act(async () => { oldResult = current.run('Save record', ({ isCurrent }) => { oldIsCurrent = isCurrent; return old.promise; }); });
  await render('b');
  expect(oldIsCurrent()).toBe(false);
  const staleTask = vi.fn();
  expect(await oldRun('Save old record', staleTask)).toEqual({ ok: false, stale: true });
  expect(staleTask).not.toHaveBeenCalled();
  const next = deferred();
  let nextResult;
  await act(async () => { nextResult = current.run('Save record', () => next.promise); });
  await act(async () => { old.reject(new Error('Old failure')); expect(await oldResult).toEqual({ ok: false, stale: true }); });
  expect(current.busy).toBe(true);
  expect(current.error).toBeNull();
  await act(async () => { next.resolve('new'); await nextResult; });
  expect(current.busy).toBe(false);
});

it('does not display cancellations as failures', async () => {
  await render('a');
  await act(async () => expect(await current.run('Load record', async () => { throw new DOMException('Cancelled', 'AbortError'); })).toEqual({ ok: false, cancelled: true }));
  expect(current.error).toBeNull();
  expect(current.busy).toBe(false);
});

it('retains confirmation on rejection, blocks dismissal while busy, and closes after retry', async () => {
  await render('a', true);
  const pending = deferred();
  const remove = vi.fn().mockImplementationOnce(() => pending.promise).mockResolvedValueOnce(undefined);
  await act(async () => current.request('Delete record', remove));
  let result;
  await act(async () => { result = current.confirm(); current.close(); });
  expect(current.open).toBe(true);
  expect(current.busy).toBe(true);
  await act(async () => { pending.reject(new Error('Permission denied')); await result; });
  expect(current.open).toBe(true);
  expect(current.error.message).toBe('Permission denied');
  await act(async () => current.confirm());
  expect(current.open).toBe(false);
  expect(remove).toHaveBeenCalledTimes(2);
});

it('keeps a new entity confirmation protected when an older request settles', async () => {
  await render('a', true);
  const old = deferred();
  await act(async () => current.request('Delete old', () => old.promise));
  let oldResult;
  await act(async () => { oldResult = current.confirm(); });
  await render('b', true);
  const next = deferred();
  await act(async () => current.request('Delete new', () => next.promise));
  let nextResult;
  await act(async () => { nextResult = current.confirm(); });
  await act(async () => { old.resolve(); await oldResult; current.close(); });
  expect(current.open).toBe(true);
  expect(current.busy).toBe(true);
  await act(async () => { next.resolve(); await nextResult; });
  expect(current.open).toBe(false);
});
