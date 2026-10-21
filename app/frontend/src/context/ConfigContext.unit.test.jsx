// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ConfigProvider, useConfig } from './ConfigContext';

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
    localStorage.setItem('progenitor_density', 'presentation');
    mount();
    expect(seen.current.config.density.default).toBe('presentation');
    expect(document.documentElement.dataset.density).toBe('presentation');
  });

  it('falls back to comfortable for an unknown stored value', () => {
    localStorage.setItem('progenitor_density', 'huge');
    mount();
    expect(document.documentElement.dataset.density).toBe('comfortable');
  });

  it('persists a selection and removes it again on reset', () => {
    mount();
    act(() => seen.current.setDensity('compact'));
    expect(localStorage.getItem('progenitor_density')).toBe('compact');
    expect(document.documentElement.dataset.density).toBe('compact');
    act(() => seen.current.resetSettings());
    expect(localStorage.getItem('progenitor_density')).toBeNull();
    expect(document.documentElement.dataset.density).toBe('comfortable');
  });

  it('ignores a stored session timeout outside the slider range', () => {
    localStorage.setItem('progenitor_idle_timeout_minutes', '999');
    mount();
    expect(seen.current.config.session.idleTimeoutMinutes).toBe(30);
  });
});
