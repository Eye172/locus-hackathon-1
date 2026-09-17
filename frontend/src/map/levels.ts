/**
 * Level-of-detail model of the globe. Each level loads and draws only what it needs:
 *   planet  — the whole Earth, one bubble per country            (geo/countries.json, ~6 KB)
 *   country — one country, one bubble per city                     (geo/c/{ISO}.json, loaded on entry)
 *   city    — one city, a pin + flat card per university           (same file, filtered to the city)
 *   campus  — one university, detailed map (entered separately)
 * Going down a level needs a double click; zoom is clamped to the level's range.
 */
export type Level = 'planet' | 'country' | 'city' | 'campus'
export type BBox = [number, number, number, number]  // south, west, north, east

export interface CountryInfo { qid: string; iso: string; ru: string; en: string; kk: string; count: number; cities: number; center: [number, number]; bbox: BBox }
export interface CityInfo { id: number; name: string; lat: number; lon: number; bbox: BBox; count: number }
export interface UniPoint { qid: string; name: string; name_en?: string; name_kk?: string; lat: number; lon: number; c: number }
export interface CountryFile { iso: string; cities: CityInfo[]; unis: UniPoint[] }

let countriesP: Promise<CountryInfo[]> | null = null
const countryFiles = new Map<string, Promise<CountryFile>>()

export function loadCountries(): Promise<CountryInfo[]> {
  countriesP ??= fetch('/geo/countries.json').then((r) => r.json() as Promise<CountryInfo[]>).catch(() => { countriesP = null; return [] })
  return countriesP
}

export function loadCountry(iso: string): Promise<CountryFile> {
  let p = countryFiles.get(iso)
  if (!p) {
    p = fetch(`/geo/c/${iso}.json`).then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json() as Promise<CountryFile> })
    p.catch(() => countryFiles.delete(iso))
    countryFiles.set(iso, p)
  }
  return p
}

const R = 6371
export function distKm(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const toRad = Math.PI / 180
  const dLat = (bLat - aLat) * toRad, dLon = (bLon - aLon) * toRad
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(aLat * toRad) * Math.cos(bLat * toRad) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)))
}

const inBox = (b: BBox, lat: number, lon: number, padDeg = 0) =>
  lat >= b[0] - padDeg && lat <= b[2] + padDeg && lon >= b[1] - padDeg && lon <= b[3] + padDeg

/** The country a point belongs to: the containing bbox with the nearest centre, else the nearest centre (≤ 1500 km). */
export function countryAt(list: CountryInfo[], lat: number, lon: number): CountryInfo | null {
  const byDist = (a: CountryInfo, b: CountryInfo) => distKm(lat, lon, a.center[0], a.center[1]) - distKm(lat, lon, b.center[0], b.center[1])
  const containing = list.filter((c) => inBox(c.bbox, lat, lon, 0.5)).sort(byDist)
  if (containing[0]) return containing[0]
  const nearest = [...list].sort(byDist)[0]
  return nearest && distKm(lat, lon, nearest.center[0], nearest.center[1]) <= 1500 ? nearest : null
}

/** The city group of a university: by id, else the nearest city centre within 25 km. */
export function cityOf(file: CountryFile, qid: string, lat: number, lon: number): CityInfo | null {
  const u = file.unis.find((x) => x.qid === qid)
  if (u) return file.cities[u.c] ?? null
  let best: CityInfo | null = null, bestD = 25
  for (const c of file.cities) {
    const d = distKm(lat, lon, c.lat, c.lon)
    if (d < bestD) { best = c; bestD = d }
  }
  return best
}

/** An ad-hoc city frame around a point (for universities outside the prepared data). */
export function pointCity(name: string, lat: number, lon: number): CityInfo {
  const dLat = 6 / 111, dLon = 6 / (111 * Math.max(0.2, Math.cos((lat * Math.PI) / 180)))
  return { id: -1, name, lat, lon, bbox: [lat - dLat, lon - dLon, lat + dLat, lon + dLon], count: 1 }
}

export function padBox(b: BBox, factor: number, minDeg: number): BBox {
  const dLat = Math.max(minDeg, (b[2] - b[0]) * factor), dLon = Math.max(minDeg, (b[3] - b[1]) * factor)
  return [Math.max(-85, b[0] - dLat), Math.max(-180, b[1] - dLon), Math.min(85, b[2] + dLat), Math.min(180, b[3] + dLon)]
}

export const lngLatBounds = (b: BBox): [[number, number], [number, number]] => [[b[1], b[0]], [b[3], b[2]]]

// ---- which basemap layers each level draws (everything else is switched off, so its tiles are never requested)
const LEVEL_LAYERS: Record<Level, RegExp> = {
  planet: /^(background|bluemarble|boundary_2|boundary_disputed|lvl-country-.*)$/,  // our localized names replace the basemap's
  country: /^(background|bluemarble|esri|boundary_[23]|boundary_disputed|label_country_\d|label_state|road_motorway|road_trunk_primary|water_name_point_label|lvl-city-.*)$/,
  city: /^(background|esri|boundary_[23]|label_city(_capital)?|label_town|label_village|label_other|road_(motorway(_link)?|trunk_primary|secondary_tertiary|minor|link)|bridge_(motorway(_link)?|trunk_primary|secondary_tertiary|street|link)|water_name_(point|line)_label|waterway_line_label|highway-name-major|airport|lvl-uni-.*)$/,
  campus: /^(?!lvl-(country|city)-).*$/,
}
export const layerVisibleAt = (level: Level, id: string) => LEVEL_LAYERS[level].test(id)

export interface LevelLimits { minZoom: number; maxZoom: number; maxPitch: number; rotate: boolean }
export const PLANET_LIMITS: LevelLimits = { minZoom: 1.0, maxZoom: 3.3, maxPitch: 0, rotate: false }
export const CAMPUS_LIMITS: LevelLimits = { minZoom: 12.5, maxZoom: 19, maxPitch: 70, rotate: true }
/** Country and city ranges depend on how large the place is on screen (fit zoom). */
export const countryLimits = (fitZoom: number): LevelLimits => ({ minZoom: Math.max(1.2, fitZoom - 0.7), maxZoom: Math.min(10, fitZoom + 4), maxPitch: 0, rotate: false })
export const cityLimits = (fitZoom: number): LevelLimits => ({ minZoom: Math.max(6, fitZoom - 1), maxZoom: Math.min(15.5, fitZoom + 3.5), maxPitch: 50, rotate: true })

export const localName = (x: { ru?: string; en?: string; kk?: string; name?: string; name_en?: string; name_kk?: string }, lang: string) =>
  (lang === 'en' ? x.en ?? x.name_en : lang === 'kk' ? x.kk ?? x.name_kk : undefined) ?? x.ru ?? x.name ?? x.en ?? x.name_en ?? ''
