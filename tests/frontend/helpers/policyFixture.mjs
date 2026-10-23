import { randomUUID } from 'node:crypto';
import { queryNestedEvidence } from './nestedRoutineFixture.mjs';
const quote = (value) => `'${value.replaceAll("'", "''")}'`;
export function seedPolicyRoutine(approval = false) {
  const suffix = randomUUID().replaceAll('-', '');
  const policyId = `policy_${suffix}`, id = `routine_policy_${suffix}`;
  const base = JSON.parse(queryNestedEvidence("SELECT value->'payload' FROM store WHERE prefix='progena.state' AND key='policy:default';"));
  const policy = { ...base, id: policyId, name: `Policy ${suffix.slice(-8)}`, values: { ...base.values, graph_step_limit: 20 } };
  const graph = { id, description: 'Disposable policy execution fixture', policy_id: policyId, name: `Policy routine ${suffix.slice(-8)}`, version: '2.0.0', entry: approval ? 'decision' : 'count',
    nodes: approval ? [
      { id: 'decision', output_key: 'decision', label: 'Approval', kind: 'human', config: { message: 'Approve policy test?', allowed_responses: ['approve', 'decline'] } },
      { id: 'after', label: 'After', kind: 'eval', output_key: 'after', config: { expression: '42' } },
    ] : [
      { id: 'count', label: 'Increment', kind: 'eval', output_key: 'count', config: { expression: 'count + 1', defaults: { count: 0 } } },
      { id: 'after', label: 'After', kind: 'eval', output_key: 'after', config: { expression: 'count' } },
    ], edges: approval ? [{ from: 'decision', to: 'after', level: 'normal' }] : [
      { from: 'count', to: 'count', level: 'normal', loop_guard: { expression: 'count < 3', on_false: 'after' } },
    ], input_schema: { type: 'object', properties: {} } };
  function write(key, doc) { queryNestedEvidence(`INSERT INTO store(prefix,key,value,created_at,updated_at) VALUES ('progena.state',${quote(key)},${quote(JSON.stringify({ payload: doc }))}::jsonb,now(),now()) ON CONFLICT(prefix,key) DO UPDATE SET value=EXCLUDED.value,updated_at=now();`); }
  write(`policy:${policyId}`, policy); write(`routine:${id}`, graph);
  const copies = [];
  return { id, policyId, graph, policy,
    registerCopy: (copyId) => copies.push(copyId),
    readGraph: () => JSON.parse(queryNestedEvidence(`SELECT value->'payload' FROM store WHERE prefix='progena.state' AND key=${quote(`routine:${id}`)};`)),
    readPolicy: (selectedId = policyId) => JSON.parse(queryNestedEvidence(`SELECT value->'payload' FROM store WHERE prefix='progena.state' AND key=${quote(`policy:${selectedId}`)};`)),
    writePolicy: (selectedId, doc) => write(`policy:${selectedId}`, doc),
    revisions: () => JSON.parse(queryNestedEvidence(`SELECT json_agg(revision_id) FROM policy_revisions WHERE policy_id=${quote(policyId)};`)),
    cleanup: () => queryNestedEvidence(`DELETE FROM store WHERE prefix='progena.state' AND key IN (${quote(`routine:${id}`)},${quote(`policy:${policyId}`)}${copies.map((copyId) => `,${quote(`policy:${copyId}`)}`).join('')});`) };
}
