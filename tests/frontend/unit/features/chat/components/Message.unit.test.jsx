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

describe('interruption markers', () => {
  it('renders the cancellation marker after partial content', async () => {
    await renderMessage(message([answer('Partial', 1)], {
      responseStatus: 'cancelled',
      interruptionReason: 'user_cancelled',
    }));

    expect(host.textContent).toContain('Partial');
    expect(host.textContent).toContain('Cancelled by you.');
    expect(host.querySelector('.message-wrapper.assistant')?.classList.contains('entry--chain-item')).toBe(true);
    expect(host.querySelector('article[aria-label="Interrupted"]')?.classList.contains('entry--chain-end')).toBe(true);
  });

  it('renders an empty cancelled turn as only its interruption marker', async () => {
    await renderMessage(message([], {
      responseStatus: 'cancelled',
      interruptionReason: 'user_cancelled',
    }));

    expect(host.textContent).toContain('Interrupted');
    expect(host.textContent).toContain('Cancelled by you.');
    expect(host.querySelector('.message-wrapper.assistant')).toBeNull();
  });
});


describe('assistant work groups', () => {
  it('renders every reasoning phase as a top-level work group without a turn wrapper', async () => {
    await renderMessage(message([
      tool('generic-start', 'tool_start', 900),
      {
        kind: 'stream_event',
        id: 'reasoning-one',
        timestamp: 1_000,
        event: {
          id: 'reasoning-one',
          eventType: 'reasoning_delta',
          timestamp: 1_000,
          content: '**Inspect files**\n\nRead configuration first.',
          metadata: {},
        },
      },
      tool('read-start', 'tool_start', 1_100, 'read_file', { tool_call_id: 'read' }),
      {
        kind: 'stream_event',
        id: 'reasoning-two',
        timestamp: 1_300,
        event: {
          id: 'reasoning-two',
          eventType: 'reasoning_delta',
          timestamp: 1_300,
          content: '**Search references**\n\nThen find callers.',
          metadata: {},
        },
      },
      tool('search-start', 'tool_start', 1_400, 'search_web', { tool_call_id: 'search' }),
    ]));

    const groups = [...host.querySelectorAll('.work-group')];

    expect(host.querySelector('section[aria-label="Assistant work"]')).toBeNull();
    expect(groups).toHaveLength(3);
    expect(groups.map((group) => group.querySelector('.work-group__title')?.textContent))
      .toEqual(['Worked', 'Inspect files', 'Search references']);
  });

  it('reveals full reasoning and named tool rows with one group expansion', async () => {
    await renderMessage(message([
      {
        kind: 'stream_event',
        id: 'reasoning',
        timestamp: 1_000,
        event: {
          id: 'reasoning',
          eventType: 'reasoning_delta',
          timestamp: 1_000,
          content: '**Inspect files**\n\nFirst observation.\n\nSecond observation.',
          metadata: {},
        },
      },
      tool('read-start', 'tool_start', 1_100, 'read_file', { tool_call_id: 'read' }),
    ]));

    const group = host.querySelector('.work-group');
    await act(async () => group.querySelector('.work-group__toggle').click());

    expect(host.textContent).toContain('First observation.');
    expect(host.textContent).toContain('Second observation.');
    expect(host.querySelector('.work-group__reasoning-toggle')).toBeNull();
    expect(host.querySelector('.tool-call-entry__disclosure summary')?.textContent).toContain('Read File');
  });

  it('keeps delegated-agent cards and the answer as siblings of collapsed root work', async () => {
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

    const work = host.querySelector('.work-group');
    const subagent = host.querySelector('.subagent-card');
    const remoteAgent = host.querySelector('.a2a-call-card');
    const finalAnswer = [...host.querySelectorAll('.msg--assistant')]
      .find((element) => element.textContent.includes('Final answer remains readable.'));

    expect(work).not.toBeNull();
    expect(subagent).not.toBeNull();
    expect(remoteAgent).not.toBeNull();
    expect(finalAnswer).not.toBeNull();
    expect(work.contains(subagent)).toBe(false);
    expect(work.contains(remoteAgent)).toBe(false);
    expect(work.contains(finalAnswer)).toBe(false);
  });

  it('keeps failed-tool evidence without calling a completed response failed', async () => {
    await renderMessage(message([
      tool('failed-start', 'tool_start', 1_000, 'read_file', { tool_call_id: 'failed' }),
      tool('failed-result', 'tool_result', 2_000, 'read_file', {
        tool_call_id: 'failed',
        success: false,
        error: 'Permission denied',
      }),
      answer('Recovered and completed the request.', 3_000),
    ]));

    const work = host.querySelector('.work-group');
    expect(work.textContent).toContain('Worked for 2s');
    expect(work.textContent).toContain('1 failed');
    expect(work.textContent).not.toContain('Failed');

    await act(async () => work.querySelector('.work-group__toggle').click());

    expect(host.querySelector('.tool-call-entry--error')).not.toBeNull();
    expect(host.textContent).toContain('Permission denied');
  });
});


describe('terminal replies without tool work', () => {
  it.each(['failed', 'interrupted'])(
    'keeps provider %s visible even when there are no work groups',
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

    expect(host.querySelector('.work-group__badge--error')).not.toBeNull();
    expect(host.querySelector('[role="status"]')).toBeNull();
  });
});

describe('scheduled task input', () => {
  it('renders persisted scheduled user input as a scheduled task', async () => {
    await renderMessage({
      id: 'scheduled-user',
      role: 'user',
      content: 'Run the report',
      isScheduled: true,
    });

    expect(host.textContent).toContain('Scheduled task');
    expect(host.textContent).toContain('Scheduled');
    expect(host.querySelector('.message.scheduled')).not.toBeNull();
  });
});
