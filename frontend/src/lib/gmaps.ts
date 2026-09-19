/**
 * Google Maps JavaScript API for the 3D campus page: the official dynamic loader, Places (New) nearby / text
 * search and Routes. Everything is memoised per session so toggling layers does not repeat paid requests.
 * The key comes from VITE_GOOGLE_MAPS_3D_KEY (restrict it by HTTP referrer in the Cloud console).
 */
/* eslint-disable @typescript-eslint/no-explicit-any */

export const GOOGLE_3D_KEY = ((import.meta.env.VITE_GOOGLE_MAPS_3D_KEY as string | undefined) ?? '').trim()

export interface LatLng { lat: number; lng: number }
export type PlaceGroup = 'cafe' | 'food' | 'shops' | 'fun' | 'culture' | 'park' | 'sport' | 'transit' | 'health'

/** Places (New) primary types per map layer, checked against the API on 17.09.2026. */
export const PLACE_TYPES: Record<PlaceGroup, { types: string[]; radius: number }> = {
  cafe: { types: ['cafe', 'coffee_shop', 'bakery', 'tea_house', 'dessert_shop', 'ice_cream_shop'], radius: 1500 },
  food: { types: ['restaurant', 'fast_food_restaurant', 'meal_takeaway', 'food_court', 'cafeteria', 'pizza_restaurant', 'hamburger_restaurant'], radius: 1500 },
  shops: { types: ['supermarket', 'grocery_store', 'convenience_store', 'shopping_mall', 'market', 'department_store', 'book_store'], radius: 1500 },
  fun: { types: ['movie_theater', 'night_club', 'bowling_alley', 'amusement_center', 'amusement_park', 'karaoke', 'video_arcade', 'bar'], radius: 3000 },
  culture: { types: ['museum', 'art_gallery', 'performing_arts_theater', 'cultural_center', 'concert_hall', 'opera_house', 'philharmonic_hall', 'historical_landmark', 'library'], radius: 3000 },
  park: { types: ['park', 'garden', 'botanical_garden', 'plaza'], radius: 2500 },
  sport: { types: ['gym', 'fitness_center', 'sports_complex', 'stadium', 'swimming_pool', 'sports_club', 'arena'], radius: 2500 },
  transit: { types: ['subway_station', 'train_station', 'light_rail_station', 'bus_station', 'transit_station', 'bus_stop'], radius: 1200 },
  health: { types: ['pharmacy', 'drugstore', 'hospital', 'medical_clinic', 'doctor'], radius: 2000 },
}

export interface GPlace {
  id: string
  name: string
  lat: number
  lng: number
  type: string | null
  typeLabel: string | null
  rating: number | null
  ratings: number | null
  url: string | null
  source: 'google' | 'osm'
}

export interface GRoute { meters: number; seconds: number; path: LatLng[]; mode: 'WALKING' | 'DRIVING' }

declare global {
  interface Window {
    google?: any
    gm_authFailure?: () => void
  }
}

let authListeners: ((msg: string) => void)[] = []
export function onGoogleAuthError(cb: (msg: string) => void): () => void {
  authListeners.push(cb)
  return () => { authListeners = authListeners.filter((f) => f !== cb) }
}

/** The official inline bootstrap: `google.maps.importLibrary` is available immediately, the script loads on first use. */
function bootstrap(params: Record<string, string>) {
  const w = window as any
  const g = (w.google ??= {})
  const maps = (g.maps ??= {})
  if (maps.importLibrary) return
  const libs = new Set<string>()
  let loading: Promise<void> | null = null
  const load = () => (loading ??= new Promise<void>((resolve, reject) => {
    const q = new URLSearchParams({ ...params, libraries: [...libs].join(','), callback: 'google.maps.__ib__' })
    const s = document.createElement('script')
    s.src = `https://maps.googleapis.com/maps/api/js?${q}`
    s.async = true
    maps.__ib__ = resolve
    s.onerror = () => { loading = null; reject(new Error('Google Maps JavaScript API could not load')) }
    document.head.append(s)
  }))
  maps.importLibrary = (name: string, ...rest: unknown[]) => { libs.add(name); return load().then(() => maps.importLibrary(name, ...rest)) }
  w.gm_authFailure = () => authListeners.forEach((f) => f('auth'))
}

export interface GoogleLibs { maps3d: any; marker: any; places: any; routes: any }
let libsPromise: Promise<GoogleLibs> | null = null

/* Does Google's 3D map have real 3D here? It is a textured photogrammetry mesh (buildings, trees) in ~2 500 cities and
 * satellite imagery draped over terrain everywhere else - all of Kazakhstan, for one. The Maps JavaScript API cannot
 * say which (checked 18.09.2026: no property or event on Map3DElement, and the coverage map in the docs is a private
 * Maps Platform dataset). The renderer's own downloads can: the Google Earth nodes it fetches carry an imagery epoch
 * (`!3u…`) when they are terrain under a satellite photo, and none when they are mesh with its own texture. Measured on
 * the campus scene: mesh nodes Tokyo 633 of 719 (every one of the 453 at depth >= 16), Hong Kong 1 174 of 1 356,
 * Astana 0 of 964, Almaty 0 of 923.
 * Undocumented, so a change on Google's side only leaves the answer unknown (no grey city), never wrong. */
type EarthNode = { t: number; depth: number; mesh: boolean }
const earthNodes: EarthNode[] = []
const earthAny: number[] = []   // start times of every Earth data request (metadata included)
let watchingNodes = false
let nodesObserved = false       // the observer really runs: only then can "no data" be told from "cannot see"
let lastEarthAt = 0             // when the last Earth download finished (performance.now())
function watchEarthNodes() {
  if (watchingNodes || typeof PerformanceObserver === 'undefined') return
  watchingNodes = true
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        if (!e.name.includes('/rt/earth/')) continue
        // only answered requests: a failed one is an entry too (responseStatus 0); browsers without the field count all
        const status = (e as PerformanceResourceTiming & { responseStatus?: number }).responseStatus
        if (status !== undefined && (status < 200 || status >= 400)) continue
        earthAny.push(e.startTime)
        lastEarthAt = performance.now()
        const m = /\/rt\/earth\/NodeData\/pb=!1m2!1s([0-7]+)!2u\d+!2e\d+(!3u\d+)?/.exec(e.name)
        if (m) earthNodes.push({ t: e.startTime, depth: m[1].length, mesh: !m[2] })
      }
      if (earthNodes.length > 30000) earthNodes.splice(0, earthNodes.length - 30000)
      if (earthAny.length > 30000) earthAny.splice(0, earthAny.length - 30000)
    }).observe({ type: 'resource', buffered: true })  // an observer, not the timing buffer: that one stops at 250
    nodesObserved = true
  } catch { /* no resource timing: the surface stays unknown */ }
}

/** Has Google's 3D map downloaded any Earth data since `since`? null when this browser cannot tell. A map that gets
 *  nothing (the key's daily 3D quota spent: "Maps Demo Key limit reached") stays black with a spinner for good. */
export function earthDataSince(since: number): boolean | null {
  if (!nodesObserved) return null
  return earthAny.some((t) => t >= since)
}

/** 'mesh' (Google draws real 3D here), 'flat' (terrain + satellite photo only) or null (too few nodes yet), judged by
 *  the city-scale nodes (octree depth >= 12) fetched since `since` (a performance.now() time). Where Google has no 3D
 *  not one of them is a mesh (Taraz, Osh, Nukus, Khorog...); a coarse mesh stops early and the deeper nodes are flat
 *  photo (Hong Kong's Clear Water Bay: mesh to depth 16 only), so it is the share of mesh nodes, not their depth, that
 *  tells. 'flat' only once the detailed nodes (depth >= 16) are in, the coarse levels arrive first. */
export function earthSurfaceSince(since: number, minNodes = 40): 'mesh' | 'flat' | null {
  let city = 0, mesh = 0, deep = 0
  for (const n of earthNodes) {
    if (n.t < since || n.depth < 12) continue
    city++
    if (n.mesh) mesh++
    if (n.depth >= 16) deep++
  }
  if (mesh >= 6 && mesh / city >= 0.02) return 'mesh'
  // early: where Google has a mesh the first city-scale nodes already are mesh, so 16 of them without one is a flat
  // place. A later mesh node still wins - the caller keeps asking and drops the grey city then.
  return deep >= minNodes || (city >= 16 && mesh === 0) ? 'flat' : null
}

/** Is the scene around the camera loaded well enough to be shown (the cloud dive waits for it)? Enough detailed nodes,
 *  or the downloads have gone quiet. null when this browser cannot tell. */
export function earthLoadedSince(since: number, deepNodes = 120, quietMs = 900): boolean | null {
  if (!nodesObserved) return null
  let any = 0, deep = 0
  for (const n of earthNodes) {
    if (n.t < since) continue
    any++
    if (n.depth >= 16) deep++
  }
  return deep >= deepNodes || (any >= 30 && performance.now() - lastEarthAt > quietMs)
}

export function loadGoogle3D(lang: string): Promise<GoogleLibs> {
  if (!GOOGLE_3D_KEY) return Promise.reject(new Error('no-key'))
  watchEarthNodes()
  if (!libsPromise) {
    bootstrap({ key: GOOGLE_3D_KEY, v: 'weekly', language: lang })
    const im = (n: string) => window.google.maps.importLibrary(n)
    libsPromise = Promise.all([im('maps3d'), im('marker'), im('places'), im('routes')])
      .then(([maps3d, marker, places, routes]) => ({ maps3d, marker, places, routes }))
      .catch((e) => { libsPromise = null; throw e })
  }
  return libsPromise
}

/** Street View (the profile's walk tab) through the same loader and key as the 3D map. */
export function loadStreetView(lang: string): Promise<any> {
  if (!GOOGLE_3D_KEY) return Promise.reject(new Error('no-key'))
  bootstrap({ key: GOOGLE_3D_KEY, v: 'weekly', language: lang })
  return window.google.maps.importLibrary('streetView')
}

const memo = new Map<string, Promise<unknown>>()
function once<T>(key: string, f: () => Promise<T>): Promise<T> {
  let p = memo.get(key) as Promise<T> | undefined
  if (!p) {
    p = f().catch((e) => { memo.delete(key); throw e })
    memo.set(key, p)
  }
  return p
}

const FIELDS = ['id', 'displayName', 'location', 'primaryType', 'primaryTypeDisplayName', 'rating', 'userRatingCount', 'googleMapsURI']

function toPlace(p: any): GPlace | null {
  const loc = p.location?.toJSON?.() ?? p.location
  if (!loc || typeof loc.lat !== 'number') return null
  return {
    id: `g:${p.id}`, name: p.displayName ?? '', lat: loc.lat, lng: loc.lng,
    type: p.primaryType ?? null, typeLabel: p.primaryTypeDisplayName ?? null,
    rating: typeof p.rating === 'number' ? p.rating : null, ratings: typeof p.userRatingCount === 'number' ? p.userRatingCount : null,
    url: p.googleMapsURI ?? null, source: 'google',
  }
}

/** Keyword for the Text Search fallback of each layer (Nearby Search and Text Search have separate quotas). */
const TEXT_QUERY: Record<PlaceGroup, string> = {
  cafe: 'cafe coffee', food: 'restaurant canteen', shops: 'supermarket grocery store', fun: 'cinema entertainment',
  culture: 'museum theatre', park: 'park', sport: 'gym sports', transit: 'metro bus station', health: 'pharmacy',
}

const isQuota = (e: any) => /RESOURCE_EXHAUSTED|OVER_QUERY_LIMIT|429/.test(`${e?.code ?? ''} ${e?.message ?? e}`)
const isDaily = (e: any) => /per day/i.test(String(e?.message ?? e))
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

// at most three Places requests at a time: bursts from switching many layers on trip the per-minute quota
let running = 0
const waiting: (() => void)[] = []
async function slot<T>(f: () => Promise<T>): Promise<T> {
  if (running >= 3) await new Promise<void>((r) => waiting.push(r))
  running++
  try { return await f() } finally { running--; waiting.shift()?.() }
}

async function withRetry<T>(f: () => Promise<T>): Promise<T> {
  for (let i = 0; ; i++) {
    try { return await slot(f) } catch (e) {
      if (!isQuota(e) || isDaily(e) || i >= 2) throw e
      await sleep(1200 * (i + 1))
    }
  }
}

/**
 * Nearby Search is switched off once its daily quota is used up and Text Search takes over. The flag is kept
 * until Google's quota day ends (midnight Pacific time), so reloads do not burn requests on known 429s.
 */
const QUOTA_KEY = 'campuslens.gplaces.exhausted'
const quotaDay = () => new Date().toLocaleDateString('en-CA', { timeZone: 'America/Los_Angeles' })
const keyTag = GOOGLE_3D_KEY.slice(-6)  // quotas belong to the key's project: a new key starts clean
const exhausted: { nearby: boolean; text: boolean } = (() => {
  try {
    const v = JSON.parse(localStorage.getItem(QUOTA_KEY) ?? 'null')
    if (v?.day === quotaDay() && v?.key === keyTag) return { nearby: !!v.nearby, text: !!v.text }
  } catch { /* storage unavailable */ }
  return { nearby: false, text: false }
})()
function markExhausted(kind: 'nearby' | 'text') {
  exhausted[kind] = true
  try { localStorage.setItem(QUOTA_KEY, JSON.stringify({ day: quotaDay(), key: keyTag, ...exhausted })) } catch { /* storage unavailable */ }
}
export const placesQuota = () => ({ ...exhausted })

function typed(list: GPlace[], group: PlaceGroup, raw: any[]): GPlace[] {
  const allowed = new Set(PLACE_TYPES[group].types)
  return list.filter((_, i) => {
    const p = raw[i]
    return allowed.has(p?.primaryType) || (p?.types ?? []).some((t: string) => allowed.has(t))
  })
}

export async function nearbyPlaces(libs: GoogleLibs, group: PlaceGroup, center: LatLng, lang: string): Promise<{ items: GPlace[]; via: 'nearby' | 'text' }> {
  const { types, radius } = PLACE_TYPES[group]
  const key = `nearby:${group}:${center.lat.toFixed(5)},${center.lng.toFixed(5)}:${lang}`
  return once(key, async () => {
    if (!exhausted.nearby) {
      try {
        const { places } = await withRetry<any>(() => libs.places.Place.searchNearby({
          fields: FIELDS, locationRestriction: { center, radius }, includedPrimaryTypes: types,
          maxResultCount: 20, rankPreference: 'DISTANCE', language: lang,
        }))
        return { items: (places as any[]).map(toPlace).filter((x): x is GPlace => !!x), via: 'nearby' as const }
      } catch (e) {
        if (!(isQuota(e) && isDaily(e))) throw e
        markExhausted('nearby')
      }
    }
    if (exhausted.text) throw new Error('quota')
    try {
      const { places } = await withRetry<any>(() => libs.places.Place.searchByText({
        textQuery: TEXT_QUERY[group], fields: [...FIELDS, 'types'], locationBias: { center, radius },
        rankPreference: 'DISTANCE', maxResultCount: 20, language: lang,
      }))
      const raw = places as any[]
      const items = typed(raw.map(toPlace) as GPlace[], group, raw)
        .filter((p) => p && haversineM(center, p) <= radius * 1.2)
      return { items, via: 'text' as const }
    } catch (e) {
      if (isQuota(e) && isDaily(e)) markExhausted('text')
      throw e
    }
  })
}

export function textPlaces(libs: GoogleLibs, query: string, center: LatLng, radius: number, lang: string): Promise<(GPlace & { primary: string | null })[]> {
  const key = `text:${query}:${center.lat.toFixed(4)},${center.lng.toFixed(4)}:${lang}`
  return once(key, async () => {
    if (exhausted.text) return []
    try {
      const { places } = await withRetry<any>(() => libs.places.Place.searchByText({
        textQuery: query, fields: FIELDS, locationBias: { center, radius }, maxResultCount: 20, language: lang,
      }))
      return (places as any[]).map((p) => { const g = toPlace(p); return g ? { ...g, primary: p.primaryType ?? null } : null })
        .filter((x): x is GPlace & { primary: string | null } => !!x)
    } catch (e) {
      if (isQuota(e) && isDaily(e)) markExhausted('text')
      throw e
    }
  })
}

/** A route, or null when there is none; a failed request is not remembered, so the next call tries again. */
export async function computeRoute(libs: GoogleLibs, origin: LatLng, destination: LatLng, mode: 'WALKING' | 'DRIVING'): Promise<GRoute | null> {
  const key = `route:${mode}:${origin.lat.toFixed(5)},${origin.lng.toFixed(5)}>${destination.lat.toFixed(5)},${destination.lng.toFixed(5)}`
  try {
    return await once(key, async () => {
      const { routes } = await libs.routes.Route.computeRoutes({
        origin, destination, travelMode: mode, fields: ['path', 'distanceMeters', 'durationMillis'],
      })
      const r = routes?.[0]
      if (!r || typeof r.distanceMeters !== 'number') return null
      const path: LatLng[] = (r.path ?? []).map((p: any) => (p.toJSON ? p.toJSON() : p))
      return { meters: r.distanceMeters, seconds: Math.round((r.durationMillis ?? 0) / 1000), path, mode }
    })
  } catch (e) {
    console.warn('[gmaps] route failed', mode, e)
    return null
  }
}

/** Ground height (m above sea level) from Open-Meteo; the 3D camera looks at a point at this altitude. */
export function groundHeight(p: LatLng): Promise<number | null> {
  const key = `elev:${p.lat.toFixed(4)},${p.lng.toFixed(4)}`
  return once(key, async () => {
    try {
      const r = await fetch(`https://api.open-meteo.com/v1/elevation?latitude=${p.lat.toFixed(4)}&longitude=${p.lng.toFixed(4)}`)
      const d = await r.json()
      const e = d?.elevation?.[0]
      return typeof e === 'number' && Number.isFinite(e) ? e : null
    } catch {
      return null
    }
  })
}

export function haversineM(a: LatLng, b: LatLng): number {
  const R = 6371008.8
  const p1 = (a.lat * Math.PI) / 180
  const p2 = (b.lat * Math.PI) / 180
  const dp = p2 - p1
  const dl = ((b.lng - a.lng) * Math.PI) / 180
  const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

/** Walking minutes from a straight-line distance: 4.8 km/h with a 1.3 detour factor. */
export const walkMinutes = (m: number) => Math.max(1, Math.round((m * 1.3) / 80))

/** Camera range (m) that frames points spread over `spanM` metres at a 60° tilt. */
export const rangeFor = (spanM: number) => Math.min(60000, Math.max(450, spanM * 1.9))
