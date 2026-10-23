import { describe, expect, it } from 'vitest';
import reducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';
import {
  createProvider,
  fetchProviders,
  testProviderConnection,
} from '@frontend/features/settings/state/providers';

const provider = {
  id: 'foundry-main', name: 'Foundry', kind: 'foundry',
  base_url: 'https://example.openai.azure.com/openai/v1', default_model: 'gpt-5',
  secret_source: 'environment', env_var: 'AZURE_OPENAI_API_KEY',
  has_api_key: true, is_default: true, revision: 1,
};
const snapshot = (providers, requestId, goModels = []) => applyUiEvent({
  type: 'providers.snapshot', payload: { providers, go_models: goModels, request_id: requestId },
});

describe('provider request lifecycle', () => {
  it('keeps a GET pending until its correlated providers snapshot arrives', () => {
    let state = reducer(undefined, fetchProviders.pending('load-a', {}));
    state = reducer(state, fetchProviders.fulfilled({ request_id: 'load-a', event_types: ['providers.snapshot'] }, 'load-a', {}));
    expect(state.providers.loaded).toBe(false);
    expect(state.providers.loading).toBe(true);

    state = reducer(state, snapshot([provider], 'load-a', ['grok-4.7']));
    expect(state.providers.loading).toBe(false);
    expect(state.providers.items).toEqual([provider]);
    expect(state.providers.goModels).toEqual(['grok-4.7']);
  });

  it('drops stale account catalogs and errors when the authoritative registrations change', () => {
    let state = reducer(undefined, applyUiEvent({
      type: 'providers.snapshot',
      payload: {
        providers: [{ id: 'old', kind: 'chatgpt' }],
        chatgpt_models: { old: [{ slug: 'old-model', display_name: 'Old model' }] },
        chatgpt_model_errors: { old: 'Reconnect required' },
      },
    }));
    state = reducer(state, applyUiEvent({
      type: 'providers.snapshot',
      payload: {
        providers: [{ id: 'new', kind: 'chatgpt' }],
        chatgpt_models: { new: [{ slug: 'new-model', display_name: 'New model' }] },
        chatgpt_model_errors: {},
      },
    }));

    expect(state.providers.chatgptModelsByProviderId.old).toBeUndefined();
    expect(state.providers.chatgptModelErrorsByProviderId.old).toBeUndefined();
    expect(state.providers.chatgptModelsByProviderId.new[0].slug).toBe('new-model');
  });

  it('settles a CRUD request only from its correlated authoritative snapshot', () => {
    const args = { providersUrl: '/api/ui/providers', provider: { name: 'Go' } };
    let state = reducer(undefined, createProvider.pending('create-a', args));
    state = reducer(state, snapshot([provider], 'other-client'));
    expect(state.providers.requests['create-a'].loading).toBe(true);

    state = reducer(state, snapshot([provider, { ...provider, id: 'go', name: 'Go' }], 'create-a'));
    expect(state.providers.requests['create-a'].loading).toBe(false);
    expect(state.providers.requests['create-a'].error).toBeNull();
  });

  it('keeps connection-test results separate from public provider records', () => {
    const args = { providersUrl: '/api/ui/providers', providerId: provider.id };
    let state = reducer(undefined, testProviderConnection.pending('test-a', args));
    state = reducer(state, applyUiEvent({
      type: 'providers.tested',
      payload: { provider_id: provider.id, ok: false, error: 'Connection rejected', request_id: 'test-a' },
    }));

    expect(state.providers.items).toEqual([]);
    expect(state.providers.testsByProviderId[provider.id]).toEqual({
      ok: false, error: 'Connection rejected', requestId: 'test-a',
    });
    expect(state.providers.requests['test-a'].loading).toBe(false);
  });

  it('does not retain write-only API keys in provider state', () => {
    const state = reducer(undefined, createProvider.pending('create-key', {
      providersUrl: '/api/ui/providers',
      provider: { name: 'Private', api_key: 'not-for-redux' },
    }));

    expect(JSON.stringify(state.providers)).not.toContain('not-for-redux');
  });
});
