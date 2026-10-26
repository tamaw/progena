import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  STOP_ESCALATION_MS,
  routineHistoryStatus,
  routineRunControlMode,
  routineRunControlTarget,
} from '@frontend/features/routines/models/routineRunControl';

const lifecycle = (status, extra = {}) => ({
  agent_id: 'routine',
  session_id: 'session',
  thread_id: 'routine:routine:revision:session',
  request_id: 'attempt',
  status,
  ...extra,
});

afterEach(() => {
  vi.useRealTimers();
});

describe('routine run control mode', () => {
  it('reveals cancellation escalation exactly five seconds after the local Stop request', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-26T12:00:00.000Z'));
    const startedAt = Date.now();

    expect(routineRunControlMode({
      lifecycle: lifecycle('started'), agentId: 'routine', pendingAction: 'stop', stopRequestedAtMs: startedAt,
    }).kind).toBe('stopping');

    vi.advanceTimersByTime(STOP_ESCALATION_MS - 1);
    expect(routineRunControlMode({
      lifecycle: lifecycle('stopping'), agentId: 'routine', stopRequestedAtMs: startedAt,
    }).kind).toBe('stopping');

    vi.advanceTimersByTime(1);
    expect(routineRunControlMode({
      lifecycle: lifecycle('stopping'), agentId: 'routine', stopRequestedAtMs: startedAt,
    }).kind).toBe('cancel');
  });

  it('targets only an exact lifecycle attempt, never a history row or incomplete event', () => {
    expect(routineRunControlTarget(lifecycle('started'), 'routine')).toEqual({
      threadId: 'routine:routine:revision:session', requestId: 'attempt', sessionId: 'session',
    });
    expect(routineRunControlTarget(lifecycle('started', { thread_id: undefined }), 'routine')).toBeNull();
    expect(routineRunControlTarget(lifecycle('started'), 'another-routine')).toBeNull();
  });

  it('offers native resume only for a confirmed stopped lifecycle, never cancellation', () => {
    expect(routineRunControlMode({ lifecycle: lifecycle('stopped'), agentId: 'routine' }).kind).toBe('resume');
    expect(routineRunControlMode({ lifecycle: lifecycle('cancelled'), agentId: 'routine' })).toBeNull();
  });

  it('uses durable lifecycle status for history badges rather than checkpoint dates', () => {
    const run = { thread_id: 'routine:routine:revision:session', failed: false };
    expect(routineHistoryStatus(run, lifecycle('stopped'))).toBe('stopped');
    expect(routineHistoryStatus(run, lifecycle('cancelled'))).toBe('cancelled');
    expect(routineHistoryStatus(run, undefined)).toBe('success');
  });
});
