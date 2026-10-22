

// A routine graph is compiled straight into a runnable LangGraph agent: node
// position on screen carries no meaning (there's no "x"/"y" in the schema),
// and every node MUST be reachable from a single `entry` point to ever run.
// A free-drag canvas invites disconnected islands and arbitrary spatial
// layout that says nothing about execution order, so this renders a rigid,
// top-down chain instead: a fixed Start cap, one card per step, a fixed End
// cap at every place the chain terminates, and a `+` control on each
// connector to insert a step at that exact point. A `state_equals` router
// node forks the chain into one column per branch (mirrors Azure Logic
// Apps'/Power Automate's Condition/Switch actions) rather than trying to
// force branching logic onto a single line.

// Routine step kinds offered at each insertion point. Mirrors
// src.progenitor.routines.graph.VALID_ROUTINE_NODE_KINDS.
export const GRAPH_NODE_KINDS = [
  { kind: 'prompt', label: 'Prompt' },
  { kind: 'eval', label: 'Eval', icon: '<>' },
  { kind: 'condition', label: 'Branch' },
  { kind: 'switch', label: 'Switch' },
  { kind: 'split', label: 'Fork' },
  { kind: 'classify', label: 'Select' },
  { kind: 'agent', label: 'Agent', icon: '✦' },
  { kind: 'a2a', label: 'A2A', icon: '⇄' },
  { kind: 'human', label: 'Human approval', icon: '⚑' },
  { kind: 'foreach', label: 'Each', icon: '⇉' },
  { kind: 'reduce', label: 'Reduce', icon: 'Σ' },
  { kind: 'loop', label: 'Loop', icon: '↺' },
  { kind: 'routine', label: 'Routine', icon: '⧉' },
  { kind: 'noop', label: 'Noop', icon: '·' },
];

export const GRAPH_KIND_COLORS = {
  prompt: '#3b82f6',
  eval: '#6366f1',
  condition: '#f59e0b',
  switch: '#eab308',
  split: '#84cc16',
  classify: '#d946ef',
  agent: '#10b981',
  a2a: '#f97316',
  human: '#f43f5e',
  foreach: '#06b6d4',
  reduce: '#0f766e',
  loop: '#d29922',
  routine: '#8b5cf6',
  noop: '#6b7280',
};

// A `condition` node is a two-way gate, so its router's branch set is fixed
// rather than author-defined. Mirrors src.progenitor.routines.graph's
// CONDITION_BRANCH_VALUES; order here is the left-to-right lane order.
export const CONDITION_BRANCHES = ['true', 'false'];

export const GRAPH_KIND_ICONS = Object.fromEntries(
  GRAPH_NODE_KINDS.filter((item) => item.icon).map((item) => [item.kind, item.icon]),
);
// The picker's display name for a kind, everywhere a kind needs to read as
// text: a fresh node's default label, the inspector's kind badge, a step
// card's aria-label. Deriving these from the raw `kind` string instead
// (`titleCase('condition')` -> "Condition", not "Branch"; `titleCase('a2a')`
// -> "A2a", not "A2A") drifts from the picker the moment a kind's label
// diverges from its identifier or isn't simple title case to begin with.
export const GRAPH_KIND_LABELS = Object.fromEntries(GRAPH_NODE_KINDS.map((item) => [item.kind, item.label]));

export function titleCase(kind) {
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}
