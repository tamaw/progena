import { describe, expect, it } from 'vitest';
import {
  buildCredentialPatch, buildSettingsPatch, matchesChannel, settingsToDraft,
} from '@frontend/features/channels/channelModel';

describe('channel list filters', () => {
  const on = (status) => ({ enabled: true, status });
  it.each([
    ['Started', on('connected')],
    ['Started', on('connecting')],
    ['Errored', on('error')],
    ['Errored', on('unconfigured')],
    ['Errored', on('disconnected')],
    ['Stopped', { enabled: false, status: 'disabled' }],
  ])('puts the channel only under %s', (expected, channel) => {
    for (const filter of ['Started', 'Stopped', 'Errored']) {
      expect(matchesChannel(channel, { filter, search: '' })).toBe(filter === expected);
    }
    expect(matchesChannel(channel, { filter: 'All', search: '' })).toBe(true);
  });
});

const fields = [
  { key: 'chat_id', label: 'Chat ID', kind: 'text' },
  { key: 'allowed_chat_ids', label: 'Allowed chat IDs', kind: 'list' },
  { key: 'timeout', label: 'Timeout', kind: 'integer', min: 1, max: 30 },
  { key: 'polling_interval', label: 'Polling interval', kind: 'number', min: 0.5, max: 10 },
  { key: 'enabled', label: 'Polling enabled', kind: 'boolean' },
];

describe('channel settings patches', () => {
  it('sends only changed settings after converting a draft', () => {
    const baseline = {
      chat_id: '123', allowed_chat_ids: ['a'], timeout: 10, polling_interval: 2, enabled: true,
    };
    const draft = settingsToDraft(baseline, fields);
    draft.chat_id = '456';

    expect(buildSettingsPatch(draft, baseline, fields)).toEqual({
      settings: { chat_id: '456' }, errors: {},
    });
  });

  it('splits comma and newline list values while dropping whitespace', () => {
    const result = buildSettingsPatch(
      { chat_id: '', allowed_chat_ids: ' alpha, beta\n\n gamma ', timeout: 10, polling_interval: 2, enabled: false },
      { chat_id: '', allowed_chat_ids: [], timeout: 10, polling_interval: 2, enabled: false },
      fields,
    );

    expect(result).toEqual({ settings: { allowed_chat_ids: ['alpha', 'beta', 'gamma'] }, errors: {} });
  });

  it('rejects invalid and out-of-range numeric settings', () => {
    const result = buildSettingsPatch(
      { chat_id: '', allowed_chat_ids: '', timeout: '2.5', polling_interval: '12', enabled: false },
      { chat_id: '', allowed_chat_ids: [], timeout: 1, polling_interval: 1, enabled: false },
      fields,
    );
    expect(result.settings).toEqual({});
    expect(result.errors).toEqual({
      timeout: 'Timeout must be a whole number.',
      polling_interval: 'Polling interval must be at most 10.',
    });
  });
});

describe('channel credential patches', () => {
  const credentials = [
    { key: 'bot_token', source: 'environment', env_var: 'TELEGRAM_BOT_TOKEN', configured: true },
    { key: 'webhook_secret', source: 'database', env_var: null, configured: true },
  ];

  it('builds environment, write-only database, and clear patch shapes', () => {
    expect(buildCredentialPatch({
      bot_token: { source: 'environment', envVar: 'NEW_TELEGRAM_TOKEN' },
      webhook_secret: { source: 'database', value: 'private-value' },
    }, credentials)).toEqual({
      bot_token: { source: 'environment', env_var: 'NEW_TELEGRAM_TOKEN' },
      webhook_secret: { source: 'database', value: 'private-value' },
    });

    expect(buildCredentialPatch({
      bot_token: { clear: true },
    }, credentials)).toEqual({ bot_token: null });
  });
});
