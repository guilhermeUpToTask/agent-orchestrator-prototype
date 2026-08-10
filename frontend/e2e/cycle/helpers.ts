import { expect, type Page } from '@playwright/test';

/**
 * The console's operator vocabulary, in one place.
 *
 * These helpers are written against ROLES AND ACCESSIBLE NAMES only — never
 * CSS classes, never test ids. That is not purity: Phase 9 is about to move
 * this markup around, and a suite pinned to class names would break on every
 * refactor step while proving nothing about whether the operator can still do
 * the thing. A suite pinned to "there is a button called Approve intent"
 * breaks only when the operator's path actually changes, which is exactly when
 * it should.
 *
 * It also means these tests double as an accessibility assertion: a control
 * this file cannot address by name is a control a screen reader cannot
 * announce.
 */

/** Every operator gate action is two-step: act, then confirm. */
export async function confirmAction(page: Page, name: string | RegExp) {
  await page.getByRole('button', { name }).click();
  await page.getByRole('button', { name: /^Confirm:/ }).click();
}

/** Open the pending review gate from the plan page. */
export async function openGate(page: Page) {
  await page.getByRole('button', { name: 'Review & decide' }).click();
  return page.getByRole('dialog', { name: 'Approval gate' });
}

/**
 * Create a project and a plan from the composer, returning the plan id.
 *
 * **The project is created over the API when one already exists**, and that is
 * a finding rather than a convenience. `Plans.tsx:141` branches
 * `projects.length > 0 ? <Select> : <inline create form>`, so the composer
 * offers a way to create a project ONLY while there are none — from the second
 * project onward the operator has to know to go to Settings → Projects.
 * Recorded for the Phase 9 analysis; until it changes, a browser-only helper
 * could not give two specs independent projects, and specs that share a
 * project share its single long-lived plan.
 */
export async function createPlan(page: Page, project: string, brief: string): Promise<string> {
  await page.goto('/');

  const existing = await page.request.get('/api/projects').then((r) => r.json());
  if (!existing.some((candidate: { name: string }) => candidate.name === project)) {
    if (existing.length > 0) {
      const created = await page.request.post('/api/projects', { data: { name: project } });
      expect(created.ok(), 'could not create a project over the API').toBeTruthy();
      await page.reload();
    }
  }

  await page.getByRole('button', { name: 'Open project plan' }).click();

  const nameField = page.getByRole('textbox', { name: 'Project', exact: true });
  if (await nameField.isVisible().catch(() => false)) {
    await nameField.fill(project);
    await page.getByRole('button', { name: 'Create project' }).click();
  }
  const picker = page.getByRole('combobox', { name: 'Project' });
  await expect(picker).toBeVisible();
  await picker.selectOption({ label: project });

  await page.getByRole('textbox', { name: 'Describe what you want built.' }).fill(brief);
  await page.getByRole('button', { name: 'Create & analyze brief' }).click();

  await page.waitForURL(/\/plans\/[0-9a-f-]{36}/);
  const planId = page.url().split('/plans/')[1];
  expect(planId).toMatch(/^[0-9a-f-]{36}$/);
  return planId;
}

/**
 * Answer the reasoner until the intent gate opens.
 *
 * The stub asks one clarifying question before it commits a proposal, so
 * reaching the gate takes a reply — which is the real discovery flow rather
 * than a shortcut around it.
 */
export async function answerUntilIntentGate(page: Page, answer: string) {
  const composer = page.getByRole('textbox', { name: /Message the reasoner/ });
  await expect(composer).toBeVisible();
  await composer.fill(answer);
  await composer.press('Enter');
  await expect(page.getByRole('button', { name: 'Review & decide' })).toBeVisible({
    timeout: 60_000,
  });
}

/** Read the plan's server-side truth, for asserting the UI against it. */
export async function planState(page: Page, planId: string) {
  const response = await page.request.get(`/api/plans/${planId}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}
