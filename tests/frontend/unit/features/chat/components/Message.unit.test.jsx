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

  it('keeps a persisted interrupted turn before the next user message and completed answer', async () => {
    await act(async () => root.render(
      <>
        <Message message={message([], { responseStatus: 'interrupted' })} />
        <Message message={{ id: 'next-user', role: 'user', content: 'Please retry.' }} />
        <Message message={message([answer('The retry succeeded.', 2)], { id: 'next-answer' })} />
      </>,
    ));

    const markers = host.querySelectorAll('article[aria-label="Interrupted"]');
    expect(markers).toHaveLength(1);
    expect(markers[0].textContent).toContain('This turn stopped before it finished.');
    const retry = host.querySelector('.message-wrapper.user');
    expect(markers[0].compareDocumentPosition(retry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(host.lastElementChild.textContent).toContain('The retry succeeded.');
  });
});


describe('assistant work groups', () => {

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

  it('retains provider-only reasoning detail and summary bodies when text content is empty', async () => {
    await renderMessage(message([{
      kind: 'stream_event', id: 'provider-detail', timestamp: 1_000,
      event: { id: 'provider-detail', eventType: 'reasoning_summary', content: '',
        metadata: { detail: 'The provider retained an explanation of the result.' }, timestamp: 1_000 },
    }, {
      kind: 'stream_event', id: 'provider-summary', timestamp: 1_500,
      event: { id: 'provider-summary', eventType: 'reasoning_summary', content: '',
        metadata: { summary: '**Provider check**\n\nThe provider summary also contains substantive reasoning.' }, timestamp: 1_500 },
    }, answer('Result.', 2_000)]));
    await act(async () => host.querySelector('.work-group__toggle').click());
    expect(host.querySelector('.work-group__reasoning-text').textContent)
      .toContain('The provider retained an explanation of the result.');
    expect(host.textContent).toContain('The provider summary also contains substantive reasoning.');
    expect(host.textContent.match(/Provider check/g)).toHaveLength(1);
  });


  it('keeps an interim reply connected between work phases, without a typing cursor', async () => {
    await renderMessage(message([
      tool('before', 'tool_result', 1_000),
      { ...answer('An interim update.', 2_000), id: 'interim' },
      tool('after', 'tool_start', 3_000),
    ], { responseStatus: 'running', isStreaming: true, workActive: true }));

    const interim = host.querySelector('.msg--assistant');
    const groups = host.querySelectorAll('.work-group');
    expect(groups).toHaveLength(2);
    expect(groups[0].compareDocumentPosition(interim) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(interim.compareDocumentPosition(groups[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(interim.classList.contains('entry--chain-item')).toBe(true);
    expect(interim.querySelector('.stream-cursor')).toBeNull();
    expect(interim.closest('.work-group')).toBeNull();
  });

  it('ends only the final reply and preserves each intermediate reply after completion', async () => {
    await renderMessage(message([
      { ...answer('Checking now.', 1_000), id: 'interim' },
      tool('check', 'tool_result', 2_000),
      answer('All checked.', 3_000),
    ]));
    const replies = host.querySelectorAll('.msg--assistant');
    expect(replies[0].classList.contains('entry--chain-item')).toBe(true);
    expect(replies[1].classList.contains('entry--chain-end')).toBe(true);
    expect(replies[0].textContent).toContain('Checking now.');
    expect(replies[1].textContent).toContain('All checked.');
  });

  it('keeps a root failure visible when only a successful delegation card remains', async () => {
    await renderMessage(message([
      tool('hidden-tool', 'tool_start', 1_000),
      { kind: 'subagent', id: 'successful-helper', toolCallId: 'helper', subagentName: 'helper', status: 'success', entries: [] },
    ], { responseStatus: 'failed' }), {
      streamEventFilter: (event) => event.eventType === 'subagent_start',
    });
    await act(async () => host.querySelector('.work-group__toggle').click());
    expect(host.querySelector('.subagent-card')).not.toBeNull();
    expect(host.querySelector('.work-group__status')?.textContent).toBe('Failed');
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


describe('provider usage tooltip', () => {
  it('shows the final-call input with the response output when the token icon is focused', async () => {
    await renderMessage(message([{
      ...answer('Answer', 2),
      tokenCount: 125,
      finalCallTokenCount: 24,
    }], { contextUsage: { inputTokens: 4321, outputTokens: 24, totalTokens: 4345 } }));
    await act(async () => host.querySelector('[aria-label="125 output tokens"]').focus());
    expect(document.querySelector('[role="tooltip"]').textContent).toContain('Input (last call)4,321');
    expect(document.querySelector('[role="tooltip"]').textContent).toContain('Output125');
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
      const statusRow = status === 'interrupted'
        ? host.querySelector('article[aria-label="Interrupted"]')
        : host.querySelector('[role="status"]');
      expect(statusRow?.textContent.toLowerCase()).toContain(status);
      if (status === 'interrupted') {
        expect(host.querySelector('.message-wrapper.assistant').compareDocumentPosition(statusRow)
          & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      }
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
  const scheduled = {
    id: 'scheduled-user',
    role: 'user',
    content: 'Run the report\n\nInclude every **regional** total.',
    timestamp: 1_000,
    isScheduled: true,
    scheduled: { schedule_id: 'schedule-1', run_id: 'run-1' },
  };

  it('identifies scheduled input by title and dot colour without a Scheduled badge', async () => {
    await renderMessage(scheduled);

    expect(host.querySelector('.tool-call-entry__title')?.textContent).toBe('Scheduled task');
    expect(host.querySelector('.scheduled-badge')).toBeNull();
    expect(host.textContent.replace('Scheduled task', '')).not.toMatch(/Scheduled/);
    expect(host.querySelector('.entry__badge--user.entry__badge--scheduled')).not.toBeNull();
    expect(host.querySelector('.msg__time')).not.toBeNull();
  });

  it('collapses the body behind a native disclosure that keeps the full text', async () => {
    await renderMessage(scheduled);

    const details = host.querySelector('details.tool-event-disclosure');
    const summary = details.querySelector(':scope > summary');
    expect(details.open).toBe(false);
    expect(summary.getAttribute('aria-label')).toBe('Scheduled task: Run the report');
    expect(summary.querySelector('.tool-call-entry__summary').textContent).toBe('Run the report');
    expect(details.querySelector('.message.scheduled .message-content').innerHTML)
      .toContain('<strong>regional</strong>');

    await act(async () => summary.click());
    expect(details.open).toBe(true);
    await act(async () => summary.click());
    expect(details.open).toBe(false);
  });

  it('copies the full body from the collapsed summary', async () => {
    const writeText = vi.fn().mockResolvedValue();
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    await renderMessage(scheduled);

    const details = host.querySelector('details.tool-event-disclosure');
    await act(async () => details.querySelector('button[aria-label="Copy message"]').click());

    expect(writeText).toHaveBeenCalledWith(scheduled.content);
    expect(details.open).toBe(false);
  });

  it('leaves ordinary user messages expanded with their Message heading', async () => {
    await renderMessage({ id: 'ordinary', role: 'user', content: 'Plain request', timestamp: 1_000 });

    expect(host.querySelector('details')).toBeNull();
    expect(host.querySelector('.msg__role')?.textContent).toBe('Message');
    expect(host.querySelector('.entry__badge--scheduled')).toBeNull();
    expect(host.querySelector('.message-content')?.textContent).toContain('Plain request');
  });
});
