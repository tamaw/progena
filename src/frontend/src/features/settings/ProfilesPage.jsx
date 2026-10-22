import { useCallback, useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../../lib/routes';
import {
  PaneEmptyState, PaneList, PaneListFilters, PaneListFooter, PaneListSearch,
  PaneListSortButton, PaneListToolbar, PaneListRows,
} from '../../components/layout/PaneList';
import PageShell from '../../components/layout/PageShell';
import DetailPane from '../../components/layout/DetailPane';
import DetailHeader, {
  DeleteActionIcon, DetailHeaderActions, DetailHeaderAction,
} from '../../components/layout/DetailHeader';
import Select from '../../components/Select';
import PopupModal from '../../components/PopupModal';
import InlineFailure from '../../components/InlineFailure';
import useConfirmAction from '../../hooks/useConfirmAction';
import useEntityDraft from '../../hooks/useEntityDraft';
import useMissingEntityRoute from '../../hooks/useMissingEntityRoute';
import { useConfig } from '../../context/ConfigContext';
import { deleteModelProfile, fetchModelProfiles, patchModelProfile, selectModelProfiles, selectModelProfilesState } from './state/modelProfiles';
import { fetchProviders, selectGoResponsesModels, selectProviders } from './state/providers';
import { selectRealtimeStreamReady } from '../../realtime/realtimeSlice';
import { draftDiscarded, entityDraftKey } from '../../store/editorDraftsSlice';
import { useSnapshotSave } from '../../hooks/useSnapshotSave';
import './profiles.css';

const FILTERS = ['All', 'Inherited', 'Customized'];
const EDITABLE_FIELDS = ['provider_id', 'deployment', 'reasoning_enabled', 'reasoning_effort', 'reasoning_summary'];
const PRICING_FIELDS = ['currency', 'input_per_million', 'cached_input_per_million', 'output_per_million'];
const providerLabel = (profile, providers) => {
  const provider = profile.provider_id ? providers.find((candidate) => candidate.id === profile.provider_id) : null;
  return provider ? provider.name : 'Provider unavailable';
};
const REASONING_ENABLED_OPTIONS = [
  { value: '', label: 'Inherit' },
  { value: 'true', label: 'Enabled' },
  { value: 'false', label: 'Disabled' },
];

const GPT_5_MINI_GLOBAL_STANDARD_PRICING = Object.freeze({
  currency: 'USD',
  input_per_million: '0.25',
  cached_input_per_million: '0.025',
  output_per_million: '2.00',
});

const EMPTY_PROFILE_EDIT = Object.freeze({ draft: null, baseline: null, validationError: null });
const PROFILE_SNAPSHOT_EVENTS = Object.freeze(['profiles.snapshot']);

const emptyPricing = () => Object.fromEntries(PRICING_FIELDS.map((field) => [field, '']));
const isCustomized = (profile) => Boolean(profile.pricing)
  || ['provider_id', 'reasoning_enabled', 'reasoning_effort', 'reasoning_summary']
    .some((field) => profile[field] != null);

function toDraft(profile) {
  return {
    provider_id: profile.provider_id ?? '',
    deployment: profile.deployment ?? '',
    reasoning_enabled: profile.reasoning_enabled == null ? '' : String(profile.reasoning_enabled),
    reasoning_effort: profile.reasoning_effort ?? '',
    reasoning_summary: profile.reasoning_summary ?? '',
    pricing: profile.pricing ? { ...profile.pricing } : emptyPricing(),
  };
}

function buildPricing(draft) {
  const pricing = Object.fromEntries(
    PRICING_FIELDS.map((field) => [field, draft.pricing[field].trim()]),
  );
  return Object.values(pricing).some(Boolean) ? pricing : null;
}

function validatePricing(draft) {
  const pricing = buildPricing(draft);
  if (!pricing) return null;
  if (PRICING_FIELDS.some((field) => !pricing[field])) {
    return 'Complete currency and all per-million rates, or clear every pricing field to mark this deployment unpriced.';
  }
  if (!/^[A-Z]{3}$/.test(pricing.currency)) {
    return 'Currency must be a three-letter uppercase ISO 4217 code.';
  }
  if (PRICING_FIELDS.slice(1).some((field) => !/^\d+(?:\.\d+)?$/.test(pricing[field]))) {
    return 'Rates must be non-negative decimal strings.';
  }
  return null;
}

function buildPatch(draft, base) {
  const values = {
    provider_id: draft.provider_id || null,
    deployment: draft.deployment.trim(),
    reasoning_enabled: draft.reasoning_enabled === '' ? null : draft.reasoning_enabled === 'true',
    reasoning_effort: draft.reasoning_effort.trim() || null,
    reasoning_summary: draft.reasoning_summary.trim() || null,
  };
  const patch = Object.fromEntries(EDITABLE_FIELDS
    .filter((field) => values[field] !== (base[field] ?? null))
    .map((field) => [field, values[field]]));
  const pricing = buildPricing(draft);
  const basePricing = buildPricing(base);
  if (JSON.stringify(pricing) !== JSON.stringify(basePricing)) patch.pricing = pricing;
  return patch;
}

function ProfileDetails({ profile, catalogue, providers, goModels, url, connected }) {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const profileKey = useMemo(
    () => entityDraftKey('profile', profile.name),
    [profile.name],
  );
  const [editState, setEditState, draftControls] = useEntityDraft(profileKey, EMPTY_PROFILE_EDIT);
  const {
    clear, beginSave, trackSave, failSave, saving, error: saveError,
  } = draftControls;
  const draft = editState.draft;
  const baseline = editState.baseline;
  const error = saveError || editState.validationError;
  const providerOptions = useMemo(
    () => providers.map((provider) => ({ value: provider.id, label: provider.name })),
    [providers],
  );
  const selectedProviderId = draft?.provider_id ?? profile.provider_id;
  const selectedProvider = providers.find((provider) => provider.id === selectedProviderId);
  const isGoProvider = selectedProvider?.kind === 'opencode_go';
  const goModelOptions = useMemo(
    () => goModels.map((model) => ({ value: model, label: model })),
    [goModels],
  );
  const deleteAction = useConfirmAction(`profile-delete:${url}:${profile.name}`);
  const conflict = Boolean(draft && baseline
    && JSON.stringify(baseline) !== JSON.stringify(toDraft(profile)));
  const startEdit = () => {
    setEditState({
      draft: toDraft(profile),
      baseline: toDraft(profile),
      validationError: null,
    });
  };
  const cancelEdit = () => {
    clear();
  };
  const buildArgs = useCallback(({ path, content }) => ({
    modelProfilesUrl: url, name: path, patch: buildPatch(JSON.parse(content), baseline),
  }), [url, baseline]);
  const save = useSnapshotSave({
    dispatch, thunk: patchModelProfile, buildArgs, path: profile.name,
    content: draft ? JSON.stringify(draft) : '',
    savedContent: baseline ? JSON.stringify(baseline) : '',
    saving, realtimeConnected: connected,
    beginSave, trackSave, failSave, events: PROFILE_SNAPSHOT_EVENTS,
  });
  const handleSave = () => {
    if (!draft || conflict || saving) return;
    if (!draft.deployment.trim()) {
      setEditState((current) => ({ ...current, validationError: isGoProvider ? 'A Responses model is required.' : 'Deployment is required.' }));
      return;
    }
    if (isGoProvider && !goModels.includes(draft.deployment)) {
      setEditState((current) => ({ ...current, validationError: 'Choose a supported OpenCode Go Responses model.' }));
      return;
    }
    const pricingError = validatePricing(draft);
    if (pricingError) {
      setEditState((current) => ({ ...current, validationError: pricingError }));
      return;
    }
    if (!Object.keys(buildPatch(draft, baseline)).length) {
      cancelEdit();
      return;
    }
    void save();
  };
  const updateDraft = (field, value) => setEditState((current) => ({
    ...current,
    draft: { ...current.draft, [field]: value },
    validationError: null,
  }));
  const updatePricing = (field, value) => setEditState((current) => ({
    ...current,
    draft: {
      ...current.draft,
      pricing: { ...current.draft.pricing, [field]: value },
    },
    validationError: null,
  }));
  const applyGpt5MiniGlobalStandardPricing = () => setEditState((current) => ({
    ...current,
    draft: {
      ...current.draft,
      pricing: { ...GPT_5_MINI_GLOBAL_STANDARD_PRICING },
    },
    validationError: null,
  }));

  const handleDelete = () => {
    deleteAction.request('Delete profile', async ({ isCurrent }) => {
      await dispatch(deleteModelProfile({ modelProfilesUrl: url, name: profile.name })).unwrap();
      dispatch(draftDiscarded(profileKey));
      if (isCurrent()) navigate('/profiles', { replace: true });
    });
  };

  return (
    <>
      <DetailHeader title={profile.name} meta={[
        <span key="provider" className="profiles-badge profiles-badge--provider">{providerLabel(profile, providers)}</span>,
        <span key="deployment" className="profiles-badge profiles-badge--deployment">{profile.deployment}</span>,
        isCustomized(profile) ? <span key="custom" className="profiles-badge profiles-badge--customized">Customized</span> : null,
      ]} actions={(
        <DetailHeaderActions menuLabel="Profile actions">
          <DetailHeaderAction
            icon={<DeleteActionIcon />}
            label="Delete"
            ariaLabel="Delete profile"
            tooltip="Delete profile"
            variant="danger"
            onClick={handleDelete}
            disabled={saving || deleteAction.busy || !connected || catalogue.loading || Boolean(catalogue.error)}
          />
        </DetailHeaderActions>
      )} />
      <div className="detail-pane__body">
        {error && !draft ? <InlineFailure failure={error} operation="Save profile" /> : null}
        {conflict ? (
          <div className="profiles-alert profiles-alert--conflict" role="status">
            <span>This profile has been updated remotely. Your draft has been kept.</span>
            <button type="button" className="repo-btn repo-btn--ghost" onClick={startEdit} disabled={saving}>Restart edit</button>
          </div>
        ) : null}
        <section className="agents-section">
          <header className="agents-section__head">
            <h2>Configuration</h2>
            {draft ? (
              <div className="profiles-section__actions">
                <button type="button" className="repo-btn repo-btn--ghost" onClick={cancelEdit} disabled={saving}>Cancel</button>
                <button type="button" className="repo-btn repo-btn--primary" onClick={handleSave} disabled={saving || conflict || !connected}>{saving ? 'Saving…' : 'Save'}</button>
              </div>
            ) : (
              <button type="button" className="repo-btn repo-btn--ghost" onClick={startEdit} disabled={!connected || catalogue.loading || Boolean(catalogue.error)}>Edit</button>
            )}
          </header>
          {draft ? (
            <form className="agents-card-facts agents-draft profiles-form" onSubmit={(event) => { event.preventDefault(); handleSave(); }}>
              <label className="agents-draft-field profiles-field">
                <span className="agents-draft-field__label">Provider</span>
                <Select
                  value={draft.provider_id}
                  onChange={(value) => updateDraft('provider_id', value)}
                  options={providerOptions}
                  ariaLabel="Provider"
                  disabled={saving || !providers.length}
                />
              </label>
              <label className="agents-draft-field profiles-field">
                <span className="agents-draft-field__label">{isGoProvider ? 'Responses model' : 'Deployment'} <span aria-hidden="true" className="profiles-field__required">*</span></span>
                {isGoProvider ? (
                  <Select
                    value={draft.deployment}
                    onChange={(value) => updateDraft('deployment', value)}
                    options={goModelOptions}
                    ariaLabel="OpenCode Go Responses model"
                    placeholder="Choose a supported model"
                    disabled={saving || !goModels.length}
                  />
                ) : (
                  <input className="set-text-input" type="text" value={draft.deployment} onChange={(event) => updateDraft('deployment', event.target.value)} disabled={saving} required />
                )}
              </label>
              <label className="agents-draft-field profiles-field">
                <span className="agents-draft-field__label">Reasoning enabled</span>
                <Select
                  value={draft.reasoning_enabled}
                  onChange={(value) => updateDraft('reasoning_enabled', value)}
                  options={REASONING_ENABLED_OPTIONS}
                  ariaLabel="Reasoning enabled"
                  disabled={saving || isGoProvider}
                />
              </label>
              {['reasoning_effort', 'reasoning_summary'].map((field) => (
                <label className="agents-draft-field profiles-field" key={field}>
                  <span className="agents-draft-field__label">{field === 'reasoning_effort' ? 'Reasoning effort' : 'Reasoning summary'}</span>
                  <input className="set-text-input" type="text" value={draft[field]} onChange={(event) => updateDraft(field, event.target.value)} disabled={saving || isGoProvider} placeholder="Inherit" />
                </label>
              ))}
              <fieldset className="profiles-pricing">
                <legend>Estimated token pricing</legend>
                <div className="profiles-pricing__preset">
                  <button type="button" className="repo-btn repo-btn--ghost" onClick={applyGpt5MiniGlobalStandardPricing} disabled={saving}>Use GPT-5 mini Global Standard preset</button>
                  <a href="https://azure.microsoft.com/en-us/pricing/details/azure-openai/" target="_blank" rel="noreferrer">Pricing source</a>
                </div>
                <label className="agents-draft-field profiles-field">
                  <span className="agents-draft-field__label">Currency</span>
                  <input className="set-text-input" type="text" value={draft.pricing.currency} onChange={(event) => updatePricing('currency', event.target.value)} disabled={saving} placeholder="USD" maxLength="3" />
                </label>
                {[
                  ['input_per_million', 'Input per 1M tokens', '0.25'],
                  ['cached_input_per_million', 'Cached input per 1M tokens', '0.025'],
                  ['output_per_million', 'Output per 1M tokens', '2.00'],
                ].map(([field, label, placeholder]) => (
                  <label className="agents-draft-field profiles-field" key={field}>
                    <span className="agents-draft-field__label">{label}</span>
                    <input className="set-text-input" type="text" inputMode="decimal" value={draft.pricing[field]} onChange={(event) => updatePricing(field, event.target.value)} disabled={saving} placeholder={placeholder} />
                  </label>
                ))}
              </fieldset>
              {error ? <InlineFailure failure={error} operation="Save profile" /> : null}
              <p className="profiles-form__hint">{saving ? 'Saving…' : isGoProvider ? 'Foundry reasoning overrides do not apply to OpenCode Go.' : 'Clear effort or summary to remove its override.'}</p>
            </form>
          ) : (
            <dl className="agents-card-facts profiles-config-grid">
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Name</dt><dd className="agents-card-fact__value">{profile.name}</dd></div>
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Provider</dt><dd className="agents-card-fact__value">{providerLabel(profile, providers)}</dd></div>
              <div className="agents-card-fact"><dt className="agents-card-fact__label">{selectedProvider?.kind === 'opencode_go' ? 'Responses model' : 'Deployment'}</dt><dd className="agents-card-fact__value">{profile.deployment}</dd></div>
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Reasoning enabled</dt><dd className="agents-card-fact__value">{profile.reasoning_enabled == null ? 'Inherit' : profile.reasoning_enabled ? 'Enabled' : 'Disabled'}</dd></div>
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Reasoning effort</dt><dd className="agents-card-fact__value">{profile.reasoning_effort || 'Inherit'}</dd></div>
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Reasoning summary</dt><dd className="agents-card-fact__value">{profile.reasoning_summary || 'Inherit'}</dd></div>
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Estimated pricing</dt><dd className="agents-card-fact__value">{profile.pricing ? `${profile.pricing.currency} current rates` : 'Unpriced'}</dd></div>
              {profile.pricing ? (
                <>
                  <div className="agents-card-fact"><dt className="agents-card-fact__label">Input per 1M</dt><dd className="agents-card-fact__value">{profile.pricing.input_per_million}</dd></div>
                  <div className="agents-card-fact"><dt className="agents-card-fact__label">Cached input per 1M</dt><dd className="agents-card-fact__value">{profile.pricing.cached_input_per_million}</dd></div>
                  <div className="agents-card-fact"><dt className="agents-card-fact__label">Output per 1M</dt><dd className="agents-card-fact__value">{profile.pricing.output_per_million}</dd></div>
                </>
              ) : null}
            </dl>
          )}
        </section>
      </div>
      {deleteAction.open ? (
        <PopupModal
          eyebrow="Delete profile"
          title={`Delete “${profile.name}”?`}
          message="This model profile will be permanently deleted."
          error={deleteAction.error}
          confirmLabel="Delete profile"
          busyLabel="Deleting…"
          onClose={deleteAction.close}
          onConfirm={deleteAction.confirm}
          busy={deleteAction.busy}
        />
      ) : null}
    </>
  );
}

export default function ProfilesPage() {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const { profileName } = useParams();
  const { getApiUrl } = useConfig();
  const url = getApiUrl('modelProfiles');
  const providersUrl = getApiUrl('providers');
  const connected = useSelector(selectRealtimeStreamReady);
  const profiles = useSelector(selectModelProfiles);
  const catalogue = useSelector(selectModelProfilesState);
  const providers = useSelector(selectProviders);
  const goModels = useSelector(selectGoResponsesModels);
  const [filter, setFilter] = useState('All');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('Name');
  useEffect(() => {
    if (!connected) return undefined;
    dispatch(fetchModelProfiles({ modelProfilesUrl: url }));
    dispatch(fetchProviders({ providersUrl }));
    return undefined;
  }, [connected, dispatch, providersUrl, url]);
  const visible = useMemo(() => profiles.filter((profile) => (
    (filter === 'All' || isCustomized(profile) === (filter === 'Customized'))
    && `${profile.name} ${providerLabel(profile, providers)} ${profile.deployment}`.toLowerCase().includes(query.trim().toLowerCase())
  )).sort((a, b) => (sort === 'Provider' ? providerLabel(a, providers).localeCompare(providerLabel(b, providers)) : 0) || a.name.localeCompare(b.name)), [profiles, providers, filter, query, sort]);
  const selected = profiles.find((profile) => profile.name === profileName)
    || (!profileName ? visible[0] || profiles[0] : null);
  useEntityUrlSync('/profiles', selected?.name, selected?.name);
  useMissingEntityRoute({
    hydrated: catalogue.loaded,
    entityId: profileName,
    entities: profiles,
    getEntityId: (profile) => profile.name,
    fallbackPath: '/profiles',
  });

  const openCreateProfileChat = useCallback(() => {
    window.dispatchEvent(new CustomEvent('progena:open-quick-chat', {
      detail: { mode: 'profile-create' },
    }));
  }, []);
  const loadingMessage = !connected ? 'Waiting for the live connection…'
    : !catalogue.loaded ? 'Loading model profiles…' : 'No model profiles configured.';

  return (
    <PageShell className="profiles-page" label="Model profiles">
      <PaneList className="profiles-pane" label="Model profiles">
        <PaneListFilters
          filters={FILTERS}
          activeFilter={filter}
          onFilterChange={setFilter}
          actionLabel="+ New"
          onAction={openCreateProfileChat}
        />
        <PaneListToolbar>
          <PaneListSearch value={query} placeholder="Search profiles…" onChange={setQuery} />
          <PaneListSortButton title="Sort profiles" options={['Name', 'Provider']} value={sort} onSelect={setSort} />
        </PaneListToolbar>
        {!connected ? <div className="profiles-list-notice" role="status">Offline — reconnecting. {catalogue.loaded ? 'Saved configuration remains visible.' : 'Waiting for profile data.'}</div> : null}
        {catalogue.error ? (
          <InlineFailure
            failure={catalogue.error}
            operation="Load model profiles"
            onRetry={() => dispatch(fetchModelProfiles({ modelProfilesUrl: url }))}
          />
        ) : null}
        {!profiles.length && !catalogue.error ? <PaneEmptyState>{loadingMessage}</PaneEmptyState>
          : !profiles.length ? null
            : !visible.length ? <PaneEmptyState>No model profiles match this view.</PaneEmptyState>
            : <PaneListRows items={visible} getItemKey={(profile) => profile.name} renderItem={(profile) => (
              <button type="button" className={`sess-row profiles-pane-row ${selected?.name === profile.name ? 'is-selected' : ''}`}
                aria-current={selected?.name === profile.name ? 'true' : undefined}
                onClick={() => navigate(buildEntityPath('/profiles', profile.name, profile.name), { replace: true })}
                aria-label={`${profile.name}, ${providerLabel(profile, providers)}, ${profile.deployment}`}>
                <span className="sess-row__top"><span className="profiles-pane-row__name">{profile.name}</span>{isCustomized(profile) ? <span className="profiles-badge profiles-badge--customized">Customized</span> : null}</span>
                <span className="sess-row__title"><span className="profiles-badge profiles-badge--provider">{providerLabel(profile, providers)}</span><span className="profiles-badge profiles-badge--deployment">{profile.deployment}</span></span>
              </button>
            )} />}
        <PaneListFooter>
          <span><span className="num">{catalogue.loaded ? profiles.length : '—'}</span> profiles</span>
          <span><span className="num">{catalogue.loaded ? profiles.filter(isCustomized).length : '—'}</span> customized</span>
        </PaneListFooter>
      </PaneList>
      <DetailPane className="detail-pane--dense profiles-detail" label="Model profile detail">
        {selected ? <ProfileDetails key={`${url}:${selected.name}`} profile={selected} catalogue={catalogue} providers={providers} goModels={goModels} url={url} connected={connected} />
          : <div className="detail-pane__body">{!catalogue.error ? <PaneEmptyState detail>{catalogue.loaded ? 'Select a profile to view its configuration.' : loadingMessage}</PaneEmptyState> : null}</div>}
      </DetailPane>
    </PageShell>
  );
}
