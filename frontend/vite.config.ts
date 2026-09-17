import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// `vite --mode alt` runs a second dev pair (5180 -> API on 8010) next to the default one (5173 -> 8000).
export default defineConfig(({ mode }) => {
  const api = mode === 'alt' ? 'http://127.0.0.1:8010' : 'http://127.0.0.1:8000'
  return {
    plugins: [react(), tailwindcss()],
    // MapLibre 6 resolves its web worker relative to its own module URL; pre-bundling breaks that path in dev.
    optimizeDeps: { exclude: ['maplibre-gl'] },
    server: {
      port: mode === 'alt' ? 5180 : 5173,
      strictPort: mode === 'alt',
      proxy: {
        '/api': { target: api, changeOrigin: true },
        '^/s/': { target: api, changeOrigin: true },  // share pages only; a bare '/s' would swallow /src/*
      },
    },
    build: { outDir: 'dist', sourcemap: false },
  }
})
