import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { Link } from 'react-router-dom';
import {
  PaneEmptyState, PaneList, PaneListFilters, PaneListSearch, PaneListSortButton,
  PaneListToolbar, PaneListRows,
} from '../../components/layout/PaneList';
import PageShell from '../../components/layout/PageShell';
import DetailPane from '../../components/layout/DetailPane';
import DetailHeader, {
  DetailHeaderAction, DetailHeaderActions, DetailStatus,
  PlayActionIcon, ReconnectActionIcon, SendActionIcon, StopActionIcon,
} from '../../components/layout/DetailHeader';
import { CapabilityChip, CardFact, MetricCard } from '../../components/DetailCards';
import InlineFailure from '../../components/InlineFailure';
import NumberStepper from '../../components/NumberStepper';
import { useConfig } from '../../context/ConfigContext';
import { useNotifications } from '../../context/NotificationContext';
import { reportFailure } from '../../lib/failures';
import { formatCompactRelativeAge } from '../../lib/displayTime';
import useEntityDraft from '../../hooks/useEntityDraft';
import { useListSelection } from '../../hooks/useListSelection';
import { useSnapshotSave } from '../../hooks/useSnapshotSave';
import { entityDraftKey } from '../../store/editorDraftsSlice';
import { selectRealtimeStreamReady } from '../../realtime/realtimeSlice';
import { UI_EVENT } from '../../realtime/eventVocabulary';
import { selectChatSessions } from '../chat/state/chats';
import {
  fetchChannels, patchChannel, reconnectChannel, selectChannels, selectChannelsState,
  selectChannelTest, testChannel,
} from './state';
import {
  buildCredentialPatch, buildSettingsPatch, CHANNEL_FILTERS, CREDENTIAL_SOURCE, credentialSourceSummary,
  matchesChannel, settingsToDraft, statusPresentation,
} from './channelModel';
import styles from './ChannelsPage.module.css';

const SNAPSHOT_EVENTS = [UI_EVENT.CHANNELS_SNAPSHOT];
const byCategory = (channel) => channel.category || 'Other';
const channelId = (channel) => channel.id;

/** The most recent tracked request of one type for a channel, if any. */
function requestFor(state, channelId, type) {
  let latest = null;
  for (const request of Object.values(state.requests)) {
    if (request.channelId === channelId && request.type === type) latest = request;
  }
  return latest;
}

function settingDisplayValue(field, value) {
  if (field.kind === 'boolean') return value ? 'Enabled' : 'Disabled';
  if (Array.isArray(value)) return value.join(', ') || '—';
  return value === '' || value === undefined || value === null ? '—' : String(value);
}

function SettingFacts({ channel, fields }) {
  return (
    <dl className="agents-card-facts">
      {fields.map((field) => (
        <div className="agents-card-fact" key={field.key}>
          <dt className="agents-card-fact__label">{field.label}</dt>
          <dd className="agents-card-fact__value">{settingDisplayValue(field, channel.settings?.[field.key])}</dd>
        </div>
      ))}
    </dl>
  );
}

function SettingsSummary({ channel }) {
  const fields = channel.fields || [];
  const advanced = fields.filter((field) => field.advanced);
  return (
    <>
      <SettingFacts channel={channel} fields={fields.filter((field) => !field.advanced)} />
      {advanced.length ? (
        <details className={styles.advanced}>
          <summary>Advanced settings</summary>
          <SettingFacts channel={channel} fields={advanced} />
        </details>
      ) : null}
    </>
  );
}

const capabilityLabel = (key) => key.charAt(0).toUpperCase() + key.slice(1);

function LinkedSessions({ sessions }) {
  if (!sessions.length) {
    return <PaneEmptyState>No conversations have arrived through this channel yet.</PaneEmptyState>;
  }
  return (
    <ul className={styles.sessions}>
      {sessions.map((session) => (
        <li key={session.thread_id}>
          <Link to={`/chat/${encodeURIComponent(session.thread_id)}`}><code>{session.thread_id}</code></Link>
        </li>
      ))}
    </ul>
  );
}

function ChannelRow({ channel, selected, onSelect }) {
  const status = statusPresentation(channel.status);
  return (
    <button
      type="button"
      className={`sess-row agents-agent-row ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(channel.id)}
    >
      <span className="sess-row__top">
        <span className={`sess-row__dot sess-row__dot--${status.tone}`} title={status.label} />
        <span className="sess-row__agent agents-agent-row__name">{channel.name}</span>
        <span className="sess-row__project">{channel.handle || '—'}</span>
      </span>
      <span className="sess-row__age">{formatCompactRelativeAge(channel.updated_at)}</span>
      <span className="sess-row__title">{status.label}</span>
      <span className="sess-row__msgs">
        <span><span className="num">{Number(channel.stats?.inbound || 0).toLocaleString()}</span> in</span>
        <span><span className="num">{Number(channel.stats?.outbound || 0).toLocaleString()}</span> out</span>
        <span><span className="num">{channel.conversationCount}</span> conversations</span>
      </span>
    </button>
  );
}

function SettingsField({ field, value, disabled, onChange }) {
  const label = field.required ? `${field.label} *` : field.label;
  if (field.kind === 'boolean') {
    return (
      <label className="agents-draft-field">
        <span className="agents-draft-field__label">{label}</span>
        <span className={styles.booleanField}>
          <span>{value ? 'Enabled' : 'Disabled'}</span>
          <label className={`set-switch ${value ? 'is-on' : ''}`}>
            <input type="checkbox" checked={value === true} disabled={disabled} onChange={(event) => onChange(event.target.checked)} aria-label={field.label} />
            <span className="set-switch__knob" aria-hidden="true" />
          </label>
        </span>
        {field.help ? <span className="agents-draft-field__hint">{field.help}</span> : null}
      </label>
    );
  }
  if (field.kind === 'integer') {
    const number = Number(value);
    return (
      <label className="agents-draft-field">
        <span className="agents-draft-field__label">{label}</span>
        <NumberStepper
          value={Number.isFinite(number) ? number : field.min ?? 0}
          min={Number.isFinite(field.min) ? field.min : Number.MIN_SAFE_INTEGER}
          max={Number.isFinite(field.max) ? field.max : Number.MAX_SAFE_INTEGER}
          step={Number.isFinite(field.step) ? field.step : 1}
          onChange={onChange}
          ariaLabel={field.label}
        />
        {field.help ? <span className="agents-draft-field__hint">{field.help}</span> : null}
      </label>
    );
  }
  const list = field.kind === 'list';
  return (
    <label className="agents-draft-field">
      <span className="agents-draft-field__label">{label}</span>
      {list ? (
        <textarea className="set-text-input" value={value ?? ''} disabled={disabled} onChange={(event) => onChange(event.target.value)} placeholder={field.placeholder} rows="3" />
      ) : (
        <input className="set-text-input" type={field.kind === 'number' ? 'number' : 'text'} value={value ?? ''} disabled={disabled} min={field.min} max={field.max} step={field.step} placeholder={field.placeholder} onChange={(event) => onChange(event.target.value)} />
      )}
      {field.help ? <span className="agents-draft-field__hint">{field.help}</span> : null}
    </label>
  );
}

function CredentialsEditor({ channel, databaseAllowed, drafts, setDrafts, disabled }) {
  const update = (key, value) => setDrafts((current) => ({ ...current, [key]: { ...current[key], ...value } }));
  return (
    <section className="agents-section">
      <header className="agents-section__head"><h2>Credentials</h2></header>
      {channel.credentials?.map((credential) => {
        const draft = drafts[credential.key] || {
          source: credential.source || CREDENTIAL_SOURCE.ENVIRONMENT, envVar: credential.env_var || credential.default_env_var || '', value: '', clear: false,
        };
        return (
          <fieldset className={styles.credential} key={credential.key} disabled={disabled}>
            <legend>{credential.label}</legend>
            {credential.help ? <p className={styles.hint}>{credential.help}</p> : null}
            <label className={styles.radio}>
              <input type="radio" checked={draft.source === CREDENTIAL_SOURCE.ENVIRONMENT} onChange={() => update(credential.key, { source: CREDENTIAL_SOURCE.ENVIRONMENT, clear: false })} />
              Environment variable
            </label>
            <input className="set-text-input" value={draft.envVar} onChange={(event) => update(credential.key, { envVar: event.target.value, clear: false })} disabled={draft.source !== CREDENTIAL_SOURCE.ENVIRONMENT} aria-label={`${credential.label} environment variable`} placeholder={credential.default_env_var} />
            <p className={styles.hint}>Use {credential.default_env_var} or a {credential.env_var_prefix}* variable.</p>
            <label className={styles.radio}>
              <input type="radio" checked={draft.source === CREDENTIAL_SOURCE.DATABASE} disabled={!databaseAllowed} onChange={() => update(credential.key, { source: CREDENTIAL_SOURCE.DATABASE, clear: false })} />
              Stored in database
            </label>
            <input className="set-text-input" type="password" value={draft.value} onChange={(event) => update(credential.key, { value: event.target.value, clear: false })} disabled={!databaseAllowed || draft.source !== CREDENTIAL_SOURCE.DATABASE} aria-label={`${credential.label} stored value`} placeholder={credential.configured ? '•••• (unchanged)' : 'Enter secret'} />
            {!databaseAllowed ? <p className={styles.hint}>Database credential storage is not available for this backend.</p> : null}
            {credential.configured ? <button type="button" className="repo-btn repo-btn--ghost" onClick={() => update(credential.key, { clear: true, value: '' })}>Clear</button> : null}
            {draft.clear ? <p className={styles.hint}>This credential will be cleared when saved.</p> : null}
          </fieldset>
        );
      })}
    </section>
  );
}

function ChannelDetails({ channel, sessions, channelsUrl, connected, credentialStorage }) {
  const dispatch = useDispatch();
  const channelState = useSelector(selectChannelsState);
  const test = useSelector((state) => selectChannelTest(state, channel.id));
  const key = useMemo(() => entityDraftKey('channel', channel.id), [channel.id]);
  const [draft, setDraft, controls] = useEntityDraft(key, null);
  const [credentialDrafts, setCredentialDrafts] = useState({});
  const [toggleRequestId, setToggleRequestId] = useState(null);
  const baseline = useMemo(() => settingsToDraft(channel.settings, channel.fields), [channel.fields, channel.settings]);
  const settingsDraft = draft || baseline;
  const toggleRequest = toggleRequestId ? channelState.requests[toggleRequestId] || null : null;
  const reconnectRequest = useMemo(() => requestFor(channelState, channel.id, reconnectChannel.typePrefix), [channelState, channel.id]);
  const testRequest = useMemo(() => requestFor(channelState, channel.id, testChannel.typePrefix), [channelState, channel.id]);
  const { show } = useNotifications();
  // The test result arrives later on `channel.tested`; toast it once for the
  // request this view started (a remount never replays an old result).
  const pendingTestRequestId = useRef(null);
  useEffect(() => {
    const requestId = pendingTestRequestId.current;
    if (!requestId) return;
    if (test?.requestId === requestId) {
      pendingTestRequestId.current = null;
      if (test.ok) show('success', 'Test message sent', `${channel.name} delivered the test message.`);
      else reportFailure(show, 'Send test message', test.error || 'The channel did not deliver the test message.');
      return;
    }
    const request = channelState.requests[requestId];
    if (request?.error) {
      pendingTestRequestId.current = null;
      reportFailure(show, 'Send test message', request.error);
    }
  }, [channel.name, channelState.requests, show, test]);
  const { clear, beginSave, error: saveError, failSave, saving, trackSave } = controls;

  const startEdit = () => setDraft(settingsToDraft(channel.settings, channel.fields));
  const updateSetting = (field, value) => setDraft((current) => ({ ...current, [field]: value }));
  const settingsResult = useMemo(
    () => buildSettingsPatch(settingsDraft, channel.settings, channel.fields),
    [channel.fields, channel.settings, settingsDraft],
  );
  const credentialPatch = useMemo(
    () => buildCredentialPatch(credentialDrafts, channel.credentials || []),
    [channel.credentials, credentialDrafts],
  );
  const buildArgs = useCallback(() => ({
    channelsUrl,
    channelId: channel.id,
    patch: {
      ...(Object.keys(settingsResult.settings).length ? { settings: settingsResult.settings } : {}),
      ...(Object.keys(credentialPatch).length ? { credentials: credentialPatch } : {}),
    },
  }), [channel.id, channelsUrl, credentialPatch, settingsResult.settings]);
  const save = useSnapshotSave({
    dispatch,
    thunk: patchChannel,
    buildArgs,
    path: channel.id,
    content: JSON.stringify({ settings: settingsDraft, credentials: credentialPatch }),
    savedContent: JSON.stringify({ settings: baseline, credentials: {} }),
    saving,
    realtimeConnected: connected,
    beginSave,
    trackSave,
    failSave,
    events: SNAPSHOT_EVENTS,
  });
  const handleSave = () => {
    const errors = Object.values(settingsResult.errors);
    if (errors.length) {
      failSave(beginSave(), errors[0]);
      return;
    }
    if (!Object.keys(settingsResult.settings).length && !Object.keys(credentialPatch).length) {
      clear();
      setCredentialDrafts({});
      return;
    }
    void save().then((accepted) => {
      if (accepted) setCredentialDrafts({});
    });
  };
  const toggle = (enabled) => {
    setToggleRequestId(dispatch(patchChannel({ channelsUrl, channelId: channel.id, patch: { enabled } })).requestId);
  };
  const cancelEdit = () => {
    clear();
    setCredentialDrafts({});
  };
  const reconnect = () => { void dispatch(reconnectChannel({ channelsUrl, channelId: channel.id })); };
  const sendTest = () => {
    pendingTestRequestId.current = dispatch(testChannel({ channelsUrl, channelId: channel.id })).requestId;
  };
  const status = statusPresentation(channel.status);
  const channelSessions = sessions.filter((session) => session.source === channel.session_source);
  const standardFields = channel.fields?.filter((field) => !field.advanced) || [];
  const advancedFields = channel.fields?.filter((field) => field.advanced) || [];

  return (
    <DetailPane className="detail-pane--dense" label={`${channel.name} channel`}>
      <DetailHeader
        title={channel.name}
        actions={(
          <DetailHeaderActions menuLabel="Channel actions">
            <DetailHeaderAction icon={<ReconnectActionIcon />} label="Reconnect" tooltip="Reconnect channel" ariaLabel="Reconnect channel" onClick={reconnect} disabled={!connected || channel.enabled !== true || reconnectRequest?.loading} />
            <DetailHeaderAction icon={<SendActionIcon />} label="Send test" tooltip="Send a test message" ariaLabel="Send a test message" onClick={sendTest} disabled={!connected || channel.enabled !== true || testRequest?.loading} />
            {channel.enabled === true ? (
              <DetailHeaderAction icon={<StopActionIcon />} label="Stop" tooltip="Stop channel" ariaLabel="Stop channel" onClick={() => toggle(false)} disabled={!connected || toggleRequest?.loading} />
            ) : (
              <DetailHeaderAction icon={<PlayActionIcon />} label="Start" tooltip="Start channel" ariaLabel="Start channel" variant="primary" onClick={() => toggle(true)} disabled={!connected || toggleRequest?.loading} />
            )}
          </DetailHeaderActions>
        )}
        meta={[
          <DetailStatus key="status" tone={status.tone}>{status.label}</DetailStatus>,
          <span key="category" className="muted">{channel.category}</span>,
          channel.handle ? <span key="handle" className="muted">{channel.handle}</span> : null,
        ]}
      />
      <div className="detail-pane__body">
        {reconnectRequest?.error ? <InlineFailure failure={reconnectRequest.error} operation="Reconnect channel" /> : null}
        {toggleRequest?.error ? <InlineFailure failure={toggleRequest.error} operation="Update channel" /> : null}
        <div className="agents-metrics">
          <MetricCard label="Inbound" value={Number(channel.stats?.inbound || 0).toLocaleString()} detail="since backend start" />
          <MetricCard label="Outbound" value={Number(channel.stats?.outbound || 0).toLocaleString()} detail="since backend start" />
          <MetricCard label="Conversations" value={channelSessions.length} detail="channel sessions" />
          <MetricCard label="Status" value={status.label} detail={channel.status_detail || 'Current connection state'} />
        </div>
        <section className="agents-section">
          <header className="agents-section__head"><h2>About</h2></header>
          <p className="agents-skill-card__desc">{channel.description || 'No description available.'}</p>
        </section>
        <section className="agents-section">
          <header className="agents-section__head"><h2>Connection facts</h2></header>
          <div className="agents-card-facts">
            <CardFact label="Status">{channel.status_detail || status.label}</CardFact>
            <CardFact label="Handle">{channel.handle || '—'}</CardFact>
            <CardFact label="Credentials">{(channel.credentials || []).map(credentialSourceSummary).join(', ') || 'Not configured'}</CardFact>
          </div>
        </section>
        <section className="agents-section">
          <header className="agents-section__head">
            <h2>Settings</h2>
            {draft ? (
              <div className={styles.sectionActions}>
                <button type="button" className="repo-btn repo-btn--ghost" disabled={saving} onClick={cancelEdit}>Cancel</button>
                <button type="button" className="repo-btn repo-btn--primary" disabled={saving || !connected} onClick={handleSave}>{saving ? 'Saving…' : 'Save'}</button>
              </div>
            ) : (
              <button type="button" className="repo-btn repo-btn--ghost" disabled={!connected} onClick={startEdit}>Edit</button>
            )}
          </header>
          {draft ? (
            <form className="agents-card-facts agents-draft profiles-form" onSubmit={(event) => { event.preventDefault(); handleSave(); }}>
              {standardFields.map((field) => <SettingsField key={field.key} field={field} value={settingsDraft[field.key]} disabled={saving} onChange={(value) => updateSetting(field.key, value)} />)}
              {advancedFields.length ? (
                <details className={styles.advanced}>
                  <summary>Advanced settings</summary>
                  {advancedFields.map((field) => <SettingsField key={field.key} field={field} value={settingsDraft[field.key]} disabled={saving} onChange={(value) => updateSetting(field.key, value)} />)}
                </details>
              ) : null}
              {saveError ? <InlineFailure failure={saveError} operation="Save channel" /> : null}
            </form>
          ) : (
            <SettingsSummary channel={channel} />
          )}
        </section>
        {draft ? (
          <CredentialsEditor channel={channel} databaseAllowed={credentialStorage[CREDENTIAL_SOURCE.DATABASE] === true} drafts={credentialDrafts} setDrafts={setCredentialDrafts} disabled={saving} />
        ) : (
          <section className="agents-section">
            <header className="agents-section__head"><h2>Credentials</h2></header>
            <dl className="agents-card-facts">
              {(channel.credentials || []).map((credential) => (
                <div className="agents-card-fact" key={credential.key}>
                  <dt className="agents-card-fact__label">{credential.label}</dt>
                  <dd className="agents-card-fact__value">{credentialSourceSummary(credential)}</dd>
                </div>
              ))}
            </dl>
          </section>
        )}
        <section className="agents-section">
          <header className="agents-section__head"><h2>Capabilities</h2></header>
          <div className="agents-chip-row">{Object.entries(channel.capabilities || {}).map(([key, on]) => <CapabilityChip key={key} label={capabilityLabel(key)} on={on === true} />)}</div>
        </section>
        <section className="agents-section">
          <header className="agents-section__head"><h2>Linked sessions <span>{channelSessions.length}</span></h2></header>
          <LinkedSessions sessions={channelSessions} />
        </section>
      </div>
    </DetailPane>
  );
}

export default function ChannelsPage() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const channelsUrl = getApiUrl('channels');
  const connected = useSelector(selectRealtimeStreamReady);
  const channels = useSelector(selectChannels);
  const channelState = useSelector(selectChannelsState);
  const sessions = useSelector(selectChatSessions);
  const enrichedChannels = useMemo(() => channels.map((channel) => ({
    ...channel,
    conversationCount: sessions.filter((session) => session.source === channel.session_source).length,
  })), [channels, sessions]);
  const { filter, groups, search, selected, setFilter, setSearch, setSelectedId, visible } = useListSelection(enrichedChannels, { getId: channelId, match: matchesChannel, groupBy: byCategory });

  useEffect(() => {
    if (connected) dispatch(fetchChannels({ channelsUrl }));
  }, [channelsUrl, connected, dispatch]);

  return (
    <PageShell>
      <PaneList label="Channels" className="agents-pane">
        <PaneListFilters filters={CHANNEL_FILTERS} activeFilter={filter} onFilterChange={setFilter} />
        <PaneListToolbar>
          <PaneListSearch value={search} placeholder="Search channels..." onChange={setSearch} />
          <PaneListSortButton>Category</PaneListSortButton>
        </PaneListToolbar>
        {!channelState.loaded && channelState.loading ? <PaneEmptyState>Loading channels…</PaneEmptyState> : null}
        {channelState.error ? <InlineFailure failure={channelState.error} operation="Load channels" onRetry={() => dispatch(fetchChannels({ channelsUrl }))} /> : null}
        {channelState.loaded && !enrichedChannels.length ? <PaneEmptyState>No channels are configured.</PaneEmptyState> : null}
        {visible.length ? (
          <PaneListRows
            groups={groups.order.map((category) => ({ key: category, title: category, items: groups.byKey[category] }))}
            renderItem={(channel) => <ChannelRow key={channel.id} channel={channel} selected={selected?.id === channel.id} onSelect={setSelectedId} />}
          />
        ) : null}
        {channelState.loaded && enrichedChannels.length > 0 && !visible.length ? <PaneEmptyState>No channels match this view.</PaneEmptyState> : null}
      </PaneList>
      {selected ? <ChannelDetails channel={selected} sessions={sessions} channelsUrl={channelsUrl} connected={connected} credentialStorage={channelState.credentialStorage} /> : <DetailPane className="detail-pane--dense" label="Channel"><PaneEmptyState detail>Select a channel to view its details.</PaneEmptyState></DetailPane>}
    </PageShell>
  );
}
