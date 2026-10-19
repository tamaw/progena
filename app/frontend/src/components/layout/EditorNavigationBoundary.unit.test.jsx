// @vitest-environment jsdom
import React, { act, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, expect, it } from 'vitest';
import EditorNavigationBoundary from './EditorNavigationBoundary';
import editorDrafts from '../../store/editorDraftsSlice';

let host;
let root;
let navigate;
let mounted;

function Page() {
  const location = useLocation();
  useEffect(() => { mounted.push(location.pathname); }, [location.pathname]);
  return <output>{location.pathname}{location.search}</output>;
}

function Harness() {
  const routerNavigate = useNavigate();
  useEffect(() => { navigate = routerNavigate; }, [routerNavigate]);
  return <EditorNavigationBoundary><Page /></EditorNavigationBoundary>;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mounted = [];
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  delete globalThis.IS_REACT_ACT_ENVIRONMENT;
});

async function open(path) {
  const store = configureStore({ reducer: { editorDrafts } });
  await act(async () => root.render(
    <Provider store={store}><MemoryRouter initialEntries={[path]}><Harness /></MemoryRouter></Provider>,
  ));
}

it('returns to the last detail and source context before mounting collection defaults', async () => {
  await open('/routines/second?view=source');
  await act(async () => navigate('/dashboard'));
  mounted = [];
  await act(async () => navigate('/routines'));
  expect(host.textContent).toBe('/routines/second?view=source');
  expect(mounted).toEqual(['/routines/second']);
  await act(async () => navigate('/routines/explicit'));
  expect(host.textContent).toBe('/routines/explicit');
});

it('lets an explicit quick-chat handoff mount the chat collection instead of restoring a detail', async () => {
  await open('/chat/previous');
  await act(async () => navigate('/dashboard'));
  mounted = [];
  await act(async () => navigate('/chat', { state: { quickChatHandoff: true } }));
  expect(host.textContent).toBe('/chat');
  expect(mounted).toEqual(['/chat']);
});

it('allows a missing-item replacement to forget its detail instead of looping back', async () => {
  await open('/workflows/deleted');
  await act(async () => navigate('/workflows', { replace: true }));
  expect(host.textContent).toBe('/workflows');
  await act(async () => navigate('/dashboard'));
  await act(async () => navigate('/workflows'));
  expect(host.textContent).toBe('/workflows');
});
