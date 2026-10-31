import { describe, expect, it } from 'vitest';
import { formatContextWindow, isCustomized, profileRowDetail } from '@frontend/features/settings/profilesPageModel';

const base = { name: 'Sonnet', provider_id: 'foundry-1', deployment: 'claude-sonnet' };

describe('isCustomized', () => {
  it('does not count the always-present provider and deployment as customization', () => {
    expect(isCustomized(base)).toBe(false);
  });

  it('counts reasoning or pricing overrides, including an explicit false', () => {
    expect(isCustomized({ ...base, reasoning_effort: 'high' })).toBe(true);
    expect(isCustomized({ ...base, reasoning_enabled: false })).toBe(true);
    expect(isCustomized({ ...base, pricing: { currency: 'USD' } })).toBe(true);
  });
});

describe('formatContextWindow', () => {
  it('compacts token counts and hides missing values', () => {
    expect(formatContextWindow(200_000)).toBe('200K');
    expect(formatContextWindow(1_000_000)).toBe('1M');
    expect(formatContextWindow(1_048_576)).toBe('1M');
    expect(formatContextWindow(400)).toBe('400');
    expect(formatContextWindow(undefined)).toBe('');
  });
});

describe('profileRowDetail', () => {
  it('lists deployment, then reasoning effort and customized only when set', () => {
    expect(profileRowDetail(base)).toBe('claude-sonnet');
    expect(profileRowDetail({ ...base, reasoning_effort: 'high' })).toBe('claude-sonnet · reasoning high · customized');
  });
});
