import { expect, test } from '@playwright/test';
import { answerUntilIntentGate, confirmAction, createPlan, openGate, planState } from './helpers';

/**
 * One operator drives one complete cycle through the browser.
 *
 * This is the test the repository has never had. `CLAUDE.md` claimed
 * "full-cycle browser E2E is Phase 8" from Phase 6 until 2026-08-10; Phase 8
 * came and went, and until this file existed **no test had ever driven the
 * console through a plan**. Every claim that the UI worked was inference from
 * unit tests plus a human clicking around.
 *
 * It is the safety net the Phase 9 refactor rests on, so it asserts the
 * OPERATOR'S PATH rather than the implementation: roles and accessible names,
 * server truth read back over the API, and no CSS classes anywhere. The
 * refactor is free to move every div in the tree as long as somebody can still
 * get from a brief to a published cycle.
 */
test.describe.configure({ mode: 'serial' });

const BRIEF = 'Build a small greeting library with a greet(name) function and tests.';
const ANSWER = "Success is: greet('world') returns 'Hello, world!' and pytest passes.";

test('a brief becomes a published cycle, without ever leaving the browser', async ({ page }) => {
  // A cycle can complete while the console throws the whole way — the API does
  // the work, so a broken component fails silently and every assertion below
  // would still pass. Collected here rather than in a second spec: a second
  // spec would need a second project (this console's composer switches to a
  // project SELECT once one exists) and a second full cycle, to watch the same
  // run this one already drives.
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });

  const planId = await createPlan(page, 'e2e-full-cycle', BRIEF);

  // --- discovery -------------------------------------------------------
  // The brief is echoed back as the plan's own, so the operator can see the
  // thing they are about to commit to rather than trusting it was carried.
  await expect(page.getByText(BRIEF).first()).toBeVisible();
  await answerUntilIntentGate(page, ANSWER);

  // --- the intent gate -------------------------------------------------
  let gate = await openGate(page);
  await expect(gate.getByRole('heading', { name: 'Review intent' })).toBeVisible();
  // The gate is exact-revision by design; the operator must be shown WHICH
  // revision they are approving, not just "an intent".
  await expect(gate.getByText(/revision 1/)).toBeVisible();
  await confirmAction(gate, 'Approve intent');

  // Architecture runs on the stub and the draft gate opens. Nothing is
  // reloaded here: the page is driven by SSE, and a test that reloads would
  // hide a broken event stream.
  await expect(page.getByRole('button', { name: 'Review & decide' })).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.getByRole('heading', { name: /review · cycle draft/i })).toBeVisible();

  // --- the cycle-draft gate --------------------------------------------
  gate = await openGate(page);
  await expect(gate.getByRole('heading', { name: 'Review cycle draft' })).toBeVisible();
  await confirmAction(gate, 'Approve & activate cycle');

  // --- execution, then the publication gate ----------------------------
  // Dry-run execution finishes in seconds; what is being asserted is that the
  // console FOLLOWS it live, from an active cycle to the completion gate,
  // without an operator refresh.
  await expect(page.getByRole('heading', { name: /review · cycle completion/i })).toBeVisible({
    timeout: 120_000,
  });

  const beforePublication = await planState(page, planId);
  expect(beforePublication.status).toBe('waiting');
  expect(beforePublication.active_cycle.goals.length).toBeGreaterThan(0);
  for (const goal of beforePublication.active_cycle.goals) {
    expect(goal.status, `${goal.name} did not reach done`).toBe('done');
  }

  // --- publication ------------------------------------------------------
  gate = await openGate(page);
  await expect(gate.getByRole('heading', { name: 'Review cycle completion' })).toBeVisible();
  // All four dispositions are offered, because the backend said they are legal.
  for (const disposition of ['open pr', 'merge', 'retain branch', 'discard']) {
    await expect(gate.getByRole('button', { name: disposition, exact: true })).toBeVisible();
  }
  await confirmAction(gate, 'retain branch');

  // --- back to idle -----------------------------------------------------
  // The cyclic root is never terminal: a finished cycle returns it to IDLE,
  // ready for the next one. That is the property that distinguishes this
  // lifecycle from the retired nine-phase machine, and it is worth asserting
  // from the browser rather than only from the aggregate.
  await expect(page.getByRole('heading', { name: /idle/i })).toBeVisible({ timeout: 60_000 });

  const published = await planState(page, planId);
  expect(published.status).toBe('idle');
  const cycle = published.cycles[published.cycles.length - 1];
  expect(cycle.output_disposition).toBe('retain_branch');
  expect(cycle.output_reference, 'a non-discard disposition must record where the work went')
    .toBeTruthy();

  // SSE reconnects are normal operation, not defects; anything else is not.
  const real = errors.filter((error) => !/EventSource|net::ERR_ABORTED/i.test(error));
  expect(real, `console errors during a clean cycle:\n${real.join('\n')}`).toEqual([]);
});

