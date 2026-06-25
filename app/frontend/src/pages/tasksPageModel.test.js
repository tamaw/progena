import { describe, expect, it } from 'vitest';

import { formatTaskLabel, statusMeta } from './tasksPageModel';

describe('tasksPageModel', () => {
  it('formats task labels consistently', () => {
    expect(formatTaskLabel('builder_progenitor')).toBe('Builder Progenitor');
    expect(formatTaskLabel('review:queue')).toBe('Review Queue');
  });

  it('returns known and fallback status metadata', () => {
    expect(statusMeta('draft')).toEqual({ label: 'Draft', tone: 'muted' });
    expect(statusMeta('complete')).toEqual({ label: 'Complete', tone: 'ok' });
    expect(statusMeta('queued_review')).toEqual({ label: 'Queued Review', tone: 'muted' });
  });
});
