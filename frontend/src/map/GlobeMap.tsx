import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import { Map as MLMap, type GeoJSONSource, type MapLayerMouseEvent } from 'maplibre-gl'
type Map = MLMap
type MapMouseEvent = MapLayerMouseEvent
import 'maplibre-gl/dist/maplibre-gl.css'
import { prefetchPath, prefetchPlanet, loadSatelliteStyle } from './darkTheme'

export interface HoverInfo { qid: string; name: string; name_en: string; city?: string; c: string; x: number; y: number; lon: number; lat: number }
export interface GlobeHandle {
  flyToUniversity: (lat: number, lon: number) => Promise<void>
  peekAt: (lat: number, lon: number) => void
  turnTo: (lat: number, lon: number) => Promise<void>
  spinAndApproach: (lat: number, lon: number, opts?: { approachMs?: number; zoom?: number; onApproach?: () => void }) => void
  setMarkers: (on: boolean) => void
  diveZoom: (lat: number, lon: number, ms?: number, zoom?: number) => void
  landAt: (lat: number, lon: number) => void
  riseBuildings: () => void
  flyToCountry: (bbox: number[]) => void
  resetToGlobe: () => void
  getMap: () => Map | null
}
interface Props {
  onHover?: (h: HoverInfo | null) => void
  onSelect?: (h: HoverInfo) => void
  onZoom?: (zoom: number) => void
  onReady?: () => void
  /** free band for the planet: px taken by the page's title (top) and search block (bottom) */
  inset?: { top: number; bottom: number }
  /** the planet's disc has grown up into the title (true) or is clear of it again (false); fires on changes only */
  onTitleOverlap?: (overlap: boolean) => void
}

const HOME: [number, number] = [66, 44]
const NO_PAD = { top: 0, bottom: 0, left: 0, right: 0 }
const DEG = Math.PI / 180
// MapLibre sizes the globe as worldSize / 2π / cos(centre lat) and views it in perspective from 0.5 / tan(fov/2) × height
const camDist = (height: number, fov: number) => 0.5 / Math.tan((fov * DEG) / 2) * height
/** on-screen radius of the planet's disc */
function globeRadiusPx(zoom: number, lat: number, height: number, fov: number) {
  const r = (512 * 2 ** zoom) / (2 * Math.PI) / Math.max(1e-3, Math.cos(lat * DEG))
  const d = camDist(height, fov), s = r / (d + r)
  return (d * s) / Math.sqrt(1 - s * s)
}
/** the zoom at which the disc has this radius (inverse of globeRadiusPx) */
function zoomForRadius(px: number, lat: number, height: number, fov: number) {
  const d = camDist(height, fov), s = px / Math.hypot(d, px)
  return Math.log2(((d * s) / (1 - s)) * 2 * Math.PI * Math.max(1e-3, Math.cos(lat * DEG)) / 512)
}
// university markers: faded out once a university is chosen, back when the user returns to the planet
const MARKERS: { id: string; props: [string, number][] }[] = [
  { id: 'unis-glow', props: [['circle-opacity', 0.35]] },
  { id: 'unis-point', props: [['circle-opacity', 1], ['circle-stroke-opacity', 1]] },
  { id: 'unis-cluster', props: [['circle-opacity', 0.88], ['circle-stroke-opacity', 1]] },
  { id: 'unis-cluster-count', props: [['text-opacity', 1]] },
  { id: 'unis-label', props: [['text-opacity', 1]] },
]
const easeInOut = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2)
// opening shot: the planet rises large from the bottom edge, then the camera pulls back to the home view.
// Once per page load - coming back to the planet from a profile should not replay it.
let introPlayed = false
const INTRO_MS = 2600
const INTRO_HOLD_MS = 900   // the big planet on screen before the camera pulls back
const INTRO_TURN = 9  // degrees the planet turns during the pull-back: about the idle spin's speed, so it hands over
// the opening frame looks at a lower latitude: seen from below with the home latitude (44°) the North Pole faces the
// camera, and raster tiles stop at 85° - a black cap on top of the dome. From 16° it sits at the limb, edge-on.
const INTRO_LAT = 16
const INTRO_DROP = 0.07  // share of the screen height between the title and the top of the big planet
const TITLE_SLACK = 26   // px the planet may reach up into the title before it fades

/** The spiral dive as a pure function of time, shared by the flight itself and by tile prefetching.
 *  The planet turns (eastward from orbit, the short way when already zoomed in) and comes closer at the same time:
 *  zoom = "closer while turning" (peaks with the rotation) + "dive" (small at first, accelerating into the clouds). */
function planSpiral(c0: { lng: number; lat: number }, z0: number, lat: number, lon: number, zTarget: number) {
  const east = (((lon - c0.lng) % 360) + 360) % 360
  const dLng = z0 > 3.2 ? ((lon - c0.lng + 540) % 360) - 180 : (east < 180 ? east + 360 : east)
  const spinMs = 1400 + Math.abs(dLng) * 3.2       // 180° → 2.0 s, 540° → 3.1 s
  const total = spinMs + 3000                      // the dive keeps going ~3 s after the turn has settled
  const zTurnEnd = Math.max(z0, 3.4)
  const turn = (t: number) => (1 - Math.cos(Math.PI * Math.pow(t, 0.7))) / 2
  const closer = (t: number) => (1 - Math.cos(Math.PI * Math.pow(t, 0.8))) / 2
  const dive = (u: number) => Math.pow(u, 2.2)
  const at = (elapsed: number) => {
    const el = Math.max(0, elapsed)                // negative time would make the fractional powers NaN
    const ts = Math.min(1, el / spinMs), u = Math.min(1, el / total)
    const e = turn(ts)
    return { e, lng: c0.lng + dLng * e, lat: c0.lat + (lat - c0.lat) * e,
             zoom: z0 + (zTurnEnd - z0) * closer(ts) + (zTarget - zTurnEnd) * dive(u) }
  }
  const samples = () => {
    const out: { lat: number; lon: number; zoom: number }[] = []
    for (let el = 0; el <= total; el += 120) { const p = at(el); out.push({ lat: p.lat, lon: ((p.lng + 540) % 360) - 180, zoom: p.zoom }) }
    return out
  }
  return { dLng, spinMs, total, at, samples }
}
const BUILDINGS = 'building-3d'

export const GlobeMap = forwardRef<GlobeHandle, Props>(function GlobeMap({ onHover, onSelect, onZoom, onReady, inset,
  onTitleOverlap }, ref) {
  const container = useRef<HTMLDivElement>(null)
  const stars = useRef<HTMLCanvasElement>(null)
  const mapRef = useRef<Map | null>(null)
  const spinning = useRef(true)
  const markersOn = useRef(true)
  const motion = useRef(0)  // rAF id of the scripted camera move (turn + approach)
  const resetTimers = useRef<number[]>([])
  const peekTimer = useRef<number | undefined>(undefined)
  const idleTimer = useRef<number | undefined>(undefined)
  const starField = useRef<{ x: number; y: number; r: number; a: number }[]>([])
  const insetRef = useRef(inset)
  const atHome = useRef(true)  // the idle planet view: its size follows the free band; false once the user zooms or flies

  // the whole planet sits in the middle of the band between the title and the search block
  const homeView = (map: Map, lat: number) => {
    const { top = 0, bottom = 0 } = insetRef.current ?? {}
    const c = map.getContainer(), w = c.clientWidth, h = c.clientHeight
    const room = Math.min((h - top - bottom) / 2, w / 2 - 16)
    const zoom = room > 40 ? Math.min(2.6, Math.max(0.5, zoomForRadius(room, lat, h, map.getVerticalFieldOfView()))) : 1.5
    return { zoom, padding: { top, bottom, left: 0, right: 0 } }
  }
  // the opening frame: a big dome rising from below with its top just under the title, on any screen. The planet is
  // as large as the zoom limit allows (the cloud layer starts at 3.5), centred on the bottom edge if that already
  // reaches the title (MapLibre clamps the padded centre to the canvas, so the edge is as low as it goes); on a big
  // monitor the zoom limit is hit first, so the centre moves up until the top touches the title.
  const introView = (map: Map, lat: number) => {
    const c = map.getContainer(), w = c.clientWidth, h = c.clientHeight
    const fov = map.getVerticalFieldOfView()
    // the title's bottom edge + a gap (measured in Globe), and a little air below it so the dome does not crowd the title
    const top = (insetRef.current?.top ?? Math.round(0.2 * h)) + Math.round(INTRO_DROP * h)
    const zoom = Math.min(3.65, zoomForRadius(Math.min(h - top, 0.62 * w), lat, h, fov))
    const r = globeRadiusPx(zoom, lat, h, fov)
    const cy = Math.min(h, top + r)   // where the planet's centre goes on screen
    return { zoom, padding: { top: Math.max(0, 2 * cy - h), bottom: 0, left: 0, right: 0 } }
  }
  const introRef = useRef<'pending' | 'running' | 'done'>(introPlayed ? 'done' : 'pending')

  const pullBack = (map: Map) => {
    if (introRef.current !== 'pending') return
    introRef.current = 'running'
    const h = map.getContainer().clientHeight, fov = map.getVerticalFieldOfView()
    const c0 = map.getCenter(), pad = map.getPadding()
    const p0 = { top: pad.top ?? 0, bottom: pad.bottom ?? 0 }
    const r0 = globeRadiusPx(map.getZoom(), c0.lat, h, fov)
    let t0 = -1
    const step = (now: number) => {
      if (introRef.current !== 'running') return
      if (t0 < 0) t0 = now
      const u = Math.min(1, Math.max(0, (now - t0) / INTRO_MS))
      const e = easeInOut(u)
      // the target is re-read every frame: the title and search block may still be settling their layout
      const lat = c0.lat + (HOME[1] - c0.lat) * e   // the camera rises over the planet to the home latitude
      const home = homeView(map, lat)
      const r1 = globeRadiusPx(home.zoom, lat, h, fov)
      // shrink the disc's radius (not the zoom) evenly: zoom is logarithmic and would make the end drag
      const zoom = zoomForRadius(r0 + (r1 - r0) * e, lat, h, fov)
      map.jumpTo({ center: [c0.lng + INTRO_TURN * u, lat], zoom,
        padding: { top: p0.top + (home.padding.top - p0.top) * e, bottom: p0.bottom + (home.padding.bottom - p0.bottom) * e,
                   left: 0, right: 0 } })
      if (u < 1) { motion.current = requestAnimationFrame(step); return }
      motion.current = 0
      endIntro()
      atHome.current = true
      spinning.current = true
      showMarkers(true, 900)   // the universities appear once the planet has settled
    }
    spinning.current = false
    motion.current = requestAnimationFrame(step)
  }
  // the opening shot always finishes: a touch, a drag or the wheel during it would otherwise fight the scripted camera
  // (or stop it halfway, leaving a giant planet under the title). Gestures are off for its ~3.5 s and come back after.
  const HANDLERS = ['dragPan', 'scrollZoom', 'boxZoom', 'dragRotate', 'keyboard', 'doubleClickZoom', 'touchZoomRotate',
                    'touchPitch'] as const
  const setGestures = (map: Map, on: boolean) => {
    for (const h of HANDLERS) { if (on) map[h].enable(); else map[h].disable() }
  }
  const endIntro = () => {
    if (introRef.current === 'done') return
    introRef.current = 'done'
    if (mapRef.current) setGestures(mapRef.current, true)
  }

  const fitHome = () => {
    const map = mapRef.current
    if (!map || motion.current || !markersOn.current || map.isMoving() || introRef.current !== 'done') return
    const home = homeView(map, map.getCenter().lat)
    map.jumpTo(atHome.current ? home : { padding: home.padding })
  }
  useEffect(() => {
    insetRef.current = inset
    fitHome()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inset?.top, inset?.bottom])

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
    const R = globeRadiusPx(z, map.getCenter().lat, h, map.getVerticalFieldOfView()) + 8
    const { top = 0, bottom = 0, left = 0, right = 0 } = map.getPadding()
    const pad = { top, bottom, left, right }
    ctx.globalCompositeOperation = 'destination-out'
    ctx.globalAlpha = 1
    ctx.beginPath(); ctx.arc(pad.left + (w - pad.left - pad.right) / 2, pad.top + (h - pad.top - pad.bottom) / 2, R, 0, Math.PI * 2); ctx.fill()
    ctx.globalCompositeOperation = 'source-over'
  }

  const flat = useRef(false)
  const flattenBuildings = (map: Map) => {
    if (flat.current || !map.getLayer(BUILDINGS)) return
    flat.current = true
    map.setPaintProperty(BUILDINGS, 'fill-extrusion-height', 0)
    map.setPaintProperty(BUILDINGS, 'fill-extrusion-base', 0)
    map.setLayerZoomRange(BUILDINGS, 13, 24)
  }

  const animateBuildings = (map: Map) => {
    flat.current = false
    if (!map.getLayer(BUILDINGS)) return
    const t0 = performance.now()
    const step = () => {
      const t = Math.min(1, Math.max(0, performance.now() - t0) / 1200)
      const e = 1 - Math.pow(1 - t, 3)
      map.setPaintProperty(BUILDINGS, 'fill-extrusion-height', ['*', ['coalesce', ['get', 'render_height'], 12], e])
      map.setPaintProperty(BUILDINGS, 'fill-extrusion-base', ['*', ['coalesce', ['get', 'render_min_height'], 0], e])
      if (t < 1) requestAnimationFrame(step)
    }
    requestAnimationFrame(step)
  }

  const showMarkers = (on: boolean, ms = 600) => {
    const map = mapRef.current
    markersOn.current = on
    if (!map) return
    for (const { id, props } of MARKERS) {
      if (!map.getLayer(id)) continue
      for (const [prop, value] of props) {
        type PaintName = Parameters<Map['setPaintProperty']>[1]
        map.setPaintProperty(id, `${prop}-transition` as PaintName, { duration: ms, delay: 0 })  // style-spec transition, honoured at runtime
        map.setPaintProperty(id, prop as PaintName, on ? value : 0)
      }
    }
    if (!on) { map.getCanvas().style.cursor = ''; onHover?.(null) }
  }

  // does the planet reach up into the title? The top of the disc against the title's bottom edge (inset.top is that
  // edge plus a 16 px gap, see Globe). Zooming in grows the disc upwards; the title fades out as the planet touches it.
  const titleOverlap = useRef(false)
  const checkTitle = (map: Map) => {
    const inset = insetRef.current
    if (!inset || !onTitleOverlap) return
    const h = map.getContainer().clientHeight, pad = map.getPadding()
    const r = globeRadiusPx(map.getZoom(), map.getCenter().lat, h, map.getVerticalFieldOfView())
    const cy = Math.min(h, Math.max(0, (pad.top ?? 0) + (h - (pad.top ?? 0) - (pad.bottom ?? 0)) / 2))
    // the planet may overlap the bottom of the letters a little before the title gives way: it fades later on the way
    // in and comes back earlier on the way out
    const over = cy - r < inset.top - 16 - TITLE_SLACK
    if (over !== titleOverlap.current) { titleOverlap.current = over; onTitleOverlap(over) }
  }

  const cancelMotion = () => { if (motion.current) cancelAnimationFrame(motion.current); motion.current = 0; endIntro() }
  const clearResetTimers = () => { resetTimers.current.forEach((id) => window.clearTimeout(id)); resetTimers.current = [] }

  useImperativeHandle(ref, () => ({
    setMarkers: (on) => showMarkers(on),
    // One continuous camera move, so the planet never visibly stops between the turn and the dive:
    //  · rotation: eastward, half to one and a half revolutions, ease-in-out, ends exactly over the university;
    //  · zoom: a short pull-back while the rotation speeds up, then one monotonic, accelerating approach that begins
    //    while the rotation is still decelerating and runs straight into the cloud deck.
    spinAndApproach: (lat, lon, opts = {}) => {
      const map = mapRef.current
      if (!map) return
      // `approachMs` = time from onApproach (cloud dive start) to the end of the move, i.e. the dive's hidden jump
      const { approachMs = 4588, zoom: zTarget = 11.2, onApproach } = opts
      cancelMotion()
      clearResetTimers()            // a quick re-pick right after «back» must not get markers / idle spin mid-flight
      window.clearTimeout(idleTimer.current)
      map.stop()
      spinning.current = false
      atHome.current = false
      window.clearTimeout(peekTimer.current)
      showMarkers(false)
      flattenBuildings(map)  // expensive style change: do it now, while buildings are out of view, not at the jump
      const b0 = map.getBearing(), p0 = map.getPitch()
      const { top = 0, bottom = 0, left = 0, right = 0 } = map.getPadding(), pad0 = { top, bottom, left, right }
      const plan = planSpiral(map.getCenter(), map.getZoom(), lat, lon, zTarget)
      const canvas = map.getCanvas()
      prefetchPath(plan.samples(), { w: canvas.clientWidth, h: canvas.clientHeight })
      const approachStart = Math.max(0, plan.total - approachMs)
      let fired = false
      let t0 = -1
      const fire = () => { if (!fired) { fired = true; onApproach?.() } }
      const step = (now: number) => {
        if (t0 < 0) t0 = now                           // rAF timestamps can precede performance.now(): start here
        const el = Math.max(0, now - t0)
        const p = plan.at(el)
        if (Number.isFinite(p.lng) && Number.isFinite(p.lat) && Number.isFinite(p.zoom)) {
          try {
            // the band offset fades out with the turn: the campus ends up in the true centre of the screen
            const k = 1 - p.e
            map.jumpTo({ center: [((p.lng + 540) % 360) - 180, p.lat], zoom: p.zoom, bearing: b0 * k, pitch: p0 * k,
              padding: { top: pad0.top * k, bottom: pad0.bottom * k, left: pad0.left * k, right: pad0.right * k } })
          } catch (err) {
            // never leave the user on a frozen globe with hidden markers: finish the transition without the camera move
            console.warn('spinAndApproach frame failed', err)
            motion.current = 0
            fire()
            return
          }
        }
        if (el >= approachStart) fire()
        motion.current = el < plan.total ? requestAnimationFrame(step) : 0
      }
      motion.current = requestAnimationFrame(step)
    },
    getMap: () => mapRef.current,
    peekAt: (lat, lon) => {
      // while the user is still typing, the planet gently turns towards the best match (no zoom, no commitment)
      const map = mapRef.current
      if (!map || motion.current || map.getZoom() > 3.4) return
      spinning.current = false
      // at home the disc keeps its size (MapLibre would scale it by 1/cos of the new latitude)
      map.easeTo({ center: [lon, lat], zoom: atHome.current ? homeView(map, lat).zoom : undefined, duration: 1400, easing: (x) => 1 - Math.pow(1 - x, 3), essential: true })
      const canvas = map.getCanvas()
      prefetchPath(planSpiral({ lng: lon, lat }, map.getZoom(), lat, lon, 11.2).samples(), { w: canvas.clientWidth, h: canvas.clientHeight }, 260)
      window.clearTimeout(peekTimer.current)
      peekTimer.current = window.setTimeout(() => { if (map.getZoom() < 3.2) spinning.current = true }, 9000)
    },
    // cinematic sequence, driven by the CloudDive overlay: turn → dive under the clouds → silent jump → rise
    turnTo: (lat, lon) => new Promise<void>((resolve) => {
      const map = mapRef.current
      if (!map) return resolve()
      spinning.current = false
      window.clearTimeout(peekTimer.current)
      showMarkers(false)
      map.stop()
      const c0 = map.getCenter(), z0 = map.getZoom(), b0 = map.getBearing(), p0 = map.getPitch()
      // the planet makes a turn before the approach: eastward like the idle spin, at least half a revolution,
      // ending exactly over the university (a target already in view gets a full extra revolution)
      const east = (((lon - c0.lng) % 360) + 360) % 360
      const dLng = east < 180 ? east + 360 : east
      const ms = 1400 + dLng * 3.2                      // 180° → 2.0 s, 540° → 3.1 s
      const zMid = Math.min(z0, 1.6)                    // pull back so the whole globe turns in frame
      const zEnd = 2.6
      const t0 = performance.now()
      const step = (now: number) => {
        const t = Math.min(1, Math.max(0, now - t0) / ms)
        const e = easeInOut(t)
        const lng = c0.lng + dLng * e
        const zoom = t < 0.5 ? z0 + (zMid - z0) * easeInOut(t * 2) : zMid + (zEnd - zMid) * easeInOut((t - 0.5) * 2)
        map.jumpTo({ center: [((lng + 540) % 360) - 180, c0.lat + (lat - c0.lat) * e], zoom, bearing: b0 * (1 - e), pitch: p0 * (1 - e) })
        if (t < 1) requestAnimationFrame(step)
        else resolve()
      }
      requestAnimationFrame(step)
    }),
    diveZoom: (lat, lon, ms = 4500, zoom = 11.2) => {
      const map = mapRef.current
      if (!map) return
      // accelerating descent: continent → region → the city itself fills the screen before the cloud deck closes
      map.easeTo({ center: [lon, lat], zoom, pitch: 0, duration: ms, easing: (x) => x * x * (3 - 2 * x) * 0.35 + x * x * 0.65, essential: true })
    },
    landAt: (lat, lon) => {
      const map = mapRef.current
      if (!map) return
      cancelMotion()
      map.stop()
      flattenBuildings(map)  // no-op when spinAndApproach already did it
      map.jumpTo({ center: [lon, lat], zoom: 16.2, pitch: 60, bearing: -17, padding: NO_PAD })
    },
    riseBuildings: () => { const map = mapRef.current; if (map) animateBuildings(map) },
    flyToUniversity: (lat, lon) => new Promise<void>((resolve) => {
      const map = mapRef.current
      if (!map) return resolve()
      spinning.current = false
      flattenBuildings(map)
      map.once('moveend', () => { animateBuildings(map); resolve() })
      map.flyTo({ center: [lon, lat], zoom: 16.2, pitch: 60, bearing: -17, duration: 3400, curve: 1.6, essential: true })
    }),
    flyToCountry: (bbox) => {
      const map = mapRef.current
      if (!map) return
      spinning.current = false
      atHome.current = false
      map.fitBounds([[bbox[1], bbox[0]], [bbox[3], bbox[2]]], { padding: 80, pitch: 25, bearing: 0, duration: 2200, maxZoom: 6.5 })
    },
    resetToGlobe: () => {
      const map = mapRef.current
      if (!map) return
      cancelMotion()
      clearResetTimers()
      atHome.current = true
      map.flyTo({ center: HOME, ...homeView(map, HOME[1]), pitch: 0, bearing: 0, duration: 2000 })
      resetTimers.current = [
        window.setTimeout(() => showMarkers(true, 900), 900),
        window.setTimeout(() => { spinning.current = true }, 2100),
      ]
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
        attributionControl: false,
        canvasContextAttributes: { antialias: true },
        maxPitch: 70,
      })
      mapRef.current = map
      const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      if (introRef.current === 'pending' && !still) {
        introPlayed = true
        setGestures(map, false)
        map.jumpTo({ ...introView(map, INTRO_LAT), center: [HOME[0] - INTRO_TURN, INTRO_LAT] })  // before the first frame
        // the planet is textured from the first frame (local tiles, see loadSatelliteStyle): no waiting on the
        // network - a beat to take in the big planet, then the camera pulls back
        map.once('style.load', () => window.setTimeout(() => {
          if (cancelled || !map) return
          // the title may have moved since (web fonts): frame the dome against where it is now
          if (introRef.current === 'pending') map.jumpTo({ ...introView(map, INTRO_LAT), center: [HOME[0] - INTRO_TURN, INTRO_LAT] })
          pullBack(map)
        }, INTRO_HOLD_MS))
      } else {
        introRef.current = 'done'
        map.jumpTo(homeView(map, HOME[1]))  // before the first frame
      }
      ;(window as unknown as { __map?: Map }).__map = map
      const errors: string[] = []
      ;(window as unknown as { __mapErrors?: string[] }).__mapErrors = errors
      map.on('error', (e) => { errors.push(String((e as { error?: { message?: string } }).error?.message ?? e)) })
      setup(map)
      window.addEventListener('resize', onResize)
    })

    const setup = (map: Map) => {
    map.on('style.load', () => {
      // MapLibre 6 bug: with a globe style object it resizes only the camera (not the canvas) at style load, and its
      // ResizeObserver then skips the first callback as "already that size". A container that changed size between
      // `new Map` and this point (a scrollbar, the window or pane resizing) left a stale canvas: an empty black planet.
      map.resize()
      if (map.getSource('unis')) return
      map.setProjection({ type: 'globe' })
      map.once('idle', prefetchPlanet)  // after the first view is in: the prefetch must not compete with its tiles
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
      // the opening shot is the planet alone: a dome covered in hundreds of blue dots reads as noise
      if (introRef.current !== 'done') showMarkers(false, 0)
      onReady?.()
    })

    const stop = () => { if (introRef.current === 'done') spinning.current = false; window.clearTimeout(idleTimer.current) }
    const resume = () => { window.clearTimeout(idleTimer.current); idleTimer.current = window.setTimeout(() => { if (map.getZoom() < 3.2) spinning.current = true }, 5000) }
    map.on('mousedown', stop); map.on('touchstart', stop); map.on('wheel', stop)
    map.on('mouseup', resume); map.on('touchend', resume); map.on('moveend', resume)
    map.on('move', () => { onZoom?.(map.getZoom()); drawStars(); checkTitle(map) })
    map.on('zoomstart', (e) => { if (e.originalEvent) atHome.current = false })
    map.on('resize', fitHome)

    map.on('mousemove', 'unis-point', (e: MapMouseEvent) => {
      const f = e.features?.[0]
      if (!f || !markersOn.current) return
      map.getCanvas().style.cursor = 'pointer'
      const p = f.properties as HoverInfo
      const [lon, lat] = (f.geometry as GeoJSON.Point).coordinates
      onHover?.({ ...p, x: e.point.x, y: e.point.y, lon, lat })
    })
    map.on('mouseleave', 'unis-point', () => { map.getCanvas().style.cursor = ''; onHover?.(null) })
    map.on('click', 'unis-point', (e: MapMouseEvent) => {
      const f = e.features?.[0]
      if (!f || !markersOn.current) return
      const [lon, lat] = (f.geometry as GeoJSON.Point).coordinates
      onSelect?.({ ...(f.properties as HoverInfo), x: e.point.x, y: e.point.y, lon, lat })
    })
    map.on('mouseenter', 'unis-cluster', () => { if (markersOn.current) map.getCanvas().style.cursor = 'pointer' })
    map.on('mouseleave', 'unis-cluster', () => { map.getCanvas().style.cursor = '' })
    map.on('click', 'unis-cluster', async (e: MapMouseEvent) => {
      const f = e.features?.[0]
      if (!f || !markersOn.current) return
      const src = map.getSource('unis') as GeoJSONSource
      const zoom = await src.getClusterExpansionZoom(f.properties!.cluster_id as number)
      spinning.current = false
      atHome.current = false
      map.easeTo({ center: (f.geometry as GeoJSON.Point).coordinates as [number, number], zoom: Math.min(zoom + 0.3, 9), duration: 900 })
    })

    let last = performance.now()
    const spin = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      if (spinning.current && !motion.current && map.getZoom() < 3.2) {
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
