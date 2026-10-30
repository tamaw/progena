import { describe, expect, it } from 'vitest';
import {
  applyConversationSummaryActivity,
  buildConversationSummaryEntries,
  mergeConversationSummaryMarkers,
} from '@frontend/features/chat/models/conversationSummaryModel';

const rootMarker = {
  id: 'summary-1',
  anchor_message_id: 'message-1',
  summary: '## Earlier context\n\nSafe **summary**.',
  scope: { kind: 'conversation', id: 'thread-1' },
};

describe('conversation summary markers', () => {
  it('places a committed marker at its creation boundary without replacing history', () => {
    const messages = [
      { id: 'message-1', role: 'user', content: 'First' },
      { id: 'message-2', role: 'assistant', content: 'Second' },
    ];

    const entries = buildConversationSummaryEntries(messages, [rootMarker]);

    expect(entries.map((entry) => entry.kind)).toEqual(['message', 'conversation_summary', 'message']);
    expect(entries[0].message).toBe(messages[0]);
    expect(entries[2].message).toBe(messages[1]);
    expect(entries[1].marker.summary).toBe(rootMarker.summary);
  });

  it('dedupes page overlap by stable id and waits for an unloaded anchor', () => {
    const newer = { ...rootMarker, summary: 'Updated summary' };
    const merged = mergeConversationSummaryMarkers([rootMarker], [newer]);

    expect(merged).toEqual([expect.objectContaining({
      id: 'summary-1',
      summary: 'Updated summary',
      anchorMessageId: 'message-1',
    })]);
    expect(buildConversationSummaryEntries([{ id: 'message-2' }], merged)).toEqual([
      expect.objectContaining({ kind: 'message' }),
    ]);
  });

  it('retains marker and array identity for equal canonical reconciliation', () => {
    const current = mergeConversationSummaryMarkers([], [rootMarker]);
    const reconciled = mergeConversationSummaryMarkers(current, [{ ...rootMarker }]);

    expect(reconciled).toBe(current);
    expect(reconciled[0]).toBe(current[0]);
  });

  it('retains unchanged descriptors when another marker arrives', () => {
    const current = mergeConversationSummaryMarkers([], [rootMarker]);
    const reconciled = mergeConversationSummaryMarkers(current, [{
      ...rootMarker,
      id: 'summary-2',
      anchor_message_id: 'message-2',
    }]);

    expect(reconciled).not.toBe(current);
    expect(reconciled[0]).toBe(current[0]);
    expect(reconciled[1]).toMatchObject({ id: 'summary-2' });
  });

  it('reserves subagent-scope markers for their matching task card', () => {
    const entries = buildConversationSummaryEntries(
      [{ id: 'message-1', role: 'assistant', content: 'Delegating' }],
      [{
        ...rootMarker,
        id: 'summary-subagent',
        scope: { kind: 'subagent', id: 'task-tool-call-1', label: 'Researcher' },
      }],
    );

    expect(entries).toEqual([expect.objectContaining({ kind: 'message' })]);
  });

  it('tracks native callback activity separately from committed state', () => {
    const started = applyConversationSummaryActivity([], {
      event_type: 'conversation_summary_started',
      metadata: { summary_id: 'callback-1', scope: { kind: 'subagent', id: 'task-1', label: 'Researcher' } },
    });
    const repeated = applyConversationSummaryActivity(started, {
      event_type: 'conversation_summary_started',
      metadata: { summary_id: 'callback-1', scope: { kind: 'subagent', id: 'task-1', label: 'Researcher' } },
    });
    const finished = applyConversationSummaryActivity(repeated, {
      event_type: 'conversation_summary_finished',
      metadata: { summary_id: 'callback-1', scope: { kind: 'subagent', id: 'task-1', label: 'Researcher' } },
    });

    expect(repeated).toBe(started);
    expect(finished).toEqual([]);
  });
});
