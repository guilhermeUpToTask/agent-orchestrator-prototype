import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { answerUntilIntentGate, confirmAction, createPlan, openGate } from './helpers';

/**
 * Accessibility, measured rather than asserted — P9 task 4.
 *
 * The roadmap's wording is deliberate: *"Accessibility as a requirement… 182
 * `aria-*` usages is a starting point, not a result — measure it."* A count of
 * ARIA attributes says nothing about whether a page is usable; an axe run on
 * the real rendered surfaces does.
 *
 * Scope is WCAG 2.1 A and AA, which is the line most teams commit to and the
 * one worth holding. Violations fail the suite: a threshold that only reports
 * is a threshold nobody fixes.
 *
 * These specs run against the same Tier 0 server as the rest of `e2e/cycle`,
 * so they see real content — an empty page passes accessibility checks
 * trivially and proves nothing.
 */
const STANDARD = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

async function audit(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(STANDARD).analyze();
  return results.violations.map((violation) => ({
    id: violation.id,
    impact: violation.impact,
    help: violation.help,
    // `failureSummary` carries the numbers — the measured contrast ratio, the
    // two colours, the threshold. Without it a contrast failure is a rule name
    // and a selector, and the fix becomes guesswork about which colour is
    // wrong.
    nodes: violation.nodes.map((node) => `${node.target.join(' ')}\n      ${node.failureSummary}`),
  }));
}

/** Fail with the offending selectors inline — a rule id alone is not actionable. */
function expectClean(violations: Awaited<ReturnType<typeof audit>>, surface: string) {
  const report = violations
    .map((v) => `  [${v.impact}] ${v.id}: ${v.help}\n    ${v.nodes.join('\n    ')}`)
    .join('\n');
  expect(violations, `${surface} has accessibility violations:\n${report}`).toEqual([]);
}

test('the plan list is accessible', async ({ page }) => {
  await page.goto('/');
  // `exact` matters: without it "All plans" matches too and the locator is
  // ambiguous.
  await expect(page.getByRole('heading', { name: 'Plans', exact: true })).toBeVisible();
  expectClean(await audit(page), 'the plan list');
});

test('the settings sections are accessible', async ({ page }) => {
  for (const path of [
    '/settings/setup',
    '/settings/projects',
    '/settings/providers',
    '/settings/agents',
    '/settings/capabilities',
    '/settings/reasoner',
    '/settings/runner',
  ]) {
    await page.goto(path);
    await expect(page.getByRole('heading').first()).toBeVisible();
    expectClean(await audit(page), path);
  }
});

test('the manual is accessible', async ({ page }) => {
  await page.goto('/docs');
  await expect(page.getByRole('heading').first()).toBeVisible();
  expectClean(await audit(page), '/docs');
});

test('a plan under way, its gate dialog and its tabs are accessible', async ({ page }) => {
  // The surfaces with the most going on, and the ones an operator actually
  // spends time in. Audited with real content rather than empty states.
  const planId = await createPlan(page, 'e2e-a11y', 'Build a greeting library with tests.');
  await answerUntilIntentGate(page, "Success is: greet('world') returns 'Hello, world!'.");
  expectClean(await audit(page), 'a plan awaiting its intent gate');

  const gate = await openGate(page);
  await expect(gate.getByRole('heading', { name: 'Review intent' })).toBeVisible();
  expectClean(await audit(page), 'the intent gate dialog');

  await confirmAction(gate, 'Approve intent');
  await expect(page.getByRole('heading', { name: /review · cycle draft/i })).toBeVisible({
    timeout: 120_000,
  });
  const draft = await openGate(page);
  expectClean(await audit(page), 'the cycle-draft gate dialog');
  await confirmAction(draft, 'Approve & activate cycle');
  await expect(page.getByRole('heading', { name: /review · cycle completion/i })).toBeVisible({
    timeout: 120_000,
  });

  for (const tab of ['goals', 'agents', 'activity']) {
    await page.goto(`/plans/${planId}/${tab}`);
    await expect(page.getByRole('main')).not.toBeEmpty();
    expectClean(await audit(page), `the ${tab} tab`);
  }
});
