import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';

export const inboxSmokeDatabaseUrl = process.env.INBOX_SMOKE_DATABASE_URL;

const databaseUrl = inboxSmokeDatabaseUrl || process.env.PROGENITOR_DATABASE_URL;

const quote = (value) => `'${value.replaceAll("'", "''")}'`;

function sql(command) {
  if (!databaseUrl) throw new Error('No database URL configured');
  execFileSync('psql', [databaseUrl, '-v', 'ON_ERROR_STOP=1', '-q'], { input: command });
}

export function seedInboxMessage({ id, title, body, createdAt = new Date().toISOString(), read = false }) {
  const payload = JSON.stringify({ payload: { id, title, body, created_at: createdAt, read } });
  sql(`INSERT INTO store (prefix, key, value, created_at, updated_at)
       VALUES ('progenitor.state', ${quote(`inbox_message:${id}`)}, ${quote(payload)}::jsonb, now(), now())
       ON CONFLICT (prefix, key) DO UPDATE SET value = ${quote(payload)}::jsonb, updated_at = now();`);
}

export function removeInboxMessage(id) {
  sql(`DELETE FROM store WHERE prefix = 'progenitor.state' AND key = ${quote(`inbox_message:${id}`)};`);
}

export function updateInboxBody(id, newBody) {
  sql(`UPDATE store
       SET value = jsonb_set(value, '{payload,body}', to_jsonb(${quote(newBody)}::text)),
           updated_at = now()
       WHERE prefix = 'progenitor.state' AND key = ${quote(`inbox_message:${id}`)};`);
}

export function uniqueInboxId(prefix = 'pw') {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}_${randomUUID().slice(0, 6)}`;
}
