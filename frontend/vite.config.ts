import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // MapLibre 6 resolves its web worker relative to its own module URL; pre-bundling breaks that path in dev.
  optimizeDeps: { exclude: ['maplibre-gl'] },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '^/s/': { target: 'http://127.0.0.1:8000', changeOrigin: true },  // share pages only; a bare '/s' would swallow /src/*
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
