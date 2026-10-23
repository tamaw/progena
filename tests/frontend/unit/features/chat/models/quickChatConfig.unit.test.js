import { describe, expect, it } from 'vitest';
import { normalizeAiPrompt } from '@frontend/features/chat/models/quickChatConfig';

describe('normalizeAiPrompt', () => {
  it.each([
    ['trims surrounding spaces before handoff', '  summarize the workspace  ', 'summarize the workspace'],
    ['trims tabs and newlines before handoff', '\tfind the failing build\n', 'find the failing build'],
    ['ignores an empty prompt', '', ''],
    ['ignores a whitespace-only prompt', ' \t\n ', ''],
    ['ignores non-string form values', null, ''],
    ['ignores non-string objects', { prompt: 'summarize the workspace' }, ''],
    ['ignores numeric form values', 42, ''],
  ])('%s', (_caseName, input, expected) => {
    expect(normalizeAiPrompt(input)).toBe(expected);
  });
});
