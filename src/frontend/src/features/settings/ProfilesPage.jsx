import { UI_EVENT } from '../../realtime/eventVocabulary';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../../lib/routes';
import { dispatchQuickChatOpen, QUICK_CHAT_MODE } from '../../lib/browserContracts';

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
import {
  fetchProviders,
  selectChatgptModels,
  selectGoResponsesModels,
  selectProviders,
} from './state/providers';
import { selectRealtimeStreamReady } from '../../realtime/realtimeSlice';
import { draftDiscarded, entityDraftKey } from '../../store/editorDraftsSlice';
import { useSnapshotSave } from '../../hooks/useSnapshotSave';
import './profiles.css';

const FILTERS = ['All'];
const PRICING_FIELDS = ['currency', 'input_per_million', 'cached_input_per_million', 'output_per_million'];
const DESCRIPTION_MAX_LENGTH = 280;
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
const PROFILE_SNAPSHOT_EVENTS = Object.freeze([UI_EVENT.PROFILES_SNAPSHOT]);

// Every profile field is top-level, so the draft is one flat object of strings
// (one per form field) and the patch is the set of fields whose value changed.
function toDraft(profile) {
  const text = (value) => (value == null ? '' : String(value));
  return {
    description: text(profile.description),
    provider_id: text(profile.provider_id),
    deployment: text(profile.deployment),
    context_window: text(profile.context_window),
    cache_ttl_seconds: text(profile.cache_ttl_seconds),
    reasoning_enabled: text(profile.reasoning_enabled),
    reasoning_effort: text(profile.reasoning_effort),
    reasoning_summary: text(profile.reasoning_summary),
    ...Object.fromEntries(PRICING_FIELDS.map((field) => [field, text(profile[field])])),
  };
}

const POSITIVE_INTEGER = /^[1-9]\d*$/;

function validateDraft(draft) {
  if (draft.description.trim().length > DESCRIPTION_MAX_LENGTH) {
    return `Description must be at most ${DESCRIPTION_MAX_LENGTH} characters.`;
  }
  for (const [field, label] of [['context_window', 'Context window'], ['cache_ttl_seconds', 'Cache lifetime']]) {
    const value = draft[field].trim();
    if (value && !POSITIVE_INTEGER.test(value)) return `${label} must be a positive whole number.`;
  }
  const pricing = PRICING_FIELDS.map((field) => draft[field].trim());
  if (pricing.some(Boolean)) {
    if (pricing.some((value) => !value)) {
      return 'Complete currency and all per-million rates, or clear every pricing field to mark this deployment unpriced.';
    }
    if (!/^[A-Z]{3}$/.test(pricing[0])) return 'Currency must be a three-letter uppercase ISO 4217 code.';
    if (pricing.slice(1).some((value) => !/^\d+(?:\.\d+)?$/.test(value))) return 'Rates must be non-negative decimal strings.';
  }
  return null;
}

// Wire values for a draft: blanks become null, which the PATCH treats as "remove".
function draftValues(draft) {
  const textOrNull = (value) => value.trim() || null;
  const integerOrNull = (value) => (value.trim() ? Number(value.trim()) : null);
  return {
    description: textOrNull(draft.description),
    provider_id: textOrNull(draft.provider_id),
    deployment: draft.deployment.trim(),
    context_window: integerOrNull(draft.context_window),
    cache_ttl_seconds: integerOrNull(draft.cache_ttl_seconds),
    reasoning_enabled: draft.reasoning_enabled === '' ? null : draft.reasoning_enabled === 'true',
    reasoning_effort: textOrNull(draft.reasoning_effort),
    reasoning_summary: textOrNull(draft.reasoning_summary),
    ...Object.fromEntries(PRICING_FIELDS.map((field) => [field, textOrNull(draft[field])])),
  };
}

function buildPatch(draft, base) {
  const values = draftValues(draft);
  const baseValues = draftValues(base);
  const changed = Object.keys(values).filter((field) => values[field] !== baseValues[field]);
  // Pricing is all-or-nothing on the server, so any pricing change sends all four.
  const fields = changed.some((field) => PRICING_FIELDS.includes(field))
    ? [...new Set([...changed, ...PRICING_FIELDS])]
    : changed;
  return Object.fromEntries(fields.map((field) => [field, values[field]]));
}

function Fact({ label, children }) {
  return (
    <div className="agents-card-fact">
      <dt className="agents-card-fact__label">{label}</dt>
      <dd className="agents-card-fact__value">{children}</dd>
    </div>
  );
}

function Field({ label, required = false, children }) {
  return (
    <label className="agents-draft-field profiles-field">
      <span className="agents-draft-field__label">
        {label}{required ? <> <span aria-hidden="true" className="profiles-field__required">*</span></> : null}
      </span>
      {children}
    </label>
  );
}

// One titled group of flat profile fields: a form grid while editing, the
// shared card facts otherwise.
function ProfileSection({ title, editing, actions = null, children }) {
  return (
    <section className="agents-section">
      <header className="agents-section__head">
        <h2>{title}</h2>
        {actions}
      </header>
      {editing
        ? <div className="agents-card-facts agents-draft profiles-form">{children}</div>
        : <dl className="agents-card-facts profiles-config-grid">{children}</dl>}
    </section>
  );
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
  const isChatgptProvider = selectedProvider?.kind === 'chatgpt';
  const goModelOptions = useMemo(
    () => goModels.map((model) => ({ value: model, label: model })),
    [goModels],
  );
  const chatgptModels = useSelector((state) => selectChatgptModels(state, selectedProviderId));
  const chatgptModelOptions = useMemo(
    () => chatgptModels.map((model) => ({ value: model.slug, label: model.display_name })),
    [chatgptModels],
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
      setEditState((current) => ({ ...current, validationError: isGoProvider || isChatgptProvider ? 'A Responses model is required.' : 'Deployment is required.' }));
      return;
    }
    if (isGoProvider && !goModels.includes(draft.deployment)) {
      setEditState((current) => ({ ...current, validationError: 'Choose a supported OpenCode Go Responses model.' }));
      return;
    }
    if (isChatgptProvider && !chatgptModels.some((model) => model.slug === draft.deployment)) {
      setEditState((current) => ({ ...current, validationError: 'Choose a model available to this ChatGPT account.' }));
      return;
    }
    const draftError = validateDraft(draft);
    if (draftError) {
      setEditState((current) => ({ ...current, validationError: draftError }));
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
  const applyGpt5MiniGlobalStandardPricing = () => setEditState((current) => ({
    ...current,
    draft: { ...current.draft, ...GPT_5_MINI_GLOBAL_STANDARD_PRICING },
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
        <span key="provider" className="muted">{providerLabel(profile, providers)}</span>,
        <span key="deployment" className="sess-detail__model">{profile.deployment}</span>,
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
        <form className="profiles-sections" onSubmit={(event) => { event.preventDefault(); handleSave(); }}>
          <ProfileSection
            title="Profile"
            editing={Boolean(draft)}
            actions={draft ? (
              <div className="profiles-section__actions">
                <button type="button" className="repo-btn repo-btn--ghost" onClick={cancelEdit} disabled={saving}>Cancel</button>
                <button type="submit" className="repo-btn repo-btn--primary" disabled={saving || conflict || !connected}>{saving ? 'Saving…' : 'Save'}</button>
              </div>
            ) : (
              <button type="button" className="repo-btn repo-btn--ghost" onClick={startEdit} disabled={!connected || catalogue.loading || Boolean(catalogue.error)}>Edit</button>
            )}
          >
            {draft ? (
              <Field label="Description">
                <textarea className="set-text-input profiles-description" rows={2} maxLength={DESCRIPTION_MAX_LENGTH} value={draft.description} onChange={(event) => updateDraft('description', event.target.value)} disabled={saving} placeholder="What this profile is for" />
              </Field>
            ) : (
              <>
                <Fact label="Name">{profile.name}</Fact>
                <Fact label="Description">{profile.description || '—'}</Fact>
              </>
            )}
          </ProfileSection>

          <ProfileSection title="Model" editing={Boolean(draft)}>
            {draft ? (
              <>
                <Field label="Provider">
                  <Select value={draft.provider_id} onChange={(value) => updateDraft('provider_id', value)} options={providerOptions} ariaLabel="Provider" disabled={saving || !providers.length} />
                </Field>
                <Field label="Model" required>
                  {isGoProvider ? (
                    <Select value={draft.deployment} onChange={(value) => updateDraft('deployment', value)} options={goModelOptions} ariaLabel="OpenCode Go Responses model" placeholder="Choose a supported model" disabled={saving || !goModels.length} />
                  ) : isChatgptProvider ? (
                    <Select value={draft.deployment} onChange={(value) => updateDraft('deployment', value)} options={chatgptModelOptions} ariaLabel="ChatGPT plan model" placeholder="Choose an available model" disabled={saving || !chatgptModels.length} />
                  ) : (
                    <input className="set-text-input" type="text" value={draft.deployment} onChange={(event) => updateDraft('deployment', event.target.value)} disabled={saving} required aria-label="Model" />
                  )}
                </Field>
                <Field label="Context window (tokens)">
                  <input className="set-text-input" type="text" inputMode="numeric" value={draft.context_window} onChange={(event) => updateDraft('context_window', event.target.value)} disabled={saving} placeholder="Unknown" />
                </Field>
                <Field label="Cache lifetime (seconds)">
                  <input className="set-text-input" type="text" inputMode="numeric" value={draft.cache_ttl_seconds} onChange={(event) => updateDraft('cache_ttl_seconds', event.target.value)} disabled={saving} placeholder="Unknown" />
                </Field>
              </>
            ) : (
              <>
                <Fact label="Provider">{providerLabel(profile, providers)}</Fact>
                <Fact label="Model">{profile.deployment}</Fact>
                <Fact label="Context window">{profile.context_window ? `${profile.context_window.toLocaleString()} tokens` : '—'}</Fact>
                <Fact label="Cache lifetime">{profile.cache_ttl_seconds ? `${profile.cache_ttl_seconds.toLocaleString()} s` : '—'}</Fact>
              </>
            )}
          </ProfileSection>

          <ProfileSection title="Reasoning" editing={Boolean(draft)}>
            {draft ? (
              <>
                <Field label="Reasoning">
                  <Select value={draft.reasoning_enabled} onChange={(value) => updateDraft('reasoning_enabled', value)} options={REASONING_ENABLED_OPTIONS} ariaLabel="Reasoning enabled" disabled={saving || isGoProvider} />
                </Field>
                {[['reasoning_effort', 'Effort'], ['reasoning_summary', 'Summary']].map(([field, label]) => (
                  <Field label={label} key={field}>
                    <input className="set-text-input" type="text" value={draft[field]} onChange={(event) => updateDraft(field, event.target.value)} disabled={saving || isGoProvider} placeholder="Inherit" />
                  </Field>
                ))}
              </>
            ) : (
              <>
                <Fact label="Reasoning">{profile.reasoning_enabled == null ? 'Inherit' : profile.reasoning_enabled ? 'Enabled' : 'Disabled'}</Fact>
                <Fact label="Effort">{profile.reasoning_effort || 'Inherit'}</Fact>
                <Fact label="Summary">{profile.reasoning_summary || 'Inherit'}</Fact>
              </>
            )}
          </ProfileSection>

          <ProfileSection
            title="Pricing"
            editing={Boolean(draft)}
            actions={draft ? (
              <div className="profiles-pricing__preset">
                <button type="button" className="repo-btn repo-btn--ghost" onClick={applyGpt5MiniGlobalStandardPricing} disabled={saving}>Use GPT-5 mini Global Standard preset</button>
                <a href="https://azure.microsoft.com/en-us/pricing/details/azure-openai/" target="_blank" rel="noreferrer">Pricing source</a>
              </div>
            ) : null}
          >
            {draft ? (
              <>
                <Field label="Currency">
                  <input className="set-text-input" type="text" value={draft.currency} onChange={(event) => updateDraft('currency', event.target.value)} disabled={saving} placeholder="USD" maxLength="3" />
                </Field>
                {[
                  ['input_per_million', 'Input per 1M tokens', '0.25'],
                  ['cached_input_per_million', 'Cached input per 1M tokens', '0.025'],
                  ['output_per_million', 'Output per 1M tokens', '2.00'],
                ].map(([field, label, placeholder]) => (
                  <Field label={label} key={field}>
                    <input className="set-text-input" type="text" inputMode="decimal" value={draft[field]} onChange={(event) => updateDraft(field, event.target.value)} disabled={saving} placeholder={placeholder} />
                  </Field>
                ))}
              </>
            ) : profile.currency ? (
              <>
                <Fact label="Currency">{profile.currency}</Fact>
                <Fact label="Input per 1M">{profile.input_per_million}</Fact>
                <Fact label="Cached input per 1M">{profile.cached_input_per_million}</Fact>
                <Fact label="Output per 1M">{profile.output_per_million}</Fact>
              </>
            ) : (
              <Fact label="Estimated pricing">Unpriced</Fact>
            )}
          </ProfileSection>

          {draft ? (
            <>
              {error ? <InlineFailure failure={error} operation="Save profile" /> : null}
              <p className="profiles-form__hint">{saving ? 'Saving…' : isGoProvider ? 'Foundry reasoning overrides do not apply to OpenCode Go.' : isChatgptProvider && !chatgptModels.length ? 'The selected ChatGPT account has no available model catalog. Reconnect it in Providers.' : 'Clear a field to remove it from the profile; pricing needs all four values or none.'}</p>
            </>
          ) : null}
        </form>
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
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('Name');
  useEffect(() => {
    if (!connected) return undefined;
    dispatch(fetchModelProfiles({ modelProfilesUrl: url }));
    dispatch(fetchProviders({ providersUrl }));
    return undefined;
  }, [connected, dispatch, providersUrl, url]);
  const visible = useMemo(() => profiles.filter((profile) => (
    `${profile.name} ${providerLabel(profile, providers)} ${profile.deployment}`.toLowerCase().includes(query.trim().toLowerCase())
  )).sort((a, b) => (sort === 'Provider' ? providerLabel(a, providers).localeCompare(providerLabel(b, providers)) : 0) || a.name.localeCompare(b.name)), [profiles, providers, query, sort]);
  // Provider sort groups under provider headings, like Models; Name is flat.
  const providerGroups = useMemo(() => {
    if (sort !== 'Provider') return null;
    const groups = new Map();
    for (const profile of visible) {
      const label = providerLabel(profile, providers);
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(profile);
    }
    return [...groups].map(([label, items]) => ({ key: label, title: label, items }));
  }, [providers, sort, visible]);
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
    dispatchQuickChatOpen({ mode: QUICK_CHAT_MODE.PROFILE_CREATE });
  }, []);
  const loadingMessage = !connected ? 'Waiting for the live connection…'
    : !catalogue.loaded ? 'Loading model profiles…' : 'No model profiles configured.';

  return (
    <PageShell className="profiles-page" label="Model profiles">
      <PaneList className="profiles-pane" label="Model profiles">
        <PaneListFilters
          filters={FILTERS}
          activeFilter="All"
          onFilterChange={() => {}}
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
            : <PaneListRows
              {...(providerGroups ? { groups: providerGroups } : { items: visible })}
              getItemKey={(profile) => profile.name}
              renderItem={(profile) => (
                <button type="button" className={`sess-row agents-agent-row ${selected?.name === profile.name ? 'is-selected' : ''}`}
                  aria-current={selected?.name === profile.name ? 'true' : undefined}
                  onClick={() => navigate(buildEntityPath('/profiles', profile.name, profile.name), { replace: true })}
                  aria-label={`${profile.name}, ${providerLabel(profile, providers)}, ${profile.deployment}`}>
                  <span className="sess-row__top">
                    <span className="sess-row__agent agents-agent-row__name" title={profile.name}>{profile.name}</span>
                    <span className="sess-row__project">{providerLabel(profile, providers)}</span>
                  </span>
                  <span className="sess-row__title">{profile.deployment}</span>
                </button>
              )} />}
        <PaneListFooter>
          <span><span className="num">{catalogue.loaded ? profiles.length : '—'}</span> profiles</span>
        </PaneListFooter>
      </PaneList>
      <DetailPane className="detail-pane--dense profiles-detail" label="Model profile detail">
        {selected ? <ProfileDetails key={`${url}:${selected.name}`} profile={selected} catalogue={catalogue} providers={providers} goModels={goModels} url={url} connected={connected} />
          : <div className="detail-pane__body">{!catalogue.error ? <PaneEmptyState detail>{catalogue.loaded ? 'Select a profile to view its configuration.' : loadingMessage}</PaneEmptyState> : null}</div>}
      </DetailPane>
    </PageShell>
  );
}
