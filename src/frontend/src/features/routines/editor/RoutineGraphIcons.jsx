import { GRAPH_KIND_ICONS } from './graphNodeKinds';
import React from 'react';

// Renders a kind's icon. `prompt`, `condition`/`decision`, `switch`, and
// `split` get real (`currentColor`-stroked) SVGs instead of a plain
// character; everything else stays a plain Unicode glyph. This has to be a
// component (evaluated during render), not a value baked into
// GRAPH_NODE_KINDS/GRAPH_KIND_ICONS at module scope -- those are built
// eagerly on import, and this project's plain-esbuild vitest transform
// (no @vitejs/plugin-react, see vitest.config.js) compiles JSX to
// `React.createElement`, which would throw immediately on import for any
// JSX evaluated outside a component body.
//
// `condition` (Decision) keeps the plain diamond -- the base binary gate.
// `switch` and `split` are visually unrelated to it on purpose (an earlier
// pass tried differentiating them only by a small mark inside the same
// diamond, which read as near-identical at 12px): `switch` is a `{}` pair
// (the case/switch-statement glyph -- reads unambiguously as "multiple
// named cases"), `split` is an inverted fork -- a plain Y, one stem
// branching into two downward tines (a literal fork in the road: one path
// becomes several, all taken).
function DiamondIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <path d="M8 1.5 14.5 8 8 14.5 1.5 8Z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

function SwitchIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <path
        d="M6.5 2.5c-1.4 0-2 .7-2 2v1.3c0 .9-.4 1.3-1.3 1.5v.4c.9.2 1.3.6 1.3 1.5V10.5c0 1.3.6 2 2 2M9.5 2.5c1.4 0 2 .7 2 2v1.3c0 .9.4 1.3 1.3 1.5v.4c-.9.2-1.3.6-1.3 1.5V10.5c0 1.3-.6 2-2 2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SplitIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <path
        d="M8 2.5v3.8M8 6.3 4 13M8 6.3l4 6.7"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SelectIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="8" cy="8" r="2.2" fill="currentColor" />
    </svg>
  );
}

export function KindIcon({ kind }) {
  if (kind === 'prompt') {
    return (
      <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
        <path
          d="M2.5 3.5A1.5 1.5 0 0 1 4 2h8a1.5 1.5 0 0 1 1.5 1.5v5A1.5 1.5 0 0 1 12 10H6.5l-2.75 2.5V10H4a1.5 1.5 0 0 1-1.5-1.5z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
    );
  }
  if (kind === 'condition') return <DiamondIcon />;
  if (kind === 'classify') return <SelectIcon />;
  if (kind === 'switch') return <SwitchIcon />;
  if (kind === 'split') return <SplitIcon />;
  return GRAPH_KIND_ICONS[kind] || '•';
}

// Chevrons for the right tools dock / collapse seam (point toward the action:
// left = expand the panel leftward, right = collapse it away).
// Chevrons for the right tools dock / collapse seam.
export function ChevronLeftSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m9.8 4.2-3.6 3.8 3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

export function ChevronRightSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m6.2 4.2 3.6 3.8 3.6-3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

// Mirrors ChevronLeftSmall's literal "<" shape -- ChevronRightSmall above
// is actually a down chevron (used for panel-collapse affordances, not
// direction), so the run-history nav's forward arrow needs its own icon.
export function ChevronRightPointSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m6.2 4.2 3.6 3.8-3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}
