import { describe, expect, it } from 'vitest';
import {
  latestContextUsage,
  normalizeContextUsage,
  resolveComposerContextUsage,
} from '@frontend/features/chat/models/contextWindowModel';

const general = {
  name: 'general',
  provider_id: 'provider-a',
  deployment: 'gpt-a',
  context_window: 128000,
};

describe('normalizeContextUsage', () => {
  it('normalizes a backend latest-call footprint without aggregating it', () => {
    expect(normalizeContextUsage({
      input_tokens: 101,
      output_tokens: 19,
      total_tokens: 120,
      profile_name: 'general',
      deployment: 'gpt-a',
      provider_id: 'provider-a',
    })).toEqual({
      inputTokens: 101,
      outputTokens: 19,
      totalTokens: 120,
      profileName: 'general',
      deployment: 'gpt-a',
      providerId: 'provider-a',
    });
  });

  it('rejects incomplete or invalid usage rather than deriving a total', () => {
    expect(normalizeContextUsage({ input_tokens: 1, output_tokens: 2 })).toBeNull();
    expect(normalizeContextUsage({ input_tokens: 1, output_tokens: 2, total_tokens: 2.5 })).toBeNull();
  });
});

describe('resolveComposerContextUsage', () => {
  it('matches the exact parent-call profile identity', () => {
    expect(resolveComposerContextUsage({
      contextUsage: {
        inputTokens: 98,
        outputTokens: 2,
        totalTokens: 100,
        profileName: 'general',
        providerId: 'provider-a',
        deployment: 'gpt-a',
      },
      profiles: [general, {
        name: 'research',
        provider_id: 'provider-b',
        deployment: 'gpt-b',
        context_window: 200000,
      }],
    })).toEqual({ usedTokens: 100, maxTokens: 128000 });
  });

  it('hides when a switched profile no longer matches the call identity', () => {
    expect(resolveComposerContextUsage({
      contextUsage: {
        inputTokens: 98,
        outputTokens: 2,
        totalTokens: 100,
        profileName: 'general',
        providerId: 'provider-a',
        deployment: 'gpt-a',
      },
      profiles: [{ ...general, provider_id: 'provider-b', deployment: 'gpt-b', context_window: 200000 }],
    })).toBeNull();
  });

  it('does not invent a limit for ambiguous subagent/provider metadata', () => {
    expect(resolveComposerContextUsage({
      contextUsage: { inputTokens: 10, outputTokens: 5, totalTokens: 15, providerId: 'provider-a' },
      profiles: [general, { ...general, name: 'review' }],
    })).toBeNull();
  });

  it('uses zero only for a fresh conversation with one general/default profile', () => {
    expect(resolveComposerContextUsage({ profiles: [general], isFreshConversation: true }))
      .toEqual({ usedTokens: 0, maxTokens: 128000 });
    expect(resolveComposerContextUsage({ profiles: [general], isFreshConversation: false })).toBeNull();
    expect(resolveComposerContextUsage({
      contextUsage: {
        inputTokens: 10,
        outputTokens: 5,
        totalTokens: 15,
        profileName: 'general',
      },
      profiles: [{ ...general, context_window: 0 }],
    })).toBeNull();
    expect(resolveComposerContextUsage({
      profiles: [general, { ...general, name: 'default' }],
      isFreshConversation: true,
    })).toBeNull();
  });
});

describe('latestContextUsage', () => {
  it('uses the newest footprint, never a sum over assistant messages', () => {
    expect(latestContextUsage([
      { role: 'assistant', contextUsage: { inputTokens: 90, outputTokens: 10, totalTokens: 100 } },
      { role: 'assistant', contextUsage: { inputTokens: 150, outputTokens: 25, totalTokens: 175 } },
    ])).toMatchObject({ totalTokens: 175 });
  });
});
