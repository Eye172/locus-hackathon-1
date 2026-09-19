import { addProtocol } from 'maplibre-gl'
import type { Map, StyleSpecification, LayerSpecification, SkySpecification } from 'maplibre-gl'

export const STYLE_URL = 'https://tiles.openfreemap.org/styles/liberty'

/** Space sky: black beyond the horizon, a light-blue atmosphere rim, fog that fades in only near the ground. */
const SKY_SPACE: SkySpecification = {
  'sky-color': '#02030A',
  'horizon-color': '#7FB4FF',
  'fog-color': '#B9CDF0',
  'fog-ground-blend': 0.9,
  'horizon-fog-blend': 0.45,
  'sky-horizon-blend': 0.85,
  'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 1, 8, 1, 11, 0],
}

export const BLUE_MARBLE = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg'

const warmed = new Set<string>()
const keep: HTMLImageElement[] = []  // hold references so the browser does not drop in-flight loads
const inflight = new globalThis.Map<string, Promise<void>>()
/** Start loading a tile into the HTTP cache; the promise settles when it has arrived (or failed). */
function warm(url: string): Promise<void> | null {
  if (warmed.has(url)) return inflight.get(url) ?? null
  warmed.add(url)
  const im = new Image()
  im.crossOrigin = 'anonymous'  // same request mode as the map's own tile requests, so the cache entry is reused
  im.decoding = 'async'
  const done = new Promise<void>((res) => { im.onload = () => res(); im.onerror = () => res() })
  inflight.set(url, done)
  done.then(() => inflight.delete(url))
  im.src = url
  keep.push(im)
  if (keep.length > 1200) keep.splice(0, keep.length - 1200)
  return done
}
const tileUrl = (tpl: string, z: number, x: number, y: number) => tpl.replace('{z}', String(z)).replace('{y}', String(y)).replace('{x}', String(x))

/** Warm the HTTP cache with the planet at low zoom (levels 0–3 everywhere, level 4 between ±67°), so the spiral dive
 *  never shows an unloaded (black) side of the globe while it turns and grows. Runs when the browser is idle. */
export function prefetchPlanet(): void {
  const go = () => {
    for (let z = 0; z <= 4; z++) {
      const n = 1 << z
      const y0 = z === 4 ? 3 : 0, y1 = z === 4 ? 12 : n
      for (let y = y0; y < y1; y++) for (let x = 0; x < n; x++) warm(tileUrl(BLUE_MARBLE, z, x, y))
    }
  }
  const ric = (window as unknown as { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => void }).requestIdleCallback
  if (ric) ric(go, { timeout: 2500 }); else window.setTimeout(go, 1200)
}

function tileXY(lat: number, lon: number, z: number): [number, number] {
  const n = 2 ** z
  const r = (Math.max(-85, Math.min(85, lat)) * Math.PI) / 180
  const x = Math.floor(((((lon + 180) % 360) + 360) % 360 / 360) * n)
  const y = Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n)
  return [Math.min(n - 1, x), Math.max(0, Math.min(n - 1, y))]
}

/** Warm exactly the imagery a scripted camera path will show: for each sample, the tiles around the view centre at the
 *  raster level MapLibre picks for 256-px tiles (round(zoom + 1)). Blue Marble up to map zoom 7.5, Esri from zoom 4.
 *  Settles when every tile it started (or was already loading) has arrived. */
export function prefetchPath(samples: { lat: number; lon: number; zoom: number }[], view: { w: number; h: number }, cap = 600): Promise<void> {
  let n = 0
  const loads: Promise<void>[] = []
  const add = (p: Promise<void> | null) => { if (p) loads.push(p); return ++n >= cap }
  for (const s of samples) {
    const z = Math.round(s.zoom + 1)
    if (z < 5) continue                                   // levels 0–4 come from prefetchPlanet
    const tpls = [...(s.zoom <= 7.6 ? [BLUE_MARBLE] : []), ...(s.zoom >= 3.9 ? [ESRI_IMAGERY] : [])]
    const tilePx = 512 * 2 ** (s.zoom - z)
    const hw = Math.min(4, Math.ceil(view.w / 2 / tilePx)), hh = Math.min(3, Math.ceil(view.h / 2 / tilePx))
    const [cx, cy] = tileXY(s.lat, s.lon, z)
    const N = 2 ** z
    for (const tpl of tpls) {
      if (tpl === BLUE_MARBLE && z > 8) continue
      for (let dy = -hh; dy <= hh; dy++) {
        const y = cy + dy
        if (y < 0 || y >= N) continue
        for (let dx = -hw; dx <= hw; dx++) {
          if (add(warm(tileUrl(tpl, z, (((cx + dx) % N) + N) % N, y)))) return Promise.all(loads).then(() => {})
        }
      }
    }
  }
  return Promise.all(loads).then(() => {})
}
const ESRI_IMAGERY = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'

/* Where Esri has no imagery at a zoom level (z18-19 in Kyzylorda, z19 in Taraz or Khorog...) it still answers 200 with
 * a grey "Map data not yet available" tile - the same 2 521-byte JPEG everywhere. The map would show it as imagery.
 * The style loads Esri through this protocol instead: a placeholder is replaced by its parent tile's quarter, scaled
 * up (going further up while the parent is a placeholder too) - softer, but the real ground. */
const ESRI_PROTOCOL = 'esri'
const PLACEHOLDER_BYTES = 2521
const PLACEHOLDER_SHA256 = '9eafd300d61393184a4abc1d458564cfd1cd9b6f9c4e9c74687045c0a0e5b858'
const esriCache = new globalThis.Map<string, Promise<ArrayBuffer | null>>()

async function isPlaceholder(buf: ArrayBuffer): Promise<boolean> {
  if (buf.byteLength !== PLACEHOLDER_BYTES || !globalThis.crypto?.subtle) return false
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', buf))
  return Array.from(h, (b) => b.toString(16).padStart(2, '0')).join('') === PLACEHOLDER_SHA256
}

/** The imagery of tile z/y/x, or null when Esri has none at this level or above it (5 levels up at most). */
function esriTile(z: number, y: number, x: number, signal?: AbortSignal, up = 0): Promise<ArrayBuffer | null> {
  const key = `${z}/${y}/${x}`
  let p = esriCache.get(key)
  if (!p) {
    p = (async () => {
      const r = await fetch(tileUrl(ESRI_IMAGERY, z, x, y), { signal })
      if (!r.ok) throw new Error(`esri ${r.status}`)
      const buf = await r.arrayBuffer()
      if (!(await isPlaceholder(buf))) return buf
      if (z === 0 || up >= 5) return null
      const parent = await esriTile(z - 1, y >> 1, x >> 1, undefined, up + 1)   // shared by 4 children: not cancelled with one
      if (!parent) return null
      const half = await createImageBitmap(new Blob([parent]), (x & 1) * 128, (y & 1) * 128, 128, 128,
        { resizeWidth: 256, resizeHeight: 256, resizeQuality: 'high' })
      const canvas = new OffscreenCanvas(256, 256)
      canvas.getContext('2d')!.drawImage(half, 0, 0)
      half.close()
      return (await canvas.convertToBlob({ type: 'image/jpeg', quality: 0.9 })).arrayBuffer()
    })()
    p.catch(() => esriCache.delete(key))   // a failed or aborted load is tried again next time
    esriCache.set(key, p)
    if (esriCache.size > 600) esriCache.delete(esriCache.keys().next().value!)
  }
  return p
}

let esriProtocol = false
function registerEsriProtocol() {
  if (esriProtocol) return
  esriProtocol = true
  addProtocol(ESRI_PROTOCOL, async (params, abort) => {
    const m = /(\d+)\/(\d+)\/(\d+)$/.exec(params.url)
    if (!m) throw new Error(`bad esri url ${params.url}`)
    const data = await esriTile(+m[1], +m[2], +m[3], abort.signal)
    if (!data) throw new Error('no imagery here')   // the map keeps showing the lower-zoom tile it has
    return { data }
  })
}

/**
 * Paint overrides for the vector layers drawn on top of satellite imagery.
 * Fills are hidden (the imagery shows land, water and parks), roads become thin white lines,
 * labels get a dark halo, and 3D buildings are light so they read like a model on a photo.
 */
function satellitePaint(layer: LayerSpecification): { paint?: Record<string, unknown>; hide?: boolean } {
  const id = layer.id
  switch (layer.type) {
    case 'background':
      return { paint: { 'background-color': '#02030A' } }
    case 'fill':
      return { hide: true }
    case 'line': {
      if (/casing|tunnel|waterway|park_outline|track|path|bridge_.*casing/.test(id)) return { hide: true }
      const boundary = /boundary|admin/.test(id)
      const major = /motorway|trunk|primary/.test(id)
      return { paint: { 'line-color': boundary ? '#FFFFFF' : '#FFFFFF', 'line-opacity': boundary ? 0.55 : major ? 0.75 : 0.45 } }
    }
    case 'symbol':
      return { paint: { 'text-color': '#FFFFFF', 'text-halo-color': '#0B1220', 'text-halo-width': 1.4, 'text-halo-blur': 0.4, 'icon-opacity': 0.9 } }
    case 'fill-extrusion':
      return { paint: { 'fill-extrusion-color': '#E9EEF8', 'fill-extrusion-opacity': 0.94, 'fill-extrusion-vertical-gradient': true } }
    default:
      return {}
  }
}

/** Fetches the OpenFreeMap Liberty style and turns it into: colour planet (NASA Blue Marble) → satellite (Esri) → roads/labels/3D. */
export async function loadSatelliteStyle(globe = true): Promise<StyleSpecification> {
  // a snapshot of the Liberty style ships with the site: the planet should not wait for a third-party round trip
  let style: StyleSpecification
  try {
    const local = await fetch('/liberty.json')
    if (!local.ok) throw new Error(String(local.status))
    style = (await local.json()) as StyleSpecification
  } catch {
    style = (await (await fetch(STYLE_URL)).json()) as StyleSpecification
  }
  style.layers = style.layers.filter((l) => l.type !== 'raster')
  for (const src of Object.keys(style.sources)) if (style.sources[src].type === 'raster') delete style.sources[src]
  // the same Blue Marble at levels 0–3, served from this site: NASA's server needs ~2.5 s for the first view, and
  // until then the planet was a black disc. These are same-origin and small, so the globe is textured from the first
  // frame; the sharper NASA tiles draw over them as they arrive.
  style.sources.planet = { type: 'raster', tiles: ['/planet/{z}/{x}/{y}.jpg'], tileSize: 256, maxzoom: 3, attribution: 'NASA GIBS Blue Marble' }
  style.sources.bluemarble = { type: 'raster', tiles: [BLUE_MARBLE], tileSize: 256, maxzoom: 8, attribution: 'NASA GIBS Blue Marble' }
  registerEsriProtocol()
  style.sources.esri = { type: 'raster', tiles: [`${ESRI_PROTOCOL}://tile/{z}/{y}/{x}`], tileSize: 256, maxzoom: 19, attribution: 'Esri, Maxar, Earthstar Geographics' }
  const rasters: LayerSpecification[] = [
    { id: 'planet', type: 'raster', source: 'planet', maxzoom: 7.5, paint: { 'raster-fade-duration': 0, 'raster-saturation': 0.05 } },
    { id: 'bluemarble', type: 'raster', source: 'bluemarble', paint: { 'raster-opacity': ['interpolate', ['linear'], ['zoom'], 0, 1, 5.5, 1, 7.5, 0], 'raster-fade-duration': 0, 'raster-saturation': 0.05 } },
    { id: 'esri', type: 'raster', source: 'esri', minzoom: 4, paint: { 'raster-opacity': ['interpolate', ['linear'], ['zoom'], 4, 0, 6.5, 1], 'raster-saturation': -0.08, 'raster-brightness-max': 0.96, 'raster-fade-duration': 150 } },
  ]
  const bg = style.layers.findIndex((l) => l.type === 'background')
  style.layers.splice(bg + 1, 0, ...rasters)
  style.layers = style.layers.filter((layer) => {
    const rule = satellitePaint(layer)
    if (rule.hide) return false
    if (rule.paint) {
      const target = layer as LayerSpecification & { paint?: Record<string, unknown> }
      target.paint = { ...(target.paint ?? {}), ...rule.paint }
    }
    return true
  })
  ;(style as StyleSpecification & { projection?: unknown }).projection = { type: globe ? 'globe' : 'mercator' }
  style.sky = globe ? SKY_SPACE : { ...SKY_SPACE, 'sky-color': '#9EC5FF', 'horizon-color': '#DCE9FF', 'fog-color': '#DCE9FF', 'atmosphere-blend': 0 }
  return style
}

/** Runtime hot-swap helper (kept for debugging from the console). */
export function applySatelliteTheme(map: Map) {
  const style = map.getStyle()
  if (!style?.layers) return
  for (const layer of style.layers) {
    const rule = satellitePaint(layer)
    if (rule.hide) { try { map.setLayoutProperty(layer.id, 'visibility', 'none') } catch { /* ignore */ } ; continue }
    for (const [k, v] of Object.entries(rule.paint ?? {})) {
      try { map.setPaintProperty(layer.id, k as never, v as never) } catch { /* ignore */ }
    }
  }
  map.setSky(SKY_SPACE)
}
