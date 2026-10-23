import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { observeRoutineEvents, attachJsonEvidence } from '../helpers/routineFixture.mjs';
import { queryNestedEvidence, removeNestedFixtures, nestedChildThreads, parentRunHistory } from '../helpers/nestedRoutineFixture.mjs';

const quote = (value) => `'${value.replaceAll("'", "''")}'`;
test.setTimeout(420_000);
test.beforeEach(({ page }) => page.setDefaultTimeout(30_000));

for (const { answer, firstAnswer } of [{ answer: 'approve' }, { answer: 'decline' }, { answer: 'decline', firstAnswer: 'approve' }]) {
  test(`nested approval: parent waits, then ${firstAnswer ? 'two approvals' : answer} resumes the same child once`, async ({ page }, testInfo) => {
    const suffix = randomUUID().replaceAll('-', '');
    const child = {
      id: `routine_nested_approval_child_${suffix}`, name: `Nested approval child ${suffix.slice(-8)}`,
      description: 'Nested approval regression fixture', version: '2.0.0', entry: 'prepare',
      nodes: [
        { id: 'prepare', label: 'Prepare once', kind: 'eval', output_key: 'answer', config: { expression: '41' } },
        { id: 'decision', label: 'Child approval', kind: 'human', output_key: 'decision',
          config: { message: 'Approve the child before the parent continues?', allowed_responses: ['approve', 'decline'] } },
        { id: 'approved', label: 'Approved', kind: 'eval', output_key: 'answer', config: { expression: 'answer + 1' } },
        { id: 'declined', label: 'Declined', kind: 'eval', output_key: 'answer', config: { expression: '0' } },
      ],
      edges: [
        { from: 'prepare', to: 'decision', level: 'normal' },
        { from: 'decision', level: 'route', router: 'state_equals', state_key: 'decision',
          branches: { approve: 'approved', decline: 'declined' } },
      ],
      input_schema: { type: 'object', properties: {} },
    };
    const parent = {
      id: `routine_nested_approval_parent_${suffix}`, name: `Nested approval parent ${suffix.slice(-8)}`,
      description: 'Nested approval regression fixture', version: '2.0.0', entry: 'child_call',
      nodes: [
        { id: 'child_call', label: 'Call approval child', kind: 'routine', output_key: 'child_call',
          config: { routine_name: child.id, output_map: { child_answer: 'answer' } } },
        { id: 'after_child', label: 'After child', kind: 'eval', output_key: 'after_child', config: { expression: 'child_answer + 1' } },
      ],
      edges: [{ from: 'child_call', to: 'after_child', level: 'normal' }],
      input_schema: { type: 'object', properties: {} },
    };
    if (firstAnswer) {
      child.nodes.push({ id: 'confirmation', label: 'Second approval', kind: 'human', output_key: 'confirmation',
        config: { message: 'Confirm the second child approval?', allowed_responses: ['approve', 'decline'] } });
      child.edges[1] = { from: 'decision', to: 'confirmation', level: 'normal' };
      child.edges.push({ from: 'confirmation', level: 'route', router: 'state_equals', state_key: 'confirmation',
        branches: { approve: 'approved', decline: 'declined' } });
    }
    for (const graph of [child, parent]) {
      queryNestedEvidence(`INSERT INTO store (prefix,key,value,created_at,updated_at) VALUES ('progena.state',${quote(`routine:${graph.id}`)},${quote(JSON.stringify({ payload: graph }))}::jsonb,now(),now());`);
    }
    async function terminal(requestId) {
      await expect.poll(() => page.evaluate((requestId) => window.__routineEvents.find((e) =>
        /^agent\.routine\.run\.(paused|completed|failed)$/.test(e.type)
          && e.payload.request_id === requestId)?.payload, requestId), { timeout: 180_000 }).toBeTruthy();
      return page.evaluate((requestId) => window.__routineEvents.find((e) =>
        /^agent\.routine\.run\.(paused|completed|failed)$/.test(e.type)
          && e.payload.request_id === requestId).payload, requestId);
    }
    try {
      await observeRoutineEvents(page);
      await page.goto(`/routines/${parent.id}`);
      await expect(page.locator('.canvas-node[data-node-id="child_call"]')).toBeVisible({ timeout: 30_000 });
      const posted = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith(`/${parent.id}/run`));
      await page.getByRole('button', { name: 'Run routine', exact: true }).click();
      const paused = await terminal((await posted).headers()['x-request-id']);
      expect(paused.status, paused.error).toBe('paused');
      expect(parentRunHistory(paused.thread_id).after_child).toBeUndefined();
      const before = nestedChildThreads(child.id);
      expect(before).toHaveLength(1);
      const childThread = before[0].thread_id;
      expect(parentRunHistory(childThread).prepare).toHaveLength(1);
      expect(parentRunHistory(childThread).decision).toBeUndefined();
      await page.reload();
      await page.locator('.canvas-node[data-node-id="child_call"]').click();
      const approval = page.getByRole('region', { name: 'Pending approval' });
      await expect(approval).toContainText('Approve the child before the parent continues?');
      let firstDecision;
      if (firstAnswer) {
        const firstPost = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith(`/${parent.id}/approval`));
        await approval.getByRole('button', { name: 'Approve', exact: true }).click();
        const request = await firstPost;
        firstDecision = request.postDataJSON();
        expect((await terminal(request.headers()['x-request-id'])).status).toBe('paused');
        expect(parentRunHistory(paused.thread_id).after_child).toBeUndefined();
        await expect(approval).toContainText('Confirm the second child approval?');
      }
      const answered = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith(`/${parent.id}/approval`));
      await approval.getByRole('button', { name: answer === 'approve' ? 'Approve' : 'Decline', exact: true }).click();
      const answerRequest = await answered;
      if (firstDecision) expect(answerRequest.postDataJSON().interrupt_id).not.toBe(firstDecision.interrupt_id);
      const completed = await terminal(answerRequest.headers()['x-request-id']);
      expect(completed.status, completed.error).toBe('completed');
      expect(completed.thread_id).toBe(paused.thread_id);
      expect(nestedChildThreads(child.id)).toEqual(before);
      const childHistory = parentRunHistory(childThread);
      expect(childHistory.prepare).toHaveLength(1);
      expect(childHistory.decision).toHaveLength(1);
      expect(childHistory.decision[0].output.decision).toBe(firstAnswer || answer);
      if (firstAnswer) {
        expect(childHistory.confirmation).toHaveLength(1);
        expect(childHistory.confirmation[0].output.confirmation).toBe(answer);
      }
      expect(childHistory[answer === 'approve' ? 'approved' : 'declined']).toHaveLength(1);
      const parentHistory = parentRunHistory(paused.thread_id);
      expect(parentHistory.child_call).toHaveLength(1);
      expect(parentHistory.child_call[0].routine.thread_id).toBe(childThread);
      expect(parentHistory.after_child).toHaveLength(1);
      const expected = answer === 'approve' ? 43 : 1;
      expect(parentHistory.after_child[0].output.after_child).toBe(expected);
      await expect(approval).toHaveCount(0);
      await page.getByRole('button', { name: 'Close node details', exact: true }).click();
      await page.locator('.canvas-node[data-node-id="after_child"]').click();
      await expect(page.locator('.routine-node-inspector__run-output pre')).toHaveText(String(expected));
      await attachJsonEvidence(testInfo, `nested-approval-${answer}`, { paused, completed, childThread, childHistory, parentHistory });
    } finally { removeNestedFixtures({ parent, child }); }
  });
}
