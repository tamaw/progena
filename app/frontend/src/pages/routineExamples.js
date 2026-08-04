const node = (id, kind, label, config = {}) => ({
  id,
  kind,
  label,
  output_key: id,
  config,
});

const edge = (from, to) => ({ from, to });

const baseSettings = {
  checkpoint: { enabled: true, namespace: 'routine-demo' },
  retry: { max_attempts: 2, initial_interval: 0.5 },
  recursion_limit: 40,
};



export const ROUTINE_EXAMPLES = [
  {
    id: 'routine-approval-fanout',
    kind: 'subagent',
    name: 'Approval fan-out',
    role: 'HITL approval with parallel item review',
    summary: 'Human approval gates a fan-out, then joins into a response.',
    graph: {
      status: 'Ready', entry: 'request', settings: baseSettings,
      input_schema: { items: 'array' },
      output_schema: { respond: 'string' },
      nodes: [
        node('request', 'prompt', 'Collect request', { prompt_template: 'Summarize the incoming request.' }),
        node('approval', 'human', 'Human approval', { prompt: 'Approve review of every requested item.' }),
        node('fanout', 'foreach', 'Review each item', { items_path: 'items', target: 'review-item', item_key: 'item' }),
        node('review-item', 'code', 'Review item', { expression: "'reviewed:' + str(item)" }),
        node('join', 'noop', 'Join reviews', {}),
        node('respond', 'prompt', 'Respond', { prompt_template: 'Return the approved review summary.' }),
      ],
      edges: [edge('request', 'approval'), edge('approval', 'fanout'), edge('fanout', 'review-item'), edge('review-item', 'join'), edge('join', 'respond')],
    },
  },
  {
    id: 'routine-retry-loop', kind: 'subagent', name: 'Retrying verifier', role: 'Retry and bounded loop',
    summary: 'A deterministic gate loops the check back until it passes, capped by recursion_limit.',
    graph: {
      status: 'Ready', entry: 'start',
      settings: { ...baseSettings, retry: { max_attempts: 3, initial_interval: 0.1 }, recursion_limit: 12 },
      input_schema: { candidate: 'string' },
      output_schema: { gate: 'boolean', complete: 'string' },
      nodes: [
        node('start', 'prompt', 'Prepare candidate', { prompt_template: 'Prepare candidate for verification.' }),
        { ...node('seed', 'code', 'Seed attempt counter', { expression: '0' }), output_key: 'attempt' },
        { ...node('flaky', 'code', 'Attempt candidate', { expression: 'attempt + 1', inputs: { attempt: 'attempt' } }), output_key: 'attempt' },
        node('gate', 'eval', 'Verification gate', { expression: 'attempt >= 2', inputs: { attempt: 'attempt' } }),
        node('complete', 'prompt', 'Complete', { prompt_template: 'Report verification status.' }),
      ],
      edges: [
        edge('start', 'seed'), edge('seed', 'flaky'), edge('flaky', 'gate'),
        { from: 'gate', router: 'state_equals', state_key: 'gate', branches: { True: 'complete' }, default: 'flaky' },
      ],
    },
  },
  {
    id: 'routine-branch-join', kind: 'subagent', name: 'Branch and join', role: 'Conditional routing with convergence',
    summary: 'A deterministic condition sends work down two paths that converge.',
    graph: {
      status: 'Ready', entry: 'classify', settings: baseSettings,
      input_schema: { priority: 'string' },
      output_schema: { answer: 'string' },
      nodes: [
        node('classify', 'code', 'Classify', { expression: "'urgent'" }),
        node('urgent', 'agent', 'Urgent response', { agent_name: 'general-purpose', prompt_template: 'Handle urgent work.' }),
        node('normal', 'subagent', 'Normal response', { subagent_name: 'hello-world', prompt_template: 'Handle normal work.' }),
        node('merge', 'noop', 'Merge paths'),
        node('answer', 'prompt', 'Answer', { prompt_template: 'Format the merged response.' }),
      ],
      edges: [
        { from: 'classify', router: 'state_equals', state_key: 'classify', branches: { urgent: 'urgent', normal: 'normal' }, default: 'normal' },
        edge('urgent', 'merge'), edge('normal', 'merge'), edge('merge', 'answer'),
      ],
    },
  },
  {
    id: 'routine-private-context', kind: 'subagent', name: 'Runtime context handoff', role: 'Runtime context injection',
    summary: 'Runtime context is injected while an internal-only field stays out of the projected output.',
    graph: {
      status: 'Ready', entry: 'context', settings: { ...baseSettings, runtime_context: { request_id: 'demo-ctx', tenant: 'validation' } },
      input_schema: { question: 'string' },
      output_schema: { answer: 'string' },
      nodes: [
        node('context', 'prompt', 'Inject context', { prompt_template: 'Use runtime request context to answer {question}.' }),
        node('private', 'code', 'Build private trace', { expression: "'trace:' + str(request_id)" }),
        node('delegate', 'subgraph', 'Run shared subgraph', { subgraph_name: 'shared-validation-graph' }),
        node('answer', 'prompt', 'Return public answer', { prompt_template: 'Return only the public answer.' }),
      ],
      edges: [edge('context', 'private'), edge('private', 'delegate'), edge('delegate', 'answer')],
    },
  },
  {
    id: 'routine-nested-subgraph', kind: 'subagent', name: 'Nested enrichment', role: 'Native subgraph composition',
    summary: 'A routine delegates into a native subgraph and resumes locally.',
    graph: {
      status: 'Ready', entry: 'input', settings: baseSettings,
      input_schema: { text: 'string' }, output_schema: { finish: 'string' },
      nodes: [
        node('input', 'prompt', 'Read input', { prompt_template: 'Read the source text.' }),
        node('normalize', 'code', 'Normalize', { expression: 'str(text).strip()' }),
        node('child', 'subgraph', 'Enrichment subgraph', { subgraph_name: 'enrichment-child' }),
        node('score', 'eval', 'Score output', { expression: 'True', rubric_prompt: 'Is the enrichment complete?' }),
        node('finish', 'prompt', 'Finish', { prompt_template: 'Return the enriched result.' }),
      ],
      edges: [edge('input', 'normalize'), edge('normalize', 'child'), edge('child', 'score'), edge('score', 'finish')],
    },
  },
  {
    id: 'routine-streaming-audit', kind: 'subagent', name: 'Streaming audit', role: 'Streaming execution events',
    summary: 'Several connected steps emit observable execution events.',
    graph: {
      status: 'Ready', entry: 'load', settings: { ...baseSettings, stream_events: true }, input_schema: null,
      output_schema: { publish: 'string' },
      nodes: [
        node('load', 'prompt', 'Load record', { prompt_template: 'Load an audit record.' }),
        node('inspect', 'code', 'Inspect record', { expression: "'inspected'" }),
        node('branch', 'eval', 'Audit policy', { expression: 'True' }),
        node('publish', 'subagent', 'Publish event', { subagent_name: 'hello-world', prompt_template: 'Publish an audit event.' }),
        node('close', 'noop', 'Close audit'),
      ],
      edges: [edge('load', 'inspect'), edge('inspect', 'branch'), edge('branch', 'publish'), edge('publish', 'close')],
    },
  },
  {
    id: 'routine-all-controls', kind: 'subagent', name: 'All controls tour', role: 'Combined routine control demonstration',
    summary: 'Combines approval, fan-out, branch, retry, cache, checkpoint, and join.',
    graph: {
      status: 'Ready', entry: 'start', settings: { checkpoint: { enabled: true, namespace: 'all-controls' }, retry: { max_attempts: 2, initial_interval: 0.5 }, recursion_limit: 20, cache: true },
      input_schema: { items: 'array', mode: 'string' }, output_schema: { finish: 'string' },
      nodes: [
        node('start', 'prompt', 'Start run', { prompt_template: 'Start the combined controls tour.' }),
        node('approve', 'human', 'Approve controls', { prompt: 'Approve the combined demonstration.' }),
        node('fanout', 'foreach', 'Fan out', { items_path: 'items', target: 'item', item_key: 'item' }),
        node('item', 'code', 'Process item', { expression: "'processed:' + str(item)" }),
        node('route', 'code', 'Choose path', { expression: "'safe'" }),
        node('safe', 'subgraph', 'Safe subgraph', { subgraph_name: 'safe-path' }),
        node('fallback', 'prompt', 'Fallback path', { prompt_template: 'Handle fallback safely.' }),
        node('join', 'noop', 'Join all paths'),
        node('finish', 'prompt', 'Final result', { prompt_template: 'Summarize every control result.' }),
      ],
      edges: [
        edge('start', 'approve'), edge('approve', 'fanout'), edge('fanout', 'item'), edge('item', 'route'),
        { from: 'route', router: 'state_equals', state_key: 'route', branches: { safe: 'safe' }, default: 'fallback' },
        edge('safe', 'join'), edge('fallback', 'join'), edge('join', 'finish'),
      ],
    },
  },
  {
    id: 'routine-rubric-completion', kind: 'subagent', name: 'Rubric completion gate', role: 'LLM-graded structured output loop',
    summary: 'A grading prompt returns a structured completion percentage; the draft loops back below 80%.',
    graph: {
      status: 'Ready', entry: 'draft',
      settings: { ...baseSettings, recursion_limit: 16 },
      input_schema: { task: 'string' },
      output_schema: { finish: 'string' },
      $defs: {
        completionGrade: {
          type: 'object',
          properties: { completion_pct: { type: 'number' } },
          required: ['completion_pct'],
          additionalProperties: false,
        },
      },
      nodes: [
        node('draft', 'prompt', 'Draft attempt', { prompt_template: 'Draft an attempt at: {task}' }),
        node('grade', 'prompt', 'Grade against rubric', {
          prompt_template:
            'Grade the draft below against this rubric: clarity, completeness, correctness.\n\nDraft: {draft}',
          response_schema: { $ref: '#/$defs/completionGrade' },
        }),
        node('check', 'eval', 'Completion gate', { expression: 'pct >= 0.8', inputs: { pct: 'grade.completion_pct' } }),
        node('finish', 'prompt', 'Report result', { prompt_template: 'Report the final draft and its completion percentage.' }),
      ],
      edges: [
        edge('draft', 'grade'), edge('grade', 'check'),
        { from: 'check', router: 'state_equals', state_key: 'check', branches: { True: 'finish' }, default: 'draft' },
      ],
    },
  },
];
