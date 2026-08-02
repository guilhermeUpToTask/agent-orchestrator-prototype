import { expect, test } from '@playwright/test';

/**
 * Does the packaged UI boot, and does it reach its own API?
 *
 * Everything else about the frontend is covered more cheaply elsewhere. This
 * covers the one thing nothing else can: `curl` returns 200 for a shell whose
 * bundle throws on the first line, so "the wheel serves the UI" and "the UI
 * works" are different claims. These specs run against the API serving its own
 * packaged bundle — the artifact path a user installs, not the dev server.
 *
 * Screenshots are written to `e2e/screenshots/` for review. They are NOT
 * compared against baselines: pixel diffing is a maintenance cost worth paying
 * only once there is a UI worth regressing, which is Phase 8's question.
 */
const SHOTS = 'e2e/screenshots';

/**
 * Screenshot the SETTLED page. Shooting straight after an assertion catches the
 * UI mid-fetch — the first run of this suite produced a Plans screenshot with
 * no readiness banner, because `GET /api/readiness` had not resolved yet, which
 * would have had me reviewing a screen no operator ever sees.
 */
async function shoot(page: import('@playwright/test').Page, name: string) {
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: `${SHOTS}/${name}`, fullPage: true });
}

test('the console boots and renders the plans screen', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });

  await page.goto('/');

  // Rendered by React, not present in the served shell — so seeing it proves
  // the bundle executed rather than merely downloaded.
  await expect(page.getByRole('heading', { name: /plans/i }).first()).toBeVisible();
  await shoot(page, "01-plans.png");

  expect(errors, `console/page errors: ${errors.join(' | ')}`).toEqual([]);
});

test('a fresh install is told to set itself up, and the link goes to the wizard', async ({ page }) => {
  await page.goto('/');

  // The readiness banner only appears when the install is not ready, which is
  // the state a first-run operator is in.
  const cta = page.getByRole('link', { name: /get started/i });
  await expect(cta).toBeVisible();
  await cta.click();

  await expect(page).toHaveURL(/\/settings\/setup/);
  await expect(page.getByRole('heading', { name: /get started/i })).toBeVisible();
  await shoot(page, "02-setup-wizard.png");
});

test('the wizard opens on Tier 0 and asks for an agent, not an API key', async ({ page }) => {
  await page.goto('/settings/setup');

  await expect(page.getByText(/next: create an agent/i)).toBeVisible();
  await expect(page.getByText(/register a provider/i)).toHaveCount(0);

  // Switching to Tier 1 must reveal the provider step it deliberately hid.
  await page.getByLabel(/^tier$/i).selectOption('tier1');
  await expect(page.getByText(/register a provider/i).first()).toBeVisible();
  await shoot(page, "03-setup-tier1.png");
});

test('the readiness checklist reports real backend state', async ({ page }) => {
  await page.goto('/settings/readiness');

  await expect(page.getByRole('heading', { name: /launch readiness/i })).toBeVisible();
  // Served by GET /api/readiness — visible only if the browser reached the API.
  await expect(page.getByText(/catalog/i).first()).toBeVisible();
  await shoot(page, "04-readiness.png");
});

test('the manual renders in the console, not the API explorer', async ({ page }) => {
  /**
   * `/docs` is FastAPI's Swagger by default, and the SPA fallback used to
   * reserve that path outright — so this route silently rendered the API
   * explorer instead of the guides. Both are a 200 with HTML, so only a check
   * that reads the CONTENT catches it. That is why this is here and not in the
   * vitest suite: the collision lives in the packaged same-origin server, which
   * is the only place the two can compete.
   */
  await page.goto('/docs');

  await expect(page.getByRole('heading', { name: /getting started/i }).first()).toBeVisible();
  await expect(page.getByRole('link', { name: /reading the console/i })).toBeVisible();
  await expect(page.getByText(/swagger/i)).toHaveCount(0);

  // The guides are inlined at build time; an empty glob renders a full nav and
  // a body that says the page does not exist.
  await page.goto('/docs/statuses');
  await expect(page.getByRole('heading', { name: /the five statuses/i })).toBeVisible();
});

test('the settings sections all mount', async ({ page }) => {
  for (const [path, heading] of [
    ['providers', /providers/i],
    ['agents', /agents/i],
    ['projects', /projects/i],
  ] as const) {
    await page.goto(`/settings/${path}`);
    await expect(page.getByRole('heading', { name: heading }).first()).toBeVisible();
  }
  await shoot(page, "05-settings-projects.png");
});
