// @vitest-environment jsdom
import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { expect, it, vi } from 'vitest';
import { store } from '@frontend/store/store';
import { NodeConfigBody } from '@frontend/features/routines/editor/NodeConfigFields';

vi.mock('@frontend/context/ConfigContext', () => ({ useConfig: () => ({}) }));

it('switches Python source without retaining the mutually exclusive source or losing file maps', async () => {
  const inputMap = { value: 'request.amount' };
  function Editor() {
    const [config, setConfig] = useState({ code_file: '/artifacts/old.txt', input_map: inputMap });
    return <><NodeConfigBody id="calculate" kind="python" config={config} replaceNodeConfig={setConfig} updateNodeConfig={(_, patch) => setConfig(previous => ({ ...previous, ...patch }))} /><output>{JSON.stringify(config)}</output></>;
  }
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  try {
    await act(async () => root.render(<Provider store={store}><Editor /></Provider>));
    const code = container.querySelector('textarea');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(code, 'result = 42');
      code.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(JSON.parse(container.querySelector('output').textContent)).toEqual({ code: 'result = 42', input_map: inputMap });
    const file = container.querySelector('input[type="text"], input:not([type])');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(file, '/artifacts/new.txt');
      file.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(JSON.parse(container.querySelector('output').textContent)).toEqual({ code_file: '/artifacts/new.txt', input_map: inputMap });
  } finally {
    await act(async () => root.unmount());
    container.remove();
    delete globalThis.IS_REACT_ACT_ENVIRONMENT;
  }
});
