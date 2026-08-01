import { defineConfig } from 'vitest/config';

export default defineConfig({
  cacheDir: '/tmp/aipom-vite-cache',
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    exclude: ['**/.ds-sync/**', '**/node_modules/**', '**/dist/**'],
  },
});
