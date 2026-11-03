// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ConfigProvider, useConfig } from '@frontend/context/ConfigContext';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const seen = { current: null };
let root;

function Probe() {
  const value = useConfig();
  React.useEffect(() => {
    seen.current = value;
  });
  return null;
}

function mount() {
  root = createRoot(document.createElement('div'));
  act(() => root.render(<ConfigProvider workspaceHost=""><Probe /></ConfigProvider>));
}

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.density;
});
afterEach(() => act(() => root.unmount()));

describe('ConfigProvider density', () => {
  it('restores a valid stored density onto the document element', () => {
    localStorage.setItem('progena_density', 'presentation');
    mount();
    expect(seen.current.config.density.default).toBe('presentation');
    expect(document.documentElement.dataset.density).toBe('presentation');
  });

  it('falls back to comfortable for an unknown stored value', () => {
    localStorage.setItem('progena_density', 'huge');
    mount();
    expect(document.documentElement.dataset.density).toBe('comfortable');
  });

  it('persists a selection and removes it again on reset', () => {
    mount();
    act(() => seen.current.setDensity('compact'));
    expect(localStorage.getItem('progena_density')).toBe('compact');
    expect(document.documentElement.dataset.density).toBe('compact');
    act(() => seen.current.resetSettings());
    expect(localStorage.getItem('progena_density')).toBeNull();
    expect(document.documentElement.dataset.density).toBe('comfortable');
  });

  it('ignores a stored session timeout outside the slider range', () => {
    localStorage.setItem('progena_idle_timeout_minutes', '999');
    mount();
    expect(seen.current.config.session.idleTimeoutMinutes).toBe(30);
  });
});

describe('ConfigProvider header field', () => {
  it('defaults to Ask when nothing is stored', () => {
    mount();
    expect(seen.current.config.headerField.default).toBe('ask');
    expect(seen.current.config.headerField.options).toEqual(['ask', 'search', 'hidden']);
  });

  it('restores a valid stored mode and falls back to Ask for an unknown one', () => {
    localStorage.setItem('progena_header_field', 'search');
    mount();
    expect(seen.current.config.headerField.default).toBe('search');
    act(() => root.unmount());
    localStorage.setItem('progena_header_field', 'sidebar');
    mount();
    expect(seen.current.config.headerField.default).toBe('ask');
  });

  it('persists a selection, rejects invalid modes and resets to Ask', () => {
    mount();
    act(() => seen.current.setHeaderField('hidden'));
    expect(seen.current.config.headerField.default).toBe('hidden');
    expect(localStorage.getItem('progena_header_field')).toBe('hidden');
    act(() => seen.current.setHeaderField('bogus'));
    expect(seen.current.config.headerField.default).toBe('hidden');
    act(() => seen.current.resetSettings());
    expect(seen.current.config.headerField.default).toBe('ask');
    expect(localStorage.getItem('progena_header_field')).toBeNull();
  });
});
