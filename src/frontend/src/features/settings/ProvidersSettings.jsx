import { useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import InlineFailure from '../../components/InlineFailure';
import Select from '../../components/Select';
import { useConfig } from '../../context/ConfigContext';
import {
  createProvider,
  deleteProvider,
  disconnectChatgptProvider,
  fetchProviders,
  patchProvider,
  selectProviderRequest,
  selectProviderTest,
  selectProviders,
  selectProvidersState,
  testProviderConnection,
} from './state/providers';
import { selectRealtimeStreamReady } from '../../realtime/realtimeSlice';

const KIND_OPTIONS = [
  { value: 'foundry', label: 'Foundry' },
  { value: 'opencode_go', label: 'OpenCode Go' },
  { value: 'chatgpt', label: 'ChatGPT plan' },
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

function chatgptStatus(provider) {
  const status = provider.connection_status || 'unconnected';
  return ({
    connected: 'Connected',
    plan_disabled: 'ChatGPT plan use is not enabled',
    reauth_required: 'Reconnection required',
    disconnected: 'Disconnected',
    unconnected: 'Not connected',
  })[status] || 'Connection status unavailable';
}

function ChatgptSetup({ onClose, onReload, busy }) {
  return (
    <section className="set-provider-editor">
      <button type="button" className="set-back" disabled={busy} onClick={onClose}>← Providers</button>
      <header className="set-section__head">
        <h3>Connect a ChatGPT plan</h3>
        <p>Sign in on the computer with your browser, then securely import that registration on this server. The browser never receives credentials.</p>
      </header>
      <ol className="set-provider-editor__hint">
        <li>On the browser computer, run <code>python -m src.progena.settings.chatgpt_cli sign-in --credential-file PATH</code>. It opens a local <code>127.0.0.1</code> callback; do not use this server&apos;s public URL. To reconnect a saved registration, keep its credential file and add <code>--reauthorize</code>.</li>
        <li>Copy the protected credential file over SSH, for example <code>scp PATH server:PATH</code>.</li>
        <li>On this server, run <code>python -m src.progena.settings.chatgpt_cli import --credential-file PATH</code>. Add <code>--name NAME</code> to label a registration or <code>--provider-id ID</code> when importing its reauthorization.</li>
      </ol>
      <p className="set-provider-editor__hint">The server keeps its own host ID and refreshes imported credentials. Tokens are never shown or stored by this page.</p>
      <div className="set-editor-footer">
        <button type="button" className="task-btn task-btn--secondary" disabled={busy} onClick={onClose}>Back</button>
        <button type="button" className="task-btn task-btn--primary" disabled={busy} onClick={onReload}>Reload providers</button>
      </div>
    </section>
  );
}

function ProviderEditor({ provider, goModels, chatgptModels, chatgptModelError, providersUrl, onClose }) {
  const dispatch = useDispatch();
  const [draft, setDraft] = useState(() => draftFor(provider));
  const [apiKey, setApiKey] = useState('');
  const [clearApiKey, setClearApiKey] = useState(false);
  const [validationError, setValidationError] = useState('');
  const [requestId, setRequestId] = useState(null);
  const request = useSelector((state) => selectProviderRequest(state, requestId));
  const [deleteRequestId, setDeleteRequestId] = useState(null);
  const [disconnectRequestId, setDisconnectRequestId] = useState(null);
  const [testRequestId, setTestRequestId] = useState(null);
  const deleteRequest = useSelector((state) => selectProviderRequest(state, deleteRequestId));
  const disconnectRequest = useSelector((state) => selectProviderRequest(state, disconnectRequestId));
  const testRequest = useSelector((state) => selectProviderRequest(state, testRequestId));
  const test = useSelector((state) => selectProviderTest(state, provider?.id));
  const isCreate = !provider;
  const isGo = draft.kind === 'opencode_go';
  const isChatgpt = draft.kind === 'chatgpt';
  const goModelOptions = useMemo(
    () => goModels.map((model) => ({ value: model, label: model })),
    [goModels],
  );
  const chatgptModelOptions = useMemo(
    () => chatgptModels.map((model) => ({ value: model.slug, label: model.display_name })),
    [chatgptModels],
  );

  useEffect(() => {
    if (deleteRequestId && deleteRequest && !deleteRequest.loading && !deleteRequest.error) onClose();
  }, [deleteRequest, deleteRequestId, onClose]);
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
    const defaultModel = draft.default_model.trim();
    if (isChatgpt) {
      if (!chatgptModels.some((model) => model.slug === defaultModel)) {
        setValidationError('Choose a model available to this ChatGPT account.');
        return;
      }
      setRequestId(dispatch(patchProvider({
        providersUrl,
        providerId: provider.id,
        patch: { default_model: defaultModel, is_default: draft.is_default },
      })).requestId);
      return;
    }
    const name = draft.name.trim();
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

  if (isCreate && isChatgpt) {
    return <ChatgptSetup onClose={onClose} onReload={() => { dispatch(fetchProviders({ providersUrl })); onClose(); }} busy={false} />;
  }
  const busy = request?.loading === true || deleteRequest?.loading === true
    || disconnectRequest?.loading === true || testRequest?.loading === true;
  const error = validationError || request?.error || disconnectRequest?.error;
  return (
    <form className="set-provider-editor" onSubmit={submit}>
      <button type="button" className="set-back" disabled={busy} onClick={onClose}>← Providers</button>
      <header className="set-section__head">
        <h3>{isCreate ? 'Add provider' : provider.name}</h3>
        <p>{isChatgpt ? 'This saved registration is selected by its ChatGPT account and model.' : isCreate ? 'Connect a model service.' : 'Connection settings apply to new turns. Active turns keep their current connection.'}</p>
      </header>
      {isChatgpt ? (
        <div>
          <div className="set-row">
            <div className="set-row__label">
              <span className="set-row__title">ChatGPT account</span>
              <span className="set-row__sub">{provider.account?.email || 'Account identity unavailable'}</span>
            </div>
          </div>
          <div className="set-row">
            <div className="set-row__label">
              <span className="set-row__title">Status</span>
              <span className="set-row__sub">{chatgptStatus(provider)}</span>
            </div>
          </div>
          <label className="set-row set-connection-field">
            <span className="set-row__title">ChatGPT model</span>
            <Select value={draft.default_model} onChange={(value) => update('default_model', value)} options={chatgptModelOptions} placeholder="Choose an available model" ariaLabel="ChatGPT plan model" disabled={busy || !chatgptModels.length} />
          </label>
          {chatgptModelError ? <p className="set-provider-test is-error" role="status">{chatgptModelError}</p> : null}
          {!chatgptModels.length && !chatgptModelError ? <p className="set-provider-editor__hint">No account-specific models are available until the connection is restored.</p> : null}
        </div>
      ) : (
        <div>
          <label className="set-row set-connection-field">
            <span className="set-row__title">Name</span>
            <input className="set-text-input" value={draft.name} disabled={busy} onChange={(event) => update('name', event.target.value)} />
          </label>
          <label className="set-row set-connection-field">
            <span className="set-row__title">Provider type</span>
            <Select value={draft.kind} onChange={(value) => update('kind', value)} options={isCreate ? KIND_OPTIONS : KIND_OPTIONS.filter((option) => option.value !== 'chatgpt')} ariaLabel="Provider type" disabled={busy} />
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
          {provider?.has_api_key && draft.secret_source === 'database' ? (
            <div className="set-row">
              <span className="set-row__title">Clear stored API key</span>
              <label className={`set-switch ${clearApiKey ? 'is-on' : ''}`}>
                <input type="checkbox" aria-label="Clear stored API key" checked={clearApiKey} disabled={busy} onChange={(event) => { setClearApiKey(event.target.checked); setRequestId(null); }} />
                <span className="set-switch__knob" aria-hidden="true" />
              </label>
            </div>
          ) : null}
        </div>
      )}
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
            <button type="button" className="task-btn task-btn--secondary" disabled={busy || (isChatgpt && !provider.default_model)} title={isChatgpt && !provider.default_model ? 'Choose a ChatGPT model before testing this connection.' : undefined} onClick={() => setTestRequestId(dispatch(testProviderConnection({ providersUrl, providerId: provider.id })).requestId)}>{testRequest?.loading ? 'Testing…' : 'Test connection'}</button>
            {isChatgpt ? <button type="button" className="task-btn task-btn--secondary set-btn--danger" disabled={busy || provider.connection_status === 'disconnected'} onClick={() => setDisconnectRequestId(dispatch(disconnectChatgptProvider({ providersUrl, providerId: provider.id })).requestId)}>{disconnectRequest?.loading ? 'Disconnecting…' : 'Disconnect'}</button>
              : <button type="button" className="task-btn task-btn--secondary set-btn--danger" disabled={provider.is_default || busy} title={provider.is_default ? 'Switch the default provider before removing this one.' : undefined} onClick={() => setDeleteRequestId(dispatch(deleteProvider({ providersUrl, providerId: provider.id })).requestId)}>{deleteRequest?.loading ? 'Removing…' : 'Remove'}</button>}
          </div>
        </div>
      ) : null}
      {isChatgpt && provider.connection_status !== 'connected' ? <p className="set-provider-editor__hint">To reconnect this registration, run local sign-in with <code>--reauthorize</code>, transfer its protected file over SSH, then import it with <code>--provider-id {provider.id}</code>.</p> : null}
      {deleteRequest?.error ? <InlineFailure failure={deleteRequest.error} operation="Remove provider" /> : null}
      {testRequest?.error ? <InlineFailure failure={testRequest.error} operation="Test provider connection" /> : null}
      {test ? <p className={`set-provider-test ${test.ok ? 'is-success' : 'is-error'}`} role="status">{test.ok ? 'Connection succeeded.' : test.error || 'Connection failed.'}</p> : null}
      <div className="set-editor-footer">
        <button type="button" className="task-btn task-btn--secondary" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="task-btn task-btn--primary" disabled={busy || (isChatgpt && !chatgptModels.length)}>{request?.loading ? 'Saving…' : isCreate ? 'Create provider' : 'Save provider'}</button>
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
    const providerId = editor.provider?.id;
    const currentProvider = providerId
      ? providers.find((provider) => provider.id === providerId) || editor.provider
      : editor.provider;
    return (
      <section className="set-section is-active">
        <ProviderEditor
          key={providerId || 'new'}
          provider={currentProvider}
          goModels={state.goModels}
          chatgptModels={providerId ? state.chatgptModelsByProviderId[providerId] || [] : []}
          chatgptModelError={providerId ? state.chatgptModelErrorsByProviderId[providerId] || null : null}
          providersUrl={providersUrl}
          onClose={() => setEditor(null)}
        />
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
      {!state.loading && state.loaded && !providers.length ? <p className="set-provider-editor__hint">No provider is configured. Add a Foundry, OpenCode Go, or ChatGPT plan connection to enable model use.</p> : null}
      <ul className="set-connection-list">
        {providers.map((provider) => (
          <li key={provider.id} className="set-row">
            <div className="set-row__label">
              <span className="set-row__title">{provider.name}{provider.is_default ? <span className="set-provider-default">Default</span> : null}</span>
              <span className="set-row__sub">{provider.kind === 'chatgpt' ? `ChatGPT plan · ${provider.default_model || 'Choose a model'}` : `${provider.kind === 'opencode_go' ? 'OpenCode Go' : 'Foundry'} · ${provider.default_model}`}</span>
              <span className="set-row__sub">{provider.kind === 'chatgpt' ? `${provider.account?.email || 'ChatGPT account'} · ${chatgptStatus(provider)}` : displaySource(provider)}</span>
            </div>
            <button type="button" className="task-btn task-btn--secondary" aria-label={`Edit ${provider.name}`} onClick={() => setEditor({ provider })}>Edit</button>
          </li>
        ))}
      </ul>
      <div className="set-row">
        <div className="set-row__label">
          <span className="set-row__title">Add a connection</span>
          <span className="set-row__sub">Microsoft Foundry, OpenCode Go, or a ChatGPT plan registration.</span>
        </div>
        <button type="button" className="task-btn task-btn--secondary" disabled={!connected || state.loading} onClick={() => setEditor({})}>Add provider</button>
      </div>
    </section>
  );
}
