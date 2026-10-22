import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';

const databaseUrl = process.env.PROGENITOR_DATABASE_URL
  || 'postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor';
const quote = (value) => `'${value.replaceAll("'", "''")}'`;
// Preserve successful evidence even when Playwright uses only the list reporter.
export async function attachJsonEvidence(testInfo, name, evidence) {
  const path = testInfo.outputPath(name.endsWith('.json') ? name : `${name}.json`);
  writeFileSync(path, JSON.stringify(evidence, null, 2));
  await testInfo.attach(name, { path, contentType: 'application/json' });
}
function sql(command) {
  execFileSync('psql', [databaseUrl, '-v', 'ON_ERROR_STOP=1', '-q'], { input: command });
}

// Seed only the graph document. Its normal committed-store notification creates
// the catalog entry; execution and all assertions still use the live UI/runtime.
// `retry` attaches a retry policy to the first node; with a failing expression
// its backoff keeps the run in `started` for a known time, then it fails.
export function seedRoutine({ failing = false, retry } = {}) {
  const id = `routine_sse_${randomUUID().replaceAll('-', '')}`;
  const graph = {
    id, name: `SSE verification ${id.slice(-8)}`, description: 'Deterministic event transport verification',
    version: '2.0.0', entry: 'first',
    nodes: [
      { id: 'first', label: 'First', kind: 'eval', output_key: 'first', config: { expression: failing ? '1 / 0' : '9', ...(retry ? { retry } : {}) } },
      { id: 'second', label: 'Second', kind: 'eval', output_key: 'second', config: { expression: 'first + 1' } },
    ],
    edges: [{ from: 'first', to: 'second', level: 'normal' }],
    input_schema: { type: 'object', properties: {} },
    output_schema: { type: 'object', properties: { second: { type: 'number' } } },
  };
  sql(`INSERT INTO store (prefix, key, value, created_at, updated_at)
       VALUES ('progenitor.state', ${quote(`routine:${id}`)}, ${quote(JSON.stringify({ payload: graph }))}::jsonb, now(), now());`);
  return graph;
}

export function removeRoutine(id) {
  sql(`DELETE FROM store WHERE prefix = 'progenitor.state' AND key = ${quote(`routine:${id}`)};`);
}

// Persisted routine graphs the connector/editing specs render (forks, joins,
// nested lanes, for-each). Validated by tests/backend/test_e2e_routine_fixtures.py.
const graphFixtures = JSON.parse(
  readFileSync(new URL('../fixtures/routine-graphs.json', import.meta.url), 'utf8'),
);

// Insert private copies of named fixture graphs (unique ids, so parallel
// workers never share or delete each other's rows) and return
// `{ fixtureId: seededId }`. Pair with `removeRoutines(Object.values(ids))`.
export function seedGraphFixtures(fixtureIds) {
  const suffix = randomUUID().replaceAll('-', '').slice(0, 8);
  const ids = {};
  for (const fixtureId of fixtureIds) {
    const fixture = graphFixtures[fixtureId];
    if (!fixture) throw new Error(`unknown routine fixture: ${fixtureId}`);
    const id = `${fixtureId}-${suffix}`;
    const graph = { ...structuredClone(fixture), id, name: `${fixture.name} ${suffix}` };
    sql(`INSERT INTO store (prefix, key, value, created_at, updated_at)
         VALUES ('progenitor.state', ${quote(`routine:${id}`)}, ${quote(JSON.stringify({ payload: graph }))}::jsonb, now(), now());`);
    ids[fixtureId] = id;
  }
  return ids;
}

export function removeRoutines(ids) {
  for (const id of ids) removeRoutine(id);
}

export async function observeRoutineEvents(page) {
  await page.addInitScript(() => {
    window.__routineEvents = [];
    window.__routineDisconnects = 0;
    window.__routineSources = [];
    const NativeEventSource = window.EventSource;
    window.EventSource = class extends NativeEventSource {
      constructor(...args) {
        super(...args);
        window.__routineSources.push(this);
        this.addEventListener('error', () => { window.__routineDisconnects += 1; });
        this.addEventListener('ui.event', (message) => {
          const event = JSON.parse(message.data);
          if (event.type.startsWith('agent.routine.')) window.__routineEvents.push(event);
        });
      }
    };
  });
}
