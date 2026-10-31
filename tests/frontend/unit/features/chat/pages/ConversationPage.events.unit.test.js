import { describe, expect, it, vi } from 'vitest';
import { reconcileIncomingChatEvent } from '@frontend/features/chat/models/chatEventReconciliation';

describe('reconcileIncomingChatEvent', () => {
  it('reloads the matching destination without synthesizing a transcript message', () => {
    const reloadOpenChat = vi.fn();
    const show = vi.fn();

    reconcileIncomingChatEvent({
      event: {
        type: 'scheduled_response',
        data: { content: 'Finished', metadata: { thread_id: 'chat-a' } },
      },
      openThreadId: 'chat-a',
      isGenerating: false,
      reloadOpenChat,
      show,
    });

    expect(reloadOpenChat).toHaveBeenCalledWith('chat-a');
    expect(show).not.toHaveBeenCalled();
  });

  it('does not reload the open transcript for another destination', () => {
    const reloadOpenChat = vi.fn();
    const show = vi.fn();

    reconcileIncomingChatEvent({
      event: {
        type: 'agent_message',
        data: { content: 'Finished elsewhere', metadata: { thread_id: 'chat-b' } },
      },
      openThreadId: 'chat-a',
      isGenerating: false,
      reloadOpenChat,
      show,
    });

    expect(reloadOpenChat).not.toHaveBeenCalled();
    expect(show).toHaveBeenCalledWith('info', 'New chat activity', 'Finished elsewhere');
  });
});
