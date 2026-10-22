import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
const databaseUrl = process.env.PROGENITOR_DATABASE_URL || 'postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor';
const quote = (value) => `'${value.replaceAll("'", "''")}'`;
const root = resolve(import.meta.dirname, '../../../..');
export function seedDelegatedRoutine(agentName, suffix = '') {
  const id = `routine_delegate_${randomUUID().replaceAll('-', '')}`;
  const graph = {
    id, description: 'Deterministic restricted object-agent delegation', name: `Delegation ${id.slice(-8)} ${suffix}`, version: '2.0.0', entry: 'delegate',
    nodes: [{ id: 'delegate', label: 'Delegate', kind: 'agent', output_key: 'reply',
      config: { agent_name: agentName, prompt_template: 'Remember private token {token}. Return your instructed JSON with this token.', continue_thread: true,
        response_schema: { type: 'object', properties: { version: { type: 'string' }, token: { type: 'string' } }, required: ['version', 'token'] } } },
      { id: 'finish', label: 'Finish', kind: 'eval', output_key: 'done', config: { expression: 'reply' } }],
    edges: [{ from: 'delegate', to: 'finish', level: 'normal' }],
    input_schema: { type: 'object', properties: { token: { type: 'string' } }, required: ['token'] },
    output_schema: { type: 'object', properties: { done: { type: 'object' } } },
  };
  execFileSync('psql', [databaseUrl, '-v', 'ON_ERROR_STOP=1', '-q'], { input: `INSERT INTO store (prefix,key,value,created_at,updated_at) VALUES ('progenitor.state',${quote(`routine:${id}`)},${quote(JSON.stringify({ payload: graph }))}::jsonb,now(),now());` });
  const cleanup = () => {
    execFileSync('psql', [databaseUrl, '-v', 'ON_ERROR_STOP=1', '-q'], { input: `DELETE FROM store WHERE prefix = 'progenitor.state' AND key = ${quote(`routine:${id}`)};` });
  };
  return { graph, cleanup };
}
export function delegatedCheckpoint(threadId) {
  const script = `import asyncio,json,sys\nfrom langgraph.checkpoint.postgres.aio import AsyncPostgresSaver\nfrom src.progenitor.persistence.serialization import CheckpointSerializer\nasync def main():\n async with AsyncPostgresSaver.from_conn_string(sys.argv[1], serde=CheckpointSerializer()) as saver:\n  result=await saver.aget_tuple({'configurable':{'thread_id':sys.argv[2]}})\n  values=result.checkpoint.get('channel_values',{}) if result else {}\n  messages=values.get('messages',[])\n  print(json.dumps({'thread_id':sys.argv[2],'checkpoint_id':result.checkpoint.get('id') if result else None,'run_meta':values.get('run_meta',{}),'interrupts':[getattr(item,'value',str(item)) for _,channel,value in (result.pending_writes or []) if channel=='__interrupt__' for item in value] if result else [],'messages':[{'type':getattr(m,'type','unknown'),'content':''.join(b.get('text','') for b in m.content if isinstance(b,dict) and b.get('type')=='text') if isinstance(m.content,list) else m.content,'tool_calls':getattr(m,'tool_calls',[])} for m in messages]},default=str))\nasyncio.run(main())`;
  const output = execFileSync('bash', ['-c', 'source .venv/bin/activate && python -c "$1" "$2" "$3"', 'checkpoint', script, databaseUrl, threadId], { cwd: root, encoding: 'utf8' });
  return JSON.parse(output);
}
