import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import { Map as MLMap, Popup, type GeoJSONSource } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { prefetchPlanet, loadSatelliteStyle } from './darkTheme'
import {
  CAMPUS_LIMITS, PLANET_LIMITS, cityLimits, cityOf, countryAt, countryLimits, layerVisibleAt, lngLatBounds, loadCountries,
  loadCountry, localName, padBox, pointCity,
  type BBox, type CityInfo, type CountryFile, type CountryInfo, type Level, type LevelLimits, type UniPoint,
} from './levels'

type Map = MLMap

export interface LevelState { level: Level; country: CountryInfo | null; city: CityInfo | null }
export interface UniSelection extends UniPoint { cityName?: string; countryIso?: string }
export interface GlobeHandle {
  getMap: () => Map | null
  peekAt: (lat: number, lon: number) => void
  enterPlanet: () => void
  enterCountry: (iso: string) => Promise<void>
  enterCity: (iso: string, cityId: number) => Promise<void>
  levelUp: () => void
  /** search result → staged spiral flight straight to its city, the university selected */
  flyToUniversity: (u: { qid: string; name: string; lat: number; lon: number; city?: string | null }) => Promise<UniSelection | null>
  select: (qid: string | null) => void
  diveIntoCampus: (lat: number, lon: number, ms: number) => void
  landAt: (lat: number, lon: number) => void
  riseBuildings: () => void
  exitCampus: () => void
}
interface Props {
  lang: string
  t: (key: string) => string
  lite?: boolean
  onLevel?: (s: LevelState) => void
  onSelectUni?: (u: UniSelection | null) => void
  onOpenCampus?: (u: UniSelection) => void
  onHint?: (hint: 'deeper' | 'up' | null) => void
}

const HOME: [number, number] = [66, 44]
const BUILDINGS = 'building-3d'
const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] }
const esc = (s: string) => s.replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch] as string))

/** Stretchable rounded "flat card" drawn in GL behind university names (icon-text-fit), one image for all labels. */
function cardImage(fill: string, stroke: string): ImageData {
  const s = 2, w = 24 * s, h = 20 * s
  const c = document.createElement('canvas'); c.width = w; c.height = h
  const g = c.getContext('2d')!
  g.beginPath(); g.roundRect(1, 1, w - 2, h - 2, 6 * s)
  g.fillStyle = fill; g.fill(); g.lineWidth = 1 * s; g.strokeStyle = stroke; g.stroke()
  return g.getImageData(0, 0, w, h)
}

export const GlobeMap = forwardRef<GlobeHandle, Props>(function GlobeMap({ lang, t, lite, onLevel, onSelectUni, onOpenCampus, onHint }, ref) {
  const container = useRef<HTMLDivElement>(null)
  const stars = useRef<HTMLCanvasElement>(null)
  const mapRef = useRef<Map | null>(null)
  const spinning = useRef(true)
  const motion = useRef(0)
  const peekTimer = useRef<number | undefined>(undefined)
  const idleTimer = useRef<number | undefined>(undefined)
  const starField = useRef<{ x: number; y: number; r: number; a: number }[]>([])
  const layerIds = useRef<string[]>([])
  const state = useRef<LevelState>({ level: 'planet', country: null, city: null })
  const countries = useRef<CountryInfo[]>([])
  const file = useRef<CountryFile | null>(null)
  const extra = useRef<UniPoint[]>([])
  const selected = useRef<string | null>(null)
  const popup = useRef<Popup | null>(null)
  const busy = useRef(false)  // a level transition is running: ignore interaction-driven transitions
  const props = useRef({ lang, t, onLevel, onSelectUni, onOpenCampus, onHint })
  props.current = { lang, t, onLevel, onSelectUni, onOpenCampus, onHint }

  // ------------------------------------------------------------------ stars (planet level only)
  const drawStars = () => {
    const c = stars.current, map = mapRef.current
    if (!c || !map) return
    const dpr = Math.min(1.5, window.devicePixelRatio || 1)
    const w = c.clientWidth, h = c.clientHeight
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr) }
    const ctx = c.getContext('2d')!
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, w, h)
    const z = map.getZoom()
    const fade = z > 4 ? Math.max(0, 1 - (z - 4) / 3) : 1
    if (fade <= 0) return
    if (!starField.current.length) for (let i = 0; i < 420; i++) starField.current.push({ x: Math.random(), y: Math.random(), r: Math.random() * 1.3 + 0.3, a: Math.random() })
    const tt = performance.now() / 1000
    ctx.fillStyle = '#DCE4FF'
    for (const s of starField.current) {
      ctx.globalAlpha = (lite ? 0.7 : 0.55 + 0.45 * Math.sin(tt * 1.3 + s.a * 20)) * fade * 0.9
      ctx.beginPath(); ctx.arc(s.x * w, s.y * h, s.r, 0, Math.PI * 2); ctx.fill()
    }
    const R = (512 * Math.pow(2, z)) / (2 * Math.PI) + 8   // keep the planet disc clear
    ctx.globalCompositeOperation = 'destination-out'; ctx.globalAlpha = 1
    ctx.beginPath(); ctx.arc(w / 2, h / 2, R, 0, Math.PI * 2); ctx.fill()
    ctx.globalCompositeOperation = 'source-over'
  }

  // ------------------------------------------------------------------ 3D buildings (campus level only)
  const flat = useRef(false)
  const flattenBuildings = (map: Map) => {
    if (flat.current || !map.getLayer(BUILDINGS)) return
    flat.current = true
    map.setPaintProperty(BUILDINGS, 'fill-extrusion-height', 0)
    map.setPaintProperty(BUILDINGS, 'fill-extrusion-base', 0)
  }
  const animateBuildings = (map: Map) => {
    flat.current = false
    if (!map.getLayer(BUILDINGS)) return
    const t0 = performance.now()
    const step = () => {
      const k = Math.min(1, (performance.now() - t0) / 1200)
      const e = 1 - Math.pow(1 - k, 3)
      map.setPaintProperty(BUILDINGS, 'fill-extrusion-height', ['*', ['coalesce', ['get', 'render_height'], 12], e])
      map.setPaintProperty(BUILDINGS, 'fill-extrusion-base', ['*', ['coalesce', ['get', 'render_min_height'], 0], e])
      if (k < 1) requestAnimationFrame(step)
    }
    requestAnimationFrame(step)
  }

  // ------------------------------------------------------------------ level machinery
  const setVisible = (map: Map, show: (id: string) => boolean) => {
    for (const id of layerIds.current) {
      if (!map.getLayer(id)) continue
      const vis = show(id) ? 'visible' : 'none'
      if (map.getLayoutProperty(id, 'visibility') !== vis) map.setLayoutProperty(id, 'visibility', vis)
    }
  }
  const relax = (map: Map) => { map.setMaxBounds(null); map.setMinZoom(0); map.setMaxZoom(20); map.setMaxPitch(70) }
  const applyLimits = (map: Map, lim: LevelLimits, bounds?: BBox | null) => {
    map.setMinZoom(Math.min(lim.minZoom, map.getZoom()))
    map.setMaxZoom(Math.max(lim.maxZoom, map.getZoom()))
    map.setMaxPitch(lim.maxPitch)
    if (lim.rotate) { map.dragRotate.enable(); map.touchZoomRotate.enableRotation() }
    else { map.dragRotate.disable(); map.touchZoomRotate.disableRotation() }
    if (bounds) map.setMaxBounds(lngLatBounds(bounds))
  }
  const report = (next: Partial<LevelState>) => {
    state.current = { ...state.current, ...next }
    props.current.onLevel?.(state.current)
  }
  const cancelMotion = () => { if (motion.current) cancelAnimationFrame(motion.current); motion.current = 0 }
  // camera moves started inside a map input event are cancelled by MapLibre's handler manager right after the event;
  // start them on the next tick instead
  const later = (fn: () => void) => { window.setTimeout(fn, 30) }

  const flyCam = (map: Map, cam: { center: [number, number]; zoom: number; pitch?: number; bearing?: number }, ms: number) =>
    new Promise<void>((resolve) => {
      cancelMotion(); relax(map); map.stop()
      let done = false
      const finish = () => { if (!done) { done = true; resolve() } }
      map.once('moveend', finish)
      window.setTimeout(finish, ms + 400)
      map.flyTo({ center: cam.center, zoom: cam.zoom, pitch: cam.pitch ?? 0, bearing: cam.bearing ?? 0, duration: ms, curve: 1.42, essential: true })
    })

  const camFor = (map: Map, b: BBox, padding: number) => {
    relax(map)  // cameraForBounds clamps to the current level's zoom range: lift it first
    const cam = map.cameraForBounds(lngLatBounds(b), { padding })
    const c = cam?.center as { lng: number; lat: number } | [number, number] | undefined
    const center: [number, number] = Array.isArray(c) ? c : c ? [c.lng, c.lat] : [(b[1] + b[3]) / 2, (b[0] + b[2]) / 2]
    return { center, zoom: cam?.zoom ?? 5 }
  }

  const setSource = (map: Map, id: string, data: GeoJSON.FeatureCollection) => (map.getSource(id) as GeoJSONSource | undefined)?.setData(data)

  const countriesFC = (): GeoJSON.FeatureCollection => ({
    type: 'FeatureCollection',
    features: countries.current.map((c, i) => ({
      type: 'Feature', id: i, geometry: { type: 'Point', coordinates: [c.center[1], c.center[0]] },
      properties: { iso: c.iso, name: localName(c, props.current.lang), count: c.count, cities: c.cities },
    })),
  })
  const citiesFC = (f: CountryFile | null): GeoJSON.FeatureCollection => ({
    type: 'FeatureCollection',
    features: (f?.cities ?? []).map((c) => ({ type: 'Feature', id: c.id, geometry: { type: 'Point', coordinates: [c.lon, c.lat] }, properties: { id: c.id, name: c.name, count: c.count } })),
  })
  const unisOf = (city: CityInfo | null): UniPoint[] => {
    const base = city && file.current && city.id >= 0 ? file.current.unis.filter((u) => u.c === city.id) : []
    return [...base, ...extra.current.filter((x) => !base.some((b) => b.qid === x.qid))]
  }
  const unisFC = (list: UniPoint[], cityName: string): GeoJSON.FeatureCollection => ({
    type: 'FeatureCollection',
    features: list.map((u, i) => ({ type: 'Feature', id: i, geometry: { type: 'Point', coordinates: [u.lon, u.lat] }, properties: { qid: u.qid, name: localName(u, props.current.lang), city: cityName } })),
  })

  // ------------------------------------------------------------------ popups (one flat card at a time)
  const showPopup = (map: Map, lngLat: [number, number], html: string) => {
    popup.current ??= new Popup({ closeButton: false, closeOnClick: false, className: 'cl-popup', offset: 16, maxWidth: '260px' })
    popup.current.setLngLat(lngLat).setHTML(html).addTo(map)
  }
  const hidePopup = () => popup.current?.remove()
  const uniCard = (u: UniPoint, city: string, sel: boolean) =>
    `<div class="clp${sel ? ' clp-sel' : ''}"><div class="clp-t">${esc(localName(u, props.current.lang))}</div>` +
    `<div class="clp-s">${esc(city)}</div><div class="clp-h">${esc(props.current.t('lvl.dblCampus'))}</div></div>`
  const selectedCard = (_map: Map) => { hidePopup() }  // the selection is shown by the blue in-map card + the side card
  const selectionOf = (qid: string): UniSelection | null => {
    const u = unisOf(state.current.city).find((x) => x.qid === qid)
    return u ? { ...u, cityName: state.current.city?.name, countryIso: state.current.country?.iso } : null
  }
  const doSelect = (map: Map, qid: string | null) => {
    selected.current = qid
    if (map.getLayer('lvl-uni-selected')) {
      const f = ['==', ['get', 'qid'], qid ?? '__none__'] as never
      map.setFilter('lvl-uni-selected', f)
      map.setFilter('lvl-uni-card-selected', f)
      map.setFilter('lvl-uni-card', ['!=', ['get', 'qid'], qid ?? '__none__'] as never)
    }
    selectedCard(map)
    props.current.onSelectUni?.(qid ? selectionOf(qid) : null)
  }

  // ------------------------------------------------------------------ transitions
  const goPlanet = async () => {
    const map = mapRef.current
    if (!map) return
    busy.current = true
    doSelect(map, null); hidePopup()
    const from = state.current.level
    setVisible(map, (id) => layerVisibleAt(from, id) || layerVisibleAt('planet', id))  // no black gap while flying up
    report({ level: 'planet', country: null, city: null })
    await flyCam(map, { center: [map.getCenter().lng, Math.max(-35, Math.min(55, map.getCenter().lat))], zoom: 1.6 }, 1900)
    setSource(map, 'lvl-cities', EMPTY); setSource(map, 'lvl-unis', EMPTY)
    extra.current = []
    setVisible(map, (id) => layerVisibleAt('planet', id))
    applyLimits(map, PLANET_LIMITS)
    busy.current = false
    window.setTimeout(() => { if (state.current.level === 'planet') spinning.current = true }, 2500)
  }

  const goCountry = async (iso: string) => {
    const map = mapRef.current
    const c = countries.current.find((x) => x.iso === iso)
    if (!map || !c) return
    busy.current = true
    spinning.current = false
    doSelect(map, null); hidePopup()
    const from = state.current.level
    const f = await loadCountry(iso).catch(() => null)
    file.current = f
    extra.current = []
    setSource(map, 'lvl-cities', citiesFC(f)); setSource(map, 'lvl-unis', EMPTY)
    setVisible(map, (id) => (layerVisibleAt(from, id) && !id.startsWith('lvl-')) || layerVisibleAt('country', id))
    report({ level: 'country', country: c, city: null })
    const cam = camFor(map, c.bbox, 70)
    await flyCam(map, cam, from === 'planet' ? 2000 : 1500)
    setVisible(map, (id) => layerVisibleAt('country', id))
    applyLimits(map, countryLimits(cam.zoom), padBox(c.bbox, 0.6, 6))
    busy.current = false
  }

  const goCity = async (country: CountryInfo | null, city: CityInfo, ms: number | 'spiral' = 1500) => {
    const map = mapRef.current
    if (!map) return
    busy.current = true
    spinning.current = false
    hidePopup()
    const from = state.current.level
    setSource(map, 'lvl-unis', unisFC(unisOf(city), city.name))
    report({ level: 'city', country, city })
    const cam = camFor(map, city.bbox, 90)
    if (ms === 'spiral') {
      setVisible(map, (id) => layerVisibleAt('planet', id) && !id.startsWith('lvl-') || layerVisibleAt('city', id))
      await spiral(map, cam.center, cam.zoom)
    } else {
      setVisible(map, (id) => (layerVisibleAt(from, id) && !id.startsWith('lvl-')) || layerVisibleAt('city', id))
      await flyCam(map, cam, ms)
    }
    setVisible(map, (id) => layerVisibleAt('city', id))
    applyLimits(map, cityLimits(cam.zoom), padBox(city.bbox, 1.5, 0.08))
    busy.current = false
  }

  /** planet → city in one continuous move: the globe turns and grows at the same time (no stop in between) */
  const spiral = (map: Map, center: [number, number], zTarget: number) => new Promise<void>((resolve) => {
    cancelMotion(); relax(map); map.stop()
    const [lon, lat] = center
    const c0 = map.getCenter(), z0 = map.getZoom(), b0 = map.getBearing(), p0 = map.getPitch()
    const east = (((lon - c0.lng) % 360) + 360) % 360
    const dLng = z0 > 3.4 ? ((lon - c0.lng + 540) % 360) - 180 : (east < 60 ? east + 360 : east)
    const spinMs = 900 + Math.abs(dLng) * 3
    const total = spinMs + 1700
    const turn = (x: number) => (1 - Math.cos(Math.PI * Math.pow(x, 0.7))) / 2
    const grow = (u: number) => 0.15 * u + 0.85 * Math.pow(u, 1.7)
    const t0 = performance.now()
    const step = (now: number) => {
      const el = now - t0
      const e = turn(Math.min(1, el / spinMs))
      const u = Math.min(1, el / total)
      const lng = c0.lng + dLng * e
      map.jumpTo({ center: [((lng + 540) % 360) - 180, c0.lat + (lat - c0.lat) * e], zoom: z0 + (zTarget - z0) * grow(u), bearing: b0 * (1 - e), pitch: p0 * (1 - e) })
      if (el < total) motion.current = requestAnimationFrame(step)
      else { motion.current = 0; resolve() }
    }
    motion.current = requestAnimationFrame(step)
  })

  const levelUp = () => {
    const s = state.current
    if (s.level === 'city' && s.country) void goCountry(s.country.iso)
    else if (s.level === 'city' || s.level === 'country') void goPlanet()
  }

  useImperativeHandle(ref, () => ({
    getMap: () => mapRef.current,
    peekAt: (lat, lon) => {
      const map = mapRef.current
      if (!map || motion.current || busy.current || state.current.level !== 'planet') return
      spinning.current = false
      map.easeTo({ center: [lon, lat], duration: 1400, easing: (x) => 1 - Math.pow(1 - x, 3), essential: true })
      window.clearTimeout(peekTimer.current)
      peekTimer.current = window.setTimeout(() => { if (state.current.level === 'planet') spinning.current = true }, 9000)
    },
    enterPlanet: () => { void goPlanet() },
    enterCountry: (iso) => goCountry(iso),
    enterCity: async (iso, cityId) => {
      const c = countries.current.find((x) => x.iso === iso) ?? null
      if (!file.current || file.current.iso !== iso) file.current = await loadCountry(iso).catch(() => null)
      const city = file.current?.cities[cityId]
      if (city) await goCity(c, city)
    },
    levelUp,
    flyToUniversity: async (u) => {
      const map = mapRef.current
      if (!map) return null
      busy.current = true
      doSelect(map, null); hidePopup()
      if (!countries.current.length) countries.current = await loadCountries()
      const c = countryAt(countries.current, u.lat, u.lon)
      const f = c ? await loadCountry(c.iso).catch(() => null) : null
      file.current = f
      let city = f ? cityOf(f, u.qid, u.lat, u.lon) : null
      city ??= pointCity(u.city ?? u.name, u.lat, u.lon)
      extra.current = f?.unis.some((x) => x.qid === u.qid) ? [] : [{ qid: u.qid, name: u.name, lat: u.lat, lon: u.lon, c: city.id }]
      setSource(map, 'lvl-cities', citiesFC(f))
      await goCity(c, city, 'spiral')
      doSelect(map, u.qid)
      return selectionOf(u.qid)
    },
    select: (qid) => { const map = mapRef.current; if (map) doSelect(map, qid) },
    // ---- campus (level 4): driven by the page's cloud dive
    diveIntoCampus: (lat, lon, ms) => {
      const map = mapRef.current
      if (!map) return
      cancelMotion(); relax(map); map.stop()
      flattenBuildings(map)
      hidePopup()
      map.easeTo({ center: [lon, lat], zoom: Math.min(16, map.getZoom() + 2.5), duration: ms, easing: (x) => x * x, essential: true })
    },
    landAt: (lat, lon) => {
      const map = mapRef.current
      if (!map) return
      cancelMotion(); relax(map); map.stop()
      flattenBuildings(map)
      setVisible(map, (id) => layerVisibleAt('campus', id) && (!id.startsWith('lvl-uni') || id === 'lvl-uni-selected'))
      map.jumpTo({ center: [lon, lat], zoom: 16.2, pitch: 60, bearing: -17 })
      applyLimits(map, CAMPUS_LIMITS, padBox([lat - 0.02, lon - 0.03, lat + 0.02, lon + 0.03], 1, 0.05))
      report({ level: 'campus' })
    },
    riseBuildings: () => { const map = mapRef.current; if (map) animateBuildings(map) },
    exitCampus: () => {
      const s = state.current
      if (s.city) void goCity(s.country, s.city).then(() => { const map = mapRef.current; if (map) doSelect(map, selected.current) })
      else void goPlanet()
    },
  }))

  // names follow the interface language
  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.getSource('lvl-countries')) return
    setSource(map, 'lvl-countries', countriesFC())
    if (state.current.city) setSource(map, 'lvl-unis', unisFC(unisOf(state.current.city), state.current.city.name))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lang])

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
        zoom: 1.6,
        attributionControl: { compact: true },
        canvasContextAttributes: { antialias: !lite },
        pixelRatio: Math.min(window.devicePixelRatio || 1, lite ? 1 : 1.5),
        fadeDuration: 150,
        maxPitch: 0,
        minZoom: PLANET_LIMITS.minZoom,
        maxZoom: PLANET_LIMITS.maxZoom,
        doubleClickZoom: false,
        dragRotate: false,
      })
      mapRef.current = map
      ;(window as unknown as { __map?: Map }).__map = map
      ;(window as unknown as { __lvl?: () => unknown }).__lvl = () => ({ busy: busy.current, motion: motion.current, level: state.current.level, selected: selected.current })
      map.on('error', (e) => console.warn('map error:', (e as { error?: { message?: string } }).error?.message ?? e))
      setup(map)
      window.addEventListener('resize', onResize)
    })

    const setup = (map: Map) => {
      // speed limits: slower wheel/trackpad zoom, a gentle fling when the globe is thrown
      map.scrollZoom.setWheelZoomRate(1 / 700)
      map.scrollZoom.setZoomRate(1 / 160)
      map.dragPan.disable()
      map.dragPan.enable({ linearity: 0.25, maxSpeed: 700, deceleration: 3200 })
      map.touchZoomRotate.disableRotation()

      map.on('style.load', async () => {
        if (map.getSource('lvl-countries')) return
        map.setProjection({ type: 'globe' })
        prefetchPlanet(3)
        map.addImage('cl-card', cardImage('rgba(255,255,255,0.96)', 'rgba(10,10,10,0.14)'), { pixelRatio: 2, stretchX: [[12, 36]], stretchY: [[12, 28]], content: [10, 8, 38, 32] })
        map.addImage('cl-card-sel', cardImage('#1D4ED8', 'rgba(255,255,255,0.9)'), { pixelRatio: 2, stretchX: [[12, 36]], stretchY: [[12, 28]], content: [10, 8, 38, 32] })
        const bubble = (id: string, source: string, color: string, rmin: number, rmax: number, cmax: number) => {
          map.addSource(source, { type: 'geojson', data: EMPTY })
          map.addLayer({ id: `${id}-halo`, type: 'circle', source, paint: {
            'circle-radius': ['+', 6, ['interpolate', ['linear'], ['sqrt', ['get', 'count']], 1, rmin, Math.sqrt(cmax), rmax]],
            'circle-color': color, 'circle-opacity': 0.18, 'circle-blur': 0.6 } })
          map.addLayer({ id: `${id}-bubble`, type: 'circle', source, paint: {
            'circle-radius': ['interpolate', ['linear'], ['sqrt', ['get', 'count']], 1, rmin, Math.sqrt(cmax), rmax],
            'circle-color': color, 'circle-opacity': 0.9, 'circle-stroke-color': '#FFFFFF', 'circle-stroke-width': 1.4 } })
          map.addLayer({ id: `${id}-count`, type: 'symbol', source, layout: {
            'text-field': ['to-string', ['get', 'count']], 'text-size': 11, 'text-font': ['Noto Sans Bold'], 'text-allow-overlap': true, 'text-ignore-placement': true },
            paint: { 'text-color': '#FFFFFF' } })
          map.addLayer({ id: `${id}-name`, type: 'symbol', source, layout: {
            'text-field': ['get', 'name'], 'text-size': 12, 'text-font': ['Noto Sans Regular'], 'text-anchor': 'top',
            'text-radial-offset': ['+', 0.6, ['/', ['interpolate', ['linear'], ['sqrt', ['get', 'count']], 1, rmin, Math.sqrt(cmax), rmax], 12]],
            'text-max-width': 9, 'symbol-sort-key': ['-', 0, ['get', 'count']] },
            paint: { 'text-color': '#FFFFFF', 'text-halo-color': '#07101F', 'text-halo-width': 1.4 } })
        }
        bubble('lvl-country', 'lvl-countries', '#1D4ED8', 8, 30, 2700)
        bubble('lvl-city', 'lvl-cities', '#2563EB', 10, 26, 150)
        map.addSource('lvl-unis', { type: 'geojson', data: EMPTY })
        map.addLayer({ id: 'lvl-uni-glow', type: 'circle', source: 'lvl-unis', paint: { 'circle-radius': 13, 'circle-color': '#60A5FA', 'circle-opacity': 0.25, 'circle-blur': 0.7 } })
        map.addLayer({ id: 'lvl-uni-pin', type: 'circle', source: 'lvl-unis', paint: {
          'circle-radius': 6.5, 'circle-color': '#FFFFFF', 'circle-stroke-color': '#1D4ED8', 'circle-stroke-width': 3 } })
        map.addLayer({ id: 'lvl-uni-selected', type: 'circle', source: 'lvl-unis', filter: ['==', ['get', 'qid'], '__none__'], paint: {
          'circle-radius': 9, 'circle-color': '#1D4ED8', 'circle-stroke-color': '#FFFFFF', 'circle-stroke-width': 3 } })
        const cardLayout = (image: string, overlap: boolean) => ({
          'text-field': ['get', 'name'], 'text-size': 11, 'text-font': ['Noto Sans Regular'], 'text-max-width': 13,
          'text-anchor': 'bottom', 'text-offset': [0, -1.6], 'text-allow-overlap': overlap, 'icon-allow-overlap': overlap,
          'icon-image': image, 'icon-text-fit': 'both', 'icon-text-fit-padding': [5, 9, 5, 9], 'icon-anchor': 'bottom',
        })
        map.addLayer({ id: 'lvl-uni-card', type: 'symbol', source: 'lvl-unis', minzoom: 9, layout: cardLayout('cl-card', false) as never,
          paint: { 'text-color': '#0A0A0A' } })
        map.addLayer({ id: 'lvl-uni-card-selected', type: 'symbol', source: 'lvl-unis', filter: ['==', ['get', 'qid'], '__none__'],
          layout: cardLayout('cl-card-sel', true) as never, paint: { 'text-color': '#FFFFFF' } })
        layerIds.current = map.getLayersOrder()
        countries.current = await loadCountries()
        setSource(map, 'lvl-countries', countriesFC())
        setVisible(map, (id) => layerVisibleAt('planet', id))
        report({ level: 'planet', country: null, city: null })
      })

      const stop = () => { spinning.current = false; window.clearTimeout(idleTimer.current) }
      const resume = () => {
        window.clearTimeout(idleTimer.current)
        idleTimer.current = window.setTimeout(() => { if (state.current.level === 'planet' && !motion.current && !busy.current) spinning.current = true }, 5000)
      }
      map.on('mousedown', stop); map.on('touchstart', stop); map.on('wheel', stop)
      map.on('mouseup', resume); map.on('touchend', resume)

      // ---- picking: MapLibre's per-layer mouse events miss circles on the globe, so we query a small box ourselves
      // and take the object nearest to the cursor
      const PICK: Record<Level, string[]> = {
        planet: ['lvl-country-bubble'],
        country: ['lvl-city-bubble'],
        city: ['lvl-uni-pin', 'lvl-uni-card', 'lvl-uni-card-selected'],
        campus: [],
      }
      const pick = (pt: { x: number; y: number }, r: number) => {
        const layers = PICK[state.current.level].filter((id) => map.getLayer(id))
        if (!layers.length) return null
        const hits = map.queryRenderedFeatures([[pt.x - r, pt.y - r], [pt.x + r, pt.y + r]], { layers })
        let best: (typeof hits)[number] | null = null, bestD = Infinity
        for (const f of hits) {
          const [lon, lat] = (f.geometry as GeoJSON.Point).coordinates
          const q = map.project([lon, lat])
          const d = Math.hypot(q.x - pt.x, q.y - pt.y)
          if (d < bestD) { bestD = d; best = f }
        }
        return best
      }
      const tr = (k: string) => props.current.t(k)
      const cardFor = (f: NonNullable<ReturnType<typeof pick>>): { at: [number, number]; html: string } | null => {
        const p = f.properties as Record<string, unknown>
        const at = (f.geometry as GeoJSON.Point).coordinates as [number, number]
        if (state.current.level === 'planet')
          return { at, html: `<div class="clp"><div class="clp-t">${esc(String(p.name))}</div><div class="clp-s">${p.count} ${esc(tr('lvl.unis'))} · ${p.cities} ${esc(tr('lvl.cities'))}</div><div class="clp-h">${esc(tr('lvl.dblCountry'))}</div></div>` }
        if (state.current.level === 'country')
          return { at, html: `<div class="clp"><div class="clp-t">${esc(String(p.name) || '—')}</div><div class="clp-s">${p.count} ${esc(tr('lvl.unis'))}</div><div class="clp-h">${esc(tr('lvl.dblCity'))}</div></div>` }
        const u = unisOf(state.current.city).find((x) => x.qid === String(p.qid))
        return u && u.qid !== selected.current ? { at: [u.lon, u.lat], html: uniCard(u, state.current.city?.name ?? '', false) } : null
      }
      let hoverKey: string | null = null, hoverRaf = 0
      let lastPt = { x: 0, y: 0 }
      const onHover = () => {
        hoverRaf = 0
        if (busy.current || motion.current) return
        const f = pick(lastPt, 7)
        const key = f ? `${state.current.level}:${String(f.properties?.iso ?? f.properties?.id ?? f.properties?.qid)}` : null
        map.getCanvas().style.cursor = f ? 'pointer' : ''
        if (key === hoverKey) return
        hoverKey = key
        const card = f ? cardFor(f) : null
        if (card) showPopup(map, card.at, card.html)
        else if (selected.current) selectedCard(map)
        else hidePopup()
      }
      map.on('mousemove', (e) => {
        lastPt = e.point
        if (state.current.level === 'planet' && spinning.current) { spinning.current = false; resume() }
        if (!hoverRaf) hoverRaf = requestAnimationFrame(onHover)
      })
      map.on('mouseout', () => { hoverKey = null; resume(); map.getCanvas().style.cursor = ''; if (selected.current) selectedCard(map); else hidePopup() })
      map.on('movestart', () => { hoverKey = null })

      map.on('click', (e) => {
        if (busy.current || state.current.level !== 'city') return
        const f = pick(e.point, 9)
        doSelect(map, f ? String(f.properties?.qid) : null)
      })

      // double click = one level deeper
      map.on('dblclick', (e) => {
        e.preventDefault()
        if (busy.current || motion.current) return
        const s = state.current
        const f = pick(e.point, 12)
        if (s.level === 'planet') {
          const iso = (f?.properties?.iso as string | undefined) ?? countryAt(countries.current, e.lngLat.lat, e.lngLat.lng)?.iso
          if (iso) later(() => void goCountry(iso))
        } else if (s.level === 'country' && file.current) {
          let city = f ? file.current.cities[Number(f.properties?.id)] : undefined
          if (!city) {  // nearest city bubble on screen (≤ 70 px)
            let best = 70
            for (const c of file.current.cities) {
              const p = map.project([c.lon, c.lat])
              const d = Math.hypot(p.x - e.point.x, p.y - e.point.y)
              if (d < best) { best = d; city = c }
            }
          }
          if (city) { const target = city; later(() => void goCity(s.country, target)) }
        } else if (s.level === 'city') {
          if (!f) return
          const qid = String(f.properties?.qid)
          doSelect(map, qid)
          const sel = selectionOf(qid)
          if (sel) later(() => props.current.onOpenCampus?.(sel))
        }
      })

      // zoom limits: pushing past the top of a level hints at the double click; pushing past the bottom goes up a level.
      // "At the limit" = the wheel keeps turning but the zoom no longer changes (on the globe the effective limit
      // depends on latitude, so comparing with getMinZoom() is not reliable).
      let pushes = 0, pushDir = 0, lastZ = -1, resetTimer = 0, hintTimer = 0, quietUntil = 0
      map.on('wheel', (e) => {
        const s = state.current
        if (busy.current || motion.current || s.level === 'campus') { quietUntil = performance.now() + 900; pushes = 0; return }
        if (performance.now() < quietUntil) { quietUntil = performance.now() + 250; lastZ = -1; return }  // the same scroll gesture after a level change
        const dy = (e.originalEvent as WheelEvent).deltaY
        const dir = Math.sign(dy)
        const z = map.getZoom()
        // a free wheel step moves the zoom by ~0.2; at the limit it moves by (almost) nothing, even when bounds jitter it
        const stuck = lastZ >= 0 && (dir > 0 ? lastZ - z < 0.02 : z - lastZ < 0.02)
        lastZ = z
        window.clearTimeout(resetTimer); resetTimer = window.setTimeout(() => { pushes = 0; lastZ = -1 }, 500)
        if (!stuck || dir !== pushDir) { pushDir = dir; pushes = 0; return }
        pushes++
        if (dir < 0 && s.level !== 'city' && pushes >= 2) {
          props.current.onHint?.('deeper')
          window.clearTimeout(hintTimer); hintTimer = window.setTimeout(() => props.current.onHint?.(null), 2200)
        } else if (dir > 0 && s.level !== 'planet') {
          if (pushes >= 5) { pushes = 0; lastZ = -1; props.current.onHint?.(null); later(levelUp) }
          else if (pushes >= 2) {
            props.current.onHint?.('up')
            window.clearTimeout(hintTimer); hintTimer = window.setTimeout(() => props.current.onHint?.(null), 2200)
          }
        }
      })

      let last = performance.now()
      const tick = (now: number) => {
        const dt = Math.min(0.05, (now - last) / 1000)
        last = now
        if (state.current.level === 'planet') {
          if (spinning.current && !motion.current && !busy.current && map.getZoom() < 3.2) {
            const c = map.getCenter()
            map.setCenter([c.lng + 3.0 * dt, c.lat])
          }
          if (map.getZoom() < 7) drawStars()
        } else if (map.getZoom() < 7) drawStars()
        else if (stars.current && stars.current.width) { const g = stars.current.getContext('2d'); g?.clearRect(0, 0, stars.current.width, stars.current.height) }
        raf = requestAnimationFrame(tick)
      }
      raf = requestAnimationFrame(tick)
    }

    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', onResize)
      popup.current?.remove()
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
