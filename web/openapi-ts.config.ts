import { defineConfig } from '@hey-api/openapi-ts'

// `npm run api` regenerates src/api from the FastAPI schema
export default defineConfig({
  input: './openapi.json',
  output: 'src/api',
  plugins: [{ name: '@hey-api/client-fetch', throwOnError: true }, '@hey-api/typescript', { name: '@hey-api/sdk', operations: { strategy: 'flat' } }],
})
