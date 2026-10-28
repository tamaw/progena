import { describe, expect, it } from 'vitest';
import {
  spanGeometry,
  tickLabel,
  timelineRows,
  traceBounds,
} from '@frontend/features/runs/timelineModel';

const at = (seconds) => `2026-10-29T00:00:${String(seconds).padStart(2, '0')}Z`;

function span(id, parentId, kind, start, end = null) {
  return {
    id,
    parent_id: parentId,
    kind,
    name: id,
    start: start == null ? null : at(start),
    end: end == null ? null : at(end),
  };
}

describe('timeline geometry', () => {
  it('uses recorded terminal bounds without padding a completed trace', () => {
    const bounds = traceBounds({
      status: 'completed',
      started_at: at(10),
      completed_at: at(25),
      spans: [span('step', null, 'agent', 12, 20)],
    }, Date.parse(at(30)));

    expect(bounds).toEqual({
      start: Date.parse(at(10)), end: Date.parse(at(25)), duration: 15_000, isLive: false,
    });
    const geometry = spanGeometry(span('step', null, 'agent', 12, 20), bounds);
    expect(geometry.unavailable).toBe(false);
    expect(geometry.left).toBeCloseTo(13.333333333333334);
    expect(geometry.width).toBeCloseTo(53.333333333333336);
  });

  it('makes missing terminal timing a marker but grows an open live span to now', () => {
    const completedBounds = traceBounds({ status: 'completed', started_at: at(10), spans: [] }, Date.parse(at(30)));
    expect(spanGeometry(span('missing-end', null, 'tool', 12), completedBounds, Date.parse(at(30)))).toEqual({
      left: 0, width: 0, unavailable: true,
    });

    const liveBounds = traceBounds({ status: 'running', started_at: at(10), spans: [] }, Date.parse(at(30)));
    expect(spanGeometry(span('open', null, 'tool', 12), liveBounds, Date.parse(at(30)))).toEqual({
      left: 10, width: 90, unavailable: false,
    });
    expect(spanGeometry({ ...span('unavailable', null, 'tool', 12, 20), timing_source: 'unavailable' }, liveBounds)).toEqual({
      left: 10, width: 0, unavailable: true,
    });
  });

  it('clamps bars that extend beyond the trace range and formats axis ticks', () => {
    const bounds = { start: Date.parse(at(10)), end: Date.parse(at(20)), duration: 10_000, isLive: false };
    expect(spanGeometry(span('wide', null, 'agent', 5, 40), bounds)).toEqual({ left: 0, width: 100, unavailable: false });
    expect(spanGeometry(span('left-clipped', null, 'agent', 5, 15), bounds)).toEqual({ left: 0, width: 50, unavailable: false });
    expect(tickLabel(999)).toBe('999ms');
    expect(tickLabel(1_000)).toBe('1.0s');
    expect(tickLabel(60_000)).toBe('1m');
  });
});

describe('timeline rows', () => {
  it('omits a synthetic run root, preserves nesting, collapse state, and disconnected spans', () => {
    const spans = [
      span('run', null, 'run', 0, 30),
      span('parent', 'run', 'agent', 1, 25),
      span('child', 'parent', 'agent', 2, 3),
      span('orphan', 'missing', 'agent', 4, 5),
    ];

    expect(timelineRows(spans).map((row) => [row.id, row.depth, row.hasChildren])).toEqual([
      ['parent', 0, true],
      ['child', 1, false],
      ['orphan', 0, false],
    ]);
    expect(timelineRows(spans, { parent: true }).map((row) => row.id)).toEqual(['parent', 'orphan']);
  });

  it('packs non-overlapping tool leaves into stable lanes while separating overlaps', () => {
    const base = [
      span('parent', null, 'agent', 0, 50),
      span('first', 'parent', 'tool', 1, 10),
      span('overlap', 'parent', 'tool', 5, 12),
      span('later', 'parent', 'tool', 12, 20),
    ];
    const rows = timelineRows(base);
    const lanes = rows.filter((row) => row.kind === 'tool');

    expect(lanes.map((row) => [row.id, row.depth, row.spans.map((item) => item.id)])).toEqual([
      ['lane:parent:tool:0', 1, ['first', 'later']],
      ['lane:parent:tool:1', 1, ['overlap']],
    ]);
    expect(rows.flatMap((row) => row.spans).map((item) => item.id).sort()).toEqual([
      'first', 'later', 'overlap', 'parent',
    ]);
    expect(timelineRows([...base, span('appended', 'parent', 'tool', 25, 30)])
      .find((row) => row.id === 'lane:parent:tool:0').spans.map((item) => item.id))
      .toEqual(['first', 'later', 'appended']);
  });

  it('shares explicit unavailable markers but isolates open calls from interval lanes', () => {
    const rows = timelineRows([
      span('parent', null, 'agent', 0, 30),
      span('first', 'parent', 'tool', 1, 4),
      { ...span('marker-one', 'parent', 'tool', 2, 2), timing_source: 'unavailable' },
      { ...span('marker-two', 'parent', 'tool', 3, 3), timing_source: 'unavailable' },
      span('open', 'parent', 'tool', 5),
      span('later', 'parent', 'tool', 6, 9),
    ]).filter((row) => row.kind === 'tool');

    expect(rows.map((row) => [row.id, row.spans.map((item) => item.id)])).toEqual([
      ['lane:parent:tool:0', ['first', 'later']],
      ['lane:parent:tool:1', ['marker-one', 'marker-two']],
      ['lane:parent:tool:2', ['open']],
    ]);
  });

  it('terminates malformed parent cycles and emits every span once', () => {
    const rows = timelineRows([
      span('a', 'b', 'agent', 1, 2),
      span('b', 'a', 'agent', 2, 3),
    ]);
    expect(rows.map((row) => row.id).sort()).toEqual(['a', 'b']);
  });
});
