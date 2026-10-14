import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { attachJsonEvidence } from './helpers/routineFixture.mjs';

// End-to-end knowledge-base journey against a live stack with a real model and
// the Jina web MCP server (needs JINA_API_KEY in the runtime's environment):
//   1. Progenitor registers the Jina MCP server.
//   2. Progenitor creates a knowledge base and a researcher object agent with
//      write access to it, then the researcher fills it from web research.
//   3. The pages are browsed in the Knowledge UI: index, breadcrumbs, revisions,
//      and a page another curator marked deprecated.
//   4. A second, read-only agent answers questions from the modified pages.
// Set KEEP_KNOWLEDGE_E2E=1 to keep the created base/agents for manual viewing.
// Pick the research topic with KNOWLEDGE_E2E_TOPIC=mcp|raft (default mcp).

const databaseUrl = process.env.PROGENITOR_DATABASE_URL
  || 'postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor';
const RUN = Date.now().toString(36).slice(-5);
// Research topics. `keyPage` names the page the answerer should cite;
// `keyFact` must appear in that page and the answer.
const TOPICS = {
  mcp: {
    name: 'MCP Research',
    slug: 'mcp-research',
    subject: 'the Model Context Protocol',
    scope: 'what it is, its architecture of hosts/clients/servers, and its transports',
    keyPage: /transport/,
    keyFact: /stdio/i,
    searchTerm: 'stdio',
    question: 'what is the Model Context Protocol and which transports does it define?',
  },
  raft: {
    name: 'Raft Research',
    slug: 'raft-research',
    subject: 'the Raft consensus algorithm',
    scope: 'what it is, how leader election works, and how log replication works',
    keyPage: /elect/,
    keyFact: /majority/i,
    searchTerm: 'majority',
    question: 'what is Raft and how does it elect a leader?',
  },
};
const TOPIC = TOPICS[process.env.KNOWLEDGE_E2E_TOPIC || 'mcp'];
if (!TOPIC) throw new Error(`KNOWLEDGE_E2E_TOPIC must be one of: ${Object.keys(TOPICS).join(', ')}`);
const BASE_NAME = `${TOPIC.name} ${RUN}`;
const BASE_ID = `${TOPIC.slug}-${RUN}`;
const RESEARCHER = `researcher-${RUN}`;
const ANSWERER = `kb-answerer-${RUN}`;

test.describe.configure({ mode: 'serial' });
test.setTimeout(15 * 60_000);

function sql(query) {
  return execFileSync('psql', [databaseUrl, '-v', 'ON_ERROR_STOP=1', '-Atq', '-c', query], { encoding: 'utf8' }).trim();
}
const quote = (value) => `'${value.replaceAll("'", "''")}'`;

function storedPaths() {
  const out = sql(`SELECT key FROM store
    WHERE prefix = 'progenitor.state' AND key LIKE ${quote(`knowledge:${BASE_ID}/%`)} ORDER BY key`);
  return out ? out.split('\n').map((key) => key.slice(`knowledge:${BASE_ID}/`.length)) : [];
}
const conceptPaths = () => storedPaths()
  .filter((path) => path !== 'log.md' && !path.endsWith('/index.md'));

function agentManifest(name) {
  return sql(`SELECT value->'payload'->>'content' FROM store
    WHERE prefix = 'progenitor.state' AND key = ${quote(`agent:user/${name}/SYSTEM.md`)}`);
}

async function poll(fn, message, timeout = 300_000) {
  await expect.poll(fn, { message, timeout, intervals: [2_000, 5_000] }).toBeTruthy();
}

// Send one message in a fresh chat and wait for the run to finish.
async function chat(page, prompt) {
  await page.goto('/chat');
  await page.getByRole('button', { name: '+ New', exact: true }).click();
  const input = page.getByPlaceholder('What should we work on next?');
  await expect(input).toBeVisible();
  await input.fill(prompt);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  const stop = page.getByRole('button', { name: 'Stop generation' });
  await expect(stop).toBeVisible({ timeout: 60_000 });
  await expect(stop).toHaveCount(0, { timeout: 10 * 60_000 });
  const reply = page.locator('.msg--assistant .message-content').last();
  await expect(reply).toBeVisible({ timeout: 30_000 });
  return reply.innerText();
}

async function openKnowledgeBase(page) {
  await page.goto('/knowledge');
  await page.getByRole('button', { name: new RegExp(BASE_NAME) }).click();
}

test.afterAll(() => {
  if (process.env.KEEP_KNOWLEDGE_E2E) return;
  sql(`DELETE FROM store WHERE prefix = 'progenitor.state' AND (key LIKE ${quote(`knowledge:${BASE_ID}/%`)}
    OR key IN (${quote(`agent:user/${RESEARCHER}/SYSTEM.md`)}, ${quote(`agent:user/${ANSWERER}/SYSTEM.md`)}))`);
  sql(`UPDATE store SET value = jsonb_set(value, '{payload,bases}', coalesce((
      SELECT jsonb_agg(base) FROM jsonb_array_elements(value->'payload'->'bases') base
      WHERE base->>'id' <> ${quote(BASE_ID)}), '[]'::jsonb))
    WHERE prefix = 'progenitor.state' AND key = 'knowledge'`);
});

test('research agent fills a knowledge base from the web and a second agent answers from it', async ({ page }, testInfo) => {
  const evidence = { run: RUN, baseId: BASE_ID };

  await test.step('Progenitor registers the Jina web MCP server', async () => {
    const existing = sql(`SELECT value->'payload'->'mcpServers' ? 'jina' FROM store WHERE key = 'mcp'`);
    if (existing !== 't') {
      await chat(page, 'Add the Jina MCP server named "jina": url https://mcp.jina.ai/v1?include_tags=search,read, '
        + 'transport http, enabled, description "Jina web search and URL reading", header '
        + 'Authorization: "Bearer ${JINA_API_KEY}" (keep the placeholder literally; the runtime resolves it). '
        + 'Then confirm its tools were discovered.');
    }
    await poll(() => sql(`SELECT count(*) FROM store WHERE key = 'mcp_tools'
      AND value::text ILIKE '%jina%'`) !== '0', 'Jina MCP tools discovered');
    await page.goto('/tools');
    await expect(page.getByText(/jina/i).first()).toBeVisible({ timeout: 30_000 });
    evidence.mcp = 'jina';
  });

  await test.step('Progenitor creates the knowledge base and a researcher agent', async () => {
    await chat(page, `Create a knowledge base named "${BASE_NAME}" (its id will be ${BASE_ID}) with the description `
      + `"Research notes on ${TOPIC.subject}". Then create a user object agent named ${RESEARCHER} (description "Researches topics `
      + 'on the web and writes knowledge pages") that can use the Jina web search and read tools and has knowledge '
      + `write access to ${BASE_ID}. Do not run it yet.`);
    await poll(() => sql(`SELECT count(*) FROM store WHERE key = 'knowledge'
      AND value->'payload'->'bases' @> ${quote(JSON.stringify([{ id: BASE_ID }]))}::jsonb`) === '1', 'base created');
    await poll(() => new RegExp(`base:\\s*${BASE_ID}`).test(agentManifest(RESEARCHER)), 'researcher manifest grants the base');
    expect(agentManifest(RESEARCHER)).toMatch(/access:\s*write/);
    evidence.researcherManifest = agentManifest(RESEARCHER);
    await page.goto('/agents');
    await expect(page.getByText(RESEARCHER).first()).toBeVisible({ timeout: 30_000 });
  });

  await test.step('the researcher fills the knowledge base from web research', async () => {
    await chat(page, `Use the task tool with subagent_type "${RESEARCHER}" (a user object agent, not an A2A agent) to research ${TOPIC.subject} on the web (${TOPIC.scope}) `
      + 'and write at least three concept pages '
      + `into /knowledge/${BASE_ID}/, each with type, title, description and the source URLs in "sources", `
      + 'then append an ingest entry to log.md. Report the page paths it wrote.');
    await poll(() => conceptPaths().length >= 3, 'at least three concept pages written', 60_000);
    const sources = sql(`SELECT string_agg(value->'payload'->>'content', '\n') FROM store
      WHERE key LIKE ${quote(`knowledge:${BASE_ID}/%`)} AND key NOT LIKE '%log.md'`);
    expect(sources).toMatch(/^sources:/m);
    expect(sources).toMatch(/https?:\/\//);
    expect(sources).toMatch(/generated:\s*\n\s*at:/);
    expect(storedPaths()).toContain('log.md');
    evidence.researchedPages = conceptPaths();
  });

  let keyPath;
  await test.step('the pages are browsable in the Knowledge UI', async () => {
    await openKnowledgeBase(page);
    await poll(() => page.locator('.task-card.is-selected .kb-card__dot--ok').isVisible(), 'base shows indexed status', 120_000);
    await expect(page.locator('.task-card.is-selected .kb-card__info')).toContainText(`${conceptPaths().length} pages`);

    // The base index loads by default; its page links open in place, without a reload.
    const crumbs = page.locator('.knowledge-breadcrumbs');
    await expect(crumbs.getByRole('button', { name: `${BASE_NAME} root` })).toBeVisible();
    await crumbs.getByRole('button', { name: `Files in ${BASE_NAME}` }).click();
    const menu = page.locator('.knowledge-crumb-menu');
    await expect(menu.getByRole('button', { name: 'log.md' })).toBeVisible();
    await expect(menu.getByRole('button', { name: 'Index' })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await page.evaluate(() => { window.knowledgeE2eNoReload = true; });
    await page.locator('.knowledge-pages-tab .markdown-content a[href$=".md"]:not([href$="index.md"]):not([href="log.md"])').first().click();
    await expect(page).toHaveURL(/page=/);
    expect(await page.evaluate(() => window.knowledgeE2eNoReload)).toBe(true);

    // The key page: front matter facts in the header, sources and revisions below the body.
    const contents = Object.fromEntries(conceptPaths().map((path) => [path, sql(`SELECT value->'payload'->>'content'
      FROM store WHERE key = ${quote(`knowledge:${BASE_ID}/${path}`)}`)]));
    const hasKeyFact = (path) => TOPIC.keyFact.test(contents[path]);
    keyPath = conceptPaths().find((path) => TOPIC.keyPage.test(path) && hasKeyFact(path))
      || conceptPaths().find(hasKeyFact) || conceptPaths()[0];
    await page.goto(`${page.url().split('?')[0]}?page=${encodeURIComponent(keyPath)}`);
    const reader = page.locator('.knowledge-pages-tab article');
    await expect(page.getByText(/^generated /)).toBeVisible();
    await expect(reader.getByRole('heading', { name: /^sources$/i })).toHaveCount(1);
    await expect(reader.locator('.markdown-content')).not.toContainText('generated:');
    await expect(reader.getByRole('heading', { name: 'Revisions' })).toBeVisible();
    const revision = reader.locator('.knowledge-page-revisions a').first();
    await revision.click();
    await expect(page).toHaveURL(/revision=/);
    await expect(page.getByText(/^generated /)).toBeVisible();
    await revision.click();
    await expect(page).not.toHaveURL(/revision=/);
    await expect(page.getByText(/^generated /)).toBeVisible();

    // A curator retires another page: it leaves the index and the breadcrumb
    // menu but still opens by path.
    const retired = conceptPaths().find((path) => path !== keyPath && !path.includes('/'))
      || conceptPaths().find((path) => path !== keyPath);
    sql(`UPDATE store SET value = jsonb_set(value, '{payload,content}', to_jsonb(regexp_replace(
        value->'payload'->>'content', '^---\\n', E'---\\nstatus: deprecated\\n')))
      WHERE prefix = 'progenitor.state' AND key = ${quote(`knowledge:${BASE_ID}/${retired}`)}`);
    await page.goto(page.url().split('?')[0]);
    const index = page.locator('.knowledge-pages-tab .markdown-content');
    await expect(index.locator(`a[href="${retired}"]`)).toHaveCount(0, { timeout: 30_000 });
    await expect(index.locator(`a[href="${keyPath}"]`)).toHaveCount(keyPath.includes('/') ? 0 : 1);
    await crumbs.getByRole('button', { name: `Files in ${BASE_NAME}` }).click();
    await expect(menu.locator('button', { hasText: contents[retired].match(/^title:\s*(.+)$/m)?.[1] || retired })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await page.goto(`${page.url().split('?')[0]}?page=${encodeURIComponent(retired)}`);
    await expect(reader.locator('.markdown-content')).toBeVisible();
    await expect(page.locator('.knowledge-page-missing')).toHaveCount(0);
    evidence.keyPage = keyPath;
    evidence.deprecated = retired;
  });

  await test.step('global search finds knowledge pages', async () => {
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    const input = page.locator('.chrome-search-panel__input input');
    await input.fill(TOPIC.searchTerm);
    const firstResult = page.locator('.chrome-search-group .chrome-search-result').first();
    await expect(firstResult).toBeVisible({ timeout: 30_000 });
    await firstResult.click();
    await expect(page.locator('.knowledge-pages-tab article')).toBeVisible();
    await expect(page).toHaveURL(/page=/);
  });

  await test.step('a read-only agent answers from the modified knowledge base', async () => {
    await chat(page, `Create a user object agent named ${ANSWERER} (description "Answers questions from the `
      + `${TOPIC.name} knowledge base") with read-only knowledge access to ${BASE_ID} and no other tools. Do not run it yet.`);
    await poll(() => /access:\s*read/.test(agentManifest(ANSWERER)), 'answerer manifest grants read access');
    expect(agentManifest(ANSWERER)).not.toMatch(/access:\s*write/);

    const answer = await chat(page, `Use the task tool with subagent_type "${ANSWERER}" (a user object agent) to ask: "Using only the knowledge base, ${TOPIC.question} `
      + 'First use the search tool, then read the relevant pages. '
      + 'Cite the knowledge page path for every claim, and also '
      + 'try to add a page called notes/answerer.md and report whether that worked." Relay its full answer verbatim.');
    evidence.answer = answer;
    expect(answer).toContain(`/knowledge/${BASE_ID}/${keyPath}`);
    expect(answer).toMatch(TOPIC.keyFact);
    expect(conceptPaths()).not.toContain('notes/answerer.md');
  });

  await attachJsonEvidence(testInfo, 'knowledge-research', evidence);
});
