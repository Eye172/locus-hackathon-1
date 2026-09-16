import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import { Map as MLMap, type GeoJSONSource, type MapLayerMouseEvent } from 'maplibre-gl'
type Map = MLMap
type MapMouseEvent = MapLayerMouseEvent
import 'maplibre-gl/dist/maplibre-gl.css'
import { loadSatelliteStyle } from './darkTheme'

export interface HoverInfo { qid: string; name: string; name_en: string; city?: string; c: string; x: number; y: number; lon: number; lat: number }
export interface GlobeHandle {
  flyToUniversity: (lat: number, lon: number) => Promise<void>
  flyToCountry: (bbox: number[]) => void
  resetToGlobe: () => void
  getMap: () => Map | null
}
interface Props {
  onHover?: (h: HoverInfo | null) => void
  onSelect?: (h: HoverInfo) => void
  onZoom?: (zoom: number) => void
  onReady?: () => void
}

const HOME: [number, number] = [66, 44]
const BUILDINGS = 'building-3d'

export const GlobeMap = forwardRef<GlobeHandle, Props>(function GlobeMap({ onHover, onSelect, onZoom, onReady }, ref) {
  const container = useRef<HTMLDivElement>(null)
  const stars = useRef<HTMLCanvasElement>(null)
  const mapRef = useRef<Map | null>(null)
  const spinning = useRef(true)
  const idleTimer = useRef<number | undefined>(undefined)
  const starField = useRef<{ x: number; y: number; r: number; a: number }[]>([])

  const drawStars = () => {
    const c = stars.current, map = mapRef.current
    if (!c || !map) return
    const dpr = window.devicePixelRatio || 1
    const w = c.clientWidth, h = c.clientHeight
    if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr }
    const ctx = c.getContext('2d')!
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, w, h)
    if (!starField.current.length) {
      for (let i = 0; i < 420; i++) starField.current.push({ x: Math.random(), y: Math.random(), r: Math.random() * 1.3 + 0.3, a: Math.random() })
    }
    const z = map.getZoom()
    const fade = z > 4 ? Math.max(0, 1 - (z - 4) / 3) : 1
    if (fade <= 0) return
    const t = performance.now() / 1000
    for (const s of starField.current) {
      const tw = 0.55 + 0.45 * Math.sin(t * 1.3 + s.a * 20)
      ctx.globalAlpha = tw * fade * 0.9
      ctx.fillStyle = '#DCE4FF'
      ctx.beginPath(); ctx.arc(s.x * w, s.y * h, s.r, 0, Math.PI * 2); ctx.fill()
    }
    // clear the globe disc so stars never overlap the planet
    const R = (512 * Math.pow(2, z)) / (2 * Math.PI) + 8
    ctx.globalCompositeOperation = 'destination-out'
    ctx.globalAlpha = 1
    ctx.beginPath(); ctx.arc(w / 2, h / 2, R, 0, Math.PI * 2); ctx.fill()
    ctx.globalCompositeOperation = 'source-over'
  }

  const animateBuildings = (map: Map) => {
    if (!map.getLayer(BUILDINGS)) return
    const t0 = performance.now()
    const step = () => {
      const t = Math.min(1, (performance.now() - t0) / 1200)
      const e = 1 - Math.pow(1 - t, 3)
      map.setPaintProperty(BUILDINGS, 'fill-extrusion-height', ['*', ['coalesce', ['get', 'render_height'], 12], e])
      map.setPaintProperty(BUILDINGS, 'fill-extrusion-base', ['*', ['coalesce', ['get', 'render_min_height'], 0], e])
      if (t < 1) requestAnimationFrame(step)
    }
    requestAnimationFrame(step)
  }

  useImperativeHandle(ref, () => ({
    getMap: () => mapRef.current,
    flyToUniversity: (lat, lon) => new Promise<void>((resolve) => {
      const map = mapRef.current
      if (!map) return resolve()
      spinning.current = false
      if (map.getLayer(BUILDINGS)) {
        map.setPaintProperty(BUILDINGS, 'fill-extrusion-height', 0)
        map.setPaintProperty(BUILDINGS, 'fill-extrusion-base', 0)
        map.setLayerZoomRange(BUILDINGS, 13, 24)
      }
      map.once('moveend', () => { animateBuildings(map); resolve() })
      map.flyTo({ center: [lon, lat], zoom: 16.2, pitch: 60, bearing: -17, duration: 3400, curve: 1.6, essential: true })
    }),
    flyToCountry: (bbox) => {
      const map = mapRef.current
      if (!map) return
      spinning.current = false
      map.fitBounds([[bbox[1], bbox[0]], [bbox[3], bbox[2]]], { padding: 80, pitch: 25, bearing: 0, duration: 2200, maxZoom: 6.5 })
    },
    resetToGlobe: () => {
      const map = mapRef.current
      if (!map) return
      map.flyTo({ center: HOME, zoom: 1.5, pitch: 0, bearing: 0, duration: 2000 })
      window.setTimeout(() => { spinning.current = true }, 2100)
    },
  }))

  useEffect(() => {
    if (!container.current) return
    let cancelled = false
    let map: Map | null = null
    let raf = 0
    const onResize = () => drawStars()

    loadSatelliteStyle().catch(() => 'https://tiles.openfreemap.org/styles/liberty' as const).then((style) => {
      if (cancelled || !container.current) return
      map = new MLMap({
        container: container.current,
        style,
        center: HOME,
        zoom: 1.5,
        attributionControl: { compact: true },
        canvasContextAttributes: { antialias: true },
        maxPitch: 70,
      })
      mapRef.current = map
      ;(window as unknown as { __map?: Map }).__map = map
      const errors: string[] = []
      ;(window as unknown as { __mapErrors?: string[] }).__mapErrors = errors
      map.on('error', (e) => { errors.push(String((e as { error?: { message?: string } }).error?.message ?? e)) })
      setup(map)
      window.addEventListener('resize', onResize)
    })

    const setup = (map: Map) => {
    map.on('style.load', () => {
      if (map.getSource('unis')) return
      map.setProjection({ type: 'globe' })
      map.addSource('unis', { type: 'geojson', data: '/universities.geojson', cluster: true, clusterRadius: 38, clusterMaxZoom: 7 })
      map.addLayer({ id: 'unis-glow', type: 'circle', source: 'unis', filter: ['!', ['has', 'point_count']],
        paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 7, 6, 11, 12, 18], 'circle-color': '#9CD3FF', 'circle-opacity': 0.35, 'circle-blur': 0.9 } })
      map.addLayer({ id: 'unis-point', type: 'circle', source: 'unis', filter: ['!', ['has', 'point_count']],
        paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 2.4, 6, 4.2, 12, 6.5], 'circle-color': '#FFFFFF', 'circle-stroke-color': '#1D4ED8', 'circle-stroke-width': 1.5 } })
      map.addLayer({ id: 'unis-cluster', type: 'circle', source: 'unis', filter: ['has', 'point_count'],
        paint: { 'circle-radius': ['step', ['get', 'point_count'], 11, 20, 15, 100, 20, 500, 26], 'circle-color': '#1D4ED8', 'circle-opacity': 0.88, 'circle-stroke-color': '#FFFFFF', 'circle-stroke-width': 1.6 } })
      map.addLayer({ id: 'unis-cluster-count', type: 'symbol', source: 'unis', filter: ['has', 'point_count'],
        layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-size': 11, 'text-font': ['Noto Sans Bold'] },
        paint: { 'text-color': '#FFFFFF' } })
      map.addLayer({ id: 'unis-label', type: 'symbol', source: 'unis', minzoom: 6.5, filter: ['!', ['has', 'point_count']],
        layout: { 'text-field': ['get', 'name'], 'text-size': 11, 'text-font': ['Noto Sans Regular'], 'text-offset': [0, 1.1], 'text-anchor': 'top', 'text-max-width': 12 },
        paint: { 'text-color': '#C7D2FE', 'text-halo-color': '#070B18', 'text-halo-width': 1.2 } })
      onReady?.()
    })

    const stop = () => { spinning.current = false; window.clearTimeout(idleTimer.current) }
    const resume = () => { window.clearTimeout(idleTimer.current); idleTimer.current = window.setTimeout(() => { if (map.getZoom() < 3.2) spinning.current = true }, 5000) }
    map.on('mousedown', stop); map.on('touchstart', stop); map.on('wheel', stop)
    map.on('mouseup', resume); map.on('touchend', resume); map.on('moveend', resume)
    map.on('move', () => { onZoom?.(map.getZoom()); drawStars() })

    map.on('mousemove', 'unis-point', (e: MapMouseEvent) => {
      const f = e.features?.[0]
      if (!f) return
      map.getCanvas().style.cursor = 'pointer'
      const p = f.properties as HoverInfo
      const [lon, lat] = (f.geometry as GeoJSON.Point).coordinates
      onHover?.({ ...p, x: e.point.x, y: e.point.y, lon, lat })
    })
    map.on('mouseleave', 'unis-point', () => { map.getCanvas().style.cursor = ''; onHover?.(null) })
    map.on('click', 'unis-point', (e: MapMouseEvent) => {
      const f = e.features?.[0]
      if (!f) return
      const [lon, lat] = (f.geometry as GeoJSON.Point).coordinates
      onSelect?.({ ...(f.properties as HoverInfo), x: e.point.x, y: e.point.y, lon, lat })
    })
    map.on('mouseenter', 'unis-cluster', () => { map.getCanvas().style.cursor = 'pointer' })
    map.on('mouseleave', 'unis-cluster', () => { map.getCanvas().style.cursor = '' })
    map.on('click', 'unis-cluster', async (e: MapMouseEvent) => {
      const f = e.features?.[0]
      if (!f) return
      const src = map.getSource('unis') as GeoJSONSource
      const zoom = await src.getClusterExpansionZoom(f.properties!.cluster_id as number)
      spinning.current = false
      map.easeTo({ center: (f.geometry as GeoJSON.Point).coordinates as [number, number], zoom: Math.min(zoom + 0.3, 9), duration: 900 })
    })

    let last = performance.now()
    const spin = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      if (spinning.current && map.getZoom() < 3.2) {
        const c = map.getCenter()
        map.setCenter([c.lng + 3.0 * dt, c.lat])
      }
      if (map.getZoom() < 7) drawStars()
      raf = requestAnimationFrame(spin)
    }
    raf = requestAnimationFrame(spin)
    }

    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', onResize)
      map?.remove()
      mapRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="absolute inset-0 bg-[#05070F]">
      <div ref={container} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />
      <canvas ref={stars} className="absolute inset-0 w-full h-full pointer-events-none" />
    </div>
  )
})
