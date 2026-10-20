import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Builds only the public landing page (web/landing) into web/dist-landing for GitHub Pages.
// `base: './'` keeps the asset links relative, so it works at https://<user>.github.io/bayto/.
export default defineConfig({
  root: 'landing',
  base: './',
  publicDir: '../public',
  plugins: [react()],
  build: { outDir: '../dist-landing', emptyOutDir: true },
})
