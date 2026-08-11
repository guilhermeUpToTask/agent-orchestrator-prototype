import { defineConfig } from '@playwright/test';

/**
 * A LIGHT browser check, deliberately.
 *
 * The vitest suite proves what components render and what their buttons call;
 * the API-only fixtures prove the whole cycle deterministically. Neither can
 * answer the one question a packaged install raises: does the bundle actually
 * BOOT? `curl` sees the same 200 for a shell whose JavaScript throws on the
 * first line, which is exactly the failure a released wheel could ship.
 *
 * So this runs a handful of specs against the API serving its own packaged UI —
 * the real artifact path, not the Vite dev server. Full-cycle browser E2E stays
 * in Phase 8 (ROADMAP): it is slow, flaky, and duplicates fixtures that already
 * prove the lifecycle without a browser.
 */
const PORT = 8210;
const BASE_URL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${PORT}`;

/**
 * The suite starts its own stack unless E2E_BASE_URL points at one you started.
 * It used to require a server on 8210 that nothing documented and nothing
 * launched, so the only way to run it was to already know how.
 *
 * `--no-worker`, and a state directory that is deleted first: these specs assert
 * what a FRESH install shows an operator (the readiness banner, the setup wizard
 * on Tier 0), so leftover state is the one thing that would make them lie. The
 * worker is what the API-only fixtures exercise; a browser adds nothing to it.
 */
const E2E_HOME = process.env.E2E_HOME ?? '/tmp/praxis-e2e-home';
const PYTHON = process.env.E2E_PYTHON ?? '../backend/.venv/bin/python';

export default defineConfig({
  testDir: './e2e',
  // `e2e/cycle/` belongs to playwright.cycle.config.ts, which starts a server
  // with a WORKER and a seeded catalog. This suite starts one with
  // `--no-worker` against a wiped state directory on purpose, so running those
  // specs here would drive a plan nothing can advance — half of them hung and
  // half passed for the wrong reason before this line existed.
  testIgnore: '**/cycle/**',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  outputDir: '/tmp/praxis-playwright',
  use: {
    baseURL: BASE_URL,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    viewport: { width: 1440, height: 900 },
  },
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        // The wipe belongs in this command, not in a globalSetup: Playwright
        // starts webServer FIRST and runs globalSetup after it is up, so
        // deleting the state directory there pulls the database out from under
        // a live server. That produced a 500 on the first `GET /api/projects`
        // (SQLite CANTOPEN — the directory was gone), which looked exactly like
        // a product defect for as long as it took to find.
        command: `rm -rf ${E2E_HOME} && ${PYTHON} -m agent_orchestrator.infra.cli.main serve --port ${PORT} --no-worker`,
        url: `http://127.0.0.1:${PORT}/health`,
        timeout: 120_000,
        reuseExistingServer: false,
        stdout: 'pipe',
        stderr: 'pipe',
        env: { ORCHESTRATOR_HOME: E2E_HOME },
      },
});
