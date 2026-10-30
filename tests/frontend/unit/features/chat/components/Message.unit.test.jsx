// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Message } from '@frontend/features/chat/components/Message';

vi.mock('react-redux', () => ({
  useSelector: (selector) => selector({ uiData: { preferences: { agentName: 'Progena' } } }),
}));

let host;
let root;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
  });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

function tool(id, eventType, timestamp, toolName = 'read_file', metadata = {}) {
  return {
    kind: 'stream_event',
    id,
    timestamp,
    event: {
      id,
      eventType,
      timestamp,
      content: '',
      metadata: {
        tool_name: toolName,
        tool_call_id: `${id}-call`,
        args_preview: { path: `/${id}` },
        ...metadata,
      },
    },
  };
}

function answer(content, timestamp) {
  return {
    kind: 'assistant_message',
    id: 'answer',
    content,
    timestamp,
    isStreaming: false,
    error: false,
  };
}

function message(timelineItems, overrides = {}) {
  return {
    id: 'turn-1',
    role: 'assistant',
    content: '',
    responseStatus: 'completed',
    timelineItems,
    ...overrides,
  };
}

async function renderMessage(value, props = {}) {
  await act(async () => root.render(<Message message={value} {...props} />));
}

function assistantWork() {
  const section = host.querySelector('section[aria-label="Assistant work"]');
  expect(section).not.toBeNull();
  return section;
}

async function openOuterWork(section) {
  const toggle = section.querySelector('button[aria-expanded="false"]');
  expect(toggle).not.toBeNull();
  await act(async () => toggle.click());
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
}

async function openAllInnerGroups(section) {
  const toggles = [...section.querySelectorAll('.work-group__toggle')];
  await act(async () => toggles.forEach((toggle) => toggle.click()));
  return toggles;
}

describe('assistant turn work disclosure', () => {
  it('keeps 38 reasoning sections behind one collapsed assistant-work disclosure', async () => {
    const timelineItems = Array.from({ length: 38 }, (_, index) => ({
      kind: 'stream_event',
      id: `reasoning-${index + 1}`,
      timestamp: (index + 1) * 1_000,
      event: {
        id: `reasoning-${index + 1}`,
        eventType: 'reasoning_delta',
        timestamp: (index + 1) * 1_000,
        content: `**Reasoning ${index + 1}**\n\nStep ${index + 1}.`,
        metadata: {},
      },
    }));

    await renderMessage(message(timelineItems));

    const section = assistantWork();
    expect(section.querySelector('button[aria-expanded="false"]')).not.toBeNull();
    expect(section.querySelectorAll('.work-group__toggle')).toHaveLength(0);
    expect(section.textContent).not.toContain('Reasoning 38');
  });

  it('opens every reasoning section and its associated tool exactly once', async () => {
    const timelineItems = Array.from({ length: 38 }, (_, index) => {
      const number = index + 1;
      const timestamp = number * 2_000;
      return [
        {
          kind: 'stream_event',
          id: `reasoning-${number}`,
          timestamp,
          event: {
            id: `reasoning-${number}`,
            eventType: 'reasoning_delta',
            timestamp,
            content: `**Reasoning ${number}**\n\nStep ${number}.`,
            metadata: {},
          },
        },
        tool(`tool-${number}-start`, 'tool_start', timestamp + 100, 'read_file', {
          tool_call_id: `tool-${number}`,
          args_preview: { path: `/file-${number}` },
        }),
        tool(`tool-${number}-result`, 'tool_result', timestamp + 200, 'read_file', {
          tool_call_id: `tool-${number}`,
          result_preview: `Read ${number}`,
        }),
      ];
    }).flat();

    await renderMessage(message(timelineItems));

    const section = assistantWork();
    await openOuterWork(section);
    const innerToggles = await openAllInnerGroups(section);

    expect(innerToggles).toHaveLength(38);
    expect(section.querySelectorAll('.tool-call-entry')).toHaveLength(38);
    expect(section.textContent.match(/Reasoning 38/g)).toHaveLength(1);
    expect(section.textContent.match(/path: \/file-38/g)).toHaveLength(1);
  });

  it('keeps delegated-agent cards and the answer visible outside closed root work', async () => {
    await renderMessage(message([
      tool('root-start', 'tool_start', 1_000),
      {
        kind: 'subagent',
        id: 'subagent-1',
        toolCallId: 'subagent-call',
        subagentName: 'researcher',
        taskSummary: '',
        status: 'success',
        entries: [],
        resultPreview: 'Research complete',
      },
      {
        kind: 'a2a_call',
        id: 'a2a-1',
        agentName: 'remote analyst',
        status: 'success',
        messages: [],
        resultPreview: 'Remote analysis complete',
      },
      answer('Final answer remains readable.', 5_000),
    ]));

    const section = assistantWork();
    const subagent = host.querySelector('.subagent-card');
    const remoteAgent = host.querySelector('.a2a-call-card');
    const finalAnswer = [...host.querySelectorAll('.msg--assistant')]
      .find((element) => element.textContent.includes('Final answer remains readable.'));

    expect(section.querySelector('button[aria-expanded="false"]')).not.toBeNull();
    expect(subagent).not.toBeNull();
    expect(remoteAgent).not.toBeNull();
    expect(finalAnswer).not.toBeNull();
    expect(section.contains(subagent)).toBe(false);
    expect(section.contains(remoteAgent)).toBe(false);
    expect(section.contains(finalAnswer)).toBe(false);
  });

  it('keeps each reasoning section before the tool work it led to after expansion', async () => {
    await renderMessage(message([
      {
        kind: 'stream_event',
        id: 'reasoning-first',
        timestamp: 1_000,
        event: {
          id: 'reasoning-first',
          eventType: 'reasoning_delta',
          timestamp: 1_000,
          content: '**Inspect files**\n\nRead configuration first.',
          metadata: {},
        },
      },
      tool('read-start', 'tool_start', 1_100, 'read_file', { tool_call_id: 'read' }),
      tool('read-result', 'tool_result', 1_200, 'read_file', { tool_call_id: 'read', result_preview: 'Found config' }),
      {
        kind: 'stream_event',
        id: 'reasoning-second',
        timestamp: 1_300,
        event: {
          id: 'reasoning-second',
          eventType: 'reasoning_delta',
          timestamp: 1_300,
          content: '**Search references**\n\nThen find callers.',
          metadata: {},
        },
      },
      tool('search-start', 'tool_start', 1_400, 'search_web', { tool_call_id: 'search' }),
      tool('search-result', 'tool_result', 1_500, 'search_web', { tool_call_id: 'search', result_preview: 'Found callers' }),
    ]));

    const section = assistantWork();
    await openOuterWork(section);
    await openAllInnerGroups(section);

    const chronology = ['Inspect files', 'Read File', 'Search references', 'Search Web']
      .map((text) => section.textContent.indexOf(text));
    expect(chronology.every((position) => position >= 0)).toBe(true);
    expect([...chronology].sort((left, right) => left - right)).toEqual(chronology);
  });

  it('shows failed-tool evidence without turning a completed turn into a failed turn', async () => {
    await renderMessage(message([
      tool('failed-start', 'tool_start', 1_000, 'read_file', { tool_call_id: 'failed' }),
      tool('failed-result', 'tool_result', 2_000, 'read_file', {
        tool_call_id: 'failed',
        success: false,
        error: 'Permission denied',
      }),
      answer('Recovered and completed the request.', 3_000),
    ]));

    const section = assistantWork();
    expect(section.textContent).toContain('Worked for 2s');
    expect(section.textContent).toContain('1 failed');
    expect(section.textContent).not.toContain('Work failed');

    await openOuterWork(section);
    await openAllInnerGroups(section);

    expect(section.querySelector('.tool-call-entry--error')).not.toBeNull();
    expect(section.textContent).toContain('Permission denied');
  });
});


describe('terminal replies without tool work', () => {
  it.each(['failed', 'cancelled', 'interrupted'])(
    'keeps provider %s visible even when there is no work disclosure',
    async (status) => {
      await renderMessage(message(undefined, {
        content: 'Partial answer',
        responseStatus: status,
      }));
      expect(host.querySelector('section[aria-label="Assistant work"]')).toBeNull();
      expect(host.querySelector('[role="status"]')?.textContent.toLowerCase()).toBe(status);
      expect(host.textContent).toContain('Partial answer');
    },
  );

  it('keeps a provider failure status visible when filters hide all tool work', async () => {
    await renderMessage(message([
      tool('filtered-tool', 'tool_start', 1_000),
    ], {
      responseStatus: 'failed',
      tokenCount: 0,
      finalCallTokenCount: null,
      usageProvenance: 'provider',
    }), {
      streamEventFilter: () => false,
    });

    expect(host.querySelector('section[aria-label="Assistant work"]')).toBeNull();
    expect(host.querySelector('[role="status"]')?.textContent).toBe('Failed');
    expect(host.querySelectorAll('.entry--assistant')).toHaveLength(1);
    expect(host.textContent).toContain('0 output tokens');
  });

  it('uses the visible work group as the only failure indicator when work is unfiltered', async () => {
    await renderMessage(message([
      tool('visible-tool', 'tool_start', 1_000),
    ], {
      responseStatus: 'failed',
    }));

    expect(assistantWork().textContent).toContain('Work failed');
    expect(host.querySelector('[role="status"]')).toBeNull();
  });
});
