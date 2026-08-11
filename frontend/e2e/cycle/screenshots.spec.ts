import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { answerUntilIntentGate, confirmAction, createPlan, openGate } from './helpers';

/**
 * Every major surface, in both themes — P9 task 5.
 *
 * **A screenshot is not a passing test.** The roadmap is explicit about it and
 * the rule is worth restating here: a broken page produces a perfectly tidy
 * picture of a broken page. So every capture below sits beside an assertion
 * that would fail on its own, and the image is evidence for a human reviewing
 * the change rather than the check itself.
 *
 * Both themes because the console ships a theme toggle and P9 task 4 found six
 * WCAG failures that existed ONLY in light mode — the theme nobody had been
 * looking at. Capturing one theme would have kept that invisible.
 *
 * Images land in `e2e/screenshots/cycle/` and are attached to the Playwright
 * report, so a reviewer sees them without running anything.
 */
type Theme = 'light' | 'dark';

const THEMES: Theme[] = ['dark', 'light'];
const SHOTS = 'e2e/screenshots/cycle';

/** Set the theme BEFORE the app boots, so nothing is captured mid-repaint. */
async function useTheme(page: Page, theme: Theme) {
  await page.addInitScript((value) => {
    window.localStorage.setItem('praxis.theme', value as string);
  }, theme);
}

/**
 * Capture a SETTLED page, and prove the theme actually took.
 *
 * Shooting straight after an assertion catches the UI mid-fetch — the
 * packaged-ui suite learned that the expensive way and produced a Plans
 * screenshot with no readiness banner, a screen no operator ever sees.
 */
async function shoot(page: Page, info: TestInfo, name: string, theme: Theme) {
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await page.waitForLoadState('networkidle');
  const path = `${SHOTS}/${name}-${theme}.png`;
  await page.screenshot({ path, fullPage: true });
  await info.attach(`${name} (${theme})`, { path, contentType: 'image/png' });
}

for (const theme of THEMES) {
  test.describe(`${theme} theme`, () => {
    test.use({ colorScheme: theme });

    test(`the static surfaces render in ${theme}`, async ({ page }, info) => {
      await useTheme(page, theme);

      await page.goto('/');
      await expect(page.getByRole('heading', { name: 'Plans', exact: true })).toBeVisible();
      await shoot(page, info, 'plan-list', theme);

      await page.goto('/settings/setup');
      await expect(page.getByRole('heading', { name: /get started/i })).toBeVisible();
      await shoot(page, info, 'settings-setup', theme);

      await page.goto('/settings/agents');
      await expect(page.getByRole('heading', { name: /agents/i }).first()).toBeVisible();
      await shoot(page, info, 'settings-agents', theme);

      await page.goto('/docs');
      await expect(page.getByRole('heading').first()).toBeVisible();
      await expect(page.getByText(/No guide called/i)).toHaveCount(0);
      await shoot(page, info, 'manual', theme);
    });

    test(`a cycle's surfaces render in ${theme}`, async ({ page }, info) => {
      await useTheme(page, theme);
      const planId = await createPlan(
        page,
        `e2e-shots-${theme}`,
        'Build a small greeting library with a greet(name) function and tests.',
      );
      await answerUntilIntentGate(page, "Success is: greet('world') returns 'Hello, world!'.");

      // The gate an operator meets first, open and waiting on them.
      let gate = await openGate(page);
      await expect(gate.getByRole('heading', { name: 'Review intent' })).toBeVisible();
      await shoot(page, info, 'gate-intent', theme);
      await confirmAction(gate, 'Approve intent');

      await expect(page.getByRole('heading', { name: /review · cycle draft/i })).toBeVisible({
        timeout: 120_000,
      });
      gate = await openGate(page);
      await expect(gate.getByRole('heading', { name: 'Review cycle draft' })).toBeVisible();
      await shoot(page, info, 'gate-cycle-draft', theme);
      await confirmAction(gate, 'Approve & activate cycle');

      await expect(page.getByRole('heading', { name: /review · cycle completion/i })).toBeVisible({
        timeout: 120_000,
      });
      await shoot(page, info, 'plan-overview', theme);

      // The canvas is the surface where the light theme was unreadable until
      // task 4 — a hardcoded dark panel under theme-following text. Worth a
      // picture in both themes for exactly that reason.
      await page.goto(`/plans/${planId}/goals`);
      await expect(page.getByRole('heading', { name: /^goals$/i })).toBeAttached();
      await expect(page.getByRole('main')).not.toBeEmpty();
      await shoot(page, info, 'goals-canvas', theme);

      await page.goto(`/plans/${planId}/activity`);
      await expect(page.getByRole('heading', { name: /^activity$/i })).toBeAttached();
      await shoot(page, info, 'activity', theme);

      // The dock expanded, showing the attempt ledger a run leaves behind.
      await page.getByRole('button', { name: /^AGENT EVENTS/ }).click();
      await expect(page.getByRole('button', { name: 'FAILED ONLY', exact: true })).toBeVisible();
      await shoot(page, info, 'console-dock', theme);
    });
  });
}
