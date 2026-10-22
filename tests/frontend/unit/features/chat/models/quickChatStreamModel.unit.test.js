import { describe, expect, it } from 'vitest';
import { createSpectatorReplayState } from '@frontend/features/chat/models/quickChatStreamModel';

describe('createSpectatorReplayState', () => {
  it('reuses a cached streaming bubble identity while replaying its content from empty', () => {
    expect(createSpectatorReplayState({ id: 'assistant-1', content: 'partial reply' }, 'request-1')).toEqual({
      assistantId: 'assistant-1',
      assistantContent: '',
    });
  });
});
