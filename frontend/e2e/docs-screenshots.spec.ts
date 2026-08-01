import { expect, test } from '@playwright/test';

/**
 * The screenshots the documentation embeds — regenerated, never hand-cropped.
 *
 * Skipped unless DOCS_SHOTS=1, because unlike the smoke suite this one needs a
 * server with REAL WORK IN IT: a completed cycle, accepted evidence, promoted
 * refs. It is pointed at a live install with `E2E_BASE_URL`, typically right
 * after a `fixtures/first-cycle-v1` run:
 *
 *   E2E_BASE_URL=http://127.0.0.1:8000 DOCS_SHOTS=1 DOCS_PLAN=<plan id> \
 *     npx playwright test docs-screenshots
 *
 * Output goes to `docs/images/`, which IS committed — the docs need the files,
 * and a reader cannot regenerate them without a run of their own.
 */
const SHOTS = '../docs/images';
const PLAN = process.env.DOCS_PLAN ?? '';

test.skip(process.env.DOCS_SHOTS !== '1', 'set DOCS_SHOTS=1 against a live install');

async function shoot(page: import('@playwright/test').Page, name: string) {
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: `${SHOTS}/${name}`, fullPage: true });
}

test('plans list', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /plans/i }).first()).toBeVisible();
  await shoot(page, 'plans-list.png');
});

test('plan overview after a completed cycle', async ({ page }) => {
  test.skip(!PLAN, 'set DOCS_PLAN to a plan id with a completed cycle');
  await page.goto(`/plans/${PLAN}`);
  await expect(page.getByText(/idle|running|waiting/i).first()).toBeVisible();
  await shoot(page, 'plan-overview.png');
});

test('goals canvas', async ({ page }) => {
  test.skip(!PLAN, 'set DOCS_PLAN to a plan id with a completed cycle');
  await page.goto(`/plans/${PLAN}/goals`);
  await shoot(page, 'goals-canvas.png');
});

test('activity and attempts', async ({ page }) => {
  test.skip(!PLAN, 'set DOCS_PLAN to a plan id with a completed cycle');
  await page.goto(`/plans/${PLAN}/activity`);
  await shoot(page, 'activity.png');
});

test('completed cycle evidence', async ({ page }) => {
  const done = process.env.DOCS_DONE_PLAN ?? '';
  test.skip(!done, 'set DOCS_DONE_PLAN to a plan with a COMPLETED cycle');
  await page.goto(`/plans/${done}`);
  await page.getByText(/COMPLETED ·/).first().click();
  await shoot(page, 'cycle-evidence.png');
});

test('setup checklist', async ({ page }) => {
  await page.goto('/settings/readiness');
  await expect(page.getByRole('heading', { name: /launch readiness/i })).toBeVisible();
  await shoot(page, 'readiness.png');
});
