import { describe, expect, it } from 'vitest';
import {
  latestContextUsage,
  cacheCountdownLabel,
  resolveComposerCacheExpiry,
  normalizeContextUsage,
  resolveComposerContextUsage,
} from '@frontend/features/chat/models/contextWindowModel';

import { normalizeBackendChatMessage } from '@frontend/features/chat/models/chatSessionsModel';
import { createLiveAssistantTurn, finalizeLiveAssistantTurn } from '@frontend/features/chat/models/liveAssistantTurnModel';
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

  it('retains comparable compaction evidence through canonical normalization', () => {
    const usage = normalizeContextUsage({
      input_tokens: 12000, output_tokens: 500, total_tokens: 12500,
      profile_name: 'general', provider_id: 'provider-a', deployment: 'gpt-a',
      compaction: {
        before_input_tokens: 80000, after_input_tokens: 12000, freed_input_tokens: 68000,
        measurement: 'comparable_provider_footprints',
      },
    });
    const canonical = normalizeContextUsage(usage);
    expect(resolveComposerContextUsage({ contextUsage: canonical, profiles: [general] })).toMatchObject({
      usedTokens: 12500, compaction: { freedInputTokens: 68000 },
    });
  });

  it('does not advertise inconsistent or unmeasured savings', () => {
    for (const compaction of [
      { before_input_tokens: 80000, after_input_tokens: 12000, freed_input_tokens: 70000, measurement: 'comparable_provider_footprints' },
      { before_input_tokens: 80000, after_input_tokens: 12000, freed_input_tokens: 68000 },
    ]) {
      expect(normalizeContextUsage({ input_tokens: 12000, output_tokens: 500, total_tokens: 12500, compaction }))
        .not.toHaveProperty('compaction');
    }
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

describe('profile cache retention estimates', () => {
  const responseAt = Date.UTC(2026, 0, 1);
  const usage = { inputTokens: 100, outputTokens: 20, totalTokens: 120, profileName: 'general', providerId: 'provider-a', deployment: 'gpt-a' };
  const profile = { ...general, cache_ttl_seconds: 3600 };
  const message = { role: 'assistant', content: 'Answer', contextUsage: usage, responseTimestamp: responseAt };

  it('uses the matching profile duration, not the context capacity or current time', () => {
    expect(resolveComposerCacheExpiry({ messages: [message], profiles: [profile] }))
      .toEqual({ expiresAt: responseAt + 3600000, ttlSeconds: 3600 });
    expect(resolveComposerCacheExpiry({ messages: [message], profiles: [general] })).toBeNull();
    expect(resolveComposerCacheExpiry({ messages: [message], profiles: [{ ...profile, provider_id: 'other' }] })).toBeNull();
  });

  it('never turns a missing historical timestamp into a fresh countdown on reload', () => {
    const reloaded = normalizeBackendChatMessage({ role: 'assistant', content: 'Answer', response_status: 'completed', context_usage: usage });
    expect(resolveComposerCacheExpiry({ messages: [reloaded], profiles: [profile] })).toBeNull();
    const saved = normalizeBackendChatMessage({ role: 'assistant', content: 'Answer', response_status: 'completed', created_at: new Date(responseAt).toISOString(), context_usage: usage });
    expect(resolveComposerCacheExpiry({ messages: [saved], profiles: [profile] }).expiresAt).toBe(responseAt + 3600000);
  });

  it('starts a new live countdown on successful completion, not partial or cancelled output', () => {
    const turn = { ...createLiveAssistantTurn({ streamId: 'cache', startedAt: responseAt - 10000 }), content: 'Answer', contextUsage: usage };
    const complete = finalizeLiveAssistantTurn(turn, { status: 'completed', now: responseAt });
    const cancelled = finalizeLiveAssistantTurn(turn, { status: 'cancelled', now: responseAt });
    expect(resolveComposerCacheExpiry({ messages: [complete], profiles: [profile] }).expiresAt).toBe(responseAt + 3600000);
    expect(resolveComposerCacheExpiry({ messages: [cancelled], profiles: [profile] })).toBeNull();
    expect(resolveComposerCacheExpiry({ messages: [{ ...message, isStreaming: true }], profiles: [profile] })).toBeNull();
  });

  it('does not use cancelled or tool-only historical rows as responses', () => {
    for (const raw of [
      { content: 'Partial answer', response_metadata: { status: 'cancelled' } },
      { content: '', tool_calls: [{ id: 'tool', name: 'read_file', args: {} }] },
    ]) {
      const saved = normalizeBackendChatMessage({
        role: 'assistant', created_at: new Date(responseAt).toISOString(), context_usage: usage, raw,
        content: raw.content,
      });
      expect(resolveComposerCacheExpiry({ messages: [saved], profiles: [profile] })).toBeNull();
    }
  });

  it('formats minute boundaries without negative remaining time', () => {
    expect(cacheCountdownLabel(responseAt + 58 * 60000, responseAt)).toBe('58m');
    expect(cacheCountdownLabel(responseAt + 1, responseAt)).toBe('1m');
    expect(cacheCountdownLabel(responseAt, responseAt)).toBe('0m');
    expect(cacheCountdownLabel(responseAt - 1000, responseAt)).toBe('0m');
    expect(cacheCountdownLabel(responseAt + 12 * 3600000, responseAt)).toBe('12h');
  });
});
