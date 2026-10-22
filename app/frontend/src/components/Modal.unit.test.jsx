// @vitest-environment jsdom
import React, { act, createRef } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Modal from './Modal';

let host;
let root;
let opener;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('React', React);
  opener = document.createElement('button');
  opener.textContent = 'Open dialog';
  host = document.createElement('div');
  document.body.append(opener, host);
  opener.focus();
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  host.remove();
  opener.remove();
  vi.unstubAllGlobals();
});

function key(target, keyName, options = {}) {
  target.dispatchEvent(new KeyboardEvent('keydown', {
    key: keyName, bubbles: true, cancelable: true, ...options,
  }));
}

describe('Modal keyboard and dismissal behavior', () => {
  it('contains keyboard focus and restores the opener after closing', async () => {
    const initialFocusRef = createRef();
    await act(async () => root.render(
      <Modal onClose={() => {}} ariaLabel="Edit record" initialFocusRef={initialFocusRef}>
        <button type="button">Cancel</button>
        <input ref={initialFocusRef} aria-label="Record name" />
        <button type="button">Save</button>
      </Modal>,
    ));
    expect(document.activeElement).toBe(initialFocusRef.current);
    const [first, last] = document.querySelectorAll('[role="dialog"] button');
    first.focus();
    key(first, 'Tab', { shiftKey: true });
    expect(document.activeElement).toBe(last);
    key(last, 'Tab');
    expect(document.activeElement).toBe(first);
    await act(async () => root.unmount());
    root = null;
    expect(document.activeElement).toBe(opener);
  });

  it('leaves Escape consumed by a nested control alone', async () => {
    const onClose = vi.fn();
    await act(async () => root.render(
      <Modal onClose={onClose} ariaLabel="Choose a record">
        <input aria-label="Nested control" onKeyDown={(event) => event.preventDefault()} />
        <button type="button">Cancel</button>
      </Modal>,
    ));
    key(document.querySelector('[aria-label="Nested control"]'), 'Escape');
    expect(onClose).not.toHaveBeenCalled();
    key(document.querySelector('[role="dialog"] button'), 'Escape');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('prevents busy dismissal and only dismisses from the backdrop once idle', async () => {
    const onClose = vi.fn();
    const render = (busy) => (
      <Modal onClose={onClose} busy={busy} backdropClassName="modal-backdrop" ariaLabel="Create record">
        <button type="button">Save</button>
      </Modal>
    );
    await act(async () => root.render(render(true)));
    const backdrop = document.querySelector('.modal-backdrop');
    key(document.activeElement, 'Escape');
    backdrop.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    expect(onClose).not.toHaveBeenCalled();
    await act(async () => root.render(render(false)));
    document.querySelector('[role="dialog"] button').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    expect(onClose).not.toHaveBeenCalled();
    backdrop.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('wraps reverse tabbing from the dialog surface to its last control', async () => {
    await act(async () => root.render(
      <Modal onClose={() => {}} ariaLabel="Confirm action">
        <button type="button">Cancel</button>
        <button type="button">Confirm</button>
      </Modal>,
    ));
    const dialog = document.querySelector('[role="dialog"]');
    const [, last] = dialog.querySelectorAll('button');
    dialog.focus();
    key(dialog, 'Tab', { shiftKey: true });
    expect(document.activeElement).toBe(last);
  });

  it('gives Tab and Escape only to the topmost of stacked dialogs', async () => {
    const onCloseLower = vi.fn();
    const onCloseUpper = vi.fn();
    await act(async () => root.render(
      <Modal onClose={onCloseLower} ariaLabel="Lower">
        <button type="button">Lower action</button>
      </Modal>,
    ));
    await act(async () => root.render(
      <>
        <Modal onClose={onCloseLower} ariaLabel="Lower">
          <button type="button">Lower action</button>
        </Modal>
        <Modal onClose={onCloseUpper} ariaLabel="Upper">
          <button type="button">Upper first</button>
          <button type="button">Upper last</button>
        </Modal>
      </>,
    ));
    const upperFirst = document.querySelector('[aria-label="Upper"] button');
    const upperLast = document.querySelectorAll('[aria-label="Upper"] button')[1];
    upperFirst.focus();
    key(upperFirst, 'Tab');
    expect(document.activeElement).toBe(upperFirst);
    upperLast.focus();
    key(upperLast, 'Tab');
    expect(document.activeElement).toBe(upperFirst);
    key(upperFirst, 'Escape');
    expect(onCloseUpper).toHaveBeenCalledTimes(1);
    expect(onCloseLower).not.toHaveBeenCalled();
  });
});
