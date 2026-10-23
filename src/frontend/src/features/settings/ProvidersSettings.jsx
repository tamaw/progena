import { useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import InlineFailure from '../../components/InlineFailure';
import Select from '../../components/Select';
import { useConfig } from '../../context/ConfigContext';
import { createProvider, deleteProvider, fetchProviders, patchProvider, selectProviderRequest, selectProviderTest, selectProviders, selectProvidersState, testProviderConnection } from './state/providers';
import { selectRealtimeStreamReady } from '../../realtime/realtimeSlice';

const KIND_OPTIONS = [
  { value: 'foundry', label: 'Foundry' },
  { value: 'opencode_go', label: 'OpenCode Go' },
];
const SOURCE_OPTIONS = [
  { value: 'database', label: 'Database credential' },
  { value: 'environment', label: 'Environment variable' },
];
const GO_BASE_URL = 'https://opencode.ai/inference/go/openai/v1';
const ENVIRONMENT_OPTIONS = [
  { value: 'AZURE_OPENAI_API_KEY', label: 'AZURE_OPENAI_API_KEY' },
  { value: 'OPENCODE_GO_API_KEY', label: 'OPENCODE_GO_API_KEY' },
];

function displaySource(provider) {
  if (provider.secret_source === 'environment') return provider.env_var || 'Environment variable';
  return provider.has_api_key ? 'Database credential configured' : 'No credential configured';
}

function draftFor(provider) {
  return {
    name: provider?.name || '',
    kind: provider?.kind || 'foundry',
    base_url: provider?.base_url || '',
    default_model: provider?.default_model || '',
    secret_source: provider?.secret_source || 'database',
    env_var: provider?.env_var || '',
    is_default: provider?.is_default === true,
  };
}

function ProviderEditor({ provider, goModels, providersUrl, onClose }) {
  const dispatch = useDispatch();
  const [draft, setDraft] = useState(() => draftFor(provider));
  const [apiKey, setApiKey] = useState('');
  const [clearApiKey, setClearApiKey] = useState(false);
  const [validationError, setValidationError] = useState('');
  const [requestId, setRequestId] = useState(null);
  const request = useSelector((state) => selectProviderRequest(state, requestId));
  const [deleteRequestId, setDeleteRequestId] = useState(null);
  const [testRequestId, setTestRequestId] = useState(null);
  const deleteRequest = useSelector((state) => selectProviderRequest(state, deleteRequestId));
  const testRequest = useSelector((state) => selectProviderRequest(state, testRequestId));
  const test = useSelector((state) => selectProviderTest(state, provider?.id));

  useEffect(() => {
    if (deleteRequestId && deleteRequest && !deleteRequest.loading && !deleteRequest.error) onClose();
  }, [deleteRequest, deleteRequestId, onClose]);
  const isCreate = !provider;
  const goModelOptions = useMemo(() => goModels.map((model) => ({ value: model, label: model })), [goModels]);
  const isGo = draft.kind === 'opencode_go';

  useEffect(() => {
    if (!requestId || !request || request.loading || request.error) return;
    onClose();
  }, [onClose, request, requestId]);

  const update = (field, value) => {
    setDraft((current) => ({ ...current, [field]: value }));
    setValidationError('');
    setRequestId(null);
  };

  const submit = (event) => {
    event.preventDefault();
    const name = draft.name.trim();
    const defaultModel = draft.default_model.trim();
    if (!name || !defaultModel || (!isGo && !draft.base_url.trim())) {
      setValidationError('Name, default model, and Foundry base URL are required.');
      return;
    }
    if (isGo && !goModels.includes(defaultModel)) {
      setValidationError('Choose a supported OpenCode Go Responses model.');
      return;
    }
    if (draft.secret_source === 'environment' && !draft.env_var.trim()) {
      setValidationError('Choose or enter the environment variable that supplies this credential.');
      return;
    }
    const payload = {
      name,
      kind: draft.kind,
      default_model: defaultModel,
      secret_source: draft.secret_source,
      env_var: draft.secret_source === 'environment' ? draft.env_var.trim() : null,
      is_default: draft.is_default,
      base_url: isGo ? GO_BASE_URL : draft.base_url.trim(),
      ...(clearApiKey ? { api_key: null } : apiKey ? { api_key: apiKey } : {}),
    };
    const operation = isCreate
      ? dispatch(createProvider({ providersUrl, provider: payload }))
      : dispatch(patchProvider({ providersUrl, providerId: provider.id, patch: payload }));
    setRequestId(operation.requestId);
  };

  const busy = request?.loading === true || deleteRequest?.loading === true || testRequest?.loading === true;
  const error = validationError || request?.error;
  return (
    <form className="set-provider-editor" onSubmit={submit}>
      <button type="button" className="set-back" disabled={busy} onClick={onClose}>← Providers</button>
      <header className="set-section__head">
        <h3>{isCreate ? 'Add provider' : provider.name}</h3>
        <p>{isCreate ? 'Connect a model service.' : 'Connection settings apply to new turns. Active turns keep their current connection.'}</p>
      </header>
      <div>
        <label className="set-row set-connection-field">
          <span className="set-row__title">Name</span>
          <input className="set-text-input" value={draft.name} disabled={busy} onChange={(event) => update('name', event.target.value)} />
        </label>
        <label className="set-row set-connection-field">
          <span className="set-row__title">Provider type</span>
          <Select value={draft.kind} onChange={(value) => update('kind', value)} options={KIND_OPTIONS} ariaLabel="Provider type" disabled={busy} />
        </label>
        {isGo ? (
          <label className="set-row set-connection-field">
            <span className="set-row__title">Responses model</span>
            <Select value={draft.default_model} onChange={(value) => update('default_model', value)} options={goModelOptions} placeholder="Choose a model" ariaLabel="OpenCode Go Responses model" disabled={busy || !goModels.length} />
          </label>
        ) : (
          <>
            <label className="set-row set-connection-field">
              <span className="set-row__title">Foundry base URL</span>
              <input className="set-text-input" type="url" value={draft.base_url} placeholder="https://resource.openai.azure.com/openai/v1" disabled={busy} onChange={(event) => update('base_url', event.target.value)} />
            </label>
            <label className="set-row set-connection-field">
              <span className="set-row__title">Default deployment</span>
              <input className="set-text-input" value={draft.default_model} disabled={busy} onChange={(event) => update('default_model', event.target.value)} />
            </label>
          </>
        )}
        <label className="set-row set-connection-field">
          <span className="set-row__title">Credential source</span>
          <Select value={draft.secret_source} onChange={(value) => { if (value === 'environment') { setApiKey(''); setClearApiKey(false); } update('secret_source', value); }} options={SOURCE_OPTIONS} ariaLabel="Credential source" disabled={busy} />
        </label>
        {draft.secret_source === 'environment' ? (
          <label className="set-row set-connection-field">
            <span className="set-row__title">Environment variable</span>
            <input className="set-text-input" list="provider-environment-variables" value={draft.env_var} placeholder="PROGENA_PROVIDER_*_API_KEY" disabled={busy} onChange={(event) => update('env_var', event.target.value)} />
            <datalist id="provider-environment-variables">{ENVIRONMENT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</datalist>
          </label>
        ) : (
          <label className="set-row set-connection-field">
            <span className="set-row__title">API key {provider?.has_api_key ? '(replace)' : ''}</span>
            <input className="set-text-input" type="password" autoComplete="new-password" value={apiKey} placeholder="Stored only when saved" disabled={busy || clearApiKey} onChange={(event) => { setApiKey(event.target.value); setValidationError(''); setRequestId(null); }} />
          </label>
        )}
      </div>
      {provider?.has_api_key && draft.secret_source === 'database' ? (
        <div className="set-row">
          <span className="set-row__title">Clear stored API key</span>
          <label className={`set-switch ${clearApiKey ? 'is-on' : ''}`}>
            <input type="checkbox" aria-label="Clear stored API key" checked={clearApiKey} disabled={busy} onChange={(event) => { setClearApiKey(event.target.checked); setRequestId(null); }} />
            <span className="set-switch__knob" aria-hidden="true" />
          </label>
        </div>
      ) : null}
      <div className="set-row">
        <span className="set-row__title">Use as the default provider</span>
        <label className={`set-switch ${draft.is_default ? 'is-on' : ''}`}>
          <input type="checkbox" aria-label="Use as the default provider" checked={draft.is_default} disabled={busy || (provider?.is_default && draft.is_default)} onChange={(event) => update('is_default', event.target.checked)} />
          <span className="set-switch__knob" aria-hidden="true" />
        </label>
      </div>
      {isGo && !goModels.length ? <p className="set-provider-editor__hint">Supported OpenCode Go models have not arrived from the provider snapshot yet.</p> : null}
      {error ? <InlineFailure failure={error} operation={isCreate ? 'Create provider' : 'Save provider'} /> : null}
      {!isCreate ? (
        <div className="set-row">
          <div className="set-row__label">
            <span className="set-row__title">Connection</span>
            <span className="set-row__sub">Test uses saved settings, not unsaved edits.</span>
          </div>
          <div className="set-inline-control">
            <button type="button" className="task-btn task-btn--secondary" disabled={busy} onClick={() => setTestRequestId(dispatch(testProviderConnection({ providersUrl, providerId: provider.id })).requestId)}>{testRequest?.loading ? 'Testing…' : 'Test connection'}</button>
            <button type="button" className="task-btn task-btn--secondary set-btn--danger" disabled={provider.is_default || busy} title={provider.is_default ? 'Switch the default provider before removing this one.' : undefined} onClick={() => setDeleteRequestId(dispatch(deleteProvider({ providersUrl, providerId: provider.id })).requestId)}>{deleteRequest?.loading ? 'Removing…' : 'Remove'}</button>
          </div>
        </div>
      ) : null}
      {deleteRequest?.error ? <InlineFailure failure={deleteRequest.error} operation="Remove provider" /> : null}
      {testRequest?.error ? <InlineFailure failure={testRequest.error} operation="Test provider connection" /> : null}
      {test ? <p className={`set-provider-test ${test.ok ? 'is-success' : 'is-error'}`} role="status">{test.ok ? 'Connection succeeded.' : test.error || 'Connection failed.'}</p> : null}
      <div className="set-editor-footer">
        <button type="button" className="task-btn task-btn--secondary" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="task-btn task-btn--primary" disabled={busy}>{request?.loading ? 'Saving…' : isCreate ? 'Create provider' : 'Save provider'}</button>
      </div>
    </form>
  );
}

export default function ProvidersSettings() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const providersUrl = getApiUrl('providers');
  const connected = useSelector(selectRealtimeStreamReady);
  const providers = useSelector(selectProviders);
  const state = useSelector(selectProvidersState);
  const [editor, setEditor] = useState(null);

  useEffect(() => {
    if (connected) dispatch(fetchProviders({ providersUrl }));
  }, [connected, dispatch, providersUrl]);

  if (editor) {
    return (
      <section className="set-section is-active">
        <ProviderEditor key={editor.provider?.id || 'new'} provider={editor.provider} goModels={state.goModels} providersUrl={providersUrl} onClose={() => setEditor(null)} />
      </section>
    );
  }
  return (
    <section className="set-section is-active">
      <header className="set-section__head">
        <h3>Providers</h3>
        <p>Responses API connections used by the runtime. Credentials remain write-only.</p>
      </header>
      {!connected ? <p className="set-provider-editor__hint" role="status">Waiting for the live connection before loading providers.</p> : null}
      {connected && state.loading ? <p className="set-provider-editor__hint" role="status">Loading providers…</p> : null}
      {state.error ? <InlineFailure failure={state.error} operation="Load providers" onRetry={() => dispatch(fetchProviders({ providersUrl }))} /> : null}
      {!state.loading && state.loaded && !providers.length ? <p className="set-provider-editor__hint">No provider is configured. Add a Foundry or OpenCode Go connection to enable model use.</p> : null}
      <ul className="set-connection-list">
        {providers.map((provider) => (
          <li key={provider.id} className="set-row">
            <div className="set-row__label">
              <span className="set-row__title">{provider.name}{provider.is_default ? <span className="set-provider-default">Default</span> : null}</span>
              <span className="set-row__sub">{provider.kind === 'opencode_go' ? 'OpenCode Go' : 'Foundry'} · {provider.default_model}</span>
              <span className="set-row__sub">{displaySource(provider)}</span>
            </div>
            <button type="button" className="task-btn task-btn--secondary" aria-label={`Edit ${provider.name}`} onClick={() => setEditor({ provider })}>Edit</button>
          </li>
        ))}
      </ul>
      <div className="set-row">
        <div className="set-row__label">
          <span className="set-row__title">Add a connection</span>
          <span className="set-row__sub">Microsoft Foundry or OpenCode Go.</span>
        </div>
        <button type="button" className="task-btn task-btn--secondary" disabled={!connected || state.loading} onClick={() => setEditor({})}>Add provider</button>
      </div>
    </section>
  );
}
