import { expect, test } from '@playwright/test';
import { answerUntilIntentGate, confirmAction, createPlan, openGate, planState } from './helpers';

/**
 * The surfaces a completed cycle leaves behind, and the gate paths that are
 * not "approve".
 *
 * `full-cycle.spec.ts` proves an operator can get from a brief to a published
 * cycle. This proves the rest of what they can reach on the way — the goals
 * canvas, the per-goal review surface, the activity/attempt history, and the
 * settings sections — plus the gate decisions that are easy to leave untested
 * because the happy path never takes them.
 *
 * Same rule as its sibling: roles and accessible names only. A control this
 * file cannot address by name is a control a screen reader cannot announce.
 */
test.describe.configure({ mode: 'serial' });

const BRIEF = 'Build a small greeting library with a greet(name) function and tests.';
const ANSWER = "Success is: greet('world') returns 'Hello, world!' and pytest passes.";

test('an intent can be edited into a new revision before it is approved', async ({ page }) => {
  // The gate is exact-revision: editing must produce revision 2 and re-gate on
  // it, rather than quietly amending the revision the operator was shown.
  const planId = await createPlan(page, 'e2e-intent-edit', BRIEF);
  await answerUntilIntentGate(page, ANSWER);

  const gate = await openGate(page);
  const objective = gate.getByRole('textbox', { name: 'Objective' });
  await expect(objective).toBeVisible();
  await objective.fill('Deliver greet(name) returning a greeting, covered by its own tests.');
  await gate.getByRole('button', { name: /^Save as revision 2$/ }).click();

  await expect(async () => {
    const plan = await planState(page, planId);
    expect(plan.intent_proposal.revision).toBe(2);
    expect(plan.intent_proposal.objective).toContain('Deliver greet(name)');
    // Still gated — an edit is not an approval.
    expect(plan.intent_proposal.approved_at).toBeNull();
  }).toPass({ timeout: 30_000 });
});

test('the goals canvas and the plan tabs are reachable and render the cycle', async ({ page }) => {
  const planId = await createPlan(page, 'e2e-canvas', BRIEF);
  await answerUntilIntentGate(page, ANSWER);

  let plan = await planState(page, planId);
  expect(plan.activity).toBe('review:intent');
  const gate = await openGate(page);
  await confirmAction(gate, 'Approve intent');
  await expect(page.getByRole('heading', { name: /review · cycle draft/i })).toBeVisible({
    timeout: 120_000,
  });
  const draftGate = await openGate(page);
  await confirmAction(draftGate, 'Approve & activate cycle');
  await expect(page.getByRole('heading', { name: /review · cycle completion/i })).toBeVisible({
    timeout: 120_000,
  });

  plan = await planState(page, planId);
  expect(plan.active_cycle.goals.length).toBeGreaterThan(0);

  // Every tab in the plan shell mounts and shows its own surface. These are
  // links in the lifecycle navigation, so addressing them by name is also an
  // assertion that the navigation is labelled.
  //
  // FINDING, recorded here rather than fixed: only `Agents` renders a heading.
  // `Goals.tsx` has none at all (its only landmark is a `<p role="alert">` for
  // the empty state) and `Activity.tsx` has none either, so a reader navigating
  // by headings lands on two of these three tabs with nothing telling them
  // where they are. This spec therefore pins what is TRUE today — the tab is
  // reachable and its main region has content — because a safety net that
  // asserts the behaviour we intend rather than the behaviour we have cannot
  // catch a refactor breaking the latter. Tighten this to a heading assertion
  // in Phase 9 task 4, when the headings exist.
  for (const tab of ['Goals', 'Agents', 'Activity']) {
    await page.getByRole('link', { name: tab, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/plans/${planId}/${tab.toLowerCase()}`));
    await expect(page.getByRole('main')).not.toBeEmpty();
    if (tab === 'Agents') {
      // The one tab that does label itself. Asserted while it is on screen —
      // the loop moves on, and a check after it would silently pass or fail
      // against whichever tab happened to be last.
      await expect(page.getByRole('heading', { name: /agents/i }).first()).toBeVisible();
    }
  }
});

test('the settings sections all mount and report real backend state', async ({ page }) => {
  // Settings is where an operator fixes a broken runtime, so a section that
  // fails to mount is the difference between recoverable and stuck.
  // Heading text, not route slug: two of these deliberately differ
  // (`/settings/setup` is "Get started", `/settings/runner` is "Agent
  // runtime"), and asserting the slug would test the URL rather than what the
  // operator is shown.
  for (const [path, heading] of [
    ['/settings/setup', /get started/i],
    ['/settings/projects', /projects/i],
    ['/settings/providers', /providers/i],
    ['/settings/agents', /agents/i],
    ['/settings/capabilities', /capabilit/i],
    ['/settings/reasoner', /reasoner/i],
    ['/settings/runner', /agent runtime/i],
  ] as const) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: heading }).first()).toBeVisible();
  }

  // Tier 0 is what this suite runs, and the console must say so rather than
  // showing a hopeful default.
  await page.goto('/settings/runner');
  await expect(page.getByText(/dry-run/i).first()).toBeVisible();
});

test('the console dock exposes three separate controls, not one', async ({ page }) => {
  // Regression lock for the defect the P9 analysis found (Finding 3). The
  // toolbar used to be ONE <button> containing two `role="button"` spans:
  // invalid HTML, and the accessibility tree collapsed it into a single
  // control named "AGENT EVENTS · 1 FAILED ONLY" — one name covering three
  // actions. Asserting the names separately is what would have caught it.
  const planId = await createPlan(page, 'e2e-dock', BRIEF);
  expect(planId).toBeTruthy();

  const expand = page.getByRole('button', { name: /^AGENT EVENTS/ });
  const failedOnly = page.getByRole('button', { name: 'FAILED ONLY', exact: true });
  await expect(expand).toBeVisible();
  await expect(failedOnly).toBeVisible();

  // The expand control must NOT absorb the filter's label.
  await expect(expand).not.toHaveAccessibleName(/FAILED ONLY/);

  // And the filter is a toggle, so it has to say whether it is on. A control
  // that only *looks* pressed is styled, not accessible.
  await expect(failedOnly).toHaveAttribute('aria-pressed', 'false');
  await failedOnly.click();
  await expect(failedOnly).toHaveAttribute('aria-pressed', 'true');
});

test('the manual renders inside the console', async ({ page }) => {
  // The in-console manual renders the repository's own docs/guides/*.md, so a
  // broken glob shows an empty shell rather than an error.
  await page.goto('/docs');
  await expect(page.getByRole('heading').first()).toBeVisible();
  await expect(page.getByText(/No guide called/i)).toHaveCount(0);
});
