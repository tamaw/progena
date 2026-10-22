import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';

const databaseUrl = process.env.PROGENITOR_DATABASE_URL
  || 'postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor';
const quote = (value) => `'${value.replaceAll("'", "''")}'`;

function sql(command) {
  execFileSync('psql', [databaseUrl, '-v', 'ON_ERROR_STOP=1', '-q'], { input: command });
}

function addA2AToRegistry(name) {
  const agentJson = JSON.stringify({
    name,
    url: 'http://localhost:9999',
    enabled: true,
    transport: 'http',
    kind: 'a2a_agent',
    description: 'Test A2A agent for snapshot regression',
    version: '1.0.0',
  });
  sql(`
    INSERT INTO store (prefix, key, value, created_at, updated_at)
    VALUES ('progenitor.state', 'registry', '{"payload": {"agents": []}}'::jsonb, now(), now())
    ON CONFLICT (prefix, key) DO NOTHING;

    UPDATE store
    SET value = jsonb_set(value, '{payload,agents}',
        COALESCE(value->'payload'->'agents', '[]'::jsonb) || ${quote(agentJson)}::jsonb),
        updated_at = now()
    WHERE prefix = 'progenitor.state' AND key = 'registry';
  `);
}

function removeA2AFromRegistry(name) {
  sql(`
    UPDATE store
    SET value = jsonb_set(value, '{payload,agents}',
        COALESCE((
            SELECT jsonb_agg(elem)
            FROM jsonb_array_elements(value->'payload'->'agents') AS elem
            WHERE elem->>'name' != ${quote(name)}
        ), '[]'::jsonb)),
        updated_at = now()
    WHERE prefix = 'progenitor.state' AND key = 'registry';
  `);
}

test.describe.configure({ mode: 'serial' });
test.setTimeout(60_000);

test('A2A agents appear in agents.snapshot and the Agents page', async ({ page }) => {
  const agentName = `pw-a2a-snapshot-${randomUUID().slice(0, 8)}`;
  addA2AToRegistry(agentName);

  try {
    await page.goto('/agents');
    await page.getByPlaceholder('Search agents...').fill(agentName);
    await expect(
      page.locator('.agents-agent-row__name').getByText(agentName, { exact: true })
    ).toBeVisible({ timeout: 20_000 });
  } finally {
    removeA2AFromRegistry(agentName);
  }
});
