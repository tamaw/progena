import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { queryNestedEvidence, removeNestedFixtures } from '../helpers/nestedRoutineFixture.mjs';
import { delegatedCheckpoint } from '../helpers/delegatedRoutineFixture.mjs';
import { attachJsonEvidence, observeRoutineEvents } from '../helpers/routineFixture.mjs';

test.setTimeout(720_000);
const quote = (value) => `'${value.replaceAll("'", "''")}'`;
const doc = (key) => {
  const raw = queryNestedEvidence(`SELECT value FROM store WHERE prefix='progena.state' AND key=${quote(key)};`);
  if (!raw) return null;
  const value = JSON.parse(raw); return value.payload ?? value;
};
async function chat(page, text) {
  const open = page.getByRole('button', { name: 'Quick chat', exact: true });
  if (!await page.locator('.qchat-input textarea').isVisible()) await open.click();
  await page.getByRole('button', { name: 'New chat', exact: true }).click();
  await page.locator('.qchat-input textarea').fill(text);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeEnabled({ timeout: 300_000 });
  const reply = await page.locator('.topnav-pane--quick').innerText();
  await page.getByRole('button', { name: 'Close panel', exact: true }).click();
  return reply;
}
async function lifecycle(page, id, status, requestId) {
  await expect.poll(() => page.evaluate(({ id, status, requestId }) => window.__routineEvents.find((event) =>
    event.payload.agent_id === id && (!requestId || event.payload.request_id === requestId)
    && [`agent.routine.run.${status}`, 'agent.routine.run.failed'].includes(event.type))?.payload,
  { id, status, requestId }), { timeout: 180_000 }).toBeTruthy();
  const result = await page.evaluate(({ id, status, requestId }) => window.__routineEvents.find((event) =>
    event.payload.agent_id === id && (!requestId || event.payload.request_id === requestId)
    && [`agent.routine.run.${status}`, 'agent.routine.run.failed'].includes(event.type))?.payload,
  { id, status, requestId });
  expect(result.status, result.error).toBe(status); return result;
}

test('J20: approved delegated author persists a classifier; user edits and executes the child', async ({ page }, testInfo) => {
  const suffix = randomUUID().replaceAll('-', '');
  const agentName = `pw342-author-${suffix.slice(0, 8)}`;
  const childId = `routine_j20_child_${suffix}`;
  const parentId = `routine_j20_parent_${suffix}`;
  const approvalQuestion = `Approve creating the disposable classifier ${childId}?`;
  const schema = { title: 'TextClassification', type: 'object', additionalProperties: false,
    required: ['text', 'category'], properties: { text: { type: 'string' }, category: { type: 'string', enum: ['question', 'request', 'statement'] } } };
  const child = { id: childId, name: `J20 child ${suffix}`, description: 'Disposable authored classifier', version: '2.0.0', entry: 'fan',
    nodes: [
      { id: 'fan', kind: 'foreach', output_key: 'fan', config: { mode: 'send', items_from: 'texts', item_key: 'text', max_items: 3 } },
      { id: 'classify', kind: 'classify', output_key: 'classify', config: { rubric_prompt: 'Classify this text: {text}\nReturn the text verbatim and category. Text ending in ? is question, text starting Please is request, otherwise statement.', response_schema: schema } },
      { id: 'join_each', kind: 'noop', output_key: 'join_each', config: {} },
      { id: 'collected', kind: 'reduce', output_key: 'collected', config: { source_node: 'classify', operation: 'collect' } },
    ], edges: [{ from: 'fan', to: 'classify', level: 'normal' }, { from: 'classify', to: 'join_each', level: 'normal' }, { from: 'fan', to: 'join_each', level: 'foreach_empty' }, { from: 'join_each', to: 'collected', level: 'normal' }],
    input_schema: { type: 'object', required: ['texts'], properties: { texts: { type: 'array', items: { type: 'string' }, minItems: 3, maxItems: 3 } } },
    output_schema: { type: 'object', required: ['collected'], properties: { collected: { type: 'array', items: schema } } } };
  const manifest = { name: agentName, description: 'Bounded disposable routine author', version: '1.0.0', kind: 'object', tools: ['structured_output'], skills: ['/skills/managing-routines'],
    mounts: [{ path: '/skills/managing-routines/', access: 'read' }, { path: '/schemas/', access: 'read' }, { path: '/routines/', access: 'read-write' }],
    permissions: [{ operations: ['read'], paths: ['/skills/managing-routines/**', '/schemas/**', '/routines/**'], mode: 'allow' }, { operations: ['write'], paths: [`/routines/${childId}.json`], mode: 'allow' }] };
  const prompt = `You are a bounded routine author. Follow managing-routines. Read its skill and graph schema. On invocation approval has already been granted. Write ONLY /routines/${childId}.json using write_file with this exact valid graph JSON: ${JSON.stringify(child)}. Read the saved file back. Do not execute it, edit any other resource, or claim success without a successful persisted write. Return a brief confirmation with its id.`;
  const parent = { id: parentId, name: `J20 parent ${suffix}`, description: 'Approval before delegated authoring', version: '2.0.0', entry: 'approval',
    nodes: [{ id: 'approval', kind: 'human', output_key: 'approval', config: { message: approvalQuestion, allowed_responses: ['approve'] } },
      { id: 'author', kind: 'agent', output_key: 'author', config: { agent_name: agentName, prompt_template: 'Create the classifier described in your instructions. Approval has been granted.' } },
      { id: 'finish', kind: 'noop', output_key: 'finish', config: {} }],
    edges: [{ from: 'approval', to: 'author', level: 'normal' }, { from: 'author', to: 'finish', level: 'normal' }], input_schema: { type: 'object', properties: {} } };
  let stage = 'setup';
  const evidence = {};
  await observeRoutineEvents(page);
  try {
    await page.goto('/routines');
    evidence.setupChat = await chat(page, `Create two NEW resources, preserving every existing resource. First use managing-agents to write /agents/user/${agentName}/SYSTEM.md with exactly this front matter ${JSON.stringify(manifest)} and this Markdown system prompt: ${prompt} Then use managing-routines to write /routines/${parentId}.json with exactly this graph: ${JSON.stringify(parent)}. Do not create the child routine ${childId}; only the delegated author must create it later after human approval. Do not execute anything. Use existing schemas and tools to validate and persist both resources.`);
    expect(doc(`agent:user/${agentName}/SYSTEM.md`), evidence.setupChat).toBeTruthy();
    expect(doc(`routine:${parentId}`), evidence.setupChat).toMatchObject(parent);
    expect(doc(`routine:${childId}`)).toBeNull();
    stage = 'approval pause'; console.log('J20 setup persisted; running to real human approval');
    await page.goto(`/routines/${parentId}`);
    await expect(page.locator('.canvas-node[data-node-id="approval"]')).toBeVisible();
    const posted = page.waitForRequest((request) => request.method() === 'POST' && request.url().endsWith(`/${parentId}/run`));
    await page.getByRole('button', { name: 'Run routine', exact: true }).click();
    const confirmation = page.getByRole('dialog');
    if (await confirmation.isVisible()) await confirmation.getByRole('button', { name: 'Run', exact: true }).click();
    const paused = await lifecycle(page, parentId, 'paused', (await posted).headers()['x-operation-id']);
    evidence.paused = paused;
    evidence.pausedCheckpoint = delegatedCheckpoint(paused.thread_id);
    expect(evidence.pausedCheckpoint.interrupts).toContainEqual({ node: 'approval', message: approvalQuestion, allowed_responses: ['approve'] });
    expect(evidence.pausedCheckpoint.run_meta.author).toBeUndefined();
    expect(doc(`routine:${childId}`)).toBeNull();
    stage = 'approved delegated author'; console.log('J20 paused without a write; approving through Quick Chat');
    evidence.approvalChat = await chat(page, `I approve the pending question "${approvalQuestion}". Call resume_routine now with thread_id exactly ${paused.thread_id} and answer exactly approve. Resume that checkpoint; do not create or edit any agent or routine yourself. The delegated author must do the write.`);
    await lifecycle(page, parentId, 'completed');
    expect(doc(`routine:${childId}`), evidence.approvalChat).toMatchObject(child);
    evidence.authorCheckpoint = delegatedCheckpoint(`routine-agent:${paused.thread_id}:author`);
    expect(evidence.authorCheckpoint.messages.flatMap((message) => message.tool_calls).some((call) => call.name === 'write_file' && call.args.file_path === `/routines/${childId}.json`)).toBe(true);
    await expect(page.getByRole('button', { name: new RegExp(`J20 child ${suffix}`) })).toBeVisible();
    stage = 'user edit and child execution'; console.log('J20 delegated write confirmed; editing and executing child');
    await page.goto(`/routines/${childId}`);
    await page.getByRole('button', { name: 'Edit as JSON' }).click();
    const editor = page.locator('.agents-graph-editor__textarea');
    const edited = JSON.parse(await editor.inputValue());
    edited.nodes.find((node) => node.id === 'classify').config.rubric_prompt = 'Classify only the text inside <input_text>. Rules: text beginning Could you or Please is request; otherwise text ending ? is question; otherwise statement. Return the input text verbatim without tags or instructions.\n<input_text>{text}</input_text>';
    await editor.fill(JSON.stringify(edited, null, 2));
    await page.getByRole('button', { name: 'Apply changes' }).click();
    await expect(page.locator('.agents-graph-editor__saved')).toHaveText('Graph saved.');
    await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
    const runPosted = page.waitForRequest((request) => request.method() === 'POST' && request.url().endsWith(`/${childId}/run`));
    await page.getByRole('button', { name: 'Run routine', exact: true }).click();
    await page.getByRole('dialog').locator('textarea').fill(JSON.stringify(['What is 2 plus 2?', 'Please open the window.', 'Could you open the window?']));
    await page.getByRole('dialog').getByRole('button', { name: 'Run', exact: true }).click();
    evidence.childRun = await lifecycle(page, childId, 'completed', (await runPosted).headers()['x-operation-id']);
    await page.locator('.canvas-node[data-node-id="collected"]').click();
    const output = page.locator('.routine-node-inspector__run-output pre');
    await expect(output).toBeVisible();
    evidence.classifications = JSON.parse(await output.innerText());
    expect(evidence.classifications.map((item) => item.category).sort()).toEqual(['question', 'request', 'request']);
    evidence.readbackChat = await chat(page, `Read /routines/${childId}.json using read_file. Confirm whether the user's updated rubric treats Could you sentences as request. Do not write or execute anything.`);
    expect(evidence.readbackChat).toMatch(/request/i);
    evidence.stage = 'complete';
    await attachJsonEvidence(testInfo, 'j20-authoring-evidence', evidence);
  } catch (error) {
    evidence.stage = stage; evidence.error = String(error);
    await attachJsonEvidence(testInfo, 'j20-authoring-evidence', evidence); throw error;
  } finally {
    removeNestedFixtures({ parent, child });
    queryNestedEvidence(`DELETE FROM store WHERE prefix='progena.state' AND key=${quote(`agent:user/${agentName}/SYSTEM.md`)};`);
  }
});
