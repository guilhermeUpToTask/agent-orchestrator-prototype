import { defineConfig } from '@hey-api/openapi-ts';

// Input is the schema exported from the backend by scripts/export_openapi.py.
// `npm run generate:api` refreshes both the schema and the generated types.
export default defineConfig({
  input: 'openapi.json',
  output: 'src/types/generated',
  plugins: ['@hey-api/typescript'],
});
