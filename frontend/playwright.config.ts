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
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  outputDir: '/tmp/aipom-playwright',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8210',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    viewport: { width: 1440, height: 900 },
  },
});
