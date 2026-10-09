import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { observeRoutineEvents, removeRoutine, attachJsonEvidence } from './helpers/routineFixture.mjs';
import { queryNestedEvidence, parentRunHistory } from './helpers/nestedRoutineFixture.mjs';

const quote = (value) => `'${value.replaceAll("'", "''")}'`;
test.setTimeout(420_000);

for (const answer of ['approve', 'decline']) {
  test(`J11: ${answer} after reload resumes the original approval once via SSE`, async ({ page }, testInfo) => {
    const id = `routine_approval_${randomUUID().replaceAll('-', '')}`;
    const graph = {
      id, name: `Approval ${answer} ${id.slice(-8)}`, description: 'Disposable native human approval fixture',
      version: '2.0.0', entry: 'prepare',
      nodes: [
        { id: 'prepare', label: 'Prepare', output_key: 'prepare', kind: 'eval', config: { expression: '41' } },
        { id: 'decision', label: 'Approval', output_key: 'decision', kind: 'human',
          config: { message: 'Approve this prepared result?', allowed_responses: ['approve', 'decline'] } },
        { id: 'approved', label: 'Approved', output_key: 'approved', kind: 'eval', config: { expression: 'prepare + 1' } },
        { id: 'declined', label: 'Declined', output_key: 'declined', kind: 'eval', config: { expression: '0' } },
      ],
      edges: [
        { from: 'prepare', to: 'decision', level: 'normal' },
        { from: 'decision', level: 'route', router: 'state_equals', state_key: 'decision',
          branches: { approve: 'approved', decline: 'declined' } },
      ],
      input_schema: { type: 'object', properties: {} },
    };
    queryNestedEvidence(`INSERT INTO store (prefix,key,value,created_at,updated_at) VALUES ('progenitor.state',${quote(`routine:${id}`)},${quote(JSON.stringify({ payload: graph }))}::jsonb,now(),now());`);
    async function lifecycle(status, requestId) {
      await expect.poll(() => page.evaluate(({ id, status, requestId }) =>
        window.__routineEvents.find((e) => [ `agent.routine.run.${status}`, 'agent.routine.run.failed' ].includes(e.type)
          && e.payload.agent_id === id && (!requestId || e.payload.request_id === requestId))?.payload,
      { id, status, requestId }), { timeout: 180_000 }).toBeTruthy();
      const event = await page.evaluate(({ id, status, requestId }) => window.__routineEvents.find((e) =>
        [ `agent.routine.run.${status}`, 'agent.routine.run.failed' ].includes(e.type) && e.payload.agent_id === id
        && (!requestId || e.payload.request_id === requestId)).payload, { id, status, requestId });
      expect(event.status, event.error).toBe(status);
      return event;
    }
    try {
      await observeRoutineEvents(page);
      await page.goto(`/routines/${id}`);
      await expect(page.locator('.canvas-node[data-node-id="prepare"]')).toBeVisible({ timeout: 30_000 });
      const runPost = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith(`/${id}/run`));
      await page.getByRole('button', { name: 'Run routine', exact: true }).click();
      const originalRequest = (await runPost).headers()['x-request-id'];
      const paused = await lifecycle('paused', originalRequest);
      await page.locator('.canvas-node[data-node-id="decision"]').click();
      const approval = page.getByRole('region', { name: 'Pending approval' });
      await expect(approval).toContainText('Approve this prepared result?', { timeout: 30_000 });
      await expect(approval.getByRole('button', { name: 'Approve', exact: true })).toBeEnabled();
      await expect(approval.getByRole('button', { name: 'Decline', exact: true })).toBeEnabled();
      await page.reload();
      await page.locator('.canvas-node[data-node-id="decision"]').click();
      await expect(approval).toContainText('Approve this prepared result?', { timeout: 30_000 });
      await expect(approval.getByRole('button', { name: answer === 'approve' ? 'Approve' : 'Decline', exact: true })).toBeEnabled();
      const pending = await page.evaluate((thread) => window.__routineEvents.findLast((e) =>
        e.type === 'agent.routine.run_state.loaded' && e.payload.thread_id === thread
        && e.payload.pending_approvals?.length)?.payload.pending_approvals[0], paused.thread_id);
      expect(pending?.node_id).toBe('decision');
      const polling = [];
      page.on('request', (r) => { if (/\/(runs|run-state)(\?|$)/.test(r.url())) polling.push(r.url()); });
      const responsePromise = page.waitForResponse((r) => r.request().method() === 'POST' && r.url().endsWith(`/${id}/approval`));
      await approval.getByRole('button', { name: answer === 'approve' ? 'Approve' : 'Decline', exact: true }).click();
      const response = await responsePromise;
      expect(response.status()).toBe(202);
      const body = response.request().postDataJSON();
      expect(body.thread_id).toBe(paused.thread_id);
      expect(body.interrupt_id).toBe(pending.interrupt_id);
      const requestId = response.request().headers()['x-request-id'];
      expect(requestId).not.toBe(originalRequest);
      const completed = await lifecycle('completed', requestId);
      expect(completed.thread_id).toBe(paused.thread_id);
      expect(completed.session_id).toBe(paused.session_id);
      expect(completed.revision_id).toBe(paused.revision_id);
      await expect(approval).toHaveCount(0, { timeout: 30_000 });
      await expect(page.getByRole('button', { name: 'Run routine', exact: true })).toBeEnabled();
      const stale = await page.request.post(`/api/ui/agent-profiles/${id}/approval`, { data: body });
      expect(stale.status()).toBe(409);
      const history = parentRunHistory(paused.thread_id);
      expect(history.prepare).toHaveLength(1);
      expect(history.decision).toHaveLength(1);
      expect(history.decision[0].output.decision).toBe(answer);
      const branch = answer === 'approve' ? 'approved' : 'declined';
      const other = answer === 'approve' ? 'declined' : 'approved';
      expect(history[branch]).toHaveLength(1);
      expect(history[other]).toBeUndefined();
      expect(history[branch][0].output[branch]).toBe(answer === 'approve' ? 42 : 0);
      await page.getByRole('button', { name: 'Close node details', exact: true }).click();
      await page.locator(`.canvas-node[data-node-id="${branch}"]`).click();
      await expect(page.locator('.routine-node-inspector__run-output pre')).toHaveText(answer === 'approve' ? '42' : '0');
      expect(polling).toEqual([]);
      await attachJsonEvidence(testInfo, `j11-${answer}-evidence`, { paused, pending, completed, history, polling });
    } finally { removeRoutine(id); }
  });
}
