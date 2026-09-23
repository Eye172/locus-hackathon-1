import type { Candidate, Campus, CampusFacts, ClimatePack, ClimateStory, ContextPack, CostPack, Map3DBuilding, Map3DPack, Photo, Profile, RecentItem, SearchPlan, SourceStatus, Stage, UniPlan, UniPlanView, University } from './types'

export const API_BASE: string = (import.meta.env?.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ?? ''

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(API_BASE + path)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  // an outdated backend (or a proxy) answers unknown API paths with the app's HTML page
  if (!(r.headers.get('content-type') ?? '').includes('json')) throw new Error(`api-not-json:${path.split('?')[0]}`)
  return r.json() as Promise<T>
}

async function sendJSON<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(API_BASE + path, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  if (!r.ok) { let m = `${r.status}`; try { m = (await r.json()).detail ?? m } catch { /* not json */ } throw new Error(typeof m === 'string' ? m : JSON.stringify(m)) }
  return r.json() as Promise<T>
}

// answers that do not change while the page is open: a tab opened again (or a component and its neighbour asking for
// the same thing) takes the first request's promise instead of downloading and parsing it again. Failures are dropped
// so the next ask retries; `ttl` bounds answers that do change (a profile's mini card once its build is done).
const memo = new Map<string, { at: number; p: Promise<unknown> }>()
function once<T>(key: string, load: () => Promise<T>, ttl = Infinity): Promise<T> {
  const hit = memo.get(key)
  if (hit && Date.now() - hit.at < ttl) return hit.p as Promise<T>
  const p = load()
  memo.set(key, { at: Date.now(), p })
  p.catch(() => { if (memo.get(key)?.p === p) memo.delete(key) })
  return p
}

export const api = {
  search: (q: string) => getJSON<{ query: string; candidates: Candidate[] }>(`/api/search?q=${encodeURIComponent(q)}`),
  recent: () => getJSON<{ recent: RecentItem[] }>('/api/recent'),
  sources: () => getJSON<{ sources: Record<string, { enabled: boolean; needs_key: boolean; env?: string }> }>('/api/sources'),
  health: () => getJSON<{ ok: boolean; clip_ready: boolean; index: number; budget_s: number }>('/api/health'),
  profile: (qid: string) => getJSON<Profile>(`/api/profile/${qid}`),
  context: (qid: string) => once(`context:${qid}`, () => getJSON<ContextPack>(`/api/context/${qid}`)),
  climate: (qid: string) => once(`climate:${qid}`, () => getJSON<ClimatePack>(`/api/climate/${qid}`)),
  climateStory: (qid: string, lang: string) => once(`story:${qid}:${lang}`, () => getJSON<ClimateStory>(`/api/climate/${qid}/story?lang=${lang}`)),
  cost: (cityQid: string) => once(`cost:${cityQid}`, () => getJSON<CostPack>(`/api/cost/${cityQid}`)),
  mini: (qid: string) => once(`mini:${qid}`, () => getJSON<{ qid: string; name: string; name_en?: string | null; country_qid?: string | null; names: Record<string, string>; city?: string | null; country?: string | null; founded?: number | null; students?: number | null; logo_url?: string | null; lat?: number | null; lon?: number | null; profile: { coverage: string; generated_at: string; photos_total: number; photos: { id: string; thumb: string; category: string }[] } | null }>(`/api/mini/${qid}`), 30_000),
  map3d: (qid: string, lang: string) => getJSON<Map3DPack>(`/api/map3d/${qid}?lang=${lang}`),
  map3dCity: (qid: string, lang: string) => getJSON<Pick<Map3DPack, 'city_area' | 'city_status'>>(`/api/map3d/${qid}/city?lang=${lang}`),
  footprints: async (points: { id: string; lat: number; lon: number }[]) => {
    const r = await fetch(`${API_BASE}/api/map3d/footprints`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ points }) })
    if (!r.ok) throw new Error(`${r.status}`)
    return (await r.json() as { items: Record<string, (Map3DBuilding & { elevation: number | null }) | { ring: null; elevation: number | null }> }).items
  },
  compare: (a: string, b: string) => getJSON<{ a: Profile; b: Profile }>(`/api/compare?a=${a}&b=${b}`),
  compareAi: async (a: string, b: string, prefs: Record<string, boolean>) => {
    const r = await fetch(`${API_BASE}/api/compare/ai`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ a, b, prefs }) })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<CompareAi>
  },
  refresh: (qid: string) => fetch(`${API_BASE}/api/profile/${qid}/refresh`, { method: 'POST' }),
  facts: (qid: string, refresh = false) => {
    if (refresh) memo.delete(`facts:${qid}`)   // the rebuilt answer replaces the kept one
    return once(`facts:${qid}`, () => getJSON<CampusFacts>(`/api/facts/${qid}${refresh ? '?refresh=true' : ''}`))
  },
  searchPlan: () => getJSON<{ plan: SearchPlan; defaults: SearchPlan; platforms: string[] }>('/api/search-plan'),
  saveSearchPlan: (plan: SearchPlan) => sendJSON<{ plan: SearchPlan }>('PUT', '/api/search-plan', plan),
  resetSearchPlan: () => sendJSON<{ plan: SearchPlan }>('POST', '/api/search-plan/reset'),
  uniPlan: (qid: string) => getJSON<UniPlanView>(`/api/search-plan/${qid}`),
  saveUniPlan: (qid: string, up: UniPlan) => sendJSON<{ uni: UniPlan }>('PUT', `/api/search-plan/${qid}`, up),
  flag: async (qid: string, photo_id: string, reason?: string) => {
    const r = await fetch(`${API_BASE}/api/flag`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ qid, photo_id, reason }),
    })
    return r.json() as Promise<{ ok: boolean; flags: number }>
  },
}

export const thumbUrl = (p: { thumb: string }) => API_BASE + p.thumb

export interface ComparePoint { text: string; refs: string[] }
export interface CompareAi { pros_a: ComparePoint[]; pros_b: ComparePoint[]; watch_out: ComparePoint[]; summary: string; provider: string; facts: { a: FactSheet; b: FactSheet } }
export interface FactSheet {
  qid: string; name: string; name_en?: string | null; city?: string | null; country?: string | null; founded?: number | null; students?: number | null
  coverage: Record<string, { verified: number; likely: number }>; coverage_overall?: string; photos_verified_total: number; description: string
  climate?: { year: number; annual_t_mean: number | null; winter: { t_mean: number | null; t_min: number | null; snow_days: number }; summer: { t_mean: number | null; t_max: number | null; precip_days: number }; comfort_days: Record<string, number>; sun_hours?: number }
  city_context?: { distance_to_center_km?: number | null; drive_min?: number | null; stops_800m: number; airport?: { iata?: string | null; drive_min?: number } | null; poi: Record<string, number>; population?: number | null }
  budget?: { currency: string; as_of: string; total_with_dorm: number; total_with_rent: number; dorm: number; rent_1room: number }
}

/** GET /api/compare/sheets: what the compare page's advisor knows about each university (no photo counts). */
export interface AdvisorSheet {
  qid: string; name: string; city?: string | null; country?: string | null; founded?: number | null; students?: number | null; website?: string | null
  climate?: { year: number; annual_t_mean: number | null; comfort_days: Record<string, number>; sun_hours?: number
    seasons: Record<'winter' | 'spring' | 'summer' | 'autumn', { t_mean: number | null; t_hi?: number | null; t_lo?: number | null }> }
  budget?: FactSheet['budget']
  city_context?: FactSheet['city_context']
  around_campus?: {
    dorms_nearby?: { count: number; nearest: { name: string; distance_m: number; whose: string }[] }
    city_center?: { name?: string | null; distance_km?: number | null; walk_min?: number | null; drive_min?: number | null }
    places_by_kind?: Record<string, { within_1km: number | string; nearest: string }>
    campus_area_ha?: number
  }
}
export const compareSheets = (a: string, b: string, lang: string) => getJSON<{ a: AdvisorSheet; b: AdvisorSheet }>(`/api/compare/sheets?a=${a}&b=${b}&lang=${lang}`)

/** The compare page's advisor chat, streamed; an empty `messages` asks for the opening message. */
export function streamAdvisor(body: { a: string; b: string; lang: string; messages: { role: string; content: string }[] },
  onToken: (t: string) => void, onDone: () => void, onError: (m: string) => void, signal?: AbortSignal): Promise<void> {
  return streamPost('/api/compare/advisor', body, onToken, onDone, onError, signal)
}

/** POST + SSE (EventSource cannot POST): parses `event:`/`data:` frames from a fetch stream. */
export async function streamChat(body: { a: string; b: string; prefs: Record<string, boolean>; messages: { role: string; content: string }[] },
  onToken: (t: string) => void, onDone: (provider: string) => void, onError: (m: string) => void): Promise<void> {
  return streamPost('/api/chat', body, onToken, onDone, onError)
}

async function streamPost(path: string, body: unknown, onToken: (t: string) => void, onDone: (provider: string) => void,
  onError: (m: string) => void, signal?: AbortSignal): Promise<void> {
  let r: Response
  try {
    r = await fetch(`${API_BASE}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal })
  } catch (e) { if (!signal?.aborted) onError(String(e)); return }
  if (!r.ok || !r.body) { onError(`${r.status}`); return }
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  let finished = false
  const handle = (f: string) => {
    if (finished) return
    const ev = /^event: (.*)$/m.exec(f)?.[1]
    const data = /^data: (.*)$/m.exec(f)?.[1]
    if (!data) return
    const d = JSON.parse(data)
    if (ev === 'token') onToken(d.text)
    else if (ev === 'done') { finished = true; onDone(d.provider) }
    else if (ev === 'error') { finished = true; onError(d.message) }
  }
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      // sse-starlette ends lines with \r\n: normalise before splitting into frames
      buf = (buf + dec.decode(value, { stream: true })).replace(/\r\n/g, '\n')
      const frames = buf.split('\n\n'); buf = frames.pop() ?? ''
      frames.forEach(handle)
      if (finished) { await reader.cancel(); break }
    }
    if (buf.trim() && !finished) handle(buf)
    if (!finished && !signal?.aborted) onError('Соединение прервано. Попробуйте ещё раз.')
  } catch (e) { if (!finished && !signal?.aborted) onError(String(e)) }
  finally { reader.releaseLock() }
}

export interface StreamHandlers {
  onStage?: (s: Stage, elapsed: number) => void
  onUniversity?: (u: University, c: Campus) => void
  onCampus?: (c: Campus, u: University) => void
  onSource?: (name: string, s: SourceStatus, elapsed: number) => void
  onPhotos?: (source: string, photos: Photo[], rejected: number) => void
  onProfile?: (p: Profile, cached: boolean, final: boolean) => void
  onError?: (message: string, log: string[]) => void
}

/** Opens the SSE stream for a profile. Returns a function that closes it. */
export function streamProfile(qid: string, refresh: boolean, h: StreamHandlers): () => void {
  const es = new EventSource(`${API_BASE}/api/profile/${qid}/stream${refresh ? '?refresh=1' : ''}`)
  const parse = (e: MessageEvent) => JSON.parse(e.data as string)
  es.addEventListener('stage', (e) => { const d = parse(e as MessageEvent); h.onStage?.(d.stage, d.elapsed_ms) })
  es.addEventListener('university', (e) => { const d = parse(e as MessageEvent); h.onUniversity?.(d.university, d.campus) })
  es.addEventListener('campus', (e) => { const d = parse(e as MessageEvent); h.onCampus?.(d.campus, d.university) })
  es.addEventListener('source', (e) => {
    const d = parse(e as MessageEvent)
    h.onSource?.(d.name, { status: d.status, count: d.count, ms: d.ms, detail: d.detail, label: d.label }, d.elapsed_ms)
  })
  es.addEventListener('photos', (e) => { const d = parse(e as MessageEvent); h.onPhotos?.(d.source, d.photos, d.rejected) })
  // the profile arrives more than once: the cached one (if any), the fast one, then again every ~20 s while the
  // background pass keeps collecting, without a time limit. Only the final event closes the stream.
  es.addEventListener('profile', (e) => {
    const d = parse(e as MessageEvent)
    h.onProfile?.(d.profile, !!d.cached, d.final !== false)
    if (d.final !== false) es.close()
  })
  es.addEventListener('error', (e) => {
    const me = e as MessageEvent
    if (me.data) { const d = parse(me); h.onError?.(d.message, d.log ?? []) } else { h.onError?.('Соединение прервано. Проверьте сеть и повторите попытку.', []) }
    es.close()
  })
  return () => es.close()
}
