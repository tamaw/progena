import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
const databaseUrl = process.env.PROGENITOR_DATABASE_URL || 'postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor';
const quote = (value) => `'${value.replaceAll("'", "''")}'`;
export function queryNestedEvidence(command) { return execFileSync('psql', [databaseUrl, '-v', 'ON_ERROR_STOP=1', '-Atq'], { input: command, encoding: 'utf8' }).trim(); }
// Fixture setup writes unique documents through the normal store trigger.
// Running, selecting, pausing, and child edits still go through the live UI.
export function seedNestedFixtures({ repeatedChild = false, declaredSchemas = true } = {}) {
  const suffix = randomUUID().replaceAll('-', '');
  const child = { id: `routine_nested_child_${suffix}`, name: `Nested child ${suffix.slice(-8)}`, description: 'Disposable nested routine verification', version: '2.0.0', entry: 'compute', nodes: [{ id: 'compute', label: 'Child compute', kind: 'eval', output_key: 'compute', config: { expression: 'value + payload.offset' } }], edges: [], ...(declaredSchemas ? { input_schema: { type: 'object', properties: { value: { type: 'number' }, payload: { type: 'object' } } }, output_schema: { type: 'object', properties: { compute: { type: 'number' } } } } : {}) };
  const call = (id, answer) => ({ id, output_key: id, label: id, kind: 'routine', config: { routine_name: child.id, input_map: { value: 'initial.value', payload: 'initial.payload' }, output_map: { [answer]: 'compute' } } });
  const parent = { id: `routine_nested_parent_${suffix}`, name: `Nested parent ${suffix.slice(-8)}`, description: 'Disposable nested routine verification', version: '2.0.0', entry: 'initial', nodes: [{ id: 'initial', output_key: 'initial', label: 'Initial data', kind: 'eval', config: { expression: 'fixture', defaults: { fixture: { value: 40, payload: { offset: 2 } } } } }, call('nested_first', 'first_answer'), ...(repeatedChild ? [call('nested_second', 'second_answer')] : []), { id: 'final', output_key: 'final', label: 'Final mapped result', kind: 'eval', config: { expression: repeatedChild ? 'first_answer * 1000 + second_answer' : 'first_answer' } }], edges: repeatedChild ? [{ from: 'initial', to: 'nested_first', level: 'normal' }, { from: 'nested_first', to: 'nested_second', level: 'normal' }, { from: 'nested_second', to: 'final', level: 'normal' }] : [{ from: 'initial', to: 'nested_first', level: 'normal' }, { from: 'nested_first', to: 'final', level: 'normal' }], input_schema: { type: 'object', properties: {} }, // Run to here at the final J14 step should finish, without a hidden output projector.
    ...(repeatedChild ? {} : { output_schema: { type: 'object', properties: { final: { type: 'number' } } } }) };
  for (const graph of [child, parent]) queryNestedEvidence(`INSERT INTO store (prefix,key,value,created_at,updated_at) VALUES ('progenitor.state',${quote(`routine:${graph.id}`)},${quote(JSON.stringify({payload:graph}))}::jsonb,now(),now());`);
  return { child, parent };
}
export function removeNestedFixtures(fixtures) { for (const graph of [fixtures.parent, fixtures.child]) queryNestedEvidence(`DELETE FROM store WHERE prefix='progenitor.state' AND key=${quote(`routine:${graph.id}`)};`); }
export function nestedChildThreads(childId) { return JSON.parse(queryNestedEvidence(`SELECT COALESCE(json_agg(t),'[]'::json) FROM (SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE ${quote(`routine:${childId}:%`)} AND checkpoint_ns='') t;`)); }
export function parentCheckpointChannels(threadId) {
  return JSON.parse(queryNestedEvidence(`SELECT checkpoint->'channel_versions' FROM checkpoints WHERE thread_id=${quote(threadId)} AND checkpoint_ns='' ORDER BY checkpoint_id DESC LIMIT 1;`));
}
export function parentRunHistory(threadId) {
  const script = `import asyncio,json,sys\nfrom langgraph.checkpoint.postgres.aio import AsyncPostgresSaver\nasync def main():\n async with AsyncPostgresSaver.from_conn_string(sys.argv[1]) as saver:\n  checkpoint=await saver.aget_tuple({'configurable':{'thread_id':sys.argv[2]}})\n  print(json.dumps(checkpoint.checkpoint['channel_values'].get('run_meta',{}),default=str))\nasyncio.run(main())`;
  return JSON.parse(execFileSync('bash', ['-c', 'source .venv/bin/activate && python -c "$1" "$2" "$3"', 'history', script, databaseUrl, threadId], { cwd: resolve(import.meta.dirname, '../../../..'), encoding: 'utf8' }));
}
export function childRevisionGraph(childId, revisionId) {
  return JSON.parse(queryNestedEvidence(`SELECT graph FROM routine_graph_revisions WHERE routine_id=${quote(childId)} AND revision_id=${quote(revisionId)};`));
}
