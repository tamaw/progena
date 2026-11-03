import { describe, expect, it } from 'vitest';
import {
  buildWorkGroups,
  formatElapsed,
  buildWorkSteps,
  mergeToolCallItems,
  reasoningSectionBody,
  selectRecentLines,
  splitReasoningSections,
  summarizeWork,
  selectWorkActivity,
  workActivityItems,
} from '@frontend/features/chat/models/workGroupModel';

function eventItem(id, eventType, metadata = {}, timestamp = undefined, content = '') {
  return { kind: 'stream_event', id, timestamp, event: { id, eventType, metadata, content, timestamp } };
}


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

describe('reasoningSectionBody', () => {

  it('drops the heading line from the reasoning body', () => {
    expect(reasoningSectionBody('**Plan**\n\nList the files first.')).toBe('List the files first.');
  });
});

describe('formatElapsed', () => {
  it('switches units at the minute and hour boundaries, so multi-day turns stay readable', () => {
    expect(formatElapsed(59_400)).toBe('59s');
    expect(formatElapsed(60_000)).toBe('1m 0s');
    expect(formatElapsed(3_599_000)).toBe('59m 59s');
    expect(formatElapsed(3_600_000)).toBe('1h 0m');
    expect(formatElapsed(189_509_000)).toBe('52h 38m');
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

describe('work stretches', () => {
  it('updates one status across many summaries while preserving full reasoning bodies in activity', () => {
    const rows = buildWorkGroups([
      eventItem('pre', 'tool_start', { tool_name: 'read_file', tool_call_id: 'a' }, 900),
      eventItem('r1', 'reasoning_delta', {}, 1_000, '**Plan**'),
      eventItem('r2', 'reasoning_delta', {}, 2_000, '**Inspect result**\n\nThe returned value needs another check.'),
      eventItem('result', 'tool_result', { tool_name: 'read_file', tool_call_id: 'a' }, 3_000),
      { kind: 'assistant_message', id: 'progress', content: 'First check is complete.', timestamp: 4_000 },
      eventItem('next', 'tool_start', { tool_name: 'read_file', tool_call_id: 'b' }, 5_000),
    ]);
    expect(rows.map((row) => row.kind)).toEqual(['work_group', 'assistant_message', 'work_group']);
    expect(rows[0]).toMatchObject({ heading: 'Inspect result', startedAt: 900, endedAt: 4_000 });
    expect(rows[2]).toMatchObject({ heading: null, startedAt: 5_000 });
    const activity = workActivityItems(rows[0].items);
    expect(activity.map((item) => item.kind)).toEqual(['tool_call', 'stream_event']);
    expect(activity[1].event.content).toContain('The returned value needs another check.');
    expect(summarizeWork(rows[0].items, buildWorkSteps(rows[0].items), rows[0].endedAt).elapsedMs).toBe(3_100);
  });

  it('keeps a parallel result with its initiating stretch even after a root progress reply', () => {
    const rows = buildWorkGroups([
      eventItem('start', 'tool_start', { tool_name: 'read_file', tool_call_id: 'old' }, 1_000),
      { kind: 'a2a_call', id: 'remote-card', status: 'success' },
      { kind: 'assistant_message', id: 'interim', content: 'Still working.', timestamp: 2_000 },
      eventItem('next', 'tool_start', { tool_name: 'read_file', tool_call_id: 'new' }, 2_500),
      eventItem('end', 'tool_result', { tool_name: 'read_file', tool_call_id: 'old', result_preview: 'Ready' }, 3_000),
    ]);
    const [call] = workActivityItems(rows[0].items);
    expect(call.start.id).toBe('start');
    expect(call.end.event.metadata.result_preview).toBe('Ready');
    expect(rows[0].endedAt).toBe(2_000);
    expect(rows[2].startedAt).toBe(2_500);
  });

  it('keeps all running calls plus five finished items and reveals the hidden history on demand', () => {
    const items = [
      { kind: 'tool_call', id: 'long-running', end: null },
      ...Array.from({ length: 9 }, (_, i) => ({ kind: 'tool_call', id: `finished-${i}`, end: {} })),
      { kind: 'subagent', id: 'active-helper', status: 'running' },
    ];
    const bounded = selectWorkActivity(items, false);
    expect(bounded.hiddenCount).toBe(4);
    expect(bounded.visibleItems.map((item) => item.id)).toEqual([
      'long-running', 'finished-4', 'finished-5', 'finished-6', 'finished-7', 'finished-8', 'active-helper',
    ]);
    expect(selectWorkActivity(items, true).visibleItems).toEqual(items);
  });
});
