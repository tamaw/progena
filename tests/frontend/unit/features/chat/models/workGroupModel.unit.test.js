import { describe, expect, it } from 'vitest';
import {
  buildWorkGroups,
  formatElapsed,
  buildWorkSteps,
  mergeToolCallItems,
  partitionTurnTimeline,
  reasoningSectionBody,
  splitGroupReasoning,
  selectRecentLines,
  splitReasoningSections,
  normalizeTurnResponseStatus,
  summarizeTurnWork,
  summarizeWork,
  turnWorkHeader,
  workGroupHeader,
} from '@frontend/features/chat/models/workGroupModel';

function eventItem(id, eventType, metadata = {}, timestamp = undefined, content = '') {
  return { kind: 'stream_event', id, timestamp, event: { id, eventType, metadata, content, timestamp } };
}

describe('partitionTurnTimeline', () => {
  it('splits work, delegated-agent cards and every answer row (including a terminal error row)', () => {
    const items = [
      eventItem('a', 'tool_start', { tool_name: 'ls', tool_call_id: 'c1' }),
      { kind: 'subagent', id: 'subagent-x' },
      { kind: 'assistant_message', id: 'answer' },
      { kind: 'a2a_call', id: 'a2a-y' },
      { kind: 'assistant_message', id: 'answer-error' },
    ];
    const { workItems, cardItems, answerItems } = partitionTurnTimeline(items);
    expect(workItems.map((item) => item.id)).toEqual(['a']);
    expect(cardItems.map((item) => item.id)).toEqual(['subagent-x', 'a2a-y']);
    expect(answerItems.map((item) => item.id)).toEqual(['answer', 'answer-error']);
  });
});

describe('buildWorkSteps', () => {
  it('merges each tool call start and finish into one step, keeping arrival order across interleaved calls', () => {
    const steps = buildWorkSteps([
      eventItem('1', 'tool_start', { tool_name: 'read_file', tool_call_id: 'a', args_preview: { path: '/a' } }),
      eventItem('2', 'tool_start', { tool_name: 'read_file', tool_call_id: 'b', args_preview: { path: '/b' } }),
      eventItem('3', 'tool_error', { tool_name: 'read_file', tool_call_id: 'b', duration_ms: 12 }),
      eventItem('4', 'tool_result', { tool_name: 'read_file', tool_call_id: 'a', duration_ms: 30 }),
    ]);
    expect(steps).toHaveLength(2);
    expect(steps[0]).toMatchObject({ title: 'Read File', detail: 'path: /a', status: 'success', durationMs: 30 });
    expect(steps[1]).toMatchObject({ detail: 'path: /b', status: 'error', durationMs: 12 });
  });
  it('counts a JavaScript returned error as one failed merged tool call', () => {
    const items = [
      eventItem('start', 'tool_start', { tool_name: 'javascript', tool_call_id: 'script' }),
      eventItem('result', 'tool_result', {
        tool_name: 'javascript',
        tool_call_id: 'script',
        result_preview: '<stdout>\nbefore failure\n</stdout>\n<error type="TypeError">boom</error>',
      }),
    ];
    const steps = buildWorkSteps(items);
    expect(steps).toMatchObject([{ title: 'Javascript', status: 'error' }]);
    expect(summarizeWork(items, steps)).toMatchObject({ toolCount: 1, failedCount: 1 });
  });


  it('leaves graph steps and card-owned task/A2A calls out of the step list', () => {
    const steps = buildWorkSteps([
      eventItem('1', 'node_update', { node_name: 'model' }),
      eventItem('2', 'tool_start', { tool_name: 'task', tool_call_id: 't' }),
      eventItem('3', 'tool_start', { tool_name: 'a2a_send', tool_call_id: 'x' }),
      eventItem('4', 'reasoning_summary'),
    ]);
    expect(steps.map((step) => step.kind)).toEqual(['reasoning']);
  });
});


describe('selectRecentLines', () => {
  it('reports how many earlier lines are hidden and reveals all on request', () => {
    const lines = ['1', '2', '3', '4', '5'];
    expect(selectRecentLines(lines, false, 3)).toEqual({ visibleLines: ['3', '4', '5'], hiddenCount: 2 });
    expect(selectRecentLines(lines, true, 3)).toEqual({ visibleLines: lines, hiddenCount: 0 });
  });
});

describe('mergeToolCallItems plan updates', () => {
  it("attaches each plan update to the latest write_todos call without one, so the call and its plan are one entry", () => {
    const plan = (id) => eventItem(id, 'todos_update', { todos: [] });
    const merged = mergeToolCallItems([
      eventItem('w1', 'tool_start', { tool_name: 'write_todos', tool_call_id: 'w1' }),
      plan('p1'),
      eventItem('r1', 'tool_result', { tool_name: 'write_todos', tool_call_id: 'w1' }),
      eventItem('w2', 'tool_start', { tool_name: 'write_todos', tool_call_id: 'w2' }),
      plan('p2'),
      // A plan update with no write_todos call to attach to stays its own row.
      plan('p3'),
    ]);
    expect(merged.map((item) => (item.kind === 'tool_call' ? `${item.start.id}+${item.plan?.id}` : item.id)))
      .toEqual(['w1+p1', 'w2+p2', 'p3']);
  });

  it('previews a successful write_todos call as its plan step only', () => {
    const steps = buildWorkSteps([
      eventItem('w1', 'tool_start', { tool_name: 'write_todos', tool_call_id: 'w1' }),
      eventItem('p1', 'todos_update', { todos: [{ content: 'Next', status: 'pending' }] }),
      eventItem('r1', 'tool_result', { tool_name: 'write_todos', tool_call_id: 'w1' }),
    ]);
    expect(steps.map((step) => [step.kind, step.detail])).toEqual([['plan', 'Next']]);
  });

  it('keeps failed and still-running write_todos calls visible when another call produced the plan', () => {
    const items = [
      eventItem('failed-start', 'tool_start', { tool_name: 'write_todos', tool_call_id: 'failed' }),
      eventItem('failed-end', 'tool_error', { tool_name: 'write_todos', tool_call_id: 'failed' }),
      eventItem('saved-start', 'tool_start', { tool_name: 'write_todos', tool_call_id: 'saved' }),
      eventItem('plan', 'todos_update', { todos: [{ content: 'Saved plan', status: 'pending' }] }),
      eventItem('saved-end', 'tool_result', { tool_name: 'write_todos', tool_call_id: 'saved' }),
      eventItem('running', 'tool_start', { tool_name: 'write_todos', tool_call_id: 'running' }),
    ];

    const merged = mergeToolCallItems(items);
    expect(merged.filter((item) => item.kind === 'tool_call').map((item) => [item.id, item.plan?.id])).toEqual([
      ['call-failed', undefined],
      ['call-saved', 'plan'],
      ['call-running', undefined],
    ]);

    const steps = buildWorkSteps(items);
    expect(steps.map((step) => [step.kind, step.status])).toEqual([
      ['tool', 'error'],
      ['plan', 'success'],
      ['tool', 'running'],
    ]);
    expect(summarizeWork(items, steps)).toMatchObject({ toolCount: 2, failedCount: 1 });
  });

  it('retains a planning failure even if the call emitted a plan before failing', () => {
    const items = [
      eventItem('start', 'tool_start', { tool_name: 'write_todos', tool_call_id: 'partial' }),
      eventItem('plan', 'todos_update', { todos: [{ content: 'Partial plan', status: 'pending' }] }),
      eventItem('error', 'tool_error', { tool_name: 'write_todos', tool_call_id: 'partial' }),
    ];
    const steps = buildWorkSteps(items);
    expect(steps.map((step) => [step.kind, step.status])).toEqual([
      ['tool', 'error'],
      ['plan', 'success'],
    ]);
    expect(summarizeWork(items, steps)).toMatchObject({ toolCount: 1, failedCount: 1 });
  });
});

describe('mergeToolCallItems', () => {
  it('merges each call start and finish into one entry at the call position, leaving other rows alone', () => {
    const merged = mergeToolCallItems([
      eventItem('s-a', 'tool_start', { tool_name: 'ls', tool_call_id: 'a' }),
      eventItem('s-b', 'tool_start', { tool_name: 'ls', tool_call_id: 'b' }),
      eventItem('r', 'reasoning_delta', {}, undefined, 'x'),
      eventItem('e-b', 'tool_error', { tool_name: 'ls', tool_call_id: 'b' }),
      eventItem('e-a', 'tool_result', { tool_name: 'ls', tool_call_id: 'a' }),
    ]);
    expect(merged.map((item) => item.kind === 'tool_call' ? [item.start.id, item.end.id] : item.id))
      .toEqual([['s-a', 'e-a'], ['s-b', 'e-b'], 'r']);
  });
});

describe('splitGroupReasoning', () => {
  it("takes a headed group's opening reasoning out of its rows, and leaves headless groups alone", () => {
    const reasoning = eventItem('r', 'reasoning_delta', {}, undefined, '**Plan**\n\nfirst.');
    const tool = eventItem('t', 'tool_start', { tool_name: 'ls', tool_call_id: 'a' });
    expect(splitGroupReasoning('Plan', [reasoning, tool])).toEqual({ reasoningItem: reasoning, rest: [tool] });
    expect(splitGroupReasoning(null, [reasoning, tool])).toEqual({ reasoningItem: null, rest: [reasoning, tool] });
  });

  it('drops the heading line from the reasoning body', () => {
    expect(reasoningSectionBody('**Plan**\n\nList the files first.')).toBe('List the files first.');
  });
});

describe('formatElapsed', () => {
  it('switches units at the minute and hour boundaries, so multi-day turns stay readable', () => {
    expect(formatElapsed(59_400)).toBe('59s');
    expect(formatElapsed(60_000)).toBe('1m');
    expect(formatElapsed(3_599_000)).toBe('59m 59s');
    expect(formatElapsed(3_600_000)).toBe('1h');
    expect(formatElapsed(189_509_000)).toBe('52h 38m');
  });
});

function headerFor(items, { heading = null, active = false, endedAt = null } = {}) {
  return workGroupHeader({ heading, active, summary: summarizeWork(items, buildWorkSteps(items), endedAt) });
}

describe('workGroupHeader', () => {
  const lsCall = [
    eventItem('1', 'tool_start', { tool_name: 'ls', tool_call_id: 'a' }, 1_000),
    eventItem('2', 'tool_result', { tool_name: 'ls', tool_call_id: 'a' }, 1_200),
  ];

  it('measures elapsed time up to the end of the group, not just the last tool event', () => {
    expect(headerFor(lsCall, { endedAt: 18_000 })).toEqual({ title: 'Worked for 17s', meta: '1 tool' });
  });

  it('titles a group with its reasoning summary and keeps the counters beside it', () => {
    expect(headerFor(lsCall, { heading: 'Listing configuration files', endedAt: 18_000 }))
      .toEqual({ title: 'Listing configuration files', meta: '17s · 1 tool' });
  });

  it('shows live seconds from zero while generating without a summary', () => {
    expect(headerFor(lsCall.slice(0, 1), { active: true, endedAt: 1_000 }))
      .toEqual({ title: null, meta: '0s · 1 tool' });
  });

  it('omits a sub-second duration once done and counts failures', () => {
    const items = [
      eventItem('1', 'tool_start', { tool_name: 'ls', tool_call_id: 'a' }, 1_000),
      eventItem('2', 'tool_error', { tool_name: 'ls', tool_call_id: 'a' }, 1_100),
    ];
    expect(headerFor(items)).toEqual({ title: 'Worked', meta: '1 tool' });
    expect(summarizeWork(items, buildWorkSteps(items)).failedCount).toBe(1);
  });

  it('describes headless reasoning-only work as thinking', () => {
    const items = [eventItem('1', 'reasoning_summary', {}, 1_000, 'plain reasoning')];
    expect(headerFor(items, { endedAt: 6_000 }).title).toBe('Thought for 5s');
  });
});

describe('turn work summary', () => {
  const items = [
    eventItem('1', 'tool_start', { tool_name: 'ls', tool_call_id: 'a' }, 1_000),
    eventItem('2', 'tool_error', { tool_name: 'ls', tool_call_id: 'a' }, 2_000),
  ];

  it('keeps a retried tool failure as evidence without changing completed response state', () => {
    const summary = summarizeTurnWork([{ items }], items, 5_000);
    expect(summary).toMatchObject({ elapsedMs: 4_000, toolCount: 1, failedCount: 1 });
    expect(turnWorkHeader({ responseStatus: 'completed', running: false, summary }))
      .toEqual({ title: 'Worked for 4s', meta: '1 tool', badge: 'Completed' });
  });

  it('renders only explicit terminal states and keeps unknown history neutral', () => {
    const summary = summarizeTurnWork([], items, null);
    expect(normalizeTurnResponseStatus('old-state')).toBe('unknown');
    expect(turnWorkHeader({ responseStatus: 'failed', running: false, summary }).title).toBe('Work failed');
    expect(turnWorkHeader({ responseStatus: 'cancelled', running: false, summary }).title).toBe('Work cancelled');
    expect(turnWorkHeader({ responseStatus: 'interrupted', running: false, summary }).title).toBe('Work interrupted');
    expect(turnWorkHeader({ responseStatus: 'unknown', running: false, summary }).title).toBe('Work');
  });
});

describe('splitReasoningSections', () => {
  it('splits on summary headings, including parts streamed back to back without a separator', () => {
    const text = 'Preamble.\n**Listing files**\n\nI list them.**Reading files**\n\nThen read with a **bold** word.';
    expect(splitReasoningSections(text)).toEqual([
      { heading: null, text: 'Preamble.\n' },
      { heading: 'Listing files', text: '**Listing files**\n\nI list them.' },
      { heading: 'Reading files', text: '**Reading files**\n\nThen read with a **bold** word.' },
    ]);
  });
});

describe('buildWorkGroups', () => {
  it('opens a group per reasoning summary and files following work under the latest one', () => {
    const groups = buildWorkGroups([
      eventItem('pre', 'tool_start', { tool_name: 'ls', tool_call_id: 'a' }, 900),
      eventItem('r1', 'reasoning_delta', { reasoning_tokens: 40 }, 1_000, '**Plan**\n\nfirst.**Look**\n\nsecond.'),
      eventItem('t1', 'tool_start', { tool_name: 'ls', tool_call_id: 'b' }, 1_100),
      eventItem('r2', 'reasoning_delta', {}, 2_000, 'no heading here'),
      eventItem('t2', 'tool_start', { tool_name: 'ls', tool_call_id: 'c' }, 2_100),
    ]);
    expect(groups.map((group) => [group.heading, group.items.map((item) => item.id)])).toEqual([
      [null, ['pre']],
      ['Plan', ['r1-s0']],
      // A headless reasoning block continues the group already open.
      ['Look', ['r1-s1', 't1', 'r2-s0', 't2']],
    ]);
    expect(groups[1].items[0].event.content).toBe('**Plan**\n\nfirst.');
    // A turn-level token count no longer applies once the block is split.
    expect(groups[1].items[0].event.metadata.reasoning_tokens).toBeUndefined();
  });
});
