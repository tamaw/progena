import { test, expect } from '@playwright/test';

const RUN = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
const pageErrors = new WeakMap();
const createdRoutines = new WeakMap();
const attemptedRoutineNames = new WeakMap();
const protectedRoutineIds = new WeakMap();
const journeyEvidence = new WeakMap();

function escapedName(name) {
  return name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function routineName(journey) {
  return `pw342-${RUN}-j${journey}`;
}

async function createRoutine(page, name) {
  attemptedRoutineNames.get(page).push(name);
  await page.goto('/routines');
  await expect(page.getByRole('button', { name: '+ New' })).toBeVisible();
  const selectedId = decodeURIComponent(new URL(page.url()).pathname.split('/').filter(Boolean).at(-1) || '');
  if (selectedId !== 'routines') protectedRoutineIds.get(page).add(selectedId);
  await page.getByRole('button', { name: '+ New' }).click();
  const newChat = page.getByRole('button', { name: 'New chat' });
  if (await newChat.isVisible().catch(() => false)) await newChat.click();
  const input = page.locator('.qchat-input textarea, .qchat-input input').first();
  await expect(input).toBeVisible({ timeout: 10_000 });
  await input.fill(`Create a NEW routine as a separate resource with one deterministic Eval step that returns a supplied number. Give the new routine the exact name ${name}. Do not rename or edit any existing or selected routine.`);
  await page.getByRole('button', { name: 'Send message' }).click();
  const row = page.getByRole('button', { name: new RegExp(escapedName(name)) });
  await expect(row).toHaveCount(1, { timeout: 120_000 });
  await expect(row).toBeVisible();
  // Quick chat's scrim intercepts clicks on the routine roster until closed.
  const closePanel = page.getByRole('button', { name: 'Close panel' });
  if (await closePanel.isVisible().catch(() => false)) await closePanel.click();
  await expect(page.locator('.topnav-scrim')).toBeHidden();
  await row.click();
  const routineId = decodeURIComponent(new URL(page.url()).pathname.split('/').filter(Boolean).at(-1));
  if (protectedRoutineIds.get(page).has(routineId)) {
    throw new Error(`Routine creation selected pre-existing resource ${routineId}; refusing to edit or delete it.`);
  }
  createdRoutines.get(page).push({ id: routineId, name });
  await expect(page.locator('.routine-chain')).toBeVisible({ timeout: 30_000 });
  return row;
}

async function readGraph(page) {
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  const editor = page.locator('.agents-graph-editor textarea');
  const graph = JSON.parse(await editor.inputValue());
  await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
  return graph;
}

async function saveSimpleGraph(page, graph) {
  const nodeId = graph.nodes.find((node) => node.kind === 'eval')?.id || graph.nodes[0]?.id;
  const deterministic = {
    ...graph,
    version: '2.0.0',
    entry: nodeId,
    nodes: [{
      id: nodeId,
      kind: 'eval',
      label: 'PR342 deletion test result',
      output_key: 'result',
      description: 'Deterministic disposable test routine',
      config: { expression: 'value + 1' },
    }],
    edges: [],
    input_schema: { type: 'object', properties: { value: { type: 'number' } }, required: ['value'] },
  };
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  await page.locator('.agents-graph-editor textarea').fill(JSON.stringify(deterministic, null, 2));
  await page.getByRole('button', { name: 'Apply changes' }).click();
  await expect(page.locator('.agents-graph-editor__saved')).toContainText('Graph saved.', { timeout: 20_000 });
  await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
  await expect(page.locator('.agents-graph-validation-action')).toHaveAttribute('aria-label', 'Graph is valid');
  return deterministic;
}

async function readUiEvents(page) {
  return page.evaluate(() => window.__pw342UiEvents || []);
}

async function runOnce(page, routineId) {
  await page.getByRole('button', { name: 'Run routine' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('spinbutton', { name: 'value' }).fill('8');
  await dialog.getByRole('button', { name: 'Run', exact: true }).click();
  // Verify the actual node result; a cleared pending badge alone can precede
  // the inspector output being rendered.
  await page.locator('.canvas-node').click();
  await expect(page.locator('.routine-node-inspector__run-output')).toContainText('9', { timeout: 180_000 });
  await page.getByRole('button', { name: 'Routine run history' }).click();
  const history = page.locator('.agents-graph-runs');
  await expect(history).toBeVisible();
  await expect(history.locator('.agents-graph-runs__status--success')).toHaveCount(1, { timeout: 180_000 });
  await expect.poll(async () => (await readUiEvents(page)).some((event) => (
    event.type === 'agent.routine.runs.loaded'
    && event.payload?.agent_id === routineId
    && Array.isArray(event.payload?.runs)
    && event.payload.runs.some((run) => run.run_id && run.thread_id)
  )), { timeout: 30_000 }).toBe(true);
  const runEvent = (await readUiEvents(page)).find((event) => (
    event.type === 'agent.routine.runs.loaded'
    && event.payload?.agent_id === routineId
    && event.payload.runs?.some((run) => run.run_id && run.thread_id)
  ));
  const run = runEvent.payload.runs.find((item) => item.run_id && item.thread_id);
  expect(run.failed).toBe(false);
  return {
    run_id: run.run_id,
    thread_id: run.thread_id,
    revision_id: run.revision_id,
    event_id: runEvent.id,
    timestamp: runEvent.timestamp,
  };
}

test.beforeEach(async ({ page }) => {
  pageErrors.set(page, []);
  createdRoutines.set(page, []);
  attemptedRoutineNames.set(page, []);
  protectedRoutineIds.set(page, new Set());
  journeyEvidence.set(page, {});
  await page.addInitScript(() => {
    window.__pw342UiEvents = [];
    const NativeEventSource = window.EventSource;
    function ObservedEventSource(url, config) {
      const source = new NativeEventSource(url, config);
      source.addEventListener('ui.event', (event) => {
        try { window.__pw342UiEvents.push(JSON.parse(event.data)); } catch { /* ignore malformed frames */ }
      });
      return source;
    }
    ObservedEventSource.prototype = NativeEventSource.prototype;
    Object.setPrototypeOf(ObservedEventSource, NativeEventSource);
    window.EventSource = ObservedEventSource;
  });
  page.on('pageerror', (error) => pageErrors.get(page).push(error.message));
});

test.afterEach(async ({ page }, testInfo) => {
  const recorded = journeyEvidence.get(page) || {};
  const observedEvents = await page.evaluate(() => (window.__pw342UiEvents || []).map((event) => ({
    id: event.id,
    type: event.type,
    timestamp: event.timestamp,
    request_id: event.payload?.request_id,
    agent_id: event.payload?.agent_id,
    runs: Array.isArray(event.payload?.runs) ? event.payload.runs.map((run) => ({
      run_id: run.run_id,
      thread_id: run.thread_id,
      revision_id: run.revision_id,
      failed: run.failed,
    })) : undefined,
  })).filter((event) => event.agent_id && event.type.startsWith('agent.')));
  await testInfo.attach('pr342-journey18-evidence.json', {
    body: JSON.stringify({ ...recorded, observedEvents }, null, 2),
    contentType: 'application/json',
  });
  const cleanupErrors = [];
  const resources = createdRoutines.get(page) || [];
  const knownNames = new Set(resources.map(({ name }) => name));
  // Discover a resource through its unique roster name if authoring succeeded
  // but the test failed before recording the selected route ID.
  for (const name of attemptedRoutineNames.get(page) || []) {
    if (knownNames.has(name)) continue;
    try {
      await page.goto('/routines');
      const closePanel = page.getByRole('button', { name: 'Close panel' });
      if (await closePanel.isVisible().catch(() => false)) await closePanel.click();
      await expect(page.locator('.topnav-scrim')).toBeHidden();
      const row = page.getByRole('button', { name: new RegExp(escapedName(name)) });
      const count = await row.count();
      if (count === 1) {
        await row.click();
        const id = decodeURIComponent(new URL(page.url()).pathname.split('/').filter(Boolean).at(-1));
        if (!protectedRoutineIds.get(page).has(id)) {
          resources.push({ id, name });
          knownNames.add(name);
        }
      }
    } catch (error) {
      cleanupErrors.push(`${name} discovery: ${error.message}`);
    }
  }
  for (const resource of [...resources].reverse()) {
    try {
      await page.goto('/routines');
      const closePanel = page.getByRole('button', { name: 'Close panel' });
      if (await closePanel.isVisible().catch(() => false)) await closePanel.click();
      await expect(page.locator('.topnav-scrim')).toBeHidden();
      const row = page.getByRole('button', { name: new RegExp(escapedName(resource.name)) });
      const count = await row.count();
      // The journey itself may already have deleted the routine successfully.
      if (count === 0) continue;
      if (count !== 1) throw new Error(`Expected one UI row for ${resource.name}, found ${count}.`);
      await row.click();
      const selectedId = decodeURIComponent(new URL(page.url()).pathname.split('/').filter(Boolean).at(-1));
      if (selectedId !== resource.id) {
        throw new Error(`UI row ${resource.name} resolved to ${selectedId}, expected created resource ${resource.id}.`);
      }
      await page.getByRole('button', { name: 'Delete routine' }).click();
      const dialog = page.getByRole('dialog', { name: `Delete “${resource.name}”?` });
      await expect(dialog).toBeVisible();
      await dialog.getByRole('button', { name: 'Delete routine', exact: true }).click();
      await expect(page.getByRole('button', { name: new RegExp(escapedName(resource.name)) })).toHaveCount(0, { timeout: 30_000 });
    } catch (error) {
      cleanupErrors.push(`${resource.id} (${resource.name}): ${error.message}`);
    }
  }
  if (cleanupErrors.length) {
    await testInfo.attach('routine-cleanup-errors', {
      body: cleanupErrors.join('\n'),
      contentType: 'text/plain',
    });
    throw new Error(`Could not clean up created routine(s): ${cleanupErrors.join('; ')}`);
  }
  expect(pageErrors.get(page) || []).toEqual([]);
});

// J16 runs in delegated-routines.spec.js with native checkpoint evidence.

// J17 runs in a2a-timeout.spec.js with the existing greetings agent,
// a separate object agent, and native hello-world-async completion evidence.

test('18 user deletes a created routine while a second tab watches its live roster', async ({ page, context }) => {
  test.setTimeout(600_000);
  const name = routineName('18');
  const watcher = await context.newPage();
  const watcherErrors = [];
  watcher.on('pageerror', (error) => watcherErrors.push(error.message));

  try {
    await createRoutine(page, name);
    const routineUrl = page.url();
    const graph = await readGraph(page);
    await saveSimpleGraph(page, graph);
    const routineId = decodeURIComponent(new URL(page.url()).pathname.split('/').filter(Boolean).at(-1));
    const run = await runOnce(page, routineId);
    journeyEvidence.set(page, { resource_id: routineId, resource_name: name, run });

    await watcher.goto('/routines');
    const watcherRow = watcher.getByRole('button', { name: new RegExp(escapedName(name)) });
    await expect(watcherRow).toHaveCount(1, { timeout: 30_000 });
    await expect(watcherRow).toBeVisible();
    const closeWatcherPanel = watcher.getByRole('button', { name: 'Close panel' });
    if (await closeWatcherPanel.isVisible().catch(() => false)) await closeWatcherPanel.click();
    await expect(watcher.locator('.topnav-scrim')).toBeHidden();
    await watcherRow.click();
    await expect(watcher.locator('.routine-chain')).toBeVisible({ timeout: 30_000 });
    await expect(watcher).toHaveURL(routineUrl);

    // Reopening the second tab exercises a fresh stream subscription before
    // deletion; both tabs then remain open for the live deletion notification.
    await watcher.reload();
    await expect(watcher.locator('.routine-chain')).toBeVisible({ timeout: 30_000 });
    await page.getByRole('button', { name: 'Delete routine' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText(`Delete “${name}”?`);
    await dialog.getByRole('button', { name: 'Delete routine', exact: true }).click();
    await expect.poll(async () => (await readUiEvents(page)).some((event) => (
      event.type === 'agent.deleted' && event.payload?.agent_id === routineId
    )), { timeout: 30_000 }).toBe(true);
    await expect(page.getByRole('button', { name: new RegExp(escapedName(name)) })).toHaveCount(0, { timeout: 30_000 });
    await expect(watcher.getByRole('button', { name: new RegExp(escapedName(name)) })).toHaveCount(0, { timeout: 30_000 });
    await expect(watcher.locator('.routine-chain')).toHaveCount(0, { timeout: 20_000 });

    // A stale deep link must not reload the deleted routine into the detail pane.
    await watcher.goto(routineUrl);
    await expect(watcher.locator('.routine-chain')).toHaveCount(0, { timeout: 30_000 });
    expect(watcherErrors).toEqual([]);
    const deleteEvent = (await readUiEvents(page)).find((event) => (
      event.type === 'agent.deleted' && event.payload?.agent_id === routineId
    ));
    journeyEvidence.set(page, {
      ...journeyEvidence.get(page),
      delete_event_id: deleteEvent.id,
      delete_event_timestamp: deleteEvent.timestamp,
      watcher_roster_removed: true,
      stale_deep_link_empty: true,
      page_errors: pageErrors.get(page) || [],
      watcher_page_errors: watcherErrors,
      cleanup: 'deleted through routine UI; catalog absence confirmed in both tabs',
    });
  } finally {
    // The afterEach hook deletes only resources whose unique UI name and new
    // route ID were confirmed. Never delete the previously selected routine.
    await watcher.close();
  }
});

// J19 runs in collaboration.spec.js with live Quick Chat and tool audits.

// J20 is active in routine-author.spec.js: real Quick Chat setup,
// native approval checkpoint, and delegated child creation.
