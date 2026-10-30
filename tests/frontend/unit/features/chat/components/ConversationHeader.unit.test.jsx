// @vitest-environment jsdom
import React from 'react';
import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { LiveConversationHeader } from '@frontend/features/chat/components/ConversationHeader';
import { createLiveAssistantTurnStore } from '@frontend/features/chat/models/liveAssistantTurnStore';

function header(messages, cumulativeUsage) {
  return renderToStaticMarkup(<LiveConversationHeader
    liveTurnStore={createLiveAssistantTurnStore({ reasoningTokenCount: 999, reasoningText: 'Uncounted streamed reasoning' })}
    messages={messages} cumulativeUsage={cumulativeUsage} chatActions={[]}
    titleEditor={{}} title="Accounting" chatStatus="idle" threadId="thread-accounting"
  />);
}

describe('conversation lifetime accounting', () => {
  it('keeps the authoritative total across visible pages and does not double count live reasoning', () => {
    const usage = { output_tokens: 78477, reasoning_tokens: 15780, output_coverage: 'complete', reasoning_coverage: 'complete' };
    for (const messages of [[], [{ id: 'latest', role: 'assistant', tokenCount: 65493 }], [{ role: 'assistant', tokenCount: 10 }, { role: 'assistant', tokenCount: 65493 }]]) {
      const markup = header(messages, usage);
      expect(markup).toContain('78,477 output tokens');
      expect(markup).toContain('15,780 reasoning tokens');
      expect(markup).not.toContain('16,779');
    }
  });

  it('does not turn unavailable history into zero or text estimates', () => {
    const markup = header([{ role: 'assistant', tokenCount: 7, reasoningText: 'a'.repeat(100) }], null);
    expect(markup).toContain('output tokens unavailable');
    expect(markup).toContain('reasoning tokens unavailable');
  });

  it('distinguishes incomplete recorded totals from complete provider zero', () => {
    const markup = header([], { output_tokens: 42, output_coverage: 'partial', reasoning_tokens: 0, reasoning_coverage: 'complete' });
    expect(markup).toContain('≥42 recorded output tokens');
    expect(markup).toContain('0 reasoning tokens');
  });
});
