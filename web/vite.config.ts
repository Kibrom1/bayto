import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// BAYTO_API_URL points the dev proxy at a non-default orchestrator (used by the e2e auth run).
const API = process.env.BAYTO_API_URL ?? 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/tasks': API,
      '/sessions': API,
      '/modes': API,
      '/agents': API,
      '/workspaces': API,
      '/models': API,
      '/app-config': API,
      '/auth': API,
      '/search': API,
      '/control': {
        target: 'http://127.0.0.1:8787',
        rewrite: (path) => path.replace(/^\/control/, ''),
      },
    }
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
})
