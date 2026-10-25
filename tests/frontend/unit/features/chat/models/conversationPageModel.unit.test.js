import { describe, expect, it } from 'vitest';
import { normalizeStoredMessage } from '@frontend/features/chat/models/conversationPageModel';
import {
  applyAssistantChunk,
  createLiveAssistantTurn,
  finalizeLiveAssistantTurn,
} from '@frontend/features/chat/models/liveAssistantTurnModel';

const NOW = 1_700_000_000_000;

function failedTurnWithPartialAnswer() {
  const turn = applyAssistantChunk(createLiveAssistantTurn({ streamId: 's1', startedAt: NOW }), 'partial answer', NOW);
  return finalizeLiveAssistantTurn(turn, { status: 'error', now: NOW + 10, errorMessage: 'quota' });
}

describe('normalizeStoredMessage', () => {
  it('keeps the partial answer of a failed turn for an in-memory handoff', () => {
    const message = failedTurnWithPartialAnswer();
    expect(message.content).toBe('Error: quota');

    const promoted = normalizeStoredMessage(message, 0, { preserveTimeline: true });
    expect(JSON.stringify(promoted.timelineItems)).toContain('partial answer');
  });

  it('rebuilds stored timelines from persisted fields by default', () => {
    const restored = normalizeStoredMessage(failedTurnWithPartialAnswer(), 0);
    expect(JSON.stringify(restored.timelineItems)).not.toContain('partial answer');
  });
});
