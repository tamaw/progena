/** Credential sources stored in channel documents (wire values). */
export const CREDENTIAL_SOURCE = Object.freeze({ ENVIRONMENT: 'environment', DATABASE: 'database' });

// Backend ChannelState values (src/backend/channels/base.py) -> shared status
// tone, used by both the list dot and the header status.
const STATUS_DETAILS = {
  connected: { label: 'Connected', tone: 'idle' },
  connecting: { label: 'Connecting', tone: 'ok' },
  disconnected: { label: 'Disconnected', tone: 'warning' },
  error: { label: 'Error', tone: 'error' },
  disabled: { label: 'Disabled', tone: 'muted' },
  unconfigured: { label: 'Needs configuration', tone: 'warning' },
  unsupported: { label: 'Unsupported', tone: 'warning' },
};

const DEFAULT_STATUS = { label: 'Unknown', tone: 'warning' };

export function statusPresentation(status) {
  return STATUS_DETAILS[status] || DEFAULT_STATUS;
}

export const CHANNEL_FILTERS = ['All', 'Started', 'Stopped', 'Errored'];

const RUNNING_STATUSES = ['connected', 'connecting'];

/** Started = turned on and running; Stopped = turned off; Errored = turned on but not running. */
export function channelRunState(channel) {
  if (channel.enabled !== true) return 'Stopped';
  return RUNNING_STATUSES.includes(channel.status) ? 'Started' : 'Errored';
}

export function matchesChannel(channel, { filter, search }) {
  if (filter !== 'All' && channelRunState(channel) !== filter) return false;
  if (!search) return true;
  const query = search.trim().toLocaleLowerCase();
  if (!query) return true;
  return `${channel.name || ''} ${channel.handle || ''} ${channel.category || ''} ${channel.type || ''}`
    .toLocaleLowerCase()
    .includes(query);
}

export function settingsToDraft(settings = {}, fields = []) {
  return Object.fromEntries(fields.map((field) => {
    const value = settings[field.key];
    if (field.kind === 'list') return [field.key, Array.isArray(value) ? value.join(', ') : ''];
    if (field.kind === 'boolean') return [field.key, value === true];
    return [field.key, value ?? ''];
  }));
}

function parseNumber(value, field) {
  const parsed = typeof value === 'number' ? value : Number(String(value).trim());
  if (!Number.isFinite(parsed) || (field.kind === 'integer' && !Number.isInteger(parsed))) {
    return { error: `${field.label || field.key} must be a ${field.kind === 'integer' ? 'whole number' : 'number'}.` };
  }
  if (Number.isFinite(field.min) && parsed < field.min) {
    return { error: `${field.label || field.key} must be at least ${field.min}.` };
  }
  if (Number.isFinite(field.max) && parsed > field.max) {
    return { error: `${field.label || field.key} must be at most ${field.max}.` };
  }
  return { value: parsed };
}

function settingsValue(value, field) {
  if (field.kind === 'list') {
    return { value: String(value ?? '').split(/[\n,]/).map((entry) => entry.trim()).filter(Boolean) };
  }
  if (field.kind === 'integer' || field.kind === 'number') return parseNumber(value, field);
  if (field.kind === 'boolean') return { value: value === true };
  const text = String(value ?? '');
  if (field.required && !text.trim()) return { error: `${field.label || field.key} is required.` };
  return { value: text };
}

/**
 * Converts a generic settings form draft into a minimal PATCH settings object.
 * Invalid fields are left out and reported without changing unrelated values.
 */
export function buildSettingsPatch(draft, baseline = {}, fields = []) {
  const settings = {};
  const errors = {};
  for (const field of fields) {
    const parsed = settingsValue(draft?.[field.key], field);
    if (parsed.error) {
      errors[field.key] = parsed.error;
      continue;
    }
    if (JSON.stringify(parsed.value) !== JSON.stringify(baseline?.[field.key])) {
      settings[field.key] = parsed.value;
    }
  }
  return { settings, errors };
}

/** Build only credential changes. Write-only database values never enter Redux. */
export function buildCredentialPatch(drafts = {}, credentials = []) {
  const patch = {};
  for (const credential of credentials) {
    const draft = drafts[credential.key];
    if (!draft) continue;
    if (draft.clear) {
      patch[credential.key] = null;
      continue;
    }
    if (draft.source === CREDENTIAL_SOURCE.ENVIRONMENT) {
      const envVar = String(draft.envVar ?? '').trim();
      if (envVar && (credential.source !== CREDENTIAL_SOURCE.ENVIRONMENT || credential.env_var !== envVar)) {
        patch[credential.key] = { source: CREDENTIAL_SOURCE.ENVIRONMENT, env_var: envVar };
      }
      continue;
    }
    const value = typeof draft.value === 'string' ? draft.value : '';
    if (draft.source === CREDENTIAL_SOURCE.DATABASE && value) {
      patch[credential.key] = { source: CREDENTIAL_SOURCE.DATABASE, value };
    }
  }
  return patch;
}

export function credentialSourceSummary(credential) {
  if (credential?.source === CREDENTIAL_SOURCE.ENVIRONMENT) return credential.env_var ? `Environment · ${credential.env_var}` : 'Environment';
  if (credential?.source === CREDENTIAL_SOURCE.DATABASE) return 'Stored encrypted';
  return 'Not configured';
}
