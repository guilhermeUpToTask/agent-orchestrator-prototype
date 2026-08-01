import { defineConfig } from 'vitest/config';

export default defineConfig({
  cacheDir: '/tmp/aipom-vite-cache',
  test: {
    // jsdom, so tests can CLICK. Server-rendered markup proves what a
    // component renders for given props; it cannot prove a button is wired to
    // the mutation it claims, which is most of what the settings screens are.
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    exclude: ['**/.ds-sync/**', '**/node_modules/**', '**/dist/**'],
  },
});
