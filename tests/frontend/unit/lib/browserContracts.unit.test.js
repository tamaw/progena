// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import {
  BROWSER_EVENT,
  QUICK_CHAT_MODE,
  dispatchChatHistoryLoad,
  dispatchQuickChatOpen,
} from '@frontend/lib/browserContracts';

describe('browser event contracts', () => {
  it('delivers unchanged quick-chat request payloads', () => {
    const detail = { mode: QUICK_CHAT_MODE.INBOX_EDIT, subtitle: 'Inbox', prompt: 'Revise' };
    let received;
    window.addEventListener(BROWSER_EVENT.OPEN_QUICK_CHAT, (event) => { received = event.detail; }, { once: true });
    dispatchQuickChatOpen(detail);
    expect(received).toEqual(detail);
  });

  it('keeps promoted chat history payloads available to the destination', () => {
    const detail = { messages: [{ id: 'message-1' }], threadId: 'thread-1' };
    let received;
    window.addEventListener(BROWSER_EVENT.LOAD_CHAT_HISTORY, (event) => { received = event.detail; }, { once: true });
    dispatchChatHistoryLoad(detail);
    expect(received).toEqual(detail);
  });
});
