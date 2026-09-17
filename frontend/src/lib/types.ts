export type Level = 'verified' | 'likely' | 'unverified'
export type Category = 'campus' | 'dormitory' | 'classroom' | 'library' | 'lab' | 'sports' | 'student_life' | 'city'
export const CATEGORIES: Category[] = ['campus', 'dormitory', 'classroom', 'library', 'lab', 'sports', 'student_life', 'city']

export interface Candidate {
  qid: string
  label: string
  description?: string | null
  city?: string | null
  country?: string | null
  logo_url?: string | null
  score: number
  origin: 'index' | 'wikidata' | 'web'
}

export interface University {
  qid: string
  name: string
  names: Record<string, string>
  aliases: string[]
  description?: string | null
  website?: string | null
  social?: Record<string, string>
  commons_category?: string | null
  wikipedia: Record<string, string>
  lat?: number | null
  lon?: number | null
  coord_source?: string | null
  city?: string | null
  city_qid?: string | null
  city_lat?: number | null
  city_lon?: number | null
  country?: string | null
  founded?: number | null
  students?: number | null
  logo_url?: string | null
  image_url?: string | null
  summary?: string | null
  summary_url?: string | null
}

export interface Building {
  osm_id: string
  name?: string | null
  name_en?: string | null
  kind: 'dormitory' | 'library' | 'sports' | 'academic' | 'student_life' | 'other'
  lat: number
  lon: number
}

export interface Campus {
  osm_type?: string | null
  osm_id?: number | null
  osm_url?: string | null
  polygon?: number[][] | null
  bbox: number[]
  mode: 'polygon' | 'radius'
  radius_m: number
  buildings: Building[]
  counts: Record<string, number>
}

export interface Signal { key: string; label: string; weight: number; value?: string | null }
export type Era = '2020s' | '2010s' | '2000s' | 'older' | 'unknown'
export interface AiVerdict {
  place: 'this_university' | 'city' | 'other_place' | 'unknown' | 'not_photo'
  rel: number
  cat: string
  q: number
  flags: string[]
  era: Era
  why: string
  model: string
}
export interface PhotoRef { id: string; thumb: string; source: string; page_url: string; similarity?: number | null }

export interface Photo {
  id: string
  url: string
  page_url: string
  thumb: string
  width: number
  height: number
  source: string
  source_label: string
  sources: string[]
  sources_count: number
  title?: string | null
  author?: string | null
  license?: string | null
  date?: string | null
  date_source?: string | null
  year?: number | null
  outdated: boolean
  lat?: number | null
  lon?: number | null
  geo_distance_m?: number | null
  geo_inside?: boolean | null
  building?: string | null
  building_kind?: string | null
  category: Category
  secondary?: Category | null
  category_scores: Record<string, number>
  junk_score: number
  confidence: number
  level: Level
  signals: Signal[]
  phash?: string | null
  dhash?: string | null
  sha1?: string | null
  similar: PhotoRef[]
  is_brochure: boolean
  rejected: boolean
  reject_reason?: string | null
  preliminary: boolean
  ai?: AiVerdict | null
  quality?: number | null
  date_estimate?: Era | null
  ref_similarity?: number | null
  featured?: boolean
}

export interface InspectorStats {
  provider: string; model: string; photos: number; submitted: number; cached: number
  calls: number; tokens_in: number; tokens_out: number; ms: number; errors: number; reference: boolean
}

export interface Stage {
  key: string
  label: string
  status: 'pending' | 'running' | 'done' | 'skipped' | 'error'
  ms?: number | null
  detail?: string | null
  count?: number | null
}

export interface SourceStatus {
  status: 'disabled' | 'fetching' | 'done' | 'skipped' | 'error'
  count: number
  ms: number
  detail?: string | null
  label: string
}

export interface CategoryStats {
  verified: number
  likely: number
  rejected: number
  sources_checked: string[]
  coverage: 'strong' | 'medium' | 'weak' | 'none'
}

export interface Sentence { text: string; sources: number[] }
export interface DescriptionSource { id: number; label: string; url: string }
export interface Description { mode: 'llm' | 'template'; sentences: Sentence[]; sources: DescriptionSource[]; note?: string | null }

export interface Context {
  distance_km?: number | null
  center_name?: string | null
  transport_stops?: number | null
  climate?: { jan: number; jul: number; year: number } | null
  climate_note?: string | null
  climate_url?: string | null
}

export interface Profile {
  university: University
  campus?: Campus | null
  photos: Photo[]
  rejected: Photo[]
  categories: Record<string, CategoryStats>
  coverage: Record<string, string>
  description?: Description | null
  context?: Context | null
  walk: Photo[]
  timeline: Record<string, number>
  stages: Stage[]
  sources_status: Record<string, SourceStatus>
  log: string[]
  generated_at: string
  elapsed_ms: number
  partial: boolean
  cached: boolean
  reference?: { url: string } | null
  inspector?: InspectorStats | null
  version: string
}

export interface RecentItem { qid: string; name: string; city?: string | null; generated_at: string; elapsed_ms: number; photos: number }

export interface PoiGroup { kind: string; label: string; count: number; items: { name?: string | null; lat: number; lon: number; distance_m: number }[] }
export interface ContextPack {
  center: { name?: string | null; lat?: number | null; lon?: number | null; population?: number | null }
  route_center: { distance_km?: number | null; drive_min?: number | null; walk_min?: number | null; geometry?: GeoJSON.LineString | null; source: string }
  airport?: { name?: string | null; iata?: string | null; lat: number; lon: number; distance_km: number; drive_min?: number; road_km?: number } | null
  railway?: { name?: string | null; kind: string; lat: number; lon: number; distance_m: number } | null
  transit: { stops_800m: number; rail: { name?: string | null; kind: string; distance_m: number }[] }
  poi: PoiGroup[]
  campus_buildings: GeoJSON.FeatureCollection
  generated_at: string
  sources: { label: string; url: string }[]
}
export interface ClimateAgg { t_mean: number | null; t_max: number | null; t_min: number | null; feels: number | null; humidity: number | null; precip_mm: number; precip_days: number; snow_days: number; sun_h_day: number | null; wind_ms: number | null; cloud: number | null }
export interface ClimatePack {
  year: number; timezone?: string; source: { label: string; url: string }
  annual: ClimateAgg & { sun_hours: number }
  months: (ClimateAgg & { m: number; label: string })[]
  seasons: Record<'winter' | 'spring' | 'summer' | 'autumn', ClimateAgg & { label: string }>
  comfort: { comfortable: number; cool: number; freezing: number; hot: number; rainy: number }
  wind_rose: { dir: string; share: number; speed: number }[]
  feels_text: { mode: string; text: string }
  now?: { t: number; feels: number; humidity: number; wind_ms: number; code: number; time: string }
  forecast?: { date: string; t_max: number; t_min: number; code: number }[]
}
export interface CostPack {
  city: string; currency: string; as_of: string; city_qid: string
  items: { rent_1room: number; dorm: number; transport_pass: number; canteen_lunch: number; groceries_month: number }
  total_student_month_dorm: number; total_student_month_rent: number
  sources: { label: string; url: string }[]
}

/** GET /api/map3d/{qid}: what the Google 3D campus page draws (OSM via OpenFreeMap tiles + Open-Meteo heights). */
export interface Map3DBuilding {
  id: string; kind: 'campus' | 'dorm'; ring: [number, number][]; height: number | null; min_height: number
  lat: number; lon: number; area_m2: number; distance_m: number; main?: boolean; elevation?: number | null
}
export interface Map3DDorm {
  id: string; name: string | null; lat: number; lon: number; distance_m: number
  ownership: 'campus' | 'name' | 'unknown'; source: 'osm' | 'google'; elevation?: number | null; building?: Map3DBuilding | null
}
export interface Map3DPlace { id: string; name: string | null; type: string; lat: number; lon: number; distance_m: number; source: 'osm' }
export interface Map3DPack {
  university: { qid: string; name: string; names: Record<string, string>; aliases: string[]; city?: string | null; country?: string | null; website?: string | null; lat: number; lon: number }
  anchor: { lat: number; lon: number; elevation: number | null }
  campus: { mode: 'polygon' | 'radius'; outline: [number, number][] | null; area_ha: number | null; osm_url: string | null; buildings: Map3DBuilding[] }
  dorms: Map3DDorm[]
  places: Record<string, Map3DPlace[]>
  center: { name?: string | null; lat: number; lon: number; population?: number | null; distance_km: number; elevation?: number | null } | null
  /** the city the 3D camera is locked to (plus a directly adjacent big city); rings are [lat, lon] */
  city_area?: { names: string[]; bounds: [number, number, number, number]; rings: [number, number][][]; area_km2: number; source: string } | null
  city_status?: 'ok' | 'none' | 'pending'
  v?: number
  route_center: { distance_km?: number | null; drive_min?: number | null; walk_min?: number | null; geometry?: GeoJSON.LineString | null } | null
  photos: { id: string; lat: number; lon: number; thumb?: string | null; category?: string | null; level?: string | null; page_url?: string | null; source_label?: string | null; distance_m: number }[]
  stats: Record<string, number>
  sources: { label: string; url: string }[]
}
