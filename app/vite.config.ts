/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd())
  return {
    plugins: [react()],
    server: {
      // T2-12 · with VITE_USE_MOCKS=false, /api goes to the real back end (docker-compose.dev.yml, port 8000)
      proxy: { '/api': { target: env.VITE_API_TARGET ?? 'http://localhost:8000', changeOrigin: true } },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: false,
      testTimeout: 15_000, // longer than asyncUtilTimeout in setup.ts, so a slow find fails with its own message
    },
  }
})
