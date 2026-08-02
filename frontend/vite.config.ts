import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // `..` so the dev server may read `docs/guides/*.md`, which the docs view
  // inlines at build time. Without it the guides resolve in `vite build` but
  // 403 in `vite dev`, which is the confusing half of the failure.
  server: { port: 5173, fs: { allow: ['..'] } },
})
