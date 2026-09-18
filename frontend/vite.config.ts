import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// `vite --mode alt` runs a second dev pair (5180 -> API on 8010) next to the default one (5173 -> 8000);
// `--mode alt2` a third one (5183 -> 8020) for parallel sessions.
export default defineConfig(({ mode }) => {
  const api = mode === 'alt' ? 'http://127.0.0.1:8010' : mode === 'alt2' ? 'http://127.0.0.1:8020' : 'http://127.0.0.1:8000'
  return {
    plugins: [react(), tailwindcss()],
    // MapLibre 6 resolves its web worker relative to its own module URL; pre-bundling breaks that path in dev.
    optimizeDeps: { exclude: ['maplibre-gl'] },
    server: {
      port: mode === 'alt' ? 5180 : mode === 'alt2' ? 5183 : 5173,
      strictPort: mode === 'alt' || mode === 'alt2',
      proxy: {
        '/api': { target: api, changeOrigin: true },
        '^/s/': { target: api, changeOrigin: true },  // share pages only; a bare '/s' would swallow /src/*
      },
    },
    build: { outDir: 'dist', sourcemap: false },
  }
})
