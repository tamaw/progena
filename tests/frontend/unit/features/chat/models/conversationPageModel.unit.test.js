import { describe, expect, it } from 'vitest';
import { normalizeStoredMessage, stripEphemeralStreamEvents } from '@frontend/features/chat/models/conversationPageModel';
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

  it('keeps known zero provider output usage and response status through storage normalization', () => {
    const restored = normalizeStoredMessage({
      id: 'stored-answer',
      role: 'assistant',
      content: 'Done',
      timestamp: NOW,
      tokenCount: 0,
      finalCallTokenCount: 0,
      usageProvenance: 'provider',
      responseStatus: 'completed',
    }, 0);

    expect(restored).toMatchObject({
      tokenCount: 0,
      finalCallTokenCount: 0,
      usageProvenance: 'provider',
      responseStatus: 'completed',
    });
    expect(restored.timelineItems.at(-1)).toMatchObject({
      tokenCount: 0,
      finalCallTokenCount: 0,
      usageProvenance: 'provider',
      responseStatus: 'completed',
    });
  });

  it('preserves interruption reasons through local history hydration', () => {
    const restored = normalizeStoredMessage({
      id: 'stored-cancelled',
      role: 'assistant',
      content: '',
      timestamp: NOW,
      responseStatus: 'cancelled',
      interruptionReason: 'user_cancelled',
    }, 0);

    expect(restored).toMatchObject({
      responseStatus: 'cancelled',
      interruptionReason: 'user_cancelled',
    });
  });

  it('invalidates old serialized chunk counters, including denormalized timelines', () => {
    const restored = normalizeStoredMessage({
      id: 'old-chunk-counter',
      role: 'assistant',
      content: 'Done',
      timestamp: NOW,
      tokenCount: 12,
      finalCallTokenCount: 3,
      timelineItems: [{
        kind: 'assistant_message',
        id: 'old-chunk-counter',
        tokenCount: 12,
        finalCallTokenCount: 3,
      }],
    }, 0, { preserveTimeline: true });

    expect(restored).toMatchObject({
      tokenCount: null,
      finalCallTokenCount: null,
      usageProvenance: null,
      responseStatus: 'unknown',
    });
    expect(restored.timelineItems[0]).not.toHaveProperty('tokenCount');
    expect(restored.timelineItems[0]).not.toHaveProperty('finalCallTokenCount');
  });

  it('persists only provider-provenanced output counts in the local cache', () => {
    const oldSerialized = stripEphemeralStreamEvents({
      id: 'old-cache',
      tokenCount: 8,
      finalCallTokenCount: 2,
    });
    const providerUsage = stripEphemeralStreamEvents({
      id: 'provider-cache',
      tokenCount: 0,
      finalCallTokenCount: 0,
      usageProvenance: 'provider',
    });

    expect(oldSerialized).not.toHaveProperty('tokenCount');
    expect(oldSerialized).not.toHaveProperty('finalCallTokenCount');
    expect(providerUsage).toMatchObject({
      tokenCount: 0,
      finalCallTokenCount: 0,
      usageProvenance: 'provider',
    });
  });
});
