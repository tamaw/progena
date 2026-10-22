import { test, expect } from '@playwright/test';
import { attachJsonEvidence } from './helpers/routineFixture.mjs';

const uniqueRun = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
const createdRoutines = new WeakMap();
const attemptedRoutineNames = new WeakMap();
const pageErrors = new WeakMap();

const routineRow = (page, name) => page.locator('.pane-card-row').filter({ has: page.getByText(name, { exact: true }) });

function node(id, kind, outputKey, expression, extra = {}) {
  return {
    id,
    kind,
    label: id,
    output_key: outputKey,
    description: `PR342 functional test ${id}`,
    config: expression === undefined ? {} : { expression },
    ...extra,
  };
}

function graphFor(name, id, nodes, edges, inputSchema, outputSchema) {
  return {
    id,
    name,
    description: 'Disposable PR342 Playwright functional test routine.',
    version: '2.0.0',
    entry: nodes[0].id,
    nodes,
    edges,
    input_schema: inputSchema,
    output_schema: outputSchema,
    policy_id: 'default',
  };
}

async function createRoutine(page, name) {
  await page.addInitScript(() => {
    const eventStorageKey = '__pw342RoutineEvents';
    try {
      window.__pw342Events = JSON.parse(sessionStorage.getItem(eventStorageKey) || '[]');
    } catch {
      window.__pw342Events = [];
    }
    const original = EventSource.prototype.addEventListener;
    EventSource.prototype.addEventListener = function observeUiEvents(type, listener, options) {
      if (type !== 'ui.event') return original.call(this, type, listener, options);
      const wrapped = (event) => {
        try {
          const uiEvent = JSON.parse(event.data);
          if (uiEvent.type === 'agent.routine.run.enqueued' || uiEvent.type === 'agent.routine.runs.loaded') {
            window.__pw342Events.push(uiEvent);
            window.__pw342Events = window.__pw342Events.slice(-100);
            sessionStorage.setItem(eventStorageKey, JSON.stringify(window.__pw342Events));
          }
        } catch { /* the app reports malformed events */ }
        listener.call(this, event);
      };
      return original.call(this, type, wrapped, options);
    };
  });
  await page.goto('/routines');
  await expect(page.getByRole('button', { name: '+ New' })).toBeVisible({ timeout: 20_000 });
  await page.getByRole('button', { name: '+ New' }).click();
  const panel = page.getByRole('dialog', { name: 'Build a routine' });
  await expect(panel).toBeVisible();
  await panel.getByPlaceholder('Describe the routine you want...')
    .fill(`Create one routine named exactly "${name}". Keep it minimal; I will configure its graph in the editor.`);
  await panel.getByRole('button', { name: 'Send message' }).click();

  const row = routineRow(page, name);
  await expect(row).toHaveCount(1, { timeout: 180_000 });
  await expect(row).toBeVisible();
  // Quick chat is modal: its scrim intercepts clicks on the routine roster.
  await page.getByRole('button', { name: 'Close panel' }).click();
  await expect(page.locator('.topnav-scrim')).toBeHidden();
  await row.click();
  await expect(page.locator('.routine-chain')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.agents-graph-validation-action')).toHaveAttribute('aria-label', 'Graph is valid');
  const routineId = decodeURIComponent(new URL(page.url()).pathname.split('/').filter(Boolean).at(-1));
  createdRoutines.get(page).push({ id: routineId, name });
  return routineId;
}

async function applyGraph(page, graph) {
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  const textarea = page.locator('.agents-graph-editor__textarea');
  await expect(textarea).toBeVisible();
  await textarea.fill(JSON.stringify(graph, null, 2));
  await page.getByRole('button', { name: 'Apply changes' }).click();
  await expect(page.locator('.agents-graph-editor__saved')).toHaveText('Graph saved.', { timeout: 30_000 });
  await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
  await expect(page.locator('.agents-graph-validation-action')).toHaveAttribute('aria-label', 'Graph is valid');
}

async function readGraph(page) {
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  const value = await page.locator('.agents-graph-editor__textarea').inputValue();
  await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
  return JSON.parse(value);
}

async function reloadAndReadGraph(page) {
  await page.reload();
  await expect(page.locator('.routine-chain')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.canvas-node').first()).toBeVisible();
  return readGraph(page);
}

async function runWithValues(page, values = []) {
  await page.getByRole('button', { name: 'Run routine' }).click();
  const dialog = page.getByRole('dialog', { name: 'Set parameters' });
  if (values.length > 0) {
    await expect(dialog).toBeVisible();
    for (let index = 0; index < values.length; index += 1) {
      const input = dialog.locator('input').nth(index);
      await expect(input).toBeVisible();
      await input.fill(String(values[index]));
    }
    await dialog.getByRole('button', { name: 'Run', exact: true }).click();
  } else if (await dialog.count()) {
    await dialog.getByRole('button', { name: 'Run', exact: true }).click();
  }
}

async function runWithArray(page, value) {
  await page.getByRole('button', { name: 'Run routine' }).click();
  const dialog = page.getByRole('dialog', { name: 'Set parameters' });
  await expect(dialog).toBeVisible();
  await dialog.locator('textarea').fill(JSON.stringify(value));
  await dialog.getByRole('button', { name: 'Run', exact: true }).click();
}

async function assertRunEventIds(page, routineId, minimumRuns) {
  const inspectorClose = page.locator('.routine-node-inspector__close');
  if (await inspectorClose.isVisible().catch(() => false)) await inspectorClose.click();
  await page.getByRole('button', { name: 'Routine run history' }).click();
  const panel = page.locator('.agents-graph-runs');
  await expect(panel).toBeVisible();
  await panel.getByRole('button', { name: 'Refresh' }).click();
  await expect.poll(async () => {
    const events = await page.evaluate(() => window.__pw342Events || []);
    return events.some((event) => event.type === 'agent.routine.runs.loaded'
      && event.payload?.agent_id === routineId
      && (event.payload?.runs || []).length >= minimumRuns);
  }, { timeout: 30_000 }).toBe(true);
  const events = await page.evaluate(() => window.__pw342Events || []);
  const enqueued = events.filter((event) => event.type === 'agent.routine.run.enqueued'
    && event.payload?.agent_id === routineId);
  expect(enqueued.length, 'run enqueue events must name this routine').toBeGreaterThanOrEqual(minimumRuns);
  const historyEvent = events.filter((event) => event.type === 'agent.routine.runs.loaded'
    && event.payload?.agent_id === routineId
    && (event.payload?.runs || []).length >= minimumRuns).at(-1);
  expect(historyEvent.id).toBeTruthy();
  const observedRuns = historyEvent.payload.runs.slice(0, minimumRuns);
  const runIds = observedRuns.map((run) => run.run_id);
  expect(runIds).toHaveLength(minimumRuns);
  expect(new Set(runIds).size).toBe(minimumRuns);
  expect(observedRuns.every((run) => run.thread_id && run.run_id && run.revision_id && !run.failed)).toBe(true);
  await expect(panel.locator('.agents-graph-runs__status--success')).toHaveCount(minimumRuns);
  await page.getByRole('button', { name: 'Collapse run history' }).click();
  return observedRuns;
}

async function deleteCreatedRoutine(page, { id, name }) {
  await page.goto(`/routines/${encodeURIComponent(id)}`);
  const outputDialog = page.getByRole('dialog', { name: 'Output' });
  if (await outputDialog.isVisible().catch(() => false)) {
    await outputDialog.locator('.sidebar-modal__foot button').click({ timeout: 5_000 });
    await expect(outputDialog).toBeHidden({ timeout: 5_000 });
  }
  const scrim = page.locator('.topnav-scrim');
  if (await scrim.isVisible().catch(() => false)) {
    await page.getByRole('button', { name: 'Close panel' }).click();
  }
  await expect(scrim).toBeHidden();
  const deleteButton = page.getByRole('button', { name: 'Delete routine' });
  await expect(deleteButton).toBeVisible({ timeout: 30_000 });
  await deleteButton.click({ timeout: 30_000 });
  const dialog = page.getByRole('dialog', { name: `Delete “${name}”?` });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Delete routine', exact: true }).click();
  await expect(dialog).toHaveCount(0, { timeout: 30_000 });
  await expect(routineRow(page, name)).toHaveCount(0, { timeout: 30_000 });
}

async function expectNodeOutput(page, nodeId, expected) {
  await page.locator(`.canvas-node[data-node-id="${nodeId}"]`).click();
  const output = page.locator('.routine-node-inspector__run-output pre');
  const compactExpected = String(expected).replace(/\s+/g, '');
  await expect(output).toBeVisible({ timeout: 180_000 });
  await expect.poll(async () => (await output.textContent())?.replace(/\s+/g, '') || '', { timeout: 180_000 })
    .toContain(compactExpected);
  const inspectorClose = page.locator('.routine-node-inspector__close');
  if (await inspectorClose.isVisible().catch(() => false)) await inspectorClose.click();
}

async function expectRunButtonAvailable(page) {
  await expect(page.getByRole('button', { name: 'Run routine' })).toBeEnabled({ timeout: 180_000 });
}

async function withDisposableRoutine(page, name, testBody) {
  attemptedRoutineNames.get(page).push(name);
  const routineId = await createRoutine(page, name);
  await testBody(routineId);
}

test.beforeEach(async ({ page }) => {
  // Never let an intercepted UI click inherit the full 10-minute test timeout.
  page.setDefaultTimeout(30_000);
  createdRoutines.set(page, []);
  attemptedRoutineNames.set(page, []);
  pageErrors.set(page, []);
  page.on('pageerror', (error) => pageErrors.get(page).push(error.message));
});

test.afterEach(async ({ page }, testInfo) => {
  const errors = [];
  let cleanupPage = page;
  if (page.isClosed()) {
    try { cleanupPage = await page.context().newPage(); } catch (error) { errors.push(`open cleanup page: ${error.message}`); }
  }
  const resources = createdRoutines.get(page) || [];
  const knownNames = new Set(resources.map(({ name }) => name));
  for (const name of attemptedRoutineNames.get(page) || []) {
    if (knownNames.has(name)) continue;
    try {
      await cleanupPage.goto('/routines');
      const candidate = routineRow(cleanupPage, name);
      await expect.poll(() => candidate.count(), { timeout: 15_000 }).toBeGreaterThan(0).catch(() => {});
      if (await candidate.count()) {
        const scrim = cleanupPage.locator('.topnav-scrim');
        if (await scrim.isVisible().catch(() => false)) await cleanupPage.getByRole('button', { name: 'Close panel' }).click();
        await expect(scrim).toBeHidden();
        await candidate.click();
        const id = decodeURIComponent(new URL(cleanupPage.url()).pathname.split('/').filter(Boolean).at(-1));
        if (id) resources.push({ id, name });
      }
    } catch (error) { errors.push(`${name} discovery: ${error.message}`); }
  }
  for (const resource of [...resources].reverse()) {
    try { await deleteCreatedRoutine(cleanupPage, resource); } catch (error) { errors.push(`${resource.name} (${resource.id}): ${error.message}`); }
  }
  if (errors.length) {
    await testInfo.attach('routine-cleanup-errors', { body: errors.join('\n'), contentType: 'text/plain' });
    throw new Error(`Could not clean up test routines: ${errors.join('; ')}`);
  }
  expect(pageErrors.get(page) || []).toEqual([]);
});

test.describe('PR #342 routine journeys 06–10', () => {
  test.setTimeout(600_000);

  test('06: change a condition threshold and verify both paths', async ({ page }) => {
    const name = `pw342-${uniqueRun}-condition`;
    await withDisposableRoutine(page, name, async (id) => {
      const input = { type: 'object', properties: { value: { type: 'number' } }, required: ['value'] };
      const output = { type: 'object', properties: { selected: { type: 'string' } } };
      const nodes = [
        node('gate', 'condition', 'gate_result', 'value >= 10'),
        node('above', 'eval', 'selected', "'above'"),
        node('below', 'eval', 'selected', "'below'"),
      ];
      const edges = [
        { from: 'gate', level: 'route', router: 'state_equals', state_key: 'gate', branches: { true: 'above', false: 'below' } },
      ];
      await applyGraph(page, graphFor(name, id, nodes, edges, input, output));
      let saved = await reloadAndReadGraph(page);
      expect(saved.edges.find((edge) => edge.from === 'gate').branches).toEqual({ true: 'above', false: 'below' });

      await runWithValues(page, [7]);
      await expectNodeOutput(page, 'below', 'below');
      await expect(page.locator('[data-node-id="above"] .routine-step-card__run')).toHaveCount(0);
      await assertRunEventIds(page, id, 1);
      await expectRunButtonAvailable(page);

      // Change the threshold while holding the boundary value constant.
      saved = await readGraph(page);
      saved.nodes.find((entry) => entry.id === 'gate').config.expression = 'value >= 7';
      await applyGraph(page, saved);
      saved = await reloadAndReadGraph(page);
      expect(saved.nodes.find((entry) => entry.id === 'gate').config.expression).toBe('value >= 7');
      expect(saved.edges.find((edge) => edge.from === 'gate').branches).toEqual({ true: 'above', false: 'below' });
      await runWithValues(page, [7]);
      await expectNodeOutput(page, 'above', 'above');
      await expect(page.locator('[data-node-id="below"] .routine-step-card__run')).toHaveCount(0);
      await assertRunEventIds(page, id, 2);
    });
  });

  test('07: add a switch case and preserve its default route', async ({ page }) => {
    const name = `pw342-${uniqueRun}-switch`;
    await withDisposableRoutine(page, name, async (id) => {
      const input = { type: 'object', properties: { color: { type: 'string' } }, required: ['color'] };
      const output = { type: 'object', properties: { selected: { type: 'string' } } };
      const nodes = [
        node('choose', 'switch', 'color_key', 'color'),
        node('red', 'eval', 'selected', "'red-case'"),
        node('blue', 'eval', 'selected', "'blue-case'"),
        node('fallback', 'eval', 'selected', "'default-case'"),
      ];
      const edges = [
        { from: 'choose', level: 'route', router: 'state_equals', state_key: 'choose', branches: { red: 'red', blue: 'blue' }, default: 'fallback' },
      ];
      await applyGraph(page, graphFor(name, id, nodes, edges, input, output));
      await runWithValues(page, ['red']);
      await expectNodeOutput(page, 'red', 'red-case');
      await expect(page.locator('[data-node-id="fallback"] .routine-step-card__run')).toHaveCount(0);
      await assertRunEventIds(page, id, 1);
      await expectRunButtonAvailable(page);
      await runWithValues(page, ['unknown']);
      await expectNodeOutput(page, 'fallback', 'default-case');
      await assertRunEventIds(page, id, 2);
      await expectRunButtonAvailable(page);

      const saved = await readGraph(page);
      saved.nodes.push(node('green', 'eval', 'selected', "'green-case'"));
      saved.edges.find((edge) => edge.from === 'choose').branches.green = 'green';
      await applyGraph(page, saved);
      const persisted = await reloadAndReadGraph(page);
      const router = persisted.edges.find((edge) => edge.from === 'choose');
      expect(router.branches).toEqual({ red: 'red', blue: 'blue', green: 'green' });
      expect(router.default).toBe('fallback');
      await runWithValues(page, ['green']);
      await expectNodeOutput(page, 'green', 'green-case');
      await assertRunEventIds(page, id, 3);
    });
  });

  test('08: keep steps after foreach outside its body and preserve Send wiring', async ({ page }) => {
    const name = `pw342-${uniqueRun}-foreach`;
    await withDisposableRoutine(page, name, async (id) => {
      const input = { type: 'object', properties: { items: { type: 'array', items: { type: 'number' } } }, required: ['items'] };
      const output = { type: 'object', properties: { summary: { type: 'array', items: { type: 'number' } } } };
      const nodes = [
        node('fanout', 'foreach', 'fanout_result', undefined, { config: { mode: 'send', items_from: 'items', item_key: 'item', max_items: 3 } }),
        node('body', 'eval', 'doubled', 'item * 2'),
        node('terminator', 'noop', 'terminator_result'),
        node('summary', 'eval', 'summary', 'items'),
      ];
      const edges = [
        { from: 'fanout', to: 'body', level: 'normal' },
        { from: 'body', to: 'terminator', level: 'normal' },
        { from: 'terminator', to: 'summary', level: 'normal' },
        { from: 'fanout', to: 'terminator', level: 'foreach_empty' },
      ];
      await applyGraph(page, graphFor(name, id, nodes, edges, input, output));
      await expect(page.locator('.routine-foreach')).toHaveCount(1);
      await expect(page.locator('.routine-foreach__body [data-node-id="body"]')).toBeVisible();
      await expect(page.locator('.routine-chain > [data-node-id="summary"]')).toBeVisible();
      let saved = await reloadAndReadGraph(page);
      expect(saved.edges.find((edge) => edge.from === 'fanout')).toEqual({ from: 'fanout', to: 'body', level: 'normal' });
      expect(saved.edges.find((edge) => edge.level === 'foreach_empty')).toEqual({ from: 'fanout', to: 'terminator', level: 'foreach_empty' });
      expect(saved.nodes.find((entry) => entry.id === 'fanout').config).toMatchObject({ mode: 'send', items_from: 'items', item_key: 'item' });

      await runWithArray(page, [2, 3, 4]);
      await expectNodeOutput(page, 'summary', '[2,3,4]');
      // Send fan-out is not a loop_guard loop, so its body inspector has no
      // iteration navigation. Verify the three executions and the latest
      // output exposed by the inspector instead.
      await expect(page.locator('[data-node-id="body"] .routine-step-card__run')).toBeVisible();
      await page.locator('[data-node-id="body"]').click();
      await expect(page.locator('.routine-node-inspector__run-count')).toHaveText('3 executions this run');
      const bodyOutput = page.locator('.routine-node-inspector__run-output pre');
      await expect(bodyOutput).toContainText('8');
      await page.locator('.routine-node-inspector__close').click();
      await page.getByRole('button', { name: 'View routine output' }).click();
      await expect(page.getByRole('dialog', { name: 'Output' })).toContainText('2,');
      await page.getByRole('dialog', { name: 'Output' }).getByRole('button', { name: 'Close', exact: true }).last().click();

      // Remove/reinsert the first body node through the canvas controls. The
      // existing end-to-end editing contract covers this exact wiring edge;
      // here we assert persisted target validity after a JSON-editor rebuild.
      saved = await readGraph(page);
      saved.nodes = saved.nodes.filter((entry) => entry.id !== 'body');
      saved.nodes.splice(1, 0, node('body2', 'eval', 'doubled', 'item * 2'));
      saved.edges = saved.edges.map((edge) => {
        if (edge.level !== 'normal') return edge;
        if (edge.from === 'fanout') return { ...edge, to: 'body2' };
        if (edge.from === 'body') return { ...edge, from: 'body2' };
        return edge;
      });
      await applyGraph(page, saved);
      saved = await reloadAndReadGraph(page);
      expect(saved.edges.find((edge) => edge.from === 'fanout').to).toBe('body2');
      expect(saved.edges.find((edge) => edge.level === 'foreach_empty')).toEqual({ from: 'fanout', to: 'terminator', level: 'foreach_empty' });
      await runWithArray(page, []);
      await expectNodeOutput(page, 'summary', '[]');
      await assertRunEventIds(page, id, 2);
    });
  });

  test('09: run two deterministic split branches into a shared join', async ({ page }) => {
    const name = `pw342-${uniqueRun}-split-join`;
    await withDisposableRoutine(page, name, async (id) => {
      const output = { type: 'object', properties: { total: { type: 'number' }, result: { type: 'string' } } };
      const nodes = [
        node('split', 'split', 'split_result'),
        node('left', 'eval', 'left_value', '2'),
        node('left_gate', 'condition', 'left_gate_result', 'left > 1'),
        node('left_yes', 'eval', 'left_result', "'left-ok'"),
        node('left_no', 'eval', 'left_result', "'left-low'"),
        node('right', 'eval', 'right_value', '3'),
        node('total', 'eval', 'total', 'left + right'),
        node('result', 'eval', 'result', "'joined'"),
      ];
      const edges = [
        { from: 'split', level: 'route', router: 'state_equals', state_key: 'ignored', branches: { left: 'left', right: 'right' } },
        { from: 'left', to: 'left_gate', level: 'normal' },
        { from: 'left_gate', level: 'route', router: 'state_equals', state_key: 'left_gate', branches: { true: 'left_yes', false: 'left_no' } },
        { from: 'right', to: 'total', level: 'normal' },
        { from: ['left_yes', 'left_no'], to: 'total', level: 'fan_in' },
        { from: 'total', to: 'result', level: 'normal' },
      ];
      await applyGraph(page, graphFor(name, id, nodes, edges, { type: 'object', properties: {} }, output));
      await expect(page.locator('.routine-fork')).toHaveCount(2);
      // Anchor the assertion to the clicked DOM node. Its accessible name
      // changes to "Expand branch" on collapse, so a name-based locator would
      // re-resolve to the next still-expanded branch and read the wrong state.
      const collapse = page.locator('.routine-fork__header-collapse').first();
      await collapse.click();
      await expect(collapse).toHaveAttribute('aria-expanded', 'false');
      const expand = collapse;
      await expand.click();
      await expect(expand).toHaveAttribute('aria-expanded', 'true');
      const persisted = await reloadAndReadGraph(page);
      expect(persisted.edges.find((edge) => edge.level === 'fan_in')).toEqual({ from: ['left_yes', 'left_no'], to: 'total', level: 'fan_in' });
      await runWithValues(page);
      await expectNodeOutput(page, 'result', 'joined');
      await page.getByRole('button', { name: 'View routine output' }).click();
      const outputDialog = page.getByRole('dialog', { name: 'Output' });
      await expect(outputDialog.getByText('5', { exact: true })).toBeVisible();
      await expect(outputDialog.getByText('joined', { exact: true })).toBeVisible();
      await outputDialog.locator('.sidebar-modal__foot button').click();
      await assertRunEventIds(page, id, 1);
    });
  });

  test('10: remove a split lane and private nodes while retaining shared work', async ({ page }) => {
    const name = `pw342-${uniqueRun}-remove-lane`;
    await withDisposableRoutine(page, name, async (id) => {
      const output = { type: 'object', properties: { result: { type: 'string' } } };
      const nodes = [
        node('split', 'split', 'split_result'),
        node('left_private', 'eval', 'left_value', "'left'"),
        node('right_private', 'eval', 'right_value', "'right'"),
        node('shared', 'eval', 'result', "'shared-kept'"),
      ];
      const edges = [
        { from: 'split', level: 'route', router: 'state_equals', state_key: 'ignored', branches: { left: 'left_private', right: 'right_private' } },
        { from: ['left_private', 'right_private'], to: 'shared', level: 'fan_in' },
      ];
      await applyGraph(page, graphFor(name, id, nodes, edges, { type: 'object', properties: {} }, output));
      await expect(page.locator('.routine-fork')).toHaveCount(1);
      const graph = await readGraph(page);
      graph.nodes = graph.nodes.filter((entry) => entry.id !== 'left_private');
      const router = graph.edges.find((edge) => edge.from === 'split');
      delete router.branches.left;
      graph.edges = graph.edges.filter((edge) => edge.level !== 'fan_in');
      graph.edges.push({ from: 'right_private', to: 'shared', level: 'normal' });
      await applyGraph(page, graph);
      const persisted = await reloadAndReadGraph(page);
      expect(persisted.nodes.map((entry) => entry.id)).not.toContain('left_private');
      expect(persisted.nodes.map((entry) => entry.id)).toContain('right_private');
      expect(persisted.nodes.map((entry) => entry.id)).toContain('shared');
      expect(persisted.edges).toContainEqual({ from: 'right_private', to: 'shared', level: 'normal' });
      expect(persisted.edges.flatMap((edge) => edge.branches ? Object.values(edge.branches) : [edge.to]).flat().filter(Boolean))
        .toEqual(expect.arrayContaining(['right_private', 'shared']));

      await page.setViewportSize({ width: 700, height: 900 });
      await expect(page.locator('.routine-chain .routine-connector__btn').first()).toBeVisible();
      await runWithValues(page);
      await expectNodeOutput(page, 'shared', 'shared-kept');
      await expect(page.locator('.routine-chain').evaluate((element) => element.scrollWidth > 0)).resolves.toBe(true);
      await assertRunEventIds(page, id, 1);
    });
  });

  test('08 supplemental: Send exposes an aggregated transformed result list', async ({ page }, testInfo) => {
    const name = `pw342-${uniqueRun}-reduce`;
    await withDisposableRoutine(page, name, async (id) => {
      const nodes = [
        node('fan', 'foreach', 'fan', undefined, { config: { mode: 'send', items_from: 'items', item_key: 'item', max_items: 3 } }),
        node('mapped', 'eval', 'mapped', 'item * 2'),
        node('join_each', 'noop', 'join_each', undefined, { label: 'Join each' }),
        node('collected', 'reduce', 'collected', undefined, { config: { source_node: 'mapped', operation: 'collect' } }),
        node('total', 'reduce', 'total', undefined, { config: { source_node: 'mapped', operation: 'sum' } }),
        node('count', 'reduce', 'count', undefined, { config: { source_node: 'mapped', operation: 'count' } }),
      ];
      const edges = [
        { from: 'fan', to: 'mapped', level: 'normal' },
        { from: 'mapped', to: 'join_each', level: 'normal' },
        { from: 'fan', to: 'join_each', level: 'foreach_empty' },
        { from: 'join_each', to: 'collected', level: 'normal' },
        { from: 'collected', to: 'total', level: 'normal' },
        { from: 'total', to: 'count', level: 'normal' },
      ];
      const input = { type: 'object', properties: { items: { type: 'array', items: { type: 'number' } } }, required: ['items'] };
      const output = { type: 'object', properties: { collected: { type: 'array', items: { type: 'number' } }, total: { type: 'number' }, count: { type: 'integer' } } };
      await applyGraph(page, graphFor(name, id, nodes, edges, input, output));
      const persisted = await reloadAndReadGraph(page);
      expect(persisted.nodes.filter((entry) => entry.kind === 'reduce')).toHaveLength(3);
      expect(persisted.edges).toContainEqual({ from: 'fan', to: 'join_each', level: 'foreach_empty' });
      await runWithArray(page, [2, 3, 4]);
      await page.locator('[data-node-id="collected"]').click();
      const result = page.locator('.routine-node-inspector__run-output pre');
      await expect(result).toBeVisible({ timeout: 180_000 });
      expect(JSON.parse(await result.innerText()).sort((a, b) => a - b)).toEqual([4, 6, 8]);
      await page.locator('.routine-node-inspector__close').click();
      await expectNodeOutput(page, 'total', '18');
      await expectNodeOutput(page, 'count', '3');
      await expectRunButtonAvailable(page);
      await runWithArray(page, []);
      await expectNodeOutput(page, 'collected', '[]');
      await expectNodeOutput(page, 'total', '0');
      await expectNodeOutput(page, 'count', '0');
      const runs = await assertRunEventIds(page, id, 2);
      await attachJsonEvidence(testInfo, 'reducer-run-evidence', { routine_id: id, runs });
    });
  });
});
