import { describe, expect, it } from 'vitest';

import {
  formatCompactRelativeAge,
  formatShortLocalDateTime,
  parseDateOrNull,
} from '@frontend/lib/displayTime';

const now = new Date('2026-06-27T12:00:00Z').getTime();

describe('parseDateOrNull', () => {
  it('returns null for missing, invalid, or unsupported values', () => {
    expect(parseDateOrNull()).toBeNull();
    expect(parseDateOrNull('not-a-date')).toBeNull();
    expect(parseDateOrNull(Symbol('date'))).toBeNull();
  });

  it('returns an independent valid date', () => {
    const original = new Date('2026-06-27T12:00:00Z');
    const parsed = parseDateOrNull(original);
    expect(parsed).toEqual(original);
    expect(parsed).not.toBe(original);
  });
});

describe('formatCompactRelativeAge', () => {
  it('uses compact labels at each relative boundary and clamps future times', () => {
    expect(formatCompactRelativeAge(null, now)).toBe('--');
    expect(formatCompactRelativeAge('not-a-date', now)).toBe('--');
    expect(formatCompactRelativeAge(now - 59_999, now)).toBe('now');
    expect(formatCompactRelativeAge(now - 60_000, now)).toBe('1m');
    expect(formatCompactRelativeAge(now - 60 * 60_000, now)).toBe('1h');
    expect(formatCompactRelativeAge(now - 24 * 60 * 60_000, now)).toBe('1d');
    expect(formatCompactRelativeAge(now + 60 * 60_000, now)).toBe('now');
  });

  it('switches from days to a local date after seven days', () => {
    const sixDays = new Date(now - 6 * 24 * 60 * 60_000);
    const sevenDays = new Date(now - 7 * 24 * 60 * 60_000);
    expect(formatCompactRelativeAge(sixDays, now)).toBe('6d');
    expect(formatCompactRelativeAge(sevenDays, now)).toBe(
      sevenDays.toLocaleDateString([], { month: 'short', day: 'numeric' }),
    );
  });
});

describe('formatShortLocalDateTime', () => {
  it('formats valid values and leaves invalid values blank', () => {
    const value = '2026-06-27T12:00:00Z';
    expect(formatShortLocalDateTime(value)).toBe(new Date(value).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    }));
    expect(formatShortLocalDateTime('not-a-date')).toBe('');
  });
});
