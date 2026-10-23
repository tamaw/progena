import { describe, expect, it } from 'vitest';
import { CHAT_STATUS, deriveChatStatus } from '@frontend/features/chat/models/chatStatus';

describe('deriveChatStatus', () => {
  it.each([
    ['idle when no runtime indicator is set', { generating: false, errored: false, unread: false }, CHAT_STATUS.IDLE],
    ['complete for unread work', { generating: false, errored: false, unread: true }, CHAT_STATUS.COMPLETE],
    ['error for an errored chat', { generating: false, errored: true, unread: false }, CHAT_STATUS.ERROR],
    ['generating for an active chat', { generating: true, errored: false, unread: false }, CHAT_STATUS.GENERATING],
    ['error outranks unread completion', { generating: false, errored: true, unread: true }, CHAT_STATUS.ERROR],
    ['generating outranks an error', { generating: true, errored: true, unread: false }, CHAT_STATUS.GENERATING],
    ['generating outranks every terminal indicator', { generating: true, errored: true, unread: true }, CHAT_STATUS.GENERATING],
  ])('%s', (_description, flags, expected) => {
    expect(deriveChatStatus(flags)).toBe(expected);
  });
});
