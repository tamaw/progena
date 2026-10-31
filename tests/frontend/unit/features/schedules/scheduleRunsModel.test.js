import { describe, expect, it } from 'vitest';
import { scheduleRunStatus } from '@frontend/features/schedules/scheduleRunsModel';

describe('scheduleRunStatus', () => {
  it('maps scheduler run states to shared status tones', () => {
    expect(scheduleRunStatus('completed')).toEqual({ label: 'Completed', tone: 'idle' });
    expect(scheduleRunStatus('running')).toEqual({ label: 'Running', tone: 'ok' });
    expect(scheduleRunStatus('queued')).toEqual({ label: 'Queued', tone: 'info' });
    expect(scheduleRunStatus('error')).toEqual({ label: 'Failed', tone: 'error' });
    expect(scheduleRunStatus('interrupted')).toEqual({ label: 'Interrupted', tone: 'warning' });
  });

  it('flags missing or unrecognised states as unknown', () => {
    expect(scheduleRunStatus(undefined)).toEqual({ label: 'Unknown', tone: 'warning' });
    expect(scheduleRunStatus('paused')).toEqual({ label: 'Unknown', tone: 'warning' });
  });
});
