/**
 * Google photorealistic 3D campus map, used twice: the /map3d/:qid page and the arrival scene on the globe (after the
 * cloud dive: a slow orbit of the campus, then the map locked to the university's city). The user picks which layers are highlighted: university buildings,
 * dormitories, the city centre with a route, and nearby cafés, food, shops, entertainment, culture, parks,
 * sport, transit, health and geotagged verified photos. Buildings and the campus outline come from
 * OpenStreetMap (backend /api/map3d), places and routes from Google (Places API New, Routes), with the
 * OSM places as a fallback.
 */
/* eslint-disable @typescript-eslint/no-explicit-any */
import { Component, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { Link, useNavigate } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'
import {
  ArrowLeft, ArrowRight, BedDouble, Building2, Camera, Car, ChevronDown, Clapperboard, Coffee, Dumbbell, ExternalLink,
  Footprints, Images, Landmark, Layers, LoaderCircle, LocateFixed, LockKeyhole, Palette, Pill, RotateCw, ShoppingBag,
  SkipForward, Star, Tags, TramFront, Trees, TriangleAlert, Undo2, UtensilsCrossed, X,
} from 'lucide-react'
import { api, API_BASE } from '../lib/api'
import type { Map3DBuilding, Map3DDorm, Map3DPack } from '../lib/types'
import { useLang, useT } from '../lib/i18n'
import type { Lang } from '../lib/i18n'
import { SearchBox } from './SearchBox'
import {
  GOOGLE_3D_KEY, computeRoute, earthDataSince, earthSurfaceSince, groundHeight, haversineM, loadGoogle3D, nearbyPlaces, onGoogleAuthError,
  placesQuota, rangeFor, textPlaces, walkMinutes,
} from '../lib/gmaps'
import type { GRoute, GoogleLibs, LatLng, PlaceGroup } from '../lib/gmaps'

type LayerKey = 'campus' | 'dorms' | 'center' | PlaceGroup | 'photos'
interface LayerDef { key: LayerKey; color: string; Icon: LucideIcon }

const LAYERS: LayerDef[] = [
  { key: 'campus', color: '#22D3EE', Icon: Building2 },
  { key: 'dorms', color: '#FACC15', Icon: BedDouble },
  { key: 'center', color: '#EF4444', Icon: Landmark },
  { key: 'cafe', color: '#F59E0B', Icon: Coffee },
  { key: 'food', color: '#F97316', Icon: UtensilsCrossed },
  { key: 'shops', color: '#EC4899', Icon: ShoppingBag },
  { key: 'fun', color: '#A855F7', Icon: Clapperboard },
  { key: 'culture', color: '#6366F1', Icon: Palette },
  { key: 'park', color: '#84CC16', Icon: Trees },
  { key: 'sport', color: '#3B82F6', Icon: Dumbbell },
  { key: 'transit', color: '#64748B', Icon: TramFront },
  { key: 'health', color: '#14B8A6', Icon: Pill },
  { key: 'photos', color: '#FFFFFF', Icon: Camera },
]
const LAYER = Object.fromEntries(LAYERS.map((l) => [l.key, l])) as Record<LayerKey, LayerDef>
const PLACE_GROUPS: PlaceGroup[] = ['cafe', 'food', 'shops', 'fun', 'culture', 'park', 'sport', 'transit', 'health']
const ROUTE_COLOR = '#22C55E'
const DEFAULT_ON: LayerKey[] = ['campus', 'dorms', 'center', 'cafe', 'food', 'shops']
const LS_LAYERS = 'campuslens.map3d.layers'
const DORM_RE = /общежит|жатақхана|жатакхана|dormitor|residence hall|student (house|housing|residence|village)|студенческ\w* (дом|городок)|halls? of residence/i
// Google files student housing under the generic "service" type: accept it when the name looks residential
const RESIDENCE_RE = /\b(halls?|house|residences?|tower|housing|accommodations?|dorms?|village|apartments?)\b|блок|жатақ|общеж|резиденц/i
const GENERIC = new Set(['university', 'universitet', 'университет', 'университеті', 'университета', 'state', 'national', 'institute', 'институт',
  'college', 'колледж', 'technical', 'технический', 'государственный', 'национальный', 'academy', 'академия', 'имени', 'named', 'after',
  'мемлекеттік', 'ұлттық', 'school', 'of', 'the', 'and', 'city', 'international', 'международный', 'kazakh', 'казахский', 'қазақ'])

// ---------------------------------------------------------------- DOM helpers for Google markers

/** Lucide icons rendered once to SVG (module load, outside React's lifecycle) and cloned into markers. */
const ICONS: Record<string, SVGElement> = (() => {
  const out: Record<string, SVGElement> = {}
  for (const l of LAYERS) {
    const host = document.createElement('div')
    const root = createRoot(host)
    flushSync(() => root.render(<l.Icon size={14} strokeWidth={2.4} color={l.key === 'photos' ? '#0A0A0A' : '#FFFFFF'} />))
    const svg = host.querySelector('svg')
    if (svg) out[l.key] = svg.cloneNode(true) as SVGElement
    root.unmount()
  }
  return out
})()

function dotEl(layer: LayerKey, size = 26, ring = '#FFFFFF'): HTMLElement {
  const d = document.createElement('div')
  const c = LAYER[layer].color
  d.style.cssText = `width:${size}px;height:${size}px;border-radius:999px;background:${c};border:2px solid ${ring};display:grid;place-items:center;box-shadow:0 2px 10px rgba(0,0,0,.45);cursor:pointer`
  const icon = ICONS[layer]?.cloneNode(true)
  if (icon) d.append(icon)
  return d
}

function plateEl(title: string, sub: string, accent: string): HTMLElement {
  const d = document.createElement('div')
  d.style.cssText = 'background:#FFFFFF;color:#0A0A0A;border-radius:14px;padding:9px 14px 10px;box-shadow:0 10px 30px rgba(0,0,0,.35);max-width:280px;font-family:Inter,system-ui,sans-serif;pointer-events:none'
  const bar = document.createElement('div')
  bar.style.cssText = `width:28px;height:3px;border-radius:3px;background:${accent};margin-bottom:6px`
  const h = document.createElement('div')
  h.style.cssText = 'font:700 15px/1.2 Manrope,Inter,sans-serif;letter-spacing:-.01em'
  h.dir = 'auto'
  h.textContent = title
  const s = document.createElement('div')
  s.style.cssText = 'margin-top:3px;font:500 11.5px/1.3 Inter,sans-serif;color:#3A3F4B'
  s.textContent = sub
  d.append(bar, h, s)
  return d
}

function chipEl(text: string, color: string, extra?: string): HTMLElement {
  const d = document.createElement('div')
  d.style.cssText = 'display:flex;align-items:center;gap:6px;background:#0A0A0A;color:#fff;border-radius:999px;padding:5px 10px 5px 6px;font:600 12px Inter,sans-serif;box-shadow:0 6px 18px rgba(0,0,0,.4);pointer-events:none;white-space:nowrap'
  const dot = document.createElement('span')
  dot.style.cssText = `width:12px;height:12px;border-radius:99px;background:${color};border:2px solid #fff`
  const s = document.createElement('span')
  s.dir = 'auto'
  s.style.unicodeBidi = 'isolate'
  s.textContent = text
  d.append(dot, s)
  if (extra) {
    const x = document.createElement('span')
    x.style.cssText = 'opacity:.75'
    x.textContent = `· ${extra}`
    d.append(x)
  }
  return d
}

function photoEl(thumb: string, level?: string | null): HTMLElement {
  const d = document.createElement('div')
  const ring = level === 'verified' ? '#15803D' : level === 'likely' ? '#B45309' : '#FFFFFF'
  d.style.cssText = `width:44px;height:44px;border-radius:10px;overflow:hidden;border:3px solid ${ring};box-shadow:0 4px 14px rgba(0,0,0,.45);background:#111;cursor:pointer`
  const img = document.createElement('img')
  img.src = thumb
  img.alt = ''
  img.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block'
  d.append(img)
  return d
}

// ---------------------------------------------------------------- data helpers

interface Item {
  id: string
  layer: LayerKey
  name: string
  sub?: string | null
  lat: number
  lng: number
  distance: number
  source: 'osm' | 'google'
  url?: string | null
  rating?: number | null
  ratings?: number | null
  ownership?: Map3DDorm['ownership']
  building?: Map3DBuilding | null
  elevation?: number | null
  thumb?: string | null
  level?: string | null
}

function inRing(p: LatLng, ring: [number, number][] | null | undefined): boolean {
  if (!ring || ring.length < 4) return false
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [yi, xi] = ring[i]
    const [yj, xj] = ring[j]
    if ((yi > p.lat) !== (yj > p.lat) && p.lng < ((xj - xi) * (p.lat - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

function uniTokens(pack: Map3DPack): { words: string[]; abbr: string[] } {
  const u = pack.university
  const names = [u.name, ...Object.values(u.names ?? {}), ...(u.aliases ?? [])].filter(Boolean)
  const words = new Set<string>()
  const abbr = new Set<string>()
  for (const n of names) {
    if (n.length <= 6 && (n.match(/\p{Lu}/gu) ?? []).length >= 2) abbr.add(n.toLowerCase())
    for (const w of n.toLowerCase().split(/[^\p{L}\p{N}]+/u)) if (w.length >= 4 && !GENERIC.has(w)) words.add(w)
  }
  return { words: [...words], abbr: [...abbr] }
}

function belongs(name: string, tok: { words: string[]; abbr: string[] }): boolean {
  const low = name.toLowerCase()
  const parts = new Set(low.split(/[^\p{L}\p{N}]+/u))
  return tok.words.some((w) => low.includes(w)) || tok.abbr.some((a) => parts.has(a))
}

function span(points: LatLng[]): { center: LatLng; meters: number } {
  const lats = points.map((p) => p.lat)
  const lngs = points.map((p) => p.lng)
  const sw = { lat: Math.min(...lats), lng: Math.min(...lngs) }
  const ne = { lat: Math.max(...lats), lng: Math.max(...lngs) }
  return { center: { lat: (sw.lat + ne.lat) / 2, lng: (sw.lng + ne.lng) / 2 }, meters: haversineM(sw, ne) }
}

function useFmt(lang: Lang) {
  const loc = lang === 'en' ? 'en-GB' : lang === 'kk' ? 'kk-KZ' : 'ru-RU'
  const t = useT()
  return useMemo(() => ({
    dist: (m: number) => m < 950 ? `${Math.round(m / 10) * 10} ${t('m3d.m')}` : `${(m / 1000).toLocaleString(loc, { maximumFractionDigits: m < 9500 ? 1 : 0 })} ${t('m3d.km')}`,
    mins: (s: number) => {
      const m = Math.max(1, Math.round(s / 60))
      return m < 60 ? `${m} ${t('m3d.min')}` : `${Math.floor(m / 60)} ${t('m3d.h')} ${m % 60} ${t('m3d.min')}`
    },
    num: (n: number) => n.toLocaleString(loc),
  }), [loc, t])
}

const BUILDING_WORD: Record<Lang, (n: number) => string> = {
  ru: (n) => { const a = n % 10; const b = n % 100; return a === 1 && b !== 11 ? 'корпус' : a >= 2 && a <= 4 && (b < 12 || b > 14) ? 'корпуса' : 'корпусов' },
  en: (n) => (n === 1 ? 'building' : 'buildings'),
  kk: () => 'ғимарат',
}

function loadLayers(): Set<LayerKey> {
  try {
    const v = JSON.parse(localStorage.getItem(LS_LAYERS) ?? 'null')
    if (Array.isArray(v)) return new Set(v.filter((k) => k in LAYER))
  } catch { /* storage unavailable */ }
  return new Set(DEFAULT_ON)
}

// ---------------------------------------------------------------- component

type GroupState = { items: Item[]; source: 'google' | 'osm'; loading: boolean }
type MiniInfo = Awaited<ReturnType<typeof api.mini>>
type Intro = 'wait' | 'fly' | 'orbit' | 'done'

const ORBIT_MS = 16000
const INTRO_TILT = 62
const INTRO_HEADING = 28
const ALWAYS_ON = new Set(['sel', 'city', 'grey'])  // element groups that are not user layers
const LS_SURFACE = 'campuslens.surface.'  // + qid: 'mesh' | 'flat', what Google's 3D map is at this campus
// grey buildings where Google is flat: the backend's z14 building tiles (~1.5-2.4 km) list their non-empty z16 chunks
// (~0.4-0.6 km); each chunk is one model. Chunks, not tiles: the map culls a model by its origin alone, so a tile-sized
// model vanished while its buildings were still in the foreground.
const GREY_TILE_Z = 14
const GREY_CHUNK_Z = 16
const GREY_MAX_MODELS = 160      // chunk models on the map at once (up to ~100 KB and a few hundred buildings each)
const GREY_PER_STEP = 8          // new models per camera update
const GREY_MAX_REACH_M = 3000    // how far around the looked-at point
const GREY_MAX_RANGE_M = 15000   // higher up the buildings are specks: nothing new is loaded
function greyTileOf(p: LatLng, z: number): [number, number] {
  const n = 2 ** z
  const r = (Math.max(-85, Math.min(85, p.lat)) * Math.PI) / 180
  return [Math.floor(((p.lng + 180) / 360) * n), Math.floor(((1 - Math.asinh(Math.tan(r)) / Math.PI) / 2) * n)]
}
/** a tile's centre; a chunk's model is placed at its chunk's centre (the backend builds it around the same point) */
function greyTileCenter(x: number, y: number, z: number): LatLng {
  const n = 2 ** z
  return { lat: (Math.atan(Math.sinh(Math.PI * (1 - (2 * (y + 0.5)) / n))) * 180) / Math.PI, lng: ((x + 0.5) / n) * 360 - 180 }
}
const greyTileSide = (lat: number, z: number) => (40075016.7 * Math.cos((lat * Math.PI) / 180)) / 2 ** z
// how far out the scene lets you go: at most the university's city (or ~13 km around the campus while it is unknown)
const CITY_MAX_ALT_M = 20000
const NEAR_CAMPUS_DEG = 0.12
const NEAR_CAMPUS_ALT_M = 12000

export interface Campus3DProps {
  qid: string
  /** page: /map3d/:qid with a docked panel; arrival: full-screen scene after the cloud dive on the globe */
  variant?: 'page' | 'arrival'
  /** arrival: false while the scene loads hidden under the clouds */
  active?: boolean
  onOpenProfile?: () => void
  onBack?: () => void
  onPhotos?: () => void
  /** Google Maps cannot be used (no key, rejected key, load failure) */
  onUnavailable?: () => void
}

export function Campus3D({ qid, variant = 'page', active = true, onOpenProfile, onBack, onPhotos, onUnavailable }: Campus3DProps) {
  const arrival = variant === 'arrival'
  const nav = useNavigate()
  const lang = useLang()
  const t = useT()
  const fmt = useFmt(lang)

  const [pack, setPack] = useState<Map3DPack | null>(null)
  const [packErr, setPackErr] = useState<string | null>(null)
  const [libs, setLibs] = useState<GoogleLibs | null>(null)
  const [gErr, setGErr] = useState<'nokey' | 'auth' | 'load' | null>(GOOGLE_3D_KEY ? null : 'nokey')
  const [mapGen, setMapGen] = useState(0)
  const [steady, setSteady] = useState(false)
  const [on, setOn] = useState<Set<LayerKey>>(loadLayers)
  const [groups, setGroups] = useState<Partial<Record<PlaceGroup, GroupState>>>({})
  const [gDorms, setGDorms] = useState<Map3DDorm[]>([])
  const [toCenter, setToCenter] = useState<{ walk: GRoute | null; drive: GRoute | null } | null>(null)
  const [sel, setSel] = useState<Item | null>(null)
  const [selRoute, setSelRoute] = useState<GRoute | null | 'loading'>(null)
  const [labels, setLabels] = useState(arrival)
  const [open, setOpen] = useState<LayerKey | null>(null)
  const narrow = () => typeof window !== 'undefined' && window.innerWidth < 640
  const [panel, setPanel] = useState(() => !narrow())
  const [drawer, setDrawer] = useState(false)
  const [picking, setPicking] = useState(false)
  const [quota, setQuota] = useState(placesQuota)
  const [base, setBase] = useState<{ lat: number; lng: number; alt: number } | null>(null)
  const [mini, setMini] = useState<MiniInfo | null>(null)
  const [intro, setIntro] = useState<Intro>('wait')
  const [cityArea, setCityArea] = useState<Map3DPack['city_area']>(null)
  const [canFly, setCanFly] = useState(false)  // the opening shot waits for loaded tiles (at most 3 s)

  const hostRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<any>(null)
  const els = useRef<Record<string, HTMLElement[]>>({})
  const drawnGroups = useRef<Partial<Record<PlaceGroup, GroupState>>>({})
  const introFor = useRef(-1)
  const introFinish = useRef<(() => void) | null>(null)
  const introRef = useRef<Intro>('wait')
  const createdAt = useRef(0)
  // Google's 3D here: a real mesh, or flat satellite imagery on terrain (then the city gets grey buildings)
  const [surface, setSurface] = useState<'mesh' | 'flat' | null>(null)
  useEffect(() => { introRef.current = intro }, [intro])
  const onRef = useRef(on)
  useEffect(() => { onRef.current = on }, [on])

  const anchor: LatLng | null = pack ? { lat: pack.anchor.lat, lng: pack.anchor.lon } : null
  const baseAlt = pack?.anchor.elevation ?? base?.alt ?? 0
  const ready = intro === 'done'

  // ---- data: OSM pack
  useEffect(() => {
    let alive = true
    setPack(null); setPackErr(null); setSel(null); setSelRoute(null); setGroups({}); setGDorms([]); setToCenter(null)
    api.map3d(qid, lang).then((p) => { if (alive) setPack(p) }).catch((e) => {
      if (!alive) return
      const msg = String(e?.message ?? e)
      setPackErr(msg.startsWith('api-not-json') || msg.startsWith('404') ? t('m3d.err.backend') : msg)
    })
    return () => { alive = false }
  }, [qid, lang])  // eslint-disable-line react-hooks/exhaustive-deps

  // the city boundary usually arrives later than the rest (the server does not hold the pack for it; Nominatim is
  // paced): ask again - the lock is needed only after the ~16 s orbit
  useEffect(() => {
    if (!pack) return
    setCityArea(pack.city_area ?? null)
    if (pack.city_status !== 'pending') return
    let alive = true
    let tries = 0
    let timer = 0
    const tick = () => {
      api.map3d(qid, lang).then((p) => {
        if (!alive) return
        if (p.city_area) setCityArea(p.city_area)
        else if (p.city_status === 'pending' && ++tries < 8) timer = window.setTimeout(tick, 5000)
      }).catch(() => { /* keep the unlocked map */ })
    }
    timer = window.setTimeout(tick, 4000)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [pack])  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- Google libraries
  useEffect(() => {
    if (!GOOGLE_3D_KEY) return
    const off = onGoogleAuthError(() => setGErr('auth'))
    loadGoogle3D(lang).then(setLibs).catch((e) => setGErr(e?.message === 'no-key' ? 'nokey' : 'load'))
    return off
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps -- the script loads once; the map element gets the language below
  useEffect(() => { if (gErr) onUnavailable?.() }, [gErr])  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- where the camera starts: instant index coordinates (/api/mini), else the pack's anchor
  useEffect(() => {
    let alive = true
    setBase(null)
    setMini(null)
    api.mini(qid).then(async (m) => {
      if (!alive) return
      setMini(m)
      if (m.lat == null || m.lon == null) return
      const h = await groundHeight({ lat: m.lat, lng: m.lon })
      if (alive) setBase((b) => b ?? { lat: m.lat!, lng: m.lon!, alt: h ?? 0 })
    }).catch(() => { /* the pack will provide it */ })
    return () => { alive = false }
  }, [qid])
  useEffect(() => {
    if (pack) setBase((b) => b ?? { lat: pack.anchor.lat, lng: pack.anchor.lon, alt: pack.anchor.elevation ?? 0 })
  }, [pack])

  // ---- the 3D map element: one per university, created as soon as the libraries and a start point exist
  const hasBase = !!base
  useEffect(() => {
    const host = hostRef.current
    if (!libs || !base || !host) return
    // arrival: already tilted, so the clouds clear onto the side view (the campus framing follows with the pack)
    createdAt.current = performance.now()
    const map = new libs.maps3d.Map3DElement({
      center: { lat: base.lat, lng: base.lng, altitude: base.alt }, range: arrival ? 2600 : 26000,
      tilt: arrival ? INTRO_TILT : 0, heading: arrival ? INTRO_HEADING : 0,
      mode: labels ? 'HYBRID' : 'SATELLITE', language: lang,
      gestureHandling: 'GREEDY',  // a full-screen map: the wheel zooms without Ctrl
    })
    map.style.cssText = 'display:block;width:100%;height:100%'
    host.append(map)
    mapRef.current = map
    els.current = {}
    drawnGroups.current = {}
    setSteady(false)
    setIntro('wait')
    const onSteady = (e: any) => { if (e.isSteady) setSteady(true) }
    const onErr = () => setGErr('load')
    map.addEventListener('gmp-steadychange', onSteady)
    map.addEventListener('gmp-error', onErr)
    setMapGen((g) => g + 1)
    // page: start descending while the campus data is still on its way
    const timer = arrival ? 0 : window.setTimeout(() => {
      if (introRef.current !== 'wait') return
      map.flyCameraTo({ endCamera: { center: { lat: base.lat, lng: base.lng, altitude: base.alt }, range: 5000, tilt: 45, heading: 15 }, durationMillis: 2600 })
    }, 250)
    return () => {
      window.clearTimeout(timer)
      introFinish.current?.()
      map.removeEventListener('gmp-steadychange', onSteady)
      map.removeEventListener('gmp-error', onErr)
      map.remove()
      mapRef.current = null
      els.current = {}
    }
  }, [libs, hasBase, qid])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!active || canFly) return
    if (steady) { setCanFly(true); return }
    const timer = window.setTimeout(() => setCanFly(true), 3000)
    return () => window.clearTimeout(timer)
  }, [active, steady, canFly])

  // ---- arrival: while the scene is still hidden under the clouds, jump the camera onto the campus framing —
  // the clouds clear onto the finished side view and the orbit starts right away, no fly-in
  const placedFor = useRef(-1)
  useEffect(() => {
    const map = mapRef.current
    if (!arrival || !map || !pack || active || placedFor.current === mapGen) return
    const cam = introCam(pack, base?.alt, arrival)
    map.center = cam.center; map.range = cam.range; map.tilt = cam.tilt; map.heading = cam.heading
    placedFor.current = mapGen
  }, [pack, active, mapGen])  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- the opening shot. page: fly to the campus. arrival: fly in (skipped when already placed), then one slow orbit
  // (a gesture or «skip» ends it)
  useEffect(() => {
    const map = mapRef.current
    const host = hostRef.current
    const placed = arrival && placedFor.current === mapGen
    // arrival before the campus data (a first visit next to a live profile build, or a slow Wikidata/Nominatim): the
    // clouds have cleared, so orbit the university's point now - a still picture read as "the map does not load".
    // The campus, the dorms and the places draw themselves on the orbiting scene when the data comes.
    const early = arrival && !pack && !!base
    if (!map || !(pack || early) || !active || !(canFly || placed) || introFor.current === mapGen) return
    introFor.current = mapGen
    const cam = pack ? introCam(pack, base?.alt, arrival)
      : { center: { lat: base!.lat, lng: base!.lng, altitude: base!.alt }, range: 2600, tilt: INTRO_TILT, heading: INTRO_HEADING }
    const flyMs = arrival ? 3800 : 2600
    let phase: 'fly' | 'orbit' | 'done' = 'fly'
    let guard = 0
    const finish = () => {
      if (phase === 'done') return
      phase = 'done'
      window.clearTimeout(guard)
      map.removeEventListener('gmp-animationend', onEnd)
      host?.removeEventListener('pointerdown', onUser)
      host?.removeEventListener('wheel', onUser)
      introFinish.current = null
      setIntro('done')
    }
    const startOrbit = () => {
      if (phase !== 'fly') return
      phase = 'orbit'
      setIntro('orbit')
      map.flyCameraAround({ camera: cam, durationMillis: ORBIT_MS, repeatCount: 1 })
      window.clearTimeout(guard)
      guard = window.setTimeout(finish, ORBIT_MS + 1500)
    }
    function onEnd() { if (phase === 'fly' && arrival) startOrbit(); else finish() }
    function onUser() { if (phase === 'done') return; finish(); map.stopCameraAnimation?.() }
    map.addEventListener('gmp-animationend', onEnd)
    host?.addEventListener('pointerdown', onUser)
    host?.addEventListener('wheel', onUser, { passive: true })
    introFinish.current = () => { finish(); map.stopCameraAnimation?.() }
    if (placed || early) { startOrbit(); return }  // early: the camera already frames the point (see the element)
    setIntro('fly')
    map.stopCameraAnimation?.()
    map.flyCameraTo({ endCamera: cam, durationMillis: flyMs })
    guard = window.setTimeout(() => (arrival ? startOrbit() : finish()), flyMs + 1200)
  }, [pack, active, canFly, mapGen])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (mapRef.current) mapRef.current.language = lang }, [lang, mapGen])
  useEffect(() => { if (mapRef.current) mapRef.current.mode = labels ? 'HYBRID' : 'SATELLITE' }, [labels, mapGen])

  /** Replace the elements of one layer; they are attached only while the layer is switched on. */
  const setLayer = useCallback((key: string, list: HTMLElement[]) => {
    const map = mapRef.current
    for (const e of els.current[key] ?? []) e.remove()
    els.current[key] = list
    if (!map) return
    const visible = ALWAYS_ON.has(key) || onRef.current.has(key as LayerKey)
    if (visible) for (const e of list) map.append(e)
  }, [])

  // switch layers on / off
  useEffect(() => {
    const map = mapRef.current
    try { localStorage.setItem(LS_LAYERS, JSON.stringify([...on])) } catch { /* storage unavailable */ }
    if (!map) return
    for (const [key, list] of Object.entries(els.current)) {
      if (ALWAYS_ON.has(key)) continue
      const visible = on.has(key as LayerKey)
      for (const e of list) {
        if (visible && !e.isConnected) map.append(e)
        else if (!visible && e.isConnected) e.remove()
      }
    }
  }, [on, mapGen])

  // ---- the camera stays inside the city (plus a directly adjacent big city); everything outside is dimmed
  useEffect(() => {
    const map = mapRef.current
    if (!map || !libs || !cityArea || !ready) return
    const [s, w, n, e] = cityArea.bounds
    const lat = (s + n) / 2
    const pad = 0.045
    const padLng = pad / Math.max(Math.cos((lat * Math.PI) / 180), 0.2)
    try {
      map.bounds = { south: s - pad, west: w - padLng, north: n + pad, east: e + padLng }
      // zooming out stops at the city: from higher up a tilted camera saw far past it, to the curve of the Earth
      const diag = haversineM({ lat: s, lng: w }, { lat: n, lng: e })
      map.maxAltitude = baseAlt + Math.min(CITY_MAX_ALT_M, Math.max(4000, diag * 0.35))
    } catch { /* an older API without camera bounds: the mask still shows the city */ }
    const m3 = libs.maps3d
    const P = 0.8
    const toPath = (r: [number, number][]) => r.map(([la, lo]) => ({ lat: la, lng: lo }))
    const list: HTMLElement[] = [new m3.Polygon3DElement({
      path: [{ lat: s - P, lng: w - 2 * P }, { lat: s - P, lng: e + 2 * P }, { lat: n + P, lng: e + 2 * P }, { lat: n + P, lng: w - 2 * P }],
      innerPaths: cityArea.rings.map(toPath), altitudeMode: 'CLAMP_TO_GROUND',
      fillColor: 'rgba(4,8,20,0.58)', strokeColor: 'rgba(0,0,0,0)', strokeWidth: 0,
    })]
    for (const r of cityArea.rings) {
      list.push(new m3.Polyline3DElement({
        path: toPath(r), altitudeMode: 'CLAMP_TO_GROUND', strokeColor: 'rgba(255,255,255,0.8)', strokeWidth: 2.5, drawsOccludedSegments: true,
      }))
    }
    setLayer('city', list)
    return () => {
      setLayer('city', [])
      try { map.bounds = null; map.maxAltitude = 63170000 } catch { /* ignore */ }
    }
  }, [libs, cityArea, ready, mapGen])  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- no city boundary (yet, or none known): the camera still stays around the campus, ~13 km each way
  useEffect(() => {
    const map = mapRef.current
    if (!map || cityArea || !ready || !anchor) return
    const r = NEAR_CAMPUS_DEG
    const rLng = r / Math.max(Math.cos((anchor.lat * Math.PI) / 180), 0.2)
    try {
      map.bounds = { south: anchor.lat - r, west: anchor.lng - rLng, north: anchor.lat + r, east: anchor.lng + rLng }
      map.maxAltitude = baseAlt + NEAR_CAMPUS_ALT_M
    } catch { /* an older API without camera bounds */ }
    return () => { try { map.bounds = null; map.maxAltitude = 63170000 } catch { /* ignore */ } }
  }, [cityArea, ready, mapGen, anchor?.lat, anchor?.lng])  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- camera
  const fly = useCallback((p: LatLng, range: number, opts: { alt?: number | null; tilt?: number; heading?: number; ms?: number } = {}) => {
    const map = mapRef.current
    if (!map) return
    introFinish.current?.()
    map.stopCameraAnimation?.()
    map.flyCameraTo({
      endCamera: { center: { lat: p.lat, lng: p.lng, altitude: opts.alt ?? baseAlt }, range, tilt: opts.tilt ?? 60, heading: opts.heading ?? map.heading ?? 0 },
      durationMillis: opts.ms ?? 1700,
    })
  }, [baseAlt])

  const fit = useCallback((points: LatLng[], alt?: number | null) => {
    if (!points.length) return
    const s = span(points)
    fly(s.center, rangeFor(Math.max(s.meters, 350)), { alt })
  }, [fly])

  // ---- items per layer
  const dormItems: Item[] = useMemo(() => {
    if (!pack || !anchor) return []
    const merged: Map3DDorm[] = pack.dorms.map((d) => ({ ...d }))
    for (const g of gDorms) {
      const twin = merged.find((d) => haversineM({ lat: d.lat, lng: d.lon }, { lat: g.lat, lng: g.lon }) < 60
        || (d.building && g.building && d.building.id === g.building.id))
      if (twin) { if (!twin.name && g.name) twin.name = g.name; if (twin.ownership === 'unknown' && g.ownership !== 'unknown') twin.ownership = g.ownership; continue }
      merged.push(g)
    }
    const rank = { campus: 0, name: 1, unknown: 2 }
    return merged
      .map((d) => ({
        id: d.id, layer: 'dorms' as const, name: d.name || t('m3d.dorm'), lat: d.lat, lng: d.lon,
        distance: haversineM(anchor, { lat: d.lat, lng: d.lon }), source: d.source, ownership: d.ownership, building: d.building ?? null,
        elevation: d.elevation ?? null, sub: t(`m3d.own.${d.ownership}`),
      }))
      .sort((a, b) => rank[a.ownership!] - rank[b.ownership!] || a.distance - b.distance)
  }, [pack, gDorms, t])  // eslint-disable-line react-hooks/exhaustive-deps

  const photoItems: Item[] = useMemo(() => (pack?.photos ?? []).filter((p) => p.thumb).map((p) => ({
    id: `photo:${p.id}`, layer: 'photos' as const, name: p.source_label ?? t('m3d.photo'), sub: p.category ? t(`m3d.cat.${p.category}`) : null,
    lat: p.lat, lng: p.lon, distance: p.distance_m, source: 'osm' as const, url: p.page_url, thumb: API_BASE + p.thumb, level: p.level,
  })), [pack, t])

  const centerItem: Item | null = useMemo(() => pack?.center && anchor ? {
    id: 'center', layer: 'center', name: `${t('m3d.center')}${pack.center.name ? ` · ${pack.center.name}` : ''}`,
    lat: pack.center.lat, lng: pack.center.lon, distance: haversineM(anchor, { lat: pack.center.lat, lng: pack.center.lon }),
    source: 'osm', elevation: pack.center.elevation ?? null,
  } : null, [pack, t])  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- Google places for the layers that are on (OSM places when Google is not available)
  useEffect(() => {
    if (!pack || !anchor) return
    const wanted = PLACE_GROUPS.filter((g) => on.has(g) && !groups[g])
    if (!wanted.length) return
    const osm = (g: PlaceGroup): Item[] => (pack.places[g] ?? []).map((p) => ({
      id: p.id, layer: g, name: p.name || t(`m3d.l.${g}`), sub: p.type.replace(/_/g, ' '), lat: p.lat, lng: p.lon,
      distance: p.distance_m, source: 'osm' as const,
    }))
    if (gErr || !libs) {
      if (gErr) setGroups((s) => ({ ...s, ...Object.fromEntries(wanted.map((g) => [g, { items: osm(g), source: 'osm', loading: false }])) }))
      return
    }
    setGroups((s) => ({ ...s, ...Object.fromEntries(wanted.map((g) => [g, { items: [], source: 'google', loading: true }])) }))
    for (const g of wanted) {
      nearbyPlaces(libs, g, anchor, lang)
        .then(({ items: list }) => {
          const items = list.map((p) => ({
            id: p.id, layer: g, name: p.name, sub: p.typeLabel, lat: p.lat, lng: p.lng, distance: haversineM(anchor, p),
            source: 'google' as const, url: p.url, rating: p.rating, ratings: p.ratings,
          })).sort((a, b) => a.distance - b.distance)
          setGroups((s) => ({ ...s, [g]: items.length ? { items, source: 'google', loading: false } : { items: osm(g), source: 'osm', loading: false } }))
        })
        .catch(() => setGroups((s) => ({ ...s, [g]: { items: osm(g), source: 'osm', loading: false } })))
        .finally(() => setQuota(placesQuota()))
    }
  }, [pack, libs, gErr, on, groups, lang, t])  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- Google: dormitories by name, then their footprints from OSM tiles
  useEffect(() => {
    if (!pack || !libs || !anchor) return
    let alive = true
    const u = pack.university
    const ru = u.names?.ru || u.name
    const en = u.names?.en
    // two Text Search requests: the demo key's daily Places quota is small
    const queries = [`${ru} общежитие`, en && en !== ru ? `${en} residence dormitory` : null].filter((q): q is string => !!q)
    const tok = uniTokens(pack)
    Promise.all(queries.map((q) => textPlaces(libs, q, anchor, 8000, lang).catch(() => [])))
      .then(async (lists) => {
        const seen = new Set<string>()
        const found: Map3DDorm[] = []
        for (const p of lists.flat()) {
          if (seen.has(p.id)) continue
          seen.add(p.id)
          const d = haversineM(anchor, p)
          const onCampus = inRing(p, pack.campus.outline)
          const named = belongs(p.name, tok)
          const residential = p.primary === 'service' || p.primary === 'lodging'
          const ok = DORM_RE.test(p.name) || (residential && RESIDENCE_RE.test(p.name) && (named || onCampus || d < 2500))
          if (!ok || p.primary === 'university' || d > 8000) continue
          const own: Map3DDorm['ownership'] = onCampus ? 'campus' : named ? 'name' : 'unknown'
          found.push({ id: p.id, name: p.name, lat: p.lat, lon: p.lng, distance_m: Math.round(d), ownership: own, source: 'google' })
        }
        if (!alive || !found.length) return
        setGDorms(found)
        try {
          const fp = await api.footprints(found.map((f) => ({ id: f.id, lat: f.lat, lon: f.lon })))
          if (!alive) return
          setGDorms(found.map((f) => {
            const b = fp[f.id]
            return { ...f, elevation: b?.elevation ?? null, building: b && b.ring ? (b as Map3DBuilding) : null }
          }))
        } catch { /* keep the points */ }
      })
    return () => { alive = false }
  }, [pack, libs, lang])  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- Google: routes to the city centre
  useEffect(() => {
    if (!pack?.center || !libs || !anchor) return
    let alive = true
    const c = { lat: pack.center.lat, lng: pack.center.lon }
    if (haversineM(anchor, c) > 80000) return
    const both = () => Promise.all([computeRoute(libs, anchor, c, 'WALKING'), computeRoute(libs, anchor, c, 'DRIVING')])
    both()
      // one quiet retry: the demo key sometimes drops a request right after the map loads
      .then(async (r) => (r[0] || r[1] ? r : (await new Promise((ok) => setTimeout(ok, 1500)), alive ? both() : r)))
      .then(([walk, drive]) => { if (alive) setToCenter({ walk, drive }) })
    return () => { alive = false }
  }, [pack, libs])  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- selection: fly there and draw the walking route from the campus
  const select = useCallback((it: Item | null, flyThere = true) => {
    setSel(it)
    if (it && narrow()) { setPanel(false); setDrawer(false) }
    setSelRoute(null)
    if (!it) return
    setOn((s) => (s.has(it.layer) ? s : new Set(s).add(it.layer)))
    if (flyThere) {
      const range = it.layer === 'center' ? rangeFor(Math.max(it.distance, 600)) : it.building ? 420 : 380
      const target = it.layer === 'center' && anchor ? span([anchor, it]).center : it
      if (it.elevation != null) fly(target, range, { alt: it.elevation })
      else groundHeight(it).then((h) => fly(target, range, { alt: h ?? baseAlt }))
    }
  }, [anchor, fly, baseAlt])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!sel || !libs || !anchor || sel.layer === 'campus' || sel.layer === 'center' || sel.distance < 60) return
    let alive = true
    setSelRoute('loading')
    computeRoute(libs, anchor, sel, sel.distance > 6000 ? 'DRIVING' : 'WALKING').then((r) => { if (alive) setSelRoute(r) })
    return () => { alive = false }
  }, [sel, libs])  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- a map that receives nothing is not coming: with the key's daily 3D quota spent Google neither throws nor fires
  // gmp-error, it just stays black with a spinner. No Earth data 10 s after the scene became visible -> treated as
  // unavailable (the page falls back to its own satellite map with OSM buildings). Counted from `active`, not from the
  // element's creation: hidden under the clouds, on a phone, Google started loading only ~14 s after creation.
  useEffect(() => {
    if (!mapRef.current || !active) return
    const since = createdAt.current
    const timer = window.setTimeout(() => { if (earthDataSince(since) === false) setGErr('load') }, 10000)
    return () => window.clearTimeout(timer)
  }, [mapGen, active])

  // ---- real 3D or flat here? Read from what the renderer downloads around the campus (lib/gmaps.ts), remembered per
  // university: a revisit may be served from the renderer's cache and download nothing to judge by
  useEffect(() => {
    if (!mapRef.current) return
    let known: 'mesh' | 'flat' | null = null
    try { const v = localStorage.getItem(LS_SURFACE + qid); if (v === 'mesh' || v === 'flat') known = v } catch { /* storage unavailable */ }
    setSurface(known)
    const since = createdAt.current
    let tries = 0
    const timer = window.setInterval(() => {
      const s = earthSurfaceSince(since)
      if (s) {
        setSurface(s)
        try { localStorage.setItem(LS_SURFACE + qid, s) } catch { /* storage unavailable */ }
      }
      if (s || ++tries > 80) window.clearInterval(timer)   // up to a minute: a phone got its first detailed nodes at ~20 s
    }, 750)
    return () => window.clearInterval(timer)
  }, [mapGen])  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- where Google has no 3D: the city's buildings in grey over the satellite photo (backend pipeline/city_glb.py),
  // loaded where the camera looks - nearest chunks first, a few at a time, at most GREY_MAX_MODELS on the map (the
  // farthest go) and nothing new from high up, where buildings are specks. The highlighted campus and dorms are drawn
  // over them as before.
  useEffect(() => {
    const map = mapRef.current
    if (!libs || !pack || !map || surface !== 'flat') { setLayer('grey', []); return }
    let alive = true
    const indexes = new Map<string, [number, number, number][] | null>()   // z14 tile -> its chunks (null: asked)
    const models = new Map<string, { el: HTMLElement; at: LatLng }>()
    let timer = 0
    let last = 0
    const update = () => {
      timer = 0
      last = performance.now()
      const c = map.center as LatLng | null
      const range = Number(map.range) || 0
      if (!c || !range) return
      const reach = Math.min(GREY_MAX_REACH_M, Math.max(1200, range * 1.1))
      const want: { key: string; x: number; y: number; at: LatLng; d: number }[] = []
      if (range <= GREY_MAX_RANGE_M) {
        const [tx, ty] = greyTileOf(c, GREY_TILE_Z)
        const side = greyTileSide(c.lat, GREY_TILE_Z)
        const k = Math.ceil(reach / side) + 1
        const half = greyTileSide(c.lat, GREY_CHUNK_Z) * 0.71
        for (let dx = -k; dx <= k; dx++) {
          for (let dy = -k; dy <= k; dy++) {
            const x = tx + dx, y = ty + dy, tkey = `${x}/${y}`
            if (haversineM(c, greyTileCenter(x, y, GREY_TILE_Z)) > reach + side * 0.71) continue
            if (!indexes.has(tkey)) {   // which chunks of this tile have buildings (the server builds them on the first ask)
              indexes.set(tkey, null)
              fetch(`${API_BASE}/api/map3d/${encodeURIComponent(qid)}/grey/${x}/${y}.json`)
                .then((r) => (r.ok ? r.json() : { chunks: [] }))
                .then((j) => { indexes.set(tkey, j.chunks ?? []); if (alive) schedule() })
                .catch(() => indexes.set(tkey, []))
              continue
            }
            for (const [cx, cy] of indexes.get(tkey) ?? []) {
              const at = greyTileCenter(cx, cy, GREY_CHUNK_Z)
              const d = haversineM(c, at)
              if (d <= reach + half) want.push({ key: `${cx}/${cy}`, x: cx, y: cy, at, d })
            }
          }
        }
        want.sort((a, b) => a.d - b.d)
      }
      let added = 0
      for (const t of want.slice(0, GREY_MAX_MODELS)) {
        if (models.has(t.key)) continue
        if (added === GREY_PER_STEP) { schedule(250); break }   // the rest a moment later
        const el = new libs.maps3d.Model3DElement({
          src: `${API_BASE}/api/map3d/${encodeURIComponent(qid)}/grey16/${t.x}/${t.y}-v6.glb`,  // must end in .glb: no query
          position: { ...t.at, altitude: 0 }, altitudeMode: 'RELATIVE_TO_GROUND',
          orientation: { heading: 0, tilt: 0, roll: 0 },   // the model is already east / north / up in metres
        })
        map.append(el)
        models.set(t.key, { el, at: t.at })
        added++
      }
      // over the cap, or well behind the camera: the farthest go
      const byDist = [...models.entries()].map(([key, m]) => ({ key, m, d: haversineM(c, m.at) })).sort((a, b) => b.d - a.d)
      for (const { key, m, d } of byDist) {
        if (models.size <= GREY_MAX_MODELS && d <= reach * 1.6 + 1000) break
        m.el.remove()
        models.delete(key)
      }
      els.current.grey = [...models.values()].map((m) => m.el)
    }
    function schedule(ms = 600) { if (!timer) timer = window.setTimeout(update, Math.max(0, ms - (performance.now() - last))) }
    const onMove = () => schedule()
    update()
    map.addEventListener('gmp-centerchange', onMove)
    map.addEventListener('gmp-rangechange', onMove)
    return () => {
      alive = false
      window.clearTimeout(timer)
      map.removeEventListener('gmp-centerchange', onMove)
      map.removeEventListener('gmp-rangechange', onMove)
      for (const m of models.values()) m.el.remove()
      els.current.grey = []
    }
  }, [libs, pack, surface, mapGen])  // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------------- scene building
  // campus outline, buildings and the floating name plate
  useEffect(() => {
    if (!libs || !pack || !mapRef.current) return
    const m3 = libs.maps3d
    const list: HTMLElement[] = []
    const c = LAYER.campus.color
    if (pack.campus.outline) {
      list.push(new m3.Polyline3DElement({
        path: pack.campus.outline.map(([la, lo]) => ({ lat: la, lng: lo })), altitudeMode: 'CLAMP_TO_GROUND',
        strokeColor: c, strokeWidth: 4, drawsOccludedSegments: true,
      }))
    }
    for (const b of pack.campus.buildings) {
      const h = (b.height ?? 12) + 0.6
      const el = new m3.Polygon3DInteractiveElement({
        path: b.ring.map(([la, lo]) => ({ lat: la, lng: lo, altitude: h })), altitudeMode: 'RELATIVE_TO_GROUND', extruded: true,
        fillColor: b.main ? 'rgba(34,211,238,0.55)' : 'rgba(34,211,238,0.30)', strokeColor: b.main ? '#FFFFFF' : c, strokeWidth: b.main ? 2 : 1,
        drawsOccludedSegments: false,
      })
      el.addEventListener('gmp-click', () => select({
        id: `b:${b.id}`, layer: 'campus', name: b.main ? t('m3d.mainBuilding') : t('m3d.campusBuilding'), lat: b.lat, lng: b.lon,
        distance: b.distance_m, source: 'osm', building: b, elevation: baseAlt,
      }, false))
      list.push(el)
    }
    const main = pack.campus.buildings.find((b) => b.main) ?? pack.campus.buildings[0]
    const at = main ? { lat: main.lat, lng: main.lon } : { lat: pack.anchor.lat, lng: pack.anchor.lon }
    const name = pack.university.names?.[lang] || pack.university.name
    const bits = [pack.university.city, pack.campus.buildings.length ? `${pack.campus.buildings.length} ${BUILDING_WORD[lang](pack.campus.buildings.length)}` : null,
      pack.campus.area_ha ? `${fmt.num(pack.campus.area_ha)} ${t('m3d.ha')}` : null].filter(Boolean)
    const plate = new m3.MarkerElement({
      position: { ...at, altitude: (main?.height ?? 15) + 35 }, altitudeMode: 'RELATIVE_TO_GROUND',
      collisionBehavior: 'REQUIRED_AND_HIDES_OPTIONAL', collisionPriority: 100000,
    })
    plate.append(plateEl(name, bits.join(' · '), c))
    list.push(plate)
    setLayer('campus', list)
  }, [libs, pack, mapGen, lang])  // eslint-disable-line react-hooks/exhaustive-deps

  // dormitories: yellow prisms + markers
  useEffect(() => {
    if (!libs || !mapRef.current) return
    const m3 = libs.maps3d
    const list: HTMLElement[] = []
    dormItems.forEach((d, i) => {
      if (d.building) {
        const h = (d.building.height ?? 12) + 0.6
        const known = d.ownership !== 'unknown'
        const el = new m3.Polygon3DInteractiveElement({
          path: d.building.ring.map(([la, lo]) => ({ lat: la, lng: lo, altitude: h })), altitudeMode: 'RELATIVE_TO_GROUND', extruded: true,
          fillColor: known ? 'rgba(250,204,21,0.55)' : 'rgba(250,204,21,0.25)', strokeColor: '#FACC15', strokeWidth: known ? 2 : 1,
        })
        el.addEventListener('gmp-click', () => select(d, false))
        list.push(el)
      }
      if (i >= 12 && d.building) return  // a big campus: the yellow prisms speak for themselves
      const mk = new m3.MarkerInteractiveElement({
        position: { lat: d.lat, lng: d.lng, altitude: (d.building?.height ?? 12) + 8 }, altitudeMode: 'RELATIVE_TO_GROUND', title: d.name,
        collisionBehavior: d.ownership === 'unknown' ? 'OPTIONAL_AND_HIDES_LOWER_PRIORITY' : 'REQUIRED', collisionPriority: 60000 - Math.round(d.distance),
      })
      mk.append(dotEl('dorms', d.ownership === 'unknown' ? 20 : 24, d.ownership === 'unknown' ? '#E5E7EB' : '#0A0A0A'))
      mk.addEventListener('gmp-click', () => select(d))
      list.push(mk)
    })
    setLayer('dorms', list)
  }, [libs, dormItems, mapGen])  // eslint-disable-line react-hooks/exhaustive-deps

  // city centre + route
  useEffect(() => {
    if (!libs || !pack || !mapRef.current || !centerItem) { setLayer('center', []); return }
    const m3 = libs.maps3d
    const list: HTMLElement[] = []
    const route = toCenter?.walk && toCenter.walk.meters < 12000 ? toCenter.walk : toCenter?.drive ?? toCenter?.walk
    const geom = route?.path?.length ? route.path
      : pack.route_center?.geometry?.coordinates?.map(([lo, la]) => ({ lat: la, lng: lo }))
    if (geom?.length) {
      list.push(new m3.Polyline3DElement({
        path: geom, altitudeMode: 'CLAMP_TO_GROUND', strokeColor: ROUTE_COLOR, strokeWidth: 9,
        outerColor: '#052E16', outerWidth: 0.35, drawsOccludedSegments: true,
      }))
    }
    const mk = new m3.MarkerInteractiveElement({
      position: { lat: centerItem.lat, lng: centerItem.lng, altitude: 30 }, altitudeMode: 'RELATIVE_TO_GROUND', title: centerItem.name,
      collisionBehavior: 'REQUIRED_AND_HIDES_OPTIONAL', collisionPriority: 90000,
    })
    mk.append(chipEl(pack.center?.name ?? t('m3d.center'), LAYER.center.color, fmt.dist(route?.meters ?? centerItem.distance)))
    mk.addEventListener('gmp-click', () => select(centerItem))
    list.push(mk)
    setLayer('center', list)
  }, [libs, pack, centerItem, toCenter, mapGen])  // eslint-disable-line react-hooks/exhaustive-deps

  // place groups
  useEffect(() => {
    if (!libs || !mapRef.current) return
    const m3 = libs.maps3d
    for (const g of PLACE_GROUPS) {
      const st = groups[g]
      if (!st || st.loading || drawnGroups.current[g] === st) continue
      drawnGroups.current[g] = st
      const list = st.items.map((it) => {
        const mk = new m3.MarkerInteractiveElement({
          position: { lat: it.lat, lng: it.lng, altitude: 6 }, altitudeMode: 'RELATIVE_TO_GROUND', title: it.name,
          collisionBehavior: 'OPTIONAL_AND_HIDES_LOWER_PRIORITY', collisionPriority: 40000 - Math.round(it.distance),
        })
        mk.append(dotEl(g, 24))
        mk.addEventListener('gmp-click', () => select(it, false))
        return mk as HTMLElement
      })
      setLayer(g, list)
    }
  }, [libs, groups, mapGen])  // eslint-disable-line react-hooks/exhaustive-deps

  // verified geotagged photos
  useEffect(() => {
    if (!libs || !mapRef.current) return
    const m3 = libs.maps3d
    setLayer('photos', photoItems.map((it) => {
      const mk = new m3.MarkerInteractiveElement({
        position: { lat: it.lat, lng: it.lng, altitude: 10 }, altitudeMode: 'RELATIVE_TO_GROUND', title: it.sub ?? it.name,
        collisionBehavior: 'OPTIONAL_AND_HIDES_LOWER_PRIORITY', collisionPriority: 20000 - Math.round(it.distance),
      })
      mk.append(photoEl(it.thumb!, it.level))
      mk.addEventListener('gmp-click', () => select(it, false))
      return mk
    }))
  }, [libs, photoItems, mapGen])  // eslint-disable-line react-hooks/exhaustive-deps

  // selection: highlighted marker + route line
  useEffect(() => {
    if (!libs || !mapRef.current) return
    const m3 = libs.maps3d
    const list: HTMLElement[] = []
    if (sel && sel.layer !== 'campus') {
      const mk = new m3.MarkerElement({
        position: { lat: sel.lat, lng: sel.lng, altitude: (sel.building?.height ?? 8) + 22 }, altitudeMode: 'RELATIVE_TO_GROUND',
        collisionBehavior: 'REQUIRED_AND_HIDES_OPTIONAL', collisionPriority: 110000,
      })
      mk.append(chipEl(sel.name.length > 42 ? `${sel.name.slice(0, 40)}…` : sel.name, LAYER[sel.layer].color))
      list.push(mk)
    }
    if (sel && selRoute && selRoute !== 'loading' && selRoute.path.length) {
      list.push(new m3.Polyline3DElement({
        path: selRoute.path, altitudeMode: 'CLAMP_TO_GROUND', strokeColor: LAYER[sel.layer].color === '#FFFFFF' ? '#0EA5E9' : LAYER[sel.layer].color,
        strokeWidth: 7, outerColor: '#0A0A0A', outerWidth: 0.3, drawsOccludedSegments: true,
      }))
    }
    setLayer('sel', list)
  }, [libs, sel, selRoute, mapGen])  // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------------- derived UI data
  const countOf = (k: LayerKey): number | null => {
    if (!pack) return null
    if (k === 'campus') return pack.campus.buildings.length
    if (k === 'dorms') return dormItems.length
    if (k === 'center') return pack.center ? 1 : 0
    if (k === 'photos') return photoItems.length
    const st = groups[k]
    return st && !st.loading ? st.items.length : null
  }
  const itemsOf = (k: LayerKey): Item[] => {
    if (k === 'dorms') return dormItems
    if (k === 'photos') return photoItems
    if (k === 'center') return centerItem ? [centerItem] : []
    if (k === 'campus') return []
    return groups[k]?.items ?? []
  }
  const layers = LAYERS.filter((l) => l.key !== 'photos' || photoItems.length)
  const toggle = (k: LayerKey) => setOn((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n })
  const walk = toCenter?.walk
  const drive = toCenter?.drive
  const ownDorms = dormItems.filter((d) => d.ownership !== 'unknown')
  const nearestDorm = ownDorms[0] ?? dormItems[0]

  const campusView = () => { if (pack) { const s = campusSpan(pack); fly(s.center, rangeFor(s.meters), { tilt: 62 }) } }
  const dormView = () => {
    if (!anchor || !dormItems.length) return
    const focus = ownDorms.length ? ownDorms : dormItems.filter((d) => d.distance < 1500).slice(0, 6)
    fit([anchor, ...(focus.length ? focus : dormItems.slice(0, 3))])
  }
  const centerView = () => { if (centerItem) select(centerItem) }
  const orbit = () => {
    const map = mapRef.current
    if (!map) return
    map.stopCameraAnimation?.()
    map.flyCameraAround({ camera: { center: map.center, range: map.range, tilt: map.tilt, heading: map.heading }, durationMillis: 30000, repeatCount: 1 })
  }

  const googleNote = gErr === 'nokey' ? t('m3d.err.nokey') : gErr === 'auth' ? t('m3d.err.auth') : gErr === 'load' ? t('m3d.err.load') : null
  const title = pack ? (pack.university.names?.[lang] || pack.university.name) : mini ? (mini.names?.[lang] || mini.name) : ''
  const place = [pack?.university.city ?? mini?.city, pack?.university.country ?? mini?.country].filter(Boolean).join(', ')

  // ---------------------------------------------------------------- shared UI pieces
  const distances = pack && (
    <div className="px-4 py-3 border-b border-line space-y-2.5">
      {pack.center && (
        <Fact color={LAYER.center.color} title={`${t('m3d.toCenter')}${pack.center.name ? ` · ${pack.center.name}` : ''}`}>
          {walk ? (
            <>
              <span className="inline-flex items-center gap-1"><Footprints size={13} /> {fmt.mins(walk.seconds)} · {fmt.dist(walk.meters)}</span>
              {drive && <span className="inline-flex items-center gap-1"><Car size={13} /> {fmt.mins(drive.seconds)} · {fmt.dist(drive.meters)}</span>}
            </>
          ) : drive ? (
            <span className="inline-flex items-center gap-1"><Car size={13} /> {fmt.mins(drive.seconds)} · {fmt.dist(drive.meters)}</span>
          ) : (
            <span>{fmt.dist(pack.center.distance_km * 1000)} {t('m3d.straight')}{pack.route_center?.drive_min ? ` · ${pack.route_center.drive_min} ${t('m3d.min')} ${t('m3d.drive')}` : ''}</span>
          )}
        </Fact>
      )}
      <Fact color={LAYER.dorms.color} title={t('m3d.l.dorms')}>
        {nearestDorm ? (
          <span>
            {ownDorms.length ? `${ownDorms.length} ${t('m3d.ofUni')}` : ''}{ownDorms.length && dormItems.length > ownDorms.length ? ' · ' : ''}
            {dormItems.length > ownDorms.length ? `${dormItems.length - ownDorms.length} ${t('m3d.nearby')}` : ''}
            {' · '}{t('m3d.nearest')} {fmt.dist(nearestDorm.distance)} · <Footprints size={12} className="inline -mt-0.5" /> ~{walkMinutes(nearestDorm.distance)} {t('m3d.min')}
          </span>
        ) : <span className="text-muted">{libs ? t('m3d.noDorms') : '…'}</span>}
      </Fact>
      {(['cafe', 'food', 'shops'] as PlaceGroup[]).filter((g) => groups[g]?.items.length).map((g) => {
        const n = groups[g]!.items[0]
        return (
          <Fact key={g} color={LAYER[g].color} title={t(`m3d.near.${g}`)}>
            <button className="text-left hover:text-brand truncate max-w-full" onClick={() => select(n)}>
              <bdi>{n.name}</bdi> · {fmt.dist(n.distance)} · ~{walkMinutes(n.distance)} {t('m3d.min')}
            </button>
          </Fact>
        )
      })}
      {cityArea && (
        <Fact color="#94A3B8" title={t('m3d.lock')}>
          <span className="inline-flex items-center gap-1"><LockKeyhole size={12} /> {cityArea.names.join(' + ')}</span>
        </Fact>
      )}
    </div>
  )

  const layerList = (
    <div className="px-2 py-2">
      <div className="flex items-center px-2 py-1">
        <span className="caps text-muted">{t('m3d.layers')}</span>
        <button className="ml-auto text-xs text-muted hover:text-ink" onClick={() => setOn(new Set(layers.map((l) => l.key)))}>{t('m3d.all')}</button>
        <span className="mx-1.5 text-line-2">|</span>
        <button className="text-xs text-muted hover:text-ink" onClick={() => setOn(new Set(['campus']))}>{t('m3d.onlyCampus')}</button>
      </div>
      {layers.map((l) => {
        const isOn = on.has(l.key)
        const n = countOf(l.key)
        const st = PLACE_GROUPS.includes(l.key as PlaceGroup) ? groups[l.key as PlaceGroup] : undefined
        const items = itemsOf(l.key)
        const expandable = items.length > 0
        return (
          <div key={l.key} className="rounded-xl">
            <div className={`flex items-center gap-2.5 px-2 py-1.5 rounded-xl ${isOn ? '' : 'opacity-60'} hover:bg-canvas`}>
              <button onClick={() => toggle(l.key)} role="switch" aria-checked={isOn} title={isOn ? t('m3d.hide') : t('m3d.show')}
                className={`relative w-9 h-5 rounded-full shrink-0 transition ${isOn ? 'bg-ink' : 'bg-line-2'}`}>
                <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${isOn ? 'left-[18px]' : 'left-0.5'}`} />
              </button>
              <span className="w-6 h-6 rounded-full grid place-items-center shrink-0 border" style={{ background: l.color, borderColor: l.key === 'photos' ? '#D3D8E2' : l.color }}>
                <l.Icon size={13} color={l.key === 'photos' ? '#0A0A0A' : '#FFFFFF'} strokeWidth={2.4} />
              </span>
              <button className="flex-1 min-w-0 text-left text-sm leading-tight" onClick={() => (expandable ? setOpen(open === l.key ? null : l.key) : toggle(l.key))}>
                <span className="block truncate">{t(`m3d.l.${l.key}`)}</span>
                {st && !st.loading && st.source === 'osm' && isOn && <span className="block text-[10.5px] text-muted">OpenStreetMap</span>}
                {l.key === 'campus' && n === 0 && <span className="block text-[10.5px] text-muted">{t('m3d.noBuildings')}</span>}
              </button>
              {st?.loading && isOn ? <LoaderCircle size={14} className="animate-spin text-muted" />
                : n != null && <span className="mono text-xs text-muted">{n}</span>}
              {expandable && <ChevronDown size={14} className={`text-muted transition ${open === l.key ? 'rotate-180' : ''}`} onClick={() => setOpen(open === l.key ? null : l.key)} />}
            </div>
            {open === l.key && expandable && (
              <ul className="ml-[3.1rem] mr-1 mb-1.5 border-l border-line">
                {items.slice(0, 12).map((it) => (
                  <li key={it.id}>
                    <button onClick={() => select(it)} className={`w-full text-left pl-3 pr-1 py-1.5 hover:bg-canvas rounded-r-lg ${sel?.id === it.id ? 'bg-brand-soft' : ''}`}>
                      <div className="text-[13px] leading-tight truncate" dir="auto">{it.name}</div>
                      <div className="text-[11px] text-muted truncate">
                        {fmt.dist(it.distance)}{it.sub ? ` · ${it.sub}` : ''}{it.rating ? ` · ★ ${it.rating.toFixed(1)}` : ''}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {isOn && l.key === 'dorms' && !dormItems.length && pack && libs && (
              <div className="ml-[3.1rem] mb-1 text-[11px] text-muted">{t('m3d.noDorms')}</div>
            )}
            {isOn && st && !st.loading && !st.items.length && (
              <div className="ml-[3.1rem] mb-1 text-[11px] text-muted">{t('m3d.noItems')}</div>
            )}
          </div>
        )
      })}
    </div>
  )

  const footer = (
    <>
      {(quota.nearby || gErr) && pack && (
        <div className="mx-4 mb-2 rounded-lg bg-likely-soft text-likely px-3 py-2 text-[11.5px] leading-snug">
          {gErr ? t('m3d.fallbackList') : quota.text ? t('m3d.quotaAll') : t('m3d.quotaNearby')}
        </div>
      )}
      <div className="px-4 py-2.5 border-t border-line text-[10.5px] leading-snug text-muted">
        {t('m3d.attrib')}
        {pack?.campus.osm_url && <> · <a href={pack.campus.osm_url} target="_blank" rel="noreferrer" className="underline hover:text-ink">OSM</a></>}
      </div>
    </>
  )

  const cameraButtons = libs && pack && !gErr && (
    <div className={`absolute right-3 flex flex-col gap-1.5 z-10 ${arrival ? 'top-[9rem]' : 'top-3'}`}>
      <CamBtn onClick={campusView} icon={<Building2 size={15} />} label={t('m3d.cam.campus')} />
      <CamBtn onClick={dormView} icon={<BedDouble size={15} />} label={t('m3d.cam.dorms')} disabled={!dormItems.length} />
      <CamBtn onClick={centerView} icon={<Landmark size={15} />} label={t('m3d.cam.center')} disabled={!centerItem} />
      <CamBtn onClick={orbit} icon={<RotateCw size={15} />} label={t('m3d.cam.orbit')} />
      <CamBtn onClick={() => setLabels((v) => !v)} icon={<Tags size={15} />} label={t('m3d.cam.labels')} active={labels} />
    </div>
  )

  const selectedCard = sel && (
    <div className={`absolute z-20 left-1/2 -translate-x-1/2 w-[min(440px,calc(100%-1.5rem))] max-sm:bottom-auto max-sm:left-3 max-sm:right-16 max-sm:w-auto max-sm:translate-x-0 ${arrival ? 'bottom-[4.75rem] max-sm:top-[9rem]' : 'bottom-4 max-sm:top-3'}`}>
      <div className="card p-4 shadow-2xl">
        <div className="flex items-start gap-3">
          {sel.thumb ? <img src={sel.thumb} alt="" className="w-14 h-14 rounded-lg object-cover shrink-0" />
            : <span className="w-9 h-9 rounded-full grid place-items-center shrink-0" style={{ background: LAYER[sel.layer].color }}>{(() => { const I = LAYER[sel.layer].Icon; return <I size={17} color="#fff" strokeWidth={2.4} /> })()}</span>}
          <div className="min-w-0 flex-1">
            <div className="font-semibold leading-tight" dir="auto">{sel.name}</div>
            <div className="text-xs text-ink-2 mt-0.5">{[sel.sub, sel.source === 'google' ? 'Google Places' : sel.layer === 'photos' ? null : 'OpenStreetMap'].filter(Boolean).join(' · ')}</div>
          </div>
          <button className="btn-icon !w-8 !h-8" onClick={() => select(null)} aria-label="close"><X size={15} /></button>
        </div>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-sm">
          {sel.layer !== 'campus' && <span><span className="text-muted">{sel.layer === 'center' ? t('m3d.straight') : t('m3d.fromCampus')}:</span> <span className="mono">{fmt.dist(sel.distance)}</span></span>}
          {sel.layer === 'center' && walk && <span className="inline-flex items-center gap-1"><Footprints size={14} /> <span className="mono">{fmt.mins(walk.seconds)}</span> · {fmt.dist(walk.meters)}</span>}
          {sel.layer === 'center' && drive && <span className="inline-flex items-center gap-1"><Car size={14} /> <span className="mono">{fmt.mins(drive.seconds)}</span> · {fmt.dist(drive.meters)}</span>}
          {selRoute === 'loading' && <span className="inline-flex items-center gap-1 text-muted"><LoaderCircle size={13} className="animate-spin" /> {t('m3d.route')}</span>}
          {selRoute && selRoute !== 'loading' && (
            <span className="inline-flex items-center gap-1">{selRoute.mode === 'WALKING' ? <Footprints size={14} /> : <Car size={14} />} <span className="mono">{fmt.mins(selRoute.seconds)}</span> · {fmt.dist(selRoute.meters)} {t('m3d.byRoute')}</span>
          )}
          {!selRoute && sel.layer !== 'campus' && sel.layer !== 'center' && <span className="text-muted">~{walkMinutes(sel.distance)} {t('m3d.min')} {t('m3d.walk')}</span>}
          {sel.rating != null && <span className="inline-flex items-center gap-1"><Star size={13} className="text-likely fill-current" /> {sel.rating.toFixed(1)}{sel.ratings ? <span className="text-muted">({fmt.num(sel.ratings)})</span> : null}</span>}
          {sel.building && (
            <span><span className="text-muted">{t('m3d.height')}:</span> <span className="mono">{sel.building.height ? `${Math.round(sel.building.height)} ${t('m3d.m')}` : t('m3d.heightEst')}</span> · <span className="text-muted">{t('m3d.area')}:</span> <span className="mono">{fmt.num(sel.building.area_m2)} {t('m3d.m')}²</span></span>
          )}
        </div>
        {sel.ownership && (
          <div className={`mt-2 inline-flex text-xs rounded-full px-2 py-0.5 ${sel.ownership === 'unknown' ? 'bg-canvas text-ink-2' : 'bg-verified-soft text-verified'}`}>{t(`m3d.own.${sel.ownership}`)}</div>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          <button className="btn-ghost !py-1.5" onClick={() => select(sel)}><LocateFixed size={14} /> {t('m3d.showOnMap')}</button>
          {sel.url && sel.layer !== 'photos' && <a className="btn-ghost !py-1.5" href={sel.url} target="_blank" rel="noreferrer"><ExternalLink size={14} /> {t('m3d.openGoogle')}</a>}
          {sel.layer === 'photos' && <Link className="btn-ghost !py-1.5" to={`/u/${qid}?photo=${sel.id.slice(6)}`}><Camera size={14} /> {t('m3d.openPhoto')}</Link>}
          {sel.layer !== 'photos' && sel.layer !== 'campus' && (
            <a className="btn-ghost !py-1.5" target="_blank" rel="noreferrer"
              href={`https://www.google.com/maps/dir/?api=1&origin=${pack?.anchor.lat},${pack?.anchor.lon}&destination=${sel.lat},${sel.lng}&travelmode=${sel.distance > 6000 ? 'driving' : 'walking'}`}>
              <Footprints size={14} /> {t('m3d.route')}
            </a>
          )}
        </div>
      </div>
    </div>
  )

  // ---------------------------------------------------------------- arrival scene (globe → clouds → here)
  if (arrival) {
    const thumb = mini?.logo_url || (mini?.profile?.photos?.[0] ? API_BASE + mini.profile.photos[0].thumb : null)
    const facts = [
      place,
      mini?.founded ? `${t('m3d.founded')} ${mini.founded}` : null,
      mini?.students ? `${fmt.num(mini.students)} ${t('m3d.students')}` : null,
      mini?.profile?.photos_total ? `${mini.profile.photos_total} ${t('m3d.photosShort')}` : null,
    ].filter(Boolean)
    return (
      <div className={`absolute inset-0 z-20 overflow-hidden text-ink bg-[#0B0F1A] transition-opacity duration-300 ${active ? 'opacity-100' : 'opacity-[0.01] pointer-events-none'}`} aria-hidden={!active}>
        {/* hidden = 1 % opacity, not 0: Google does not load a fully transparent map */}
        <div ref={hostRef} className="absolute inset-0" />
        {active && (
          <>
            {/* the university, in brief: the whole card opens the full profile */}
            {/* the globe's transparent header lies over the top 3.5rem: everything here starts below it */}
            <div className="absolute z-20 top-[4.25rem] left-1/2 -translate-x-1/2 w-[min(760px,calc(100%-8rem))] max-sm:left-[3.75rem] max-sm:right-3 max-sm:translate-x-0 max-sm:w-auto m3d-drop">
              <button onClick={onOpenProfile} className="w-full text-left rounded-2xl bg-white/95 backdrop-blur-md shadow-2xl border border-white/70 p-2 pr-2.5 flex items-center gap-3 hover:bg-white transition group">
                <span className="w-12 h-12 rounded-xl overflow-hidden bg-canvas border border-line grid place-items-center shrink-0">
                  {thumb ? <img src={thumb} alt="" className="w-full h-full object-cover" /> : <Building2 size={20} className="text-muted" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block caps text-muted !text-[10px]">{t('m3d.kicker')}</span>
                  <span className="block display font-extrabold text-[17px] leading-tight truncate">{title || '…'}</span>
                  <span className="block text-[12px] text-ink-2 truncate">{facts.join(' · ')}</span>
                </span>
                {(walk || nearestDorm) && (
                  <span className="hidden lg:flex flex-col items-end gap-0.5 text-[12px] text-ink-2 shrink-0 mr-1">
                    {walk && <span className="inline-flex items-center gap-1"><Landmark size={12} /> {fmt.mins(walk.seconds)} {t('m3d.walk')}</span>}
                    {nearestDorm && <span className="inline-flex items-center gap-1"><BedDouble size={12} /> {fmt.dist(nearestDorm.distance)}</span>}
                  </span>
                )}
                <span className="btn-primary !h-9 !py-0 shrink-0 max-sm:!px-2.5">
                  <span className="max-sm:hidden">{t('m3d.profile')}</span> <ArrowRight size={15} className="transition group-hover:translate-x-0.5" />
                </span>
              </button>
              {cityArea && ready && (
                <div className="mt-1.5 flex justify-center">
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-black/55 backdrop-blur text-white/90 px-2.5 py-1 text-[11px]">
                    <LockKeyhole size={11} /> {t('m3d.lock')}: {cityArea.names.join(' + ')}
                  </span>
                </div>
              )}
            </div>
            {onBack && (
              <button onClick={onBack} title={t('m3d.backPlanet')} className="absolute z-20 top-[4.25rem] left-3 w-12 h-12 rounded-2xl bg-white/90 backdrop-blur shadow-xl grid place-items-center hover:bg-white">
                <Undo2 size={18} />
              </button>
            )}

            {!ready && (pack || intro !== 'wait') && (
              <div className="absolute z-20 bottom-5 left-1/2 -translate-x-1/2 flex items-center gap-2">
                <span className="rounded-full bg-black/50 backdrop-blur text-white/85 text-[12px] px-3 py-1.5">{intro === 'orbit' ? t('m3d.orbiting') : t('m3d.arriving')}</span>
                {!pack && !packErr && (
                  <span className="rounded-full bg-black/50 backdrop-blur text-white/85 text-[12px] px-3 py-1.5 inline-flex items-center gap-1.5">
                    <LoaderCircle size={12} className="animate-spin" /> {t('m3d.loadingCampus')}
                  </span>
                )}
                <button onClick={() => introFinish.current?.()} className="rounded-full bg-white/90 hover:bg-white text-ink text-[12px] font-medium px-3 py-1.5 inline-flex items-center gap-1.5 shadow-lg">
                  <SkipForward size={13} /> {t('m3d.skip')}
                </button>
              </div>
            )}

            {ready && (
              <>
                {cameraButtons}
                {/* layer chips: what is highlighted, one tap each */}
                <div className="absolute z-20 bottom-3 left-3 right-3 flex justify-center pointer-events-none">
                  <div className="pointer-events-auto max-w-full overflow-x-auto no-scrollbar rounded-2xl bg-black/50 backdrop-blur-md p-1.5 flex gap-1.5">
                    <button onClick={() => setDrawer((v) => !v)} className={`shrink-0 h-9 px-3 rounded-xl text-[13px] font-medium inline-flex items-center gap-1.5 ${drawer ? 'bg-white text-ink' : 'bg-white/10 text-white hover:bg-white/20'}`}>
                      <Layers size={14} /> {t('m3d.more')}
                    </button>
                    {onPhotos && (
                      <button onClick={onPhotos} className="shrink-0 h-9 px-3 rounded-xl text-[13px] font-medium inline-flex items-center gap-1.5 bg-white/10 text-white hover:bg-white/20">
                        <Images size={14} /> {t('m3d.photosBtn')}
                      </button>
                    )}
                    <span className="w-px bg-white/15 my-1 shrink-0" />
                    {layers.map((l) => {
                      const isOn = on.has(l.key)
                      const n = countOf(l.key)
                      return (
                        <button key={l.key} onClick={() => toggle(l.key)} aria-pressed={isOn} title={t(`m3d.l.${l.key}`)}
                          className={`shrink-0 h-9 pl-1.5 pr-2.5 rounded-xl text-[13px] inline-flex items-center gap-1.5 transition ${isOn ? 'bg-white text-ink shadow' : 'bg-white/5 text-white/70 hover:bg-white/15'}`}>
                          <span className="w-6 h-6 rounded-full grid place-items-center" style={{ background: isOn ? l.color : 'rgba(255,255,255,0.15)' }}>
                            <l.Icon size={12} color={l.key === 'photos' && isOn ? '#0A0A0A' : '#FFFFFF'} strokeWidth={2.4} />
                          </span>
                          {t(`m3d.s.${l.key}`)}
                          {n != null && n > 0 && <span className={`mono text-[11px] ${isOn ? 'text-muted' : 'text-white/50'}`}>{n}</span>}
                        </button>
                      )
                    })}
                  </div>
                </div>
                {drawer && (
                  <aside className="absolute z-20 left-3 top-[9rem] bottom-[4.5rem] w-[360px] max-w-[calc(100%-1.5rem)] flex flex-col rounded-2xl bg-white shadow-2xl overflow-hidden pop">
                    <div className="flex items-center px-4 pt-3 pb-1">
                      <span className="caps text-muted">{t('m3d.title')}</span>
                      <button className="ml-auto btn-icon !w-8 !h-8" onClick={() => setDrawer(false)} aria-label="close"><X size={15} /></button>
                    </div>
                    <div className="flex-1 overflow-y-auto">
                      {distances}
                      {layerList}
                    </div>
                    {footer}
                  </aside>
                )}
                {selectedCard}
              </>
            )}
            {packErr && (
              <div className="absolute z-20 bottom-20 left-1/2 -translate-x-1/2 rounded-xl bg-white shadow-xl px-4 py-2 text-sm inline-flex items-center gap-2">
                <TriangleAlert size={15} className="text-likely" /> {packErr}
              </div>
            )}
          </>
        )}
      </div>
    )
  }

  // ---------------------------------------------------------------- page (/map3d/:qid)
  return (
    <div className="relative h-[calc(100dvh-3.5rem)] bg-[#0B0F1A] overflow-hidden text-ink">
      <div className="absolute inset-0 sm:left-[380px]">
        <div ref={hostRef} className="absolute inset-0" />

        {/* loading veil */}
        {(!pack || (!googleNote && (!libs || !steady))) && !packErr && (
          <div className={`absolute pointer-events-none flex justify-center ${base ? 'inset-x-0 bottom-6' : 'inset-0 items-center'}`}>
            <div className="rounded-2xl bg-black/55 backdrop-blur px-5 py-4 text-white text-sm space-y-2 min-w-[260px]">
              <Step done={!!pack} label={t('m3d.loading.pack')} />
              {!googleNote && <Step done={!!libs && steady} label={t('m3d.loading.google')} />}
            </div>
          </div>
        )}
        {(packErr || googleNote) && (
          <div className="absolute inset-0 grid place-items-center p-6 pointer-events-none">
            <div className="card max-w-md p-5 pointer-events-auto">
              <div className="flex items-center gap-2 font-semibold"><TriangleAlert size={18} className="text-likely" /> {packErr ? t('m3d.err.pack') : 'Google Maps'}</div>
              <p className="mt-2 text-sm text-ink-2">{packErr ?? googleNote}</p>
              {googleNote && pack && <p className="mt-2 text-xs text-muted">{t('m3d.fallbackList')}</p>}
              <div className="mt-3 flex gap-2">
                <Link to={`/u/${qid}`} className="btn-ghost">{t('m3d.back')}</Link>
              </div>
            </div>
          </div>
        )}

        {cameraButtons}
        {selectedCard}
      </div>

      {/* side panel: docked on the left from sm, a bottom sheet on phones */}
      <aside className={`absolute z-20 flex flex-col bg-white sm:inset-y-0 sm:left-0 sm:w-[380px] sm:border-r sm:border-line max-sm:left-2 max-sm:right-2 max-sm:bottom-2 max-sm:h-[68dvh] max-sm:rounded-2xl max-sm:shadow-2xl transition-transform duration-300 ${panel ? '' : 'max-sm:translate-y-[calc(100%-6.75rem)]'}`}>
        <button className="sm:hidden absolute left-1/2 -translate-x-1/2 top-1.5 w-12 h-1.5 rounded-full bg-line-2" aria-label="toggle" onClick={() => setPanel((v) => !v)} />
        <div className="p-4 pb-3 border-b border-line max-sm:pt-5" onClick={(e) => { if (narrow() && !panel && e.target === e.currentTarget) setPanel(true) }}>
          <div className="flex items-center gap-2">
            <Link to={`/u/${qid}`} className="inline-flex items-center gap-1 text-xs text-muted hover:text-ink"><ArrowLeft size={13} /> {t('m3d.back')}</Link>
            <button className="ml-auto text-xs text-muted hover:text-ink sm:hidden inline-flex items-center gap-1" onClick={() => setPanel((v) => !v)}>
              <Layers size={13} /> <ChevronDown size={13} className={panel ? '' : 'rotate-180'} />
            </button>
            <button className="text-xs text-brand hover:underline max-sm:hidden" onClick={() => setPicking((v) => !v)}>{t('m3d.change')}</button>
          </div>
          {picking && (
            <div className="mt-2">
              <SearchBox size="md" autoFocus onPick={(c) => { setPicking(false); nav(`/map3d/${c.qid}`) }} />
            </div>
          )}
          <div className="caps text-muted mt-3">{t('m3d.title')}</div>
          <h1 className="display text-[22px] leading-tight font-extrabold mt-0.5 max-sm:text-[18px] max-sm:truncate" onClick={() => { if (narrow()) setPanel((v) => !v) }}>{title || <span className="shimmer inline-block h-6 w-48 rounded" />}</h1>
          {pack && (
            <div className="mt-1 text-xs text-ink-2">
              {place}
              {pack.campus.mode === 'radius' && <div className="mt-1 text-muted">{t('m3d.campusRadius')}</div>}
            </div>
          )}
        </div>
        <div className="flex-1 overflow-y-auto">
          {distances}
          {layerList}
        </div>
        {footer}
      </aside>
    </div>
  )
}

/** Google's 3D map can throw from inside its own code - seen when the key's daily 3D quota runs out ("Maps Demo Key
 *  limit reached"): the next call into maps3d fails and, thrown from an effect, took the whole app down to a blank
 *  page. Any such failure now just ends the Google scene; the page falls back as when the map is unavailable. */
class SceneBoundary extends Component<{ onFail?: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(error: unknown) {
    console.warn('[campus3d] the Google 3D scene failed, falling back:', error)
    this.props.onFail?.()
  }
  render() { return this.state.failed ? null : this.props.children }
}

export default function Campus3DSafe(props: Campus3DProps) {
  return <SceneBoundary onFail={props.onUnavailable}><Campus3D {...props} /></SceneBoundary>
}


function campusSpan(pack: Map3DPack): { center: LatLng; meters: number } {
  const pts: LatLng[] = pack.campus.outline?.map(([la, lo]) => ({ lat: la, lng: lo }))
    ?? pack.campus.buildings.map((b) => ({ lat: b.lat, lng: b.lon }))
  if (pts.length < 2) return { center: { lat: pack.anchor.lat, lng: pack.anchor.lon }, meters: 450 }
  const s = span(pts)
  return { center: s.center, meters: Math.min(Math.max(s.meters, 350), 4000) }
}

/** The opening-shot camera: the whole campus from the side. */
function introCam(pack: Map3DPack, baseAlt: number | undefined, arrival: boolean) {
  const s = campusSpan(pack)
  return {
    center: { lat: s.center.lat, lng: s.center.lng, altitude: pack.anchor.elevation ?? baseAlt ?? 0 },
    range: rangeFor(s.meters) * (arrival ? 1.15 : 1), tilt: INTRO_TILT, heading: INTRO_HEADING,
  }
}

function Step({ done, label }: { done: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2">
      {done ? <span className="w-3.5 h-3.5 rounded-full bg-emerald-400" /> : <LoaderCircle size={14} className="animate-spin" />}
      <span className={done ? 'text-white/70' : ''}>{label}</span>
    </div>
  )
}

function Fact({ color, title, children }: { color: string; title: string; children: ReactNode }) {
  return (
    <div className="flex gap-2.5">
      <span className="w-1 rounded-full shrink-0" style={{ background: color }} />
      <div className="min-w-0">
        <div className="text-[11px] text-muted leading-tight">{title}</div>
        <div className="text-[13px] mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5">{children}</div>
      </div>
    </div>
  )
}

function CamBtn({ onClick, icon, label, disabled, active }: { onClick: () => void; icon: ReactNode; label: string; disabled?: boolean; active?: boolean }) {
  return (
    <button onClick={onClick} disabled={disabled} title={label}
      className={`h-9 pl-2.5 pr-3 rounded-xl inline-flex items-center gap-2 text-[13px] font-medium shadow-lg border backdrop-blur transition disabled:opacity-40 ${active ? 'bg-ink text-white border-ink' : 'bg-white/90 text-ink border-white/60 hover:bg-white'}`}>
      {icon}<span className="max-md:hidden">{label}</span>
    </button>
  )
}
