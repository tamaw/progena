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
import useMissingEntityRoute from '../hooks/useMissingEntityRoute';
import { useConfig } from '../context/ConfigContext';
import {
  deleteModelProfile, fetchModelProfiles, patchModelProfile, selectModelProfiles, selectModelProfilesState,
} from '../store/uiDataSlice';
import { selectRealtimeStreamReady } from '../store/realtimeSlice';
import { useSnapshotSave } from '../store/uiData/snapshotSave';
import '../styles/profiles.css';

const FILTERS = ['All', 'Inherited', 'Customized'];
const EDITABLE_FIELDS = ['deployment', 'reasoning_enabled', 'reasoning_effort', 'reasoning_summary'];
const providerLabel = (provider) => provider === 'azure_openai' ? 'Azure OpenAI' : provider;
const REASONING_ENABLED_OPTIONS = [
  { value: '', label: 'Inherit' },
  { value: 'true', label: 'Enabled' },
  { value: 'false', label: 'Disabled' },
];

const isCustomized = (profile) => EDITABLE_FIELDS.slice(1).some((field) => profile[field] != null);

function toDraft(profile) {
  return {
    deployment: profile.deployment,
    reasoning_enabled: profile.reasoning_enabled == null ? '' : String(profile.reasoning_enabled),
    reasoning_effort: profile.reasoning_effort || '',
    reasoning_summary: profile.reasoning_summary || '',
  };
}

function buildPatch(draft, base) {
  const values = {
    deployment: draft.deployment.trim(),
    reasoning_enabled: draft.reasoning_enabled === '' ? null : draft.reasoning_enabled === 'true',
    reasoning_effort: draft.reasoning_effort.trim() || null,
    reasoning_summary: draft.reasoning_summary.trim() || null,
  };
  return Object.fromEntries(EDITABLE_FIELDS
    .filter((field) => values[field] !== (base[field] ?? null))
    .map((field) => [field, values[field]]));
}

function ProfileDetails({ profile, profiles, catalogue, url, connected }) {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const [draft, setDraft] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [saving, setSaving] = useState(false);
  const [pendingSave, setPendingSave] = useState(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [error, setError] = useState(null);
  const deleteAction = useConfirmAction(`profile-delete:${url}:${profile.name}`);
  const completedRequestId = catalogue.saveRequest.completedRequestId;
  const ownSnapshot = pendingSave && completedRequestId === pendingSave.requestId;
  const conflict = Boolean(draft && baseline && !ownSnapshot
    && EDITABLE_FIELDS.some((field) => (baseline[field] ?? null) !== (profile[field] ?? null)));
  const startEdit = () => {
    setBaseline(profile);
    setDraft(toDraft(profile));
    setError(null);
    setStatusMessage('');
  };
  const closeEdit = useCallback(() => {
    setDraft(null);
    setBaseline(null);
  }, []);
  const cancelEdit = () => {
    closeEdit();
    setError(null);
    setStatusMessage('');
  };
  const items = useMemo(() => profiles.map((item) => ({
    path: item.name, content: JSON.stringify(toDraft(item)),
  })), [profiles]);
  const buildArgs = useCallback(({ path, content, requestId }) => ({
    modelProfilesUrl: url, name: path, patch: buildPatch(JSON.parse(content), baseline), requestId,
  }), [url, baseline]);
  const save = useSnapshotSave({
    items, snapshotRequestId: completedRequestId, pendingSave, dispatch,
    thunk: patchModelProfile, buildArgs, path: profile.name,
    content: draft ? JSON.stringify(draft) : '',
    savedContent: baseline ? JSON.stringify(toDraft(baseline)) : '',
    saving, realtimeConnected: connected,
    setDraftContent: closeEdit, setSaving, setPendingSave, setStatusMessage, setError,
  });
  const handleSave = () => {
    if (!draft || conflict || saving) return;
    if (!draft.deployment.trim()) {
      setError(new Error('Deployment is required.'));
      return;
    }
    if (!Object.keys(buildPatch(draft, baseline)).length) {
      cancelEdit();
      return;
    }
    save();
  };
  const updateDraft = (field, value) => setDraft((current) => ({ ...current, [field]: value }));

  const handleDelete = () => {
    deleteAction.request('Delete profile', async ({ isCurrent }) => {
      await dispatch(deleteModelProfile({ modelProfilesUrl: url, name: profile.name })).unwrap();
      if (isCurrent()) navigate('/profiles', { replace: true });
    });
  };

  return (
    <>
      <DetailHeader title={profile.name} meta={[
        <span key="provider" className="profiles-badge profiles-badge--provider">{providerLabel(profile.provider)}</span>,
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
        {statusMessage ? <div className="profiles-alert profiles-alert--status" role="status">{statusMessage}</div> : null}
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
                <span className="agents-draft-field__label">Deployment <span aria-hidden="true" className="profiles-field__required">*</span></span>
                <input type="text" value={draft.deployment} onChange={(event) => updateDraft('deployment', event.target.value)} disabled={saving} required />
              </label>
              <label className="agents-draft-field profiles-field">
                <span className="agents-draft-field__label">Reasoning enabled</span>
                <Select
                  value={draft.reasoning_enabled}
                  onChange={(value) => updateDraft('reasoning_enabled', value)}
                  options={REASONING_ENABLED_OPTIONS}
                  ariaLabel="Reasoning enabled"
                  disabled={saving}
                />
              </label>
              {['reasoning_effort', 'reasoning_summary'].map((field) => (
                <label className="agents-draft-field profiles-field" key={field}>
                  <span className="agents-draft-field__label">{field === 'reasoning_effort' ? 'Reasoning effort' : 'Reasoning summary'}</span>
                  <input type="text" value={draft[field]} onChange={(event) => updateDraft(field, event.target.value)} disabled={saving} placeholder="Inherit" />
                </label>
              ))}
              {error ? <InlineFailure failure={error} operation="Save profile" /> : null}
              <p className="profiles-form__hint">{saving ? 'Saving…' : 'Inherit uses runtime environment settings. Clear effort or summary to remove its override.'}</p>
            </form>
          ) : (
            <dl className="agents-card-facts profiles-config-grid">
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Name</dt><dd className="agents-card-fact__value">{profile.name}</dd></div>
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Provider</dt><dd className="agents-card-fact__value">{providerLabel(profile.provider)}</dd></div>
              <div className="agents-card-fact"><dt className="agents-card-fact__label">Deployment</dt><dd className="agents-card-fact__value">{profile.deployment}</dd></div>
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
  const connected = useSelector(selectRealtimeStreamReady);
  const profiles = useSelector(selectModelProfiles);
  const catalogue = useSelector(selectModelProfilesState);
  const [filter, setFilter] = useState('All');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('Name');
  useEffect(() => {
    if (!connected) return undefined;
    dispatch(fetchModelProfiles({ modelProfilesUrl: url }));
    return undefined;
  }, [connected, dispatch, url]);
  const visible = useMemo(() => profiles.filter((profile) => (
    (filter === 'All' || isCustomized(profile) === (filter === 'Customized'))
    && `${profile.name} ${providerLabel(profile.provider)} ${profile.deployment}`.toLowerCase().includes(query.trim().toLowerCase())
  )).sort((a, b) => (sort === 'Provider' ? a.provider.localeCompare(b.provider) : 0) || a.name.localeCompare(b.name)), [profiles, filter, query, sort]);
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
                aria-label={`${profile.name}, ${providerLabel(profile.provider)}, ${profile.deployment}`}>
                <span className="sess-row__top"><span className="profiles-pane-row__name">{profile.name}</span>{isCustomized(profile) ? <span className="profiles-badge profiles-badge--customized">Customized</span> : null}</span>
                <span className="sess-row__title"><span className="profiles-badge profiles-badge--provider">{providerLabel(profile.provider)}</span><span className="profiles-badge profiles-badge--deployment">{profile.deployment}</span></span>
              </button>
            )} />}
        <PaneListFooter>
          <span><span className="num">{catalogue.loaded ? profiles.length : '—'}</span> profiles</span>
          <span><span className="num">{catalogue.loaded ? profiles.filter(isCustomized).length : '—'}</span> customized</span>
        </PaneListFooter>
      </PaneList>
      <DetailPane className="detail-pane--dense profiles-detail" label="Model profile detail">
        {selected ? <ProfileDetails key={`${url}:${selected.name}`} profile={selected} profiles={profiles} catalogue={catalogue} url={url} connected={connected} />
          : <div className="detail-pane__body">{!catalogue.error ? <PaneEmptyState detail>{catalogue.loaded ? 'Select a profile to view its configuration.' : loadingMessage}</PaneEmptyState> : null}</div>}
      </DetailPane>
    </PageShell>
  );
}
