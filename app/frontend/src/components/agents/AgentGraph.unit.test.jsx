import { describe, expect, it } from 'vitest';

import { forkLaneEdgePosition } from './AgentGraph';

// Regression coverage for the router-fork rail bug: the "+ Add branch" slot
// is always appended after every real lane when the fork is editable (see
// ForkBlock in AgentGraph.jsx), so the right rail terminator must land on
// that slot, not on the last real branch lane one position early.
describe('forkLaneEdgePosition', () => {
  it('marks the last real lane as the right rail terminator when read-only (no add-branch slot follows)', () => {
    expect(forkLaneEdgePosition(2, 3, false)).toEqual({ isFirst: false, isLast: true });
  });

  it('does NOT mark the last real lane as the terminator when editable, since "+ Add branch" renders after it', () => {
    expect(forkLaneEdgePosition(2, 3, true)).toEqual({ isFirst: false, isLast: false });
  });

  it('marks index 0 as the left rail terminator regardless of editability', () => {
    expect(forkLaneEdgePosition(0, 3, true)).toEqual({ isFirst: true, isLast: false });
    expect(forkLaneEdgePosition(0, 3, false)).toEqual({ isFirst: true, isLast: false });
  });

  it('a lone branch (lanesLength 1) is both the first and, when read-only, the last terminator', () => {
    expect(forkLaneEdgePosition(0, 1, false)).toEqual({ isFirst: true, isLast: true });
    expect(forkLaneEdgePosition(0, 1, true)).toEqual({ isFirst: true, isLast: false });
  });

  it('a middle lane owns neither terminator', () => {
    expect(forkLaneEdgePosition(1, 3, false)).toEqual({ isFirst: false, isLast: false });
    expect(forkLaneEdgePosition(1, 3, true)).toEqual({ isFirst: false, isLast: false });
  });
});
