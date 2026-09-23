import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { brotliCompressSync, constants as zlib, gzipSync } from 'node:zlib'

// .br and .gz copies of every built text file (bundles, universities.geojson, geo packs): the backend
// (app/main.py, _static) sends the smallest one the browser takes as is, with no compression work per request
function precompress(): Plugin {
  let outDir = 'dist'
  return {
    name: 'precompress',
    apply: 'build',
    configResolved(c) { outDir = resolve(c.root, c.build.outDir) },
    closeBundle() {
      const walk = (d: string): string[] =>
        readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(join(d, e.name)) : [join(d, e.name)]))
      for (const f of walk(outDir)) {
        if (!/\.(js|mjs|css|html|json|geojson|svg)$/.test(f)) continue
        const buf = readFileSync(f)
        if (buf.length < 1024) continue
        writeFileSync(`${f}.br`, brotliCompressSync(buf, { params: { [zlib.BROTLI_PARAM_QUALITY]: 11, [zlib.BROTLI_PARAM_SIZE_HINT]: buf.length } }))
        writeFileSync(`${f}.gz`, gzipSync(buf, { level: 9 }))
      }
    },
  }
}

// Pages are separate chunks (App.tsx): the page's own code would be asked for only once the entry script has run.
// A tiny script in index.html starts it at once, next to the entry, for the page the URL names.
const ROUTES: [string, string][] = [['^/$', 'src/pages/Globe.tsx'], ['^/u/', 'src/pages/Profile.tsx'],
  ['^/compare', 'src/pages/Compare.tsx'], ['^/saved', 'src/pages/Saved.tsx'], ['^/classic', 'src/pages/Home.tsx'],
  ['^/settings/search', 'src/pages/SearchSettings.tsx']]
function routePreload(): Plugin {
  return {
    name: 'route-preload',
    apply: 'build',
    transformIndexHtml: {
      order: 'post',
      handler(_html, ctx) {
        const chunks = Object.values(ctx.bundle ?? {}).filter((c) => c.type === 'chunk')
        const byFile = new Map(chunks.map((c) => [c.fileName, c]))
        const entry = chunks.find((c) => c.isEntry)
        const loaded = new Set<string>()
        const walk = (f: string, into: Set<string>) => { if (into.has(f)) return; into.add(f); for (const i of byFile.get(f)?.imports ?? []) walk(i, into) }
        if (entry) walk(entry.fileName, loaded)
        const cssOf = (files: Iterable<string>) => [...files].flatMap((f) => [...(byFile.get(f)?.viteMetadata?.importedCss ?? [])])
        const loadedCss = new Set(cssOf(loaded))   // the entry's own stylesheet is already a <link> in the page
        const table = ROUTES.map(([rx, src]) => {
          const c = chunks.find((x) => x.facadeModuleId?.replace(/\\/g, '/').endsWith(src))
          if (!c) return null
          const js = new Set<string>()
          walk(c.fileName, js)
          return [rx, [...js].filter((f) => !loaded.has(f)), [...new Set(cssOf(js))].filter((f) => !loadedCss.has(f))]
        }).filter(Boolean)
        const code = `(function(){var p=location.pathname,t=${JSON.stringify(table)};for(var i=0;i<t.length;i++){if(!new RegExp(t[i][0]).test(p))continue;` +
          `t[i][1].forEach(function(f){var l=document.createElement('link');l.rel='modulepreload';l.crossOrigin='';l.href='/'+f;document.head.appendChild(l)});` +
          `t[i][2].forEach(function(f){var l=document.createElement('link');l.rel='stylesheet';l.href='/'+f;document.head.appendChild(l)});break}})()`
        return [{ tag: 'script', children: code, injectTo: 'head' }]
      },
    },
  }
}

// `vite --mode alt` runs a second dev pair (5180 -> API on 8010) next to the default one (5173 -> 8000);
// `--mode alt2` a third one (5183 -> 8020) for parallel sessions.
export default defineConfig(({ mode }) => {
  const api = mode === 'alt' ? 'http://127.0.0.1:8010' : mode === 'alt2' ? 'http://127.0.0.1:8020' : 'http://127.0.0.1:8000'
  return {
    plugins: [react(), tailwindcss(), routePreload(), precompress()],
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
    // the globe's chunk is MapLibre (~0.9 MB): pages are split by route, so a profile opened from a link never loads it
    build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 1100 },
  }
})
