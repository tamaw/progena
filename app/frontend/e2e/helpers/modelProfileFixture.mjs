import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
const databaseUrl = process.env.PROGENITOR_DATABASE_URL || 'postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor';
const providerDatabaseUrl = new URL(databaseUrl);
providerDatabaseUrl.pathname = `/${process.env.BACKEND_DATABASE_NAME || 'backend'}`;
const quote = (value) => `'${value.replaceAll("'", "''")}'`;
function sql(command, url = databaseUrl) {
  return execFileSync('psql', [url, '-v', 'ON_ERROR_STOP=1', '-tAq'], { input: command, encoding: 'utf8' }).trim();
}
export function seedModelProfileRoutine(kind, execution = 'wait') {
  const suffix = randomUUID().replaceAll('-', '');
  const id = `routine_profile_${suffix}`;
  const agent = `e2e-profile-agent-${suffix}`;
  const profile = `e2e_profile_${suffix}`;
  const existing = sql("SELECT value->'payload' FROM store WHERE prefix='progenitor.state' AND key='profile';");
  const profiles = existing ? JSON.parse(existing) : {};
  const baseline = profiles.general?.provider_id
    ? profiles.general
    : Object.values(profiles).find((candidate) => candidate?.provider_id);
  const providerId = baseline?.provider_id
    || sql('SELECT id FROM provider_connection WHERE is_default LIMIT 1;', providerDatabaseUrl.href);
  const deployment = baseline?.deployment
    || sql(`SELECT default_model FROM provider_connection WHERE id = ${quote(providerId)};`, providerDatabaseUrl.href);
  if (!providerId || !deployment) throw new Error('A persisted default provider is required for model profile E2E tests.');
  const selected = { [profile]: { provider_id: providerId, deployment } };
  sql(`INSERT INTO store(prefix,key,value,created_at,updated_at) VALUES ('progenitor.state','profile',${quote(JSON.stringify({ payload: selected }))}::jsonb,now(),now()) ON CONFLICT(prefix,key) DO UPDATE SET value=jsonb_set(store.value,'{payload}',COALESCE(store.value->'payload','{}'::jsonb)||(EXCLUDED.value->'payload')),updated_at=now();`);
  const config = kind === 'classify'
    ? { rubric_prompt: 'Return answer PROFILE_DONE.', response_schema: { type: 'object', properties: { answer: { type: 'string', enum: ['PROFILE_DONE'] } }, required: ['answer'], additionalProperties: false } }
    : kind === 'prompt' ? { prompt_template: 'Reply with exactly PROFILE_DONE and no other text.' }
      : { agent_name: agent, prompt_template: 'Say hello.', execution };
  const graph = { id, name: `Profile ${kind} ${suffix.slice(-8)}`, description: 'Model profile execution verification', version: '2.0.0', entry: 'call',
    nodes: [{ id: 'call', label: 'Call', kind, output_key: 'call', config }, { id: 'after', label: 'After', kind: 'eval', output_key: 'after', config: { expression: 'call' } }],
    edges: [{ from: 'call', to: 'after', level: 'normal' }], input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: { after: {} } } };
  const content = `---\nname: ${agent}\ndescription: Model profile E2E responder.\nversion: 1.0.0\nkind: object\ntools: []\nskills: []\n---\nAlways reply exactly PROFILE_DONE.\n`;
  sql(`INSERT INTO store(prefix,key,value,created_at,updated_at) VALUES ('progenitor.state',${quote(`routine:${id}`)},${quote(JSON.stringify({ payload: graph }))}::jsonb,now(),now()),('progenitor.state',${quote(`agent:user/${agent}/SYSTEM.md`)},${quote(JSON.stringify({ payload: { content } }))}::jsonb,now(),now());`);
  const removeProfile = () => sql(`UPDATE store SET value=jsonb_set(value,'{payload}',(value->'payload')-${quote(profile)}::text),updated_at=now() WHERE prefix='progenitor.state' AND key='profile';`);
  return { graph, profile, deployment, removeProfile, cleanup: () => {
    removeProfile();
    sql(`DELETE FROM store WHERE prefix='progenitor.state' AND key IN (${quote(`routine:${id}`)},${quote(`agent:user/${agent}/SYSTEM.md`)});`);
  } };
}
