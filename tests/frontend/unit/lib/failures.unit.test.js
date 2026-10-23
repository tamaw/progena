import { describe, expect, it, vi } from 'vitest';
import { describeFailureDetail, failureMessage, reportFailure } from '@frontend/lib/failures';

describe('failure presentation boundaries', () => {
  it('extracts nested field failures without leaking the validation payload', () => {
    expect(describeFailureDetail([
      { loc: ['body', 'agents', 0, 'name'], msg: 'Required', input: { secret: 'private' } },
      { loc: ['body', 'timeout'], msg: 'Must be positive' },
    ])).toBe('agents.0.name: Required\ntimeout: Must be positive');
    expect(describeFailureDetail({ secret: 'private' })).toBe('');
  });

  it('applies the same authorization policy to native and Redux-serialized errors', () => {
    for (const status of [401, 403]) {
      const server = { status, message: 'Sensitive server detail' };
      const serialized = { code: `HTTP_${status}`, message: server.message };
      expect(failureMessage(server, 'Delete item')).toEqual(failureMessage(serialized, 'Delete item'));
      expect(failureMessage(server).message).not.toContain(server.message);
    }
  });

  it('keeps actionable application failures and suppresses cancellation toasts', () => {
    const show = vi.fn();
    reportFailure(show, 'Save item', new DOMException('Aborted', 'AbortError'));
    reportFailure(show, 'Save item', { code: 'ABORT_ERR' });
    expect(show).not.toHaveBeenCalled();
    const validation = failureMessage({ detail: 'Name already exists' }, 'Save item');
    expect(validation.message).toBe('Name already exists');
    expect(failureMessage(validation)).toBe(validation);
  });
});
