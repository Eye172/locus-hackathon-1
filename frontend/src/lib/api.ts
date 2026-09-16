import type { Candidate, Campus, ClimatePack, ContextPack, CostPack, Photo, Profile, RecentItem, SourceStatus, Stage, University } from './types'

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ?? ''

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(API_BASE + path)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json() as Promise<T>
}

export const api = {
  search: (q: string) => getJSON<{ query: string; candidates: Candidate[] }>(`/api/search?q=${encodeURIComponent(q)}`),
  recent: () => getJSON<{ recent: RecentItem[] }>('/api/recent'),
  sources: () => getJSON<{ sources: Record<string, { enabled: boolean; needs_key: boolean; env?: string }> }>('/api/sources'),
  health: () => getJSON<{ ok: boolean; clip_ready: boolean; index: number; budget_s: number }>('/api/health'),
  profile: (qid: string) => getJSON<Profile>(`/api/profile/${qid}`),
  context: (qid: string) => getJSON<ContextPack>(`/api/context/${qid}`),
  climate: (qid: string) => getJSON<ClimatePack>(`/api/climate/${qid}`),
  cost: (cityQid: string) => getJSON<CostPack>(`/api/cost/${cityQid}`),
  mini: (qid: string) => getJSON<{ qid: string; name: string; names: Record<string, string>; city?: string | null; country?: string | null; founded?: number | null; students?: number | null; logo_url?: string | null; lat?: number | null; lon?: number | null; profile: { coverage: string; generated_at: string; photos_total: number; photos: { id: string; thumb: string; category: string }[] } | null }>(`/api/mini/${qid}`),
  compare: (a: string, b: string) => getJSON<{ a: Profile; b: Profile }>(`/api/compare?a=${a}&b=${b}`),
  compareAi: async (a: string, b: string, prefs: Record<string, boolean>) => {
    const r = await fetch(`${API_BASE}/api/compare/ai`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ a, b, prefs }) })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<CompareAi>
  },
  refresh: (qid: string) => fetch(`${API_BASE}/api/profile/${qid}/refresh`, { method: 'POST' }),
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

/** POST + SSE (EventSource cannot POST): parses `event:`/`data:` frames from a fetch stream. */
export async function streamChat(body: { a: string; b: string; prefs: Record<string, boolean>; messages: { role: string; content: string }[] },
  onToken: (t: string) => void, onDone: (provider: string) => void, onError: (m: string) => void): Promise<void> {
  const r = await fetch(`${API_BASE}/api/chat`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!r.ok || !r.body) { onError(`${r.status}`); return }
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    const frames = buf.split('\n\n'); buf = frames.pop() ?? ''
    for (const f of frames) {
      const ev = /^event: (.*)$/m.exec(f)?.[1]
      const data = /^data: (.*)$/m.exec(f)?.[1]
      if (!data) continue
      const d = JSON.parse(data)
      if (ev === 'token') onToken(d.text)
      else if (ev === 'done') onDone(d.provider)
      else if (ev === 'error') onError(d.message)
    }
  }
}

export interface StreamHandlers {
  onStage?: (s: Stage, elapsed: number) => void
  onUniversity?: (u: University, c: Campus) => void
  onCampus?: (c: Campus, u: University) => void
  onSource?: (name: string, s: SourceStatus, elapsed: number) => void
  onPhotos?: (source: string, photos: Photo[], rejected: number) => void
  onProfile?: (p: Profile, cached: boolean) => void
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
  es.addEventListener('profile', (e) => { const d = parse(e as MessageEvent); h.onProfile?.(d.profile, !!d.cached); es.close() })
  es.addEventListener('error', (e) => {
    const me = e as MessageEvent
    if (me.data) { const d = parse(me); h.onError?.(d.message, d.log ?? []) } else if (es.readyState === EventSource.CLOSED) { h.onError?.('Соединение прервано', []) }
    es.close()
  })
  return () => es.close()
}
