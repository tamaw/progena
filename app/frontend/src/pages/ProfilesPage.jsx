import { useCallback, useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../lib/routes';
import {
  PaneEmptyState, PaneList, PaneListFilters, PaneListFooter, PaneListSearch,
  PaneListSortButton, PaneListToolbar, PaneListRows,
} from '../components/layout/PaneList';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import DetailHeader, {
  DeleteActionIcon, DetailHeaderActions, DetailHeaderAction,
} from '../components/layout/DetailHeader';
import Select from '../components/common/Select';
import PopupModal from '../components/common/PopupModal';
import InlineFailure from '../components/common/InlineFailure';
import useConfirmAction from '../hooks/useConfirmAction';
import useEntityDraft from '../hooks/useEntityDraft';
import useMissingEntityRoute from '../hooks/useMissingEntityRoute';
import { useConfig } from '../context/ConfigContext';
import {
  deleteModelProfile,
  fetchModelProfiles,
  fetchProviders,
  patchModelProfile,
  selectGoResponsesModels,
  selectModelProfiles,
  selectModelProfilesState,
  selectProviders,
} from '../store/uiDataSlice';
import { selectRealtimeStreamReady } from '../store/realtimeSlice';
import { draftDiscarded, entityDraftKey } from '../store/editorDraftsSlice';
import { useSnapshotSave } from '../store/uiData/snapshotSave';
import '../styles/profiles.css';

const FILTERS = ['All', 'Inherited', 'Customized'];
const EDITABLE_FIELDS = ['provider_id', 'deployment', 'reasoning_enabled', 'reasoning_effort', 'reasoning_summary'];
const providerLabel = (profile, providers) => {
  const provider = profile.provider_id ? providers.find((candidate) => candidate.id === profile.provider_id) : null;
  if (provider) return provider.name;
  if (!profile.provider_id) return 'Default provider';
  return 'Provider unavailable';
};
const REASONING_ENABLED_OPTIONS = [
  { value: '', label: 'Inherit' },
  { value: 'true', label: 'Enabled' },
  { value: 'false', label: 'Disabled' },
];

const EMPTY_PROFILE_EDIT = Object.freeze({ draft: null, baseline: null, validationError: null });
const PROFILE_SNAPSHOT_EVENTS = Object.freeze(['profiles.snapshot']);

const isCustomized = (profile) => ['provider_id', 'reasoning_enabled', 'reasoning_effort', 'reasoning_summary']
  .some((field) => profile[field] != null);

function toDraft(profile) {
  return {
    provider_id: profile.provider_id ?? '',
    deployment: profile.deployment ?? '',
    reasoning_enabled: profile.reasoning_enabled == null ? '' : String(profile.reasoning_enabled),
    reasoning_effort: profile.reasoning_effort ?? '',
    reasoning_summary: profile.reasoning_summary ?? '',
  };
}

function toBaseline(profile) {
  return Object.fromEntries(EDITABLE_FIELDS.map((field) => [field, profile[field]]));
}

function buildPatch(draft, base) {
  const values = {
    provider_id: draft.provider_id || null,
    deployment: draft.deployment.trim(),
    reasoning_enabled: draft.reasoning_enabled === '' ? null : draft.reasoning_enabled === 'true',
    reasoning_effort: draft.reasoning_effort.trim() || null,
    reasoning_summary: draft.reasoning_summary.trim() || null,
  };
  return Object.fromEntries(EDITABLE_FIELDS
    .filter((field) => values[field] !== (base[field] ?? null))
    .map((field) => [field, values[field]]));
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
  const providerOptions = useMemo(() => [
    { value: '', label: 'Default provider' },
    ...providers.map((provider) => ({ value: provider.id, label: provider.name })),
  ], [providers]);
  const selectedProviderId = draft?.provider_id ?? profile.provider_id ?? '';
  const selectedProvider = selectedProviderId
    ? providers.find((provider) => provider.id === selectedProviderId)
    : providers.find((provider) => provider.is_default);
  const isGoProvider = selectedProvider?.kind === 'opencode_go';
  const goModelOptions = useMemo(
    () => goModels.map((model) => ({ value: model, label: model })),
    [goModels],
  );
  const deleteAction = useConfirmAction(`profile-delete:${url}:${profile.name}`);
  const conflict = Boolean(draft && baseline
    && EDITABLE_FIELDS.some((field) => (baseline[field] ?? null) !== (profile[field] ?? null)));
  const startEdit = () => {
    setEditState({
      draft: toDraft(profile),
      baseline: toBaseline(profile),
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
    savedContent: baseline ? JSON.stringify(toDraft(baseline)) : '',
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
                  <input type="text" value={draft.deployment} onChange={(event) => updateDraft('deployment', event.target.value)} disabled={saving} required />
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
                  <input type="text" value={draft[field]} onChange={(event) => updateDraft(field, event.target.value)} disabled={saving || isGoProvider} placeholder="Inherit" />
                </label>
              ))}
              {error ? <InlineFailure failure={error} operation="Save profile" /> : null}
              <p className="profiles-form__hint">{saving ? 'Saving…' : isGoProvider ? 'Foundry reasoning overrides do not apply to OpenCode Go.' : 'Default provider uses the configured runtime connection. Clear effort or summary to remove its override.'}</p>
            </form>
          ) : (
            <dl className="agents-card-facts profiles-config-grid">
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Name</dt><dd className="agents-card-fact__value">{profile.name}</dd></div>
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Provider</dt><dd className="agents-card-fact__value">{providerLabel(profile, providers)}</dd></div>
              <div className="agents-card-fact"><dt className="agents-card-fact__label">{selectedProvider?.kind === 'opencode_go' ? 'Responses model' : 'Deployment'}</dt><dd className="agents-card-fact__value">{profile.deployment}</dd></div>
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Reasoning enabled</dt><dd className="agents-card-fact__value">{profile.reasoning_enabled == null ? 'Inherit' : profile.reasoning_enabled ? 'Enabled' : 'Disabled'}</dd></div>
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Reasoning effort</dt><dd className="agents-card-fact__value">{profile.reasoning_effort || 'Inherit'}</dd></div>
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Reasoning summary</dt><dd className="agents-card-fact__value">{profile.reasoning_summary || 'Inherit'}</dd></div>
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
    window.dispatchEvent(new CustomEvent('progenitor:open-quick-chat', {
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
