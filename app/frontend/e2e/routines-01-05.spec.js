import { test, expect } from '@playwright/test';
import { attachJsonEvidence, observeRoutineEvents } from './helpers/routineFixture.mjs';

// PR #342 functional journeys 01–05. All resources are created through the
// Routines/quick-chat UI and carry a per-run pw342 name. Graph edits and runs
// remain UI actions; this spec does not call backend endpoints or seed data.
const RUN = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
const pageErrors = new WeakMap();
const createdRoutines = new WeakMap();
const attemptedRoutineNames = new WeakMap();

test.describe.configure({ timeout: 200_000 });

function routineName(journey) {
  return `pw342-${RUN}-j${journey}`;
}

async function createRoutine(page, name, request) {
  attemptedRoutineNames.get(page).push(name);
  await page.goto('/routines');
  await expect(page.getByRole('button', { name: '+ New' })).toBeVisible();
  await page.getByRole('button', { name: '+ New' }).click();
  const input = page.locator('.qchat-input textarea, .qchat-input input').first();
  await expect(input).toBeVisible({ timeout: 10_000 });
  await input.fill(`${request} Name it exactly ${name}.`);
  await page.getByRole('button', { name: 'Send message' }).click();
  // Only treat the routine as created when its actual roster row appears.
  const routineButton = page.getByRole('button', { name: new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) });
  await expect(routineButton).toBeVisible({ timeout: 120_000 });
  // Quick chat is modal: its scrim intercepts clicks on the routine roster.
  await page.getByRole('button', { name: 'Close panel' }).click();
  await expect(page.locator('.topnav-scrim')).toBeHidden();
  await routineButton.click();
  const routineId = decodeURIComponent(new URL(page.url()).pathname.split('/').filter(Boolean).at(-1));
  createdRoutines.get(page).push({ id: routineId, name });
  await expect(page.locator('.routine-chain')).toBeVisible({ timeout: 30_000 });
}

async function deleteCreatedRoutine(page, { id, name }) {
  await page.goto(`/routines/${encodeURIComponent(id)}`);
  await expect(page.locator('.agents-graph-floating').first()).toBeVisible({ timeout: 20_000 });
  await page.getByRole('button', { name: 'Delete routine' }).click();
  const dialog = page.getByRole('dialog', { name: `Delete “${name}”?` });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Delete routine' }).click();
  await expect(dialog).toHaveCount(0, { timeout: 30_000 });
  await expect(page.getByRole('button', { name: new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) })).toHaveCount(0, { timeout: 20_000 });
}

async function readGraph(page) {
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  const editor = page.locator('.agents-graph-editor textarea');
  const doc = JSON.parse(await editor.inputValue());
  await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
  return doc;
}

async function applyGraph(page, graph) {
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  await page.locator('.agents-graph-editor textarea').fill(JSON.stringify(graph, null, 2));
  await page.getByRole('button', { name: 'Apply changes' }).click();
  await expect(page.locator('.agents-graph-editor__saved')).toContainText('Graph saved.', { timeout: 20_000 });
  await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
  await expect(page.locator('.agents-graph-validation-action')).toHaveAttribute('aria-label', 'Graph is valid');
}

function evalGraph(graph, expression) {
  const evalNode = graph.nodes.find((node) => node.kind === 'eval') || graph.nodes[0];
  const nodeId = evalNode.id;
  return {
    ...graph,
    version: '2.0.0',
    entry: nodeId,
    nodes: [{
      id: nodeId,
      kind: 'eval',
      label: 'Transform supplied value',
      output_key: 'result',
      description: 'Deterministic PR342 functional test step',
      config: { expression },
    }],
    edges: [],
    input_schema: {
      type: 'object',
      properties: { value: { type: 'number' } },
      required: ['value'],
    },
  };
}

async function runWithValue(page, value) {
  await page.getByRole('button', { name: 'Run routine' }).click();
  const dialog = page.getByRole('dialog', { name: 'Set parameters' });
  await expect(dialog).toBeVisible();
  // Type key by key (not fill): each keystroke re-renders the editor, and the
  // modal must not steal focus back to its Run button between keys.
  const input = dialog.getByRole('spinbutton', { name: 'value' });
  await input.click();
  await input.pressSequentially(String(value));
  await expect(input).toBeFocused();
  await expect(input).toHaveValue(String(value));
  await dialog.getByRole('button', { name: 'Run' }).click();
}

async function expectEvalResult(page, expected) {
  await page.locator('.canvas-node').first().click();
  // Concurrent journey authoring shares the runtime queue; wait for the actual
  // output rather than assuming execution starts within one heartbeat.
  await expect(page.locator('.routine-node-inspector__run-output')).toContainText(String(expected), { timeout: 180_000 });
}

test.beforeEach(async ({ page }) => {
  pageErrors.set(page, []);
  createdRoutines.set(page, []);
  attemptedRoutineNames.set(page, []);
  page.on('pageerror', (error) => pageErrors.get(page).push(error.message));
});

test.afterEach(async ({ page }, testInfo) => {
  const cleanupErrors = [];
  // If authoring persisted a routine but the test failed before selecting its
  // roster button and capturing the ID, discover only this test's unique name
  // through the UI and record the resulting selected route ID for deletion.
  const knownIds = new Set((createdRoutines.get(page) || []).map(({ id }) => id));
  for (const name of attemptedRoutineNames.get(page) || []) {
    if ([...(createdRoutines.get(page) || [])].some((resource) => resource.name === name)) continue;
    try {
      await page.goto('/routines');
      const candidate = page.getByRole('button', { name: new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) });
      if (await candidate.count()) {
        const closePanel = page.getByRole('button', { name: 'Close panel' });
        if (await closePanel.isVisible().catch(() => false)) await closePanel.click();
        await expect(page.locator('.topnav-scrim')).toBeHidden();
        await candidate.first().click();
        const id = decodeURIComponent(new URL(page.url()).pathname.split('/').filter(Boolean).at(-1));
        if (id && !knownIds.has(id)) createdRoutines.get(page).push({ id, name });
      }
    } catch (error) {
      cleanupErrors.push(`${name} discovery: ${error.message}`);
    }
  }
  for (const resource of [...(createdRoutines.get(page) || [])].reverse()) {
    try {
      await deleteCreatedRoutine(page, resource);
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

test('01 creates, saves, reloads, and runs a deterministic Eval routine twice', async ({ page }) => {
  // Model-driven authoring can consume most of the shared 150-second suite
  // timeout before the graph edit, parameter entry, and two runs begin.
  test.setTimeout(600_000);
  const name = routineName('01');
  await createRoutine(page, name, 'Create a simple routine with one deterministic Eval step that doubles a supplied number');
  const graph = evalGraph(await readGraph(page), 'value * 2');
  await applyGraph(page, graph);
  const routineUrl = page.url();

  await runWithValue(page, 4);
  // A graph with one terminal Eval has no projector node or extra final
  // checkpoint. Assert the node output delivered by the checkpoint SSE event.
  await expectEvalResult(page, 8);
  await page.reload();
  await expect(page.locator('.routine-chain')).toBeVisible();
  await expect(page.locator('.canvas-node')).toContainText('Transform supplied value');
  await runWithValue(page, 7);
  await expectEvalResult(page, 14);
  await page.getByRole('button', { name: 'Routine run history' }).click();
  const history = page.locator('.agents-graph-runs');
  await expect(history).toBeVisible();
  await expect(history.locator('.agents-graph-runs__item')).toHaveCount(2, { timeout: 30_000 });
  await expect(history.locator('.agents-graph-runs__status--success')).toHaveCount(2);
  expect(page.url()).toBe(routineUrl);
});

test('02 Progenitor-authored routine appears in the live roster and accepts a user edit', async ({ page }) => {
  // Model-driven routine creation can take several minutes; keep the timeout
  // separate from assertions on save, reload, and persisted prompt text.
  test.setTimeout(600_000);
  const name = routineName('02');
  await createRoutine(page, name, 'Create a short greeting routine with exactly one native prompt node using config.prompt_template to greet the supplied name. Include a required string name input. Do not use an eval node or another node kind in place of the prompt node.');
  const graph = await readGraph(page);
  expect(graph.nodes.length).toBeGreaterThan(0);
  // UI-authoring itself is asserted by createRoutine waiting for the persisted
  // roster row; the user-authored edit below is an explicit saved graph edit.
  const promptNode = graph.nodes.find((node) => node.kind === 'prompt');
  expect(promptNode, 'Authoring must persist the requested native prompt node').toBeTruthy();
  promptNode.config.prompt_template = 'Say exactly: Hello, {name}!';
  await applyGraph(page, graph);
  await page.reload();
  await expect(page.locator('.routine-chain')).toBeVisible();
  expect((await readGraph(page)).nodes.find((node) => node.kind === 'prompt')?.config.prompt_template).toBe('Say exactly: Hello, {name}!');
});

test('03 creates and edits the three-input classifier rubric', async ({ page }, testInfo) => {
  test.setTimeout(600_000);
  const name = routineName('03');
  await observeRoutineEvents(page);
  await createRoutine(page, name, 'Create a NEW routine using these native node kinds exactly: foreach mode send over input texts with item_key text and max_items 3; classify body with config.rubric_prompt and config.response_schema; noop Join each; reduce operation collect with source_node pointing to the classify body. Include the explicit foreach_empty continuation to Join each. Input schema texts is an array of strings. The classifier response is an object with required text and category fields, category enum question/request/statement, additionalProperties false. Classify a question-mark sentence as question, a sentence beginning Please as request, otherwise statement. Include the actual classify node, not a prompt or eval imitation. Read the managing-routines skill and schema and persist the graph document.');
  const graph = await readGraph(page);
  const classifier = graph.nodes.find((node) => node.kind === 'classify');
  expect(classifier, 'Authoring must persist a native classify node; missing content is a failure').toBeTruthy();
  expect(classifier.config.rubric_prompt).toBeTruthy();
  expect(classifier.config.response_schema).toBeTruthy();
  const responseSchema = { title: 'TextClassification', type: 'object', additionalProperties: false, required: ['text', 'category'], properties: { text: { type: 'string' }, category: { type: 'string', enum: ['question', 'request', 'statement'] } } };
  // Configure a repeatable user fixture around the native classifier authored
  // by Progenitor. The model still performs each classification at runtime.
  const rubric = 'Classify this text: {text}\nReturn the text verbatim and category. A sentence ending in ? is question, one beginning Please is request, otherwise statement. Treat Could you sentences ending in ? as question.';
  const configured = {
    ...graph, version: '2.0.0', entry: 'fan',
    nodes: [
      { id: 'fan', kind: 'foreach', label: 'Each text', output_key: 'fan', config: { mode: 'send', items_from: 'texts', item_key: 'text', max_items: 3 } },
      { ...classifier, output_key: classifier.id, config: { rubric_prompt: rubric, response_schema: responseSchema } },
      { id: 'join_each', kind: 'noop', label: 'Join each', output_key: 'join_each', config: {} },
      { id: 'collected', kind: 'reduce', label: 'Collect classifications', output_key: 'collected', config: { source_node: classifier.id, operation: 'collect' } },
    ],
    edges: [
      { from: 'fan', to: classifier.id, level: 'normal' },
      { from: classifier.id, to: 'join_each', level: 'normal' },
      { from: 'fan', to: 'join_each', level: 'foreach_empty' },
      { from: 'join_each', to: 'collected', level: 'normal' },
    ],
    input_schema: { type: 'object', required: ['texts'], properties: { texts: { type: 'array', items: { type: 'string' }, minItems: 3, maxItems: 3 } } },
    output_schema: { type: 'object', required: ['collected'], properties: { collected: { type: 'array', items: responseSchema } } },
  };
  await applyGraph(page, configured);
  const texts = ['What is 2 plus 2?', 'Please open the window.', 'Could you open the window?'];
  const evidence = [];
  async function executeAndVerify(ambiguousCategory) {
    await page.getByRole('button', { name: 'Run routine', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Set parameters' });
    await expect(dialog).toBeVisible();
    await dialog.locator('textarea').fill(JSON.stringify(texts));
    const posted = page.waitForRequest((request) => request.method() === 'POST' && /\/agent-profiles\/[^/]+\/run$/.test(request.url()));
    await dialog.getByRole('button', { name: 'Run', exact: true }).click();
    const requestId = (await posted).headers()['x-request-id'];
    let terminal;
    await expect.poll(async () => {
      terminal = await page.evaluate((id) => window.__routineEvents.find((event) =>
        event.payload?.request_id === id && ['agent.routine.run.completed', 'agent.routine.run.failed'].includes(event.type)), requestId);
      return Boolean(terminal);
    }, { timeout: 180_000 }).toBe(true);
    expect(terminal.type, terminal.payload?.error || 'Classifier execution must complete').toBe('agent.routine.run.completed');
    await page.locator('[data-node-id="collected"]').click();
    const output = page.locator('.routine-node-inspector__run-output pre');
    await expect(output).toBeVisible({ timeout: 180_000 });
    const results = JSON.parse(await output.innerText());
    expect(results).toHaveLength(3);
    expect(Object.fromEntries(results.map((result) => [result.text, result.category]))).toEqual({
      [texts[0]]: 'question', [texts[1]]: 'request', [texts[2]]: ambiguousCategory,
    });
    await page.locator('.routine-node-inspector__close').click();
    await page.locator(`[data-node-id="${classifier.id}"]`).click();
    await expect(page.locator('.routine-node-inspector__run-count')).toHaveText('3 executions this run');
    await page.locator('.routine-node-inspector__close').click();
    await expect(page.getByRole('button', { name: 'Run routine', exact: true })).toBeEnabled({ timeout: 180_000 });
    await expect.poll(() => page.evaluate(() => window.__routineEvents.some((event) => event.type === 'agent.routine.run.completed')), { timeout: 30_000 }).toBe(true);
    evidence.push({ results, events: await page.evaluate(() => window.__routineEvents) });
  }
  await executeAndVerify('question');
  await attachJsonEvidence(testInfo, 'classifier-first-run-evidence', evidence);
  configured.nodes.find((node) => node.id === classifier.id).config.rubric_prompt = 'Categorize the following sentence as question, request, or statement: {text}\nA polite sentence beginning Could you or Please is a request, including when it ends in a question mark. Other sentences ending in a question mark are questions. Remaining sentences are statements. Return the original text verbatim and its category.';
  await applyGraph(page, configured);
  await page.reload();
  await expect(page.locator('.routine-chain')).toBeVisible();
  expect((await readGraph(page)).nodes.find((node) => node.kind === 'classify')?.config.rubric_prompt).toContain('beginning Could you or Please');
  await executeAndVerify('request');
  await page.getByRole('button', { name: 'Routine run history' }).click();
  await expect(page.locator('.agents-graph-runs__status--success')).toHaveCount(2, { timeout: 180_000 });
  await attachJsonEvidence(testInfo, 'classifier-rubric-evidence', evidence);
});

test('04 rejects an invalid dangling edge and saves a repaired graph', async ({ page }) => {
  test.setTimeout(600_000);
  const name = routineName('04');
  await createRoutine(page, name, 'Create a simple deterministic routine with one Eval step that returns the supplied value');
  const graph = evalGraph(await readGraph(page), 'value');
  await applyGraph(page, graph);
  const invalid = structuredClone(graph);
  invalid.edges = [{ from: invalid.entry, to: `pw342-missing-${RUN}`, level: 'normal' }];
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  await page.locator('.agents-graph-editor textarea').fill(JSON.stringify(invalid, null, 2));
  await page.getByRole('button', { name: 'Apply changes' }).click();
  await expect(page.locator('.agents-graph-editor__error')).toBeVisible();
  await expect(page.locator('.agents-graph-editor__error')).not.toBeEmpty();
  const current = JSON.parse(await page.locator('.agents-graph-editor textarea').inputValue());
  expect(current.edges[0].to).toBe(`pw342-missing-${RUN}`);
  await page.locator('.agents-graph-editor textarea').fill(JSON.stringify(graph, null, 2));
  await page.getByRole('button', { name: 'Apply changes' }).click();
  await expect(page.locator('.agents-graph-editor__saved')).toContainText('Graph saved.', { timeout: 20_000 });
  await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
  await page.reload();
  expect((await readGraph(page)).edges).toEqual([]);
});

test('05 records a runtime Eval failure, then succeeds after correcting the expression', async ({ page }) => {
  test.setTimeout(600_000);
  const name = routineName('05');
  await createRoutine(page, name, 'Create a simple deterministic routine with one Eval step that divides a supplied number');
  const graph = evalGraph(await readGraph(page), '10 / value');
  await applyGraph(page, graph);
  await runWithValue(page, 0);
  await page.getByRole('button', { name: 'Routine run history' }).click();
  const history = page.locator('.agents-graph-runs');
  await expect(history.locator('.agents-graph-runs__status--failed').first()).toBeVisible({ timeout: 180_000 });
  await page.getByRole('button', { name: 'Collapse run history' }).click();
  const repaired = { ...graph, nodes: [{ ...graph.nodes[0], config: { expression: '0 if value == 0 else 10 / value' } }] };
  await applyGraph(page, repaired);
  await page.reload();
  await runWithValue(page, 0);
  await page.getByRole('button', { name: 'Routine run history' }).click();
  const updatedHistory = page.locator('.agents-graph-runs');
  await expect(updatedHistory.locator('.agents-graph-runs__status--success').first()).toBeVisible({ timeout: 180_000 });
  await expect(updatedHistory.locator('.agents-graph-runs__status--failed')).toBeVisible();
});
