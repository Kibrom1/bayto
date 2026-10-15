import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/tasks': 'http://127.0.0.1:8000',
      '/sessions': 'http://127.0.0.1:8000',
      '/modes': 'http://127.0.0.1:8000',
      '/agents': 'http://127.0.0.1:8000',
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
