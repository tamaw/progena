import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '../../../..');
const databaseUrl = process.env.PROGENITOR_DATABASE_URL || 'postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor';
const quote = (value) => `'${value.replaceAll("'", "''")}'`;
function sql(command) {
  return execFileSync('psql', [databaseUrl, '-v', 'ON_ERROR_STOP=1', '-tAq'], { input: command, encoding: 'utf8' }).trim();
}
export function readAgentRun(task) {
  const value = sql(`SELECT value FROM store WHERE prefix='progenitor.state' AND key=${quote(`agent_protocol_run:${task.thread_id}:${task.run_id}`)};`);
  return value ? JSON.parse(value).payload : null;
}
export function seedAgentExecution() {
  const suffix = randomUUID().replaceAll('-', '');
  const name = `e2e-gated-${suffix}`;
  const id = `routine_modes_${suffix}`;
  // Read the configured workspace root without exposing the runtime environment.
  const script = "from dotenv import load_dotenv; load_dotenv(); from app.progenitor.config import StorageConfig; print(StorageConfig.from_env().shell_workspace_dir)";
  const workspaceRoot = process.env.PROGENITOR_SHELL_WORKSPACE_DIR || execFileSync('bash', ['-c', 'source .venv/bin/activate && python -c "$1"', 'workspace', script], { cwd: root, encoding: 'utf8' }).trim();
  const workspace = resolve(workspaceRoot, name);
  mkdirSync(workspace, { recursive: true });
  const command = 'printf waiting > entered; while [ ! -f release ]; do sleep 0.2; done; printf E2E_AGENT_DONE';
  const content = `---\nname: ${name}\ndescription: Gated execution E2E fixture.\nversion: 1.0.0\nkind: object\ntools: []\nskills: []\nshell: true\n---\nExecute the exact shell command requested by the caller once using execute. Wait for that tool to finish. Never create or modify the release file. Reply only E2E_AGENT_DONE after execute returns.\n`;
  const graph = {
    id, name: `Agent execution ${suffix.slice(-8)}`, description: 'Wait and background execution with an externally controlled gate', version: '2.0.0', entry: 'delegate',
    nodes: [
      { id: 'delegate', label: 'Delegate', kind: 'agent', output_key: 'delegate', config: { agent_name: name, prompt_template: `Run this exact command once: ${command}`, continue_thread: false } },
      { id: 'after', label: 'After', kind: 'eval', output_key: 'after', config: { expression: "delegate" } },
    ],
    edges: [{ from: 'delegate', to: 'after', level: 'normal' }],
    input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: { after: {} } },
  };
  // Write as a native agent so shell: true is honoured (user/marketplace agents
  // are not allowed shell since it is a privileged capability).
  const nativeDir = resolve(root, 'data/agents/objects', name);
  mkdirSync(nativeDir, { recursive: true });
  writeFileSync(resolve(nativeDir, 'SYSTEM.md'), content);
  sql(`INSERT INTO store(prefix,key,value,created_at,updated_at) VALUES
    ('progenitor.state',${quote(`routine:${id}`)},${quote(JSON.stringify({ payload: graph }))}::jsonb,now(),now());`);
  // Trigger a runtime object-agent refresh so the newly-written native agent is
  // discovered without requiring a runtime restart.
  sql(`NOTIFY store_changed, '{"key":"agent:native/${name}/SYSTEM.md"}';`);
  // Give the runtime a moment to process the notification and refresh its registry.
  execFileSync('sleep', ['2']);
  return {
    graph, name, workspace,
    entered: () => existsSync(resolve(workspace, 'entered')),
    release: () => writeFileSync(resolve(workspace, 'release'), 'release'),
    reset: () => { for (const file of ['entered', 'release']) rmSync(resolve(workspace, file), { force: true }); },
    cleanup: () => {
      writeFileSync(resolve(workspace, 'release'), 'release');
      sql(`DELETE FROM store WHERE prefix='progenitor.state' AND key=${quote(`routine:${id}`)};`);
      rmSync(nativeDir, { recursive: true, force: true });
    },
  };
}
