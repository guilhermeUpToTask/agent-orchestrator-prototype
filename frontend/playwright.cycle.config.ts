import { defineConfig } from '@playwright/test';

/**
 * The full-cycle browser suite — the safety net Phase 9's refactor rests on.
 *
 * `playwright.config.ts` beside this one is the SMOKE suite: it runs the API
 * with `--no-worker` against a wiped state directory, because its question is
 * what a FRESH install shows an operator. This config asks the opposite
 * question — can somebody drive a whole cycle through the browser? — and needs
 * the opposite setup: a seeded catalog and a live worker. Two questions, two
 * servers, two configs. Merging them would mean one of the suites lying about
 * the state it runs against.
 *
 * TIER 0, deliberately: stub reasoner + dry-run runner, seeded by
 * `seed demo --stub`. A browser test that needs a paid model is a demo, not a
 * test — `demos/README.md` draws that line and it applies here unchanged. The
 * whole cycle completes in about twenty seconds on the stub, which is what
 * makes this lockable in CI at all.
 */
const PORT = 8310;
const BASE_URL = process.env.E2E_CYCLE_BASE_URL ?? `http://127.0.0.1:${PORT}`;
const E2E_HOME = process.env.E2E_CYCLE_HOME ?? '/tmp/aipom-cycle-e2e-home';
const PYTHON = process.env.E2E_PYTHON ?? '../backend/.venv/bin/python';
const CLI = `${PYTHON} -m agent_orchestrator.infra.cli.main`;

export default defineConfig({
  testDir: './e2e/cycle',
  // A cycle involves real planning sessions and real worker ticks. Generous
  // per-test, but nowhere near enough to hide a hang.
  timeout: 180_000,
  expect: { timeout: 30_000 },
  // Serial by construction: these specs share one server, one state directory
  // and one worker, and the plan they drive is a singleton per project.
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  outputDir: '/tmp/aipom-playwright-cycle',
  use: {
    baseURL: BASE_URL,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
    viewport: { width: 1440, height: 900 },
  },
  webServer: process.env.E2E_CYCLE_BASE_URL
    ? undefined
    : {
        // Wipe, migrate, seed, serve — in the webServer command rather than a
        // globalSetup, for the reason the smoke config documents at length:
        // Playwright starts webServer FIRST, so deleting the state directory in
        // globalSetup pulls the database out from under a live server.
        command:
          `rm -rf ${E2E_HOME} && ${CLI} db upgrade && ${CLI} seed demo --stub && ` +
          `${CLI} serve --port ${PORT} --no-migrate`,
        url: `http://127.0.0.1:${PORT}/health`,
        timeout: 180_000,
        reuseExistingServer: false,
        stdout: 'pipe',
        stderr: 'pipe',
        env: { ORCHESTRATOR_HOME: E2E_HOME },
      },
});
