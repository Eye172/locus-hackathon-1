/**
 * «Обзор» — the campus up close, next to the 3D map everywhere it is: the best photos as depth-parallax "3D photos"
 * (CampusReveal) and a walk on Google Street View. The walk's stops are built with Google: the university's own
 * places around the campus (Places API text search by its name: buildings, faculties, libraries, halls), its
 * dormitories, and the campus entrance; each stop opens the nearest panorama facing the place. Street View's own arrows
 * walk on from there, and «Автопрогулка» keeps walking straight ahead by itself.
 */
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Box, ExternalLink, Footprints, LoaderCircle, MapPin, Pause, Play, X } from 'lucide-react'
import { api } from '../lib/api'
import type { Map3DPack, Photo } from '../lib/types'
import { CampusReveal, pickHero } from './CampusReveal'
import { GOOGLE_3D_KEY, haversineM, loadGoogle3D, loadStreetView, textPlaces } from '../lib/gmaps'
import type { LatLng } from '../lib/gmaps'
import { uniName, useLang } from '../lib/i18n'

type Mode = 'photos' | 'walk'
const MAPILLARY = ((import.meta.env.VITE_MAPILLARY_TOKEN as string | undefined) ?? '').trim()
interface Stop { id: string; label: string; kind: 'entrance' | 'place' | 'dorm'; at: LatLng }

const GENERIC = /^(university|universit\w*|университет\w*|state|national|institute|институт|college|academy|академия|of|the|and|named|after|имени|kazakh|казахский|technical|технический|государственный|национальный)$/i

function bearing(a: LatLng, b: LatLng): number {
  const r = Math.PI / 180
  const y = Math.sin((b.lng - a.lng) * r) * Math.cos(b.lat * r)
  const x = Math.cos(a.lat * r) * Math.sin(b.lat * r) - Math.sin(a.lat * r) * Math.cos(b.lat * r) * Math.cos((b.lng - a.lng) * r)
  return (Math.atan2(y, x) / r + 360) % 360
}

/** The university's name words and abbreviations: a Google place belongs to it when its name carries one of them. */
function tokens(pack: Map3DPack): { words: string[]; abbr: string[] } {
  const u = pack.university
  const names = [u.name, u.name_en ?? '', ...Object.values(u.names ?? {}), ...(u.aliases ?? [])].filter(Boolean)
  const words = new Set<string>(), abbr = new Set<string>()
  for (const n of names) {
    if (n.length <= 6 && (n.match(/\p{Lu}/gu) ?? []).length >= 2) abbr.add(n.toLowerCase())
    for (const w of n.toLowerCase().split(/[^\p{L}\p{N}]+/u)) if (w.length >= 4 && !GENERIC.test(w)) words.add(w)
  }
  return { words: [...words], abbr: [...abbr] }
}

export function CampusTour({ qid, name, photos: given, onClose }: { qid: string; name?: string; photos?: Photo[]; onClose: () => void }) {
  const lang = useLang()
  const [pack, setPack] = useState<Map3DPack | null>(null)
  const [photos, setPhotos] = useState<Photo[]>(given ?? [])
  const [mode, setMode] = useState<Mode>(given && given.length ? 'photos' : 'walk')
  const [stops, setStops] = useState<Stop[]>([])
  const [stop, setStop] = useState<Stop | null>(null)
  const [pano, setPano] = useState<'idle' | 'ok' | 'none'>('idle')
  const [auto, setAuto] = useState(false)
  const [src, setSrcState] = useState<'google' | 'mapillary'>(GOOGLE_3D_KEY ? 'google' : 'mapillary')
  const srcChosen = useRef(false)
  const chooseSrc = (v: 'google' | 'mapillary') => { srcChosen.current = true; setSrcState(v) }
  const [panoId, setPanoId] = useState<string | null>(null)
  const [mly, setMly] = useState<string>('idle')
  const [inside, setInside] = useState(false)   // «Внутри»: indoor tours on Google instead of the street
  const box = useRef<HTMLDivElement>(null)
  const panoRef = useRef<any>(null)

  const chosen = useRef(false)   // the viewer picked a mode: photos arriving later no longer switch to them
  const pick = (m: Mode) => { chosen.current = true; setMode(m) }
  useEffect(() => { if (photos.length && !chosen.current) setMode('photos') }, [photos.length])
  useEffect(() => { if (given?.length) setPhotos(given) }, [given])
  // photos: the caller's (live build) or the saved profile's
  useEffect(() => {
    if (given?.length) return
    api.profile(qid).then((p) => {
      setPhotos(pickHero(p.photos, 8, p.cover))
    }).catch(() => { /* no profile yet: the walk alone */ })
  }, [qid])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.map3d(qid, lang).then(setPack).catch(() => {}) }, [qid, lang])

  // the walk's stops: entrance + the university's Google places + its dormitories
  useEffect(() => {
    if (!pack) return
    const anchor = { lat: pack.anchor.lat, lng: pack.anchor.lon }
    const entrance: Stop = { id: 'entrance', label: 'Кампус', kind: 'entrance', at: anchor }
    const dorms: Stop[] = pack.dorms.filter((d) => d.ownership !== 'unknown' && d.name).slice(0, 5)
      .map((d) => ({ id: `d:${d.id}`, label: d.name!, kind: 'dorm', at: { lat: d.lat, lng: d.lon } }))
    setStops([entrance, ...dorms])
    setStop((s) => s ?? entrance)
    if (!GOOGLE_3D_KEY) return
    let alive = true
    const tok = tokens(pack)
    const query = pack.university.name_en || pack.university.name
    // three Places text searches (memoised per session): the name alone mostly finds the university itself
    loadGoogle3D(lang).then((libs) => Promise.all([query, `${query} building`, `${query} library hall`]
      .map((q) => textPlaces(libs, q, anchor, 2500, lang).catch(() => [])))).then((lists) => lists.flat()).then((list) => {
      if (!alive) return
      // the university's own pin is the «Кампус» stop already
      const u = pack.university
      const seen = new Set<string>([u.name, u.name_en ?? '', ...Object.values(u.names ?? {})].map((n) => n.toLowerCase().replace(/\s+/g, ' ')))
      const own = list.filter((p) => {
        const low = p.name.toLowerCase()
        const parts = new Set(low.split(/[^\p{L}\p{N}]+/u))
        const named = tok.words.some((w) => low.includes(w)) || tok.abbr.some((a) => parts.has(a))
        const key = low.replace(/\s+/g, ' ')
        if (!named || seen.has(key) || haversineM(anchor, p) > 3000) return false
        seen.add(key)
        return true
      }).slice(0, 8).map((p): Stop => ({ id: p.id, label: p.name, kind: 'place', at: { lat: p.lat, lng: p.lng } }))
      // the dorms Places found are already there
      setStops([entrance, ...own.filter((o) => !dorms.some((d) => haversineM(d.at, o.at) < 60)), ...dorms])
    }).catch(() => { /* keep entrance + dorms */ })
    return () => { alive = false }
  }, [pack, lang])

  // One panorama at a time, each in a fresh element: a reused panorama keeps an old WebGL context, and with the 3D map
  // and the globe still alive under «Обзор» the browser drops the oldest context - Street View then draws only its
  // arrows over black. Dropping the old one frees its context at once.
  const dropPano = () => {
    panoRef.current?.setVisible?.(false)
    panoRef.current = null
    panoEl.current = null
    const el = box.current
    if (!el) return
    for (const c of el.querySelectorAll('canvas')) {
      const gl = (c.getContext('webgl2') ?? c.getContext('webgl')) as WebGLRenderingContext | null
      gl?.getExtension('WEBGL_lose_context')?.loseContext()
    }
    el.replaceChildren()
  }
  const svRef = useRef<any>(null)
  const panoEl = useRef<HTMLDivElement | null>(null)   // the current panorama's element
  const revives = useRef(0)
  const showPano = (id: string, pov: { heading: number; pitch: number }) => {
    const sv = svRef.current
    if (!sv || !box.current) return
    dropPano()
    const el = document.createElement('div')
    el.style.cssText = 'position:absolute;inset:0'
    box.current.appendChild(el)
    panoEl.current = el
    const p = new sv.StreetViewPanorama(el, {
      pano: id, pov, zoom: 0, addressControl: false, motionTracking: false, motionTrackingControl: false,
      fullscreenControl: false, showRoadLabels: false, clickToGo: true, linksControl: true,
    })
    p.addListener('pano_changed', () => setPanoId(p.getPano?.() ?? null))
    panoRef.current = p
    setPanoId(id)
  }
  // if the browser still takes the context away, the same place comes back in a new panorama (a few times at most)
  useEffect(() => {
    const el = box.current
    if (!el || mode !== 'walk' || src !== 'google') return
    const lost = (e: Event) => {
      if (!panoEl.current?.contains(e.target as Node)) return   // a context we released ourselves
      e.preventDefault()
      const p = panoRef.current
      if (!p || revives.current >= 3) return
      revives.current++
      const id = p.getPano?.(), pov = p.getPov?.()
      window.setTimeout(() => { if (id) showPano(id, pov ?? { heading: 0, pitch: 0 }) }, 120)
    }
    el.addEventListener('webglcontextlost', lost, true)   // the event does not bubble: listen while it goes down
    return () => el.removeEventListener('webglcontextlost', lost, true)
  }, [mode, src])  // eslint-disable-line react-hooks/exhaustive-deps

  // the panorama. «Улица»: walkable first, as in Google Maps - of the panoramas around the stop (Google's own cars and
  // trekkers, then people's photospheres), the nearest one that links on to others; a dead-end photosphere (a drone
  // shot, one room) only when nothing walkable is within ~500 m more. «Внутри»: the tours businesses and photographers
  // publish on Google (not Google's own cars) - mostly halls, gyms, libraries, linked so one can walk through them.
  // Facing the place.
  useEffect(() => {
    if (mode !== 'walk' || src !== 'google' || !stop || !GOOGLE_3D_KEY) return
    let alive = true
    setPano('idle')
    setAuto(false)
    revives.current = 0
    loadStreetView(lang).then(async (sv) => {
      svRef.current = sv
      const svc = new sv.StreetViewService()
      const S = sv.StreetViewSource
      const asks: [number, any][] = inside
        ? [[30, S.DEFAULT], [70, S.DEFAULT], [140, S.DEFAULT], [260, S.DEFAULT], [450, S.DEFAULT]]
        : [[60, S.GOOGLE], [60, S.DEFAULT], [250, S.GOOGLE], [150, S.OUTDOOR], [700, S.GOOGLE], [400, S.DEFAULT]]
      const found = await Promise.all(asks.map(([radius, source]) => svc.getPanorama({ location: stop.at, radius, preference: sv.StreetViewPreference.NEAREST, sources: [source] })
        .then((r: any) => r.data).catch(() => null)))
      const seen = new Set<string>()
      const ownCar = (d: any) => /google/i.test(d.copyright ?? '')
      const cands = found.filter((d: any) => d && !seen.has(d.location.pano) && seen.add(d.location.pano))
        .filter((d: any) => !inside || !ownCar(d))
        .map((d: any) => {
          const at = d.location.latLng.toJSON()
          const dist = haversineM(at, stop.at)
          return { d, at, score: dist + ((d.links?.length ?? 0) ? 0 : inside ? 150 : 500) }
        }).sort((a: any, b: any) => a.score - b.score)
      if (!alive || !box.current) return
      if (!cands.length) {
        dropPano()
        setPano('none')
        if (!inside && !srcChosen.current && MAPILLARY) setSrcState('mapillary')   // no Google here: the people's street photos
        return
      }
      const { d, at } = cands[0]
      showPano(d.location.pano, { heading: haversineM(at, stop.at) > 8 ? bearing(at, stop.at) : 0, pitch: 4 })
      setPano('ok')
    }).catch(() => { if (alive) setPano('none') })
    return () => { alive = false }
  }, [stop, mode, lang, src, inside])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => dropPano(), [])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (mode !== 'walk' || src !== 'google') dropPano() }, [mode, src])  // eslint-disable-line react-hooks/exhaustive-deps

  // Mapillary: street-level photos people drive and walk (flat images in sequences - its viewer steps along them)
  useEffect(() => {
    if (mode !== 'walk' || src !== 'mapillary' || !stop || !MAPILLARY) return
    let alive = true
    setMly('idle')
    // ~250 m around the stop, then ~650 m: campus lanes are often not driven, the streets around them are
    const around = (dLat: number) => {
      const dLng = dLat / Math.max(0.2, Math.cos((stop.at.lat * Math.PI) / 180))
      const bbox = [stop.at.lng - dLng, stop.at.lat - dLat, stop.at.lng + dLng, stop.at.lat + dLat].map((v) => v.toFixed(6)).join(',')
      return fetch(`https://graph.mapillary.com/images?access_token=${MAPILLARY}&fields=id,computed_geometry,is_pano,captured_at&bbox=${bbox}&limit=200`).then((r) => r.json())
    }
    around(0.0022)
      .then((j) => ((j.data ?? []).length ? j : around(0.006)))
      .then((j) => {
        if (!alive) return
        const now = Date.now()
        const best = ((j.data ?? []) as any[]).filter((x) => x.computed_geometry).map((x) => {
          const [lng, lat] = x.computed_geometry.coordinates
          const age = (now - (x.captured_at ?? 0)) / 3.15e10   // years
          return { id: String(x.id), score: haversineM(stop.at, { lat, lng }) + (x.is_pano ? 0 : 60) + Math.min(10, age) * 15 }
        }).sort((a, b) => a.score - b.score)[0]
        setMly(best ? best.id : 'none')
      })
      .catch(() => { if (alive) setMly('none') })
    return () => { alive = false }
  }, [stop, mode, src])

  // Автопрогулка: a slow turn, then a step along the link closest to where the camera looks
  useEffect(() => {
    if (!auto || pano !== 'ok') return
    const timer = window.setInterval(() => {
      const p = panoRef.current
      if (!p) return
      const links: { heading: number; pano: string }[] = p.getLinks?.() ?? []
      const h = p.getPov().heading
      const diff = (a: number) => Math.abs(((a - h + 540) % 360) - 180)
      const next = links.slice().sort((a, b) => diff(a.heading) - diff(b.heading))[0]
      if (next && diff(next.heading) < 100) { p.setPano(next.pano); p.setPov({ heading: next.heading, pitch: 2 }) }
      else p.setPov({ heading: (h + 60) % 360, pitch: 2 })   // a dead end: look around for a way on
    }, 2600)
    return () => window.clearInterval(timer)
  }, [auto, pano])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const title = name || (pack ? uniName(pack.university, lang) : '')
  const icon = useMemo(() => ({ entrance: MapPin, place: MapPin, dorm: MapPin }), [])

  return (
    <div className="fixed inset-0 z-[80] bg-black text-white">
      {mode === 'photos' && photos.length > 0 && (
        <CampusReveal qid={qid} name={title} photos={photos} onOpen={() => pick('walk')} onMap={onClose} bare />
      )}
      {mode === 'photos' && photos.length === 0 && (
        <div className="absolute inset-0 grid place-items-center text-white/70 text-sm">Фото этого вуза ещё собираются — пока можно пройтись по кампусу</div>
      )}
      {mode === 'walk' && src === 'google' && (
        <>
          <div ref={box} className="absolute inset-0" />
          {pano === 'idle' && <div className="absolute inset-0 grid place-items-center text-white/75 text-sm pointer-events-none"><span className="inline-flex items-center gap-2"><LoaderCircle size={15} className="animate-spin" /> Ищем панораму рядом…</span></div>}
          {pano === 'none' && (inside
            ? <div className="absolute inset-0 grid place-items-center px-6 text-center"><div className="text-white/75 text-sm">Тура внутри этого места в Google нет.<button onClick={() => setInside(false)} className="block mx-auto mt-3 h-9 px-4 rounded-xl bg-white text-ink text-[13px] font-medium cursor-pointer">Смотреть с улицы</button></div></div>
            : <div className="absolute inset-0 grid place-items-center text-white/75 text-sm px-6 text-center pointer-events-none">Google Street View не снимал это место. Выберите другую точку или Mapillary внизу.</div>)}
        </>
      )}
      {mode === 'walk' && src === 'mapillary' && (
        <>
          {mly !== 'idle' && mly !== 'none' && (
            <iframe key={mly} title="Mapillary" className="absolute inset-0 w-full h-full border-0" allowFullScreen
              src={`https://www.mapillary.com/embed?image_key=${mly}&style=photo`} />
          )}
          {mly === 'idle' && <div className="absolute inset-0 grid place-items-center text-white/75 text-sm pointer-events-none"><span className="inline-flex items-center gap-2"><LoaderCircle size={15} className="animate-spin" /> Ищем снимки Mapillary рядом…</span></div>}
          {mly === 'none' && <div className="absolute inset-0 grid place-items-center text-white/75 text-sm px-6 text-center pointer-events-none">В Mapillary здесь снимков нет. Выберите другую точку внизу.</div>}
        </>
      )}

      {/* top: the university, the two modes, close */}
      <div className="absolute z-[90] top-0 inset-x-0 p-3 sm:p-4 flex items-start gap-3 bg-gradient-to-b from-black/70 to-transparent pointer-events-none">
        <div className="min-w-0 flex-1 pt-1">
          <div className="text-[10px] uppercase tracking-[0.18em] text-white/60">Обзор кампуса</div>
          <div className="display text-[18px] sm:text-[24px] font-medium leading-tight truncate">{title || '…'}</div>
        </div>
        <div className="pointer-events-auto flex items-center gap-1 rounded-xl bg-black/55 backdrop-blur-md p-1 shrink-0">
          <button onClick={() => pick('photos')} className={`h-8 px-3 rounded-lg text-[13px] inline-flex items-center gap-1.5 ${mode === 'photos' ? 'bg-white text-ink' : 'text-white/80 hover:bg-white/15'}`}>
            <Box size={14} /> <span className="max-sm:hidden">3D-фото</span>{photos.length > 0 && <span className="mono text-[11px] opacity-60">{photos.length}</span>}
          </button>
          <button onClick={() => pick('walk')} className={`h-8 px-3 rounded-lg text-[13px] inline-flex items-center gap-1.5 ${mode === 'walk' ? 'bg-white text-ink' : 'text-white/80 hover:bg-white/15'}`}>
            <Footprints size={14} /> <span className="max-sm:hidden">Прогулка</span>
          </button>
        </div>
        <button onClick={onClose} className="pointer-events-auto shrink-0 w-10 h-10 rounded-xl bg-black/55 backdrop-blur-md grid place-items-center hover:bg-black/75" aria-label="Закрыть" title="Закрыть (Esc)">
          <X size={18} />
        </button>
      </div>

      {/* bottom (walk): the stops and the auto walk */}
      {mode === 'walk' && stops.length > 0 && (
        <div className="absolute z-[90] bottom-3 left-3 right-3 flex justify-center pointer-events-none">
          <div className="pointer-events-auto max-w-full overflow-x-auto no-scrollbar rounded-2xl bg-black/55 backdrop-blur-md p-1.5 flex gap-1.5">
            <div className="shrink-0 flex rounded-xl bg-white/10 p-0.5">
              {GOOGLE_3D_KEY && <button onClick={() => chooseSrc('google')} className={`h-8 px-2.5 rounded-[10px] text-[12px] ${src === 'google' ? 'bg-white text-ink' : 'text-white/80 hover:bg-white/15'}`}>Google</button>}
              {MAPILLARY && <button onClick={() => chooseSrc('mapillary')} className={`h-8 px-2.5 rounded-[10px] text-[12px] ${src === 'mapillary' ? 'bg-white text-ink' : 'text-white/80 hover:bg-white/15'}`}>Mapillary</button>}
            </div>
            {src === 'google' && (
              <div className="shrink-0 flex rounded-xl bg-white/10 p-0.5" title="Внутри: туры по залам, библиотекам и спортзалам, которые публикуют на Google сами вузы и фотографы">
                <button onClick={() => setInside(false)} className={`h-8 px-2.5 rounded-[10px] text-[12px] ${!inside ? 'bg-white text-ink' : 'text-white/80 hover:bg-white/15'}`}>Улица</button>
                <button onClick={() => setInside(true)} className={`h-8 px-2.5 rounded-[10px] text-[12px] ${inside ? 'bg-white text-ink' : 'text-white/80 hover:bg-white/15'}`}>Внутри</button>
              </div>
            )}
            {src === 'google' && panoId && pano === 'ok' && (
              <a href={`https://www.google.com/maps/@?api=1&map_action=pano&pano=${encodeURIComponent(panoId)}`} target="_blank" rel="noreferrer" title="Открыть эту панораму в Google Картах"
                className="shrink-0 h-9 px-3 rounded-xl text-[13px] inline-flex items-center gap-1.5 bg-white/10 hover:bg-white/20"><ExternalLink size={13} /> Google Карты</a>
            )}
            {src === 'google' && <button onClick={() => setAuto((v) => !v)} disabled={pano !== 'ok'}
              className={`shrink-0 h-9 px-3 rounded-xl text-[13px] font-medium inline-flex items-center gap-1.5 disabled:opacity-50 ${auto ? 'bg-white text-ink' : 'bg-white/10 hover:bg-white/20'}`}>
              {auto ? <Pause size={14} /> : <Play size={14} />} Автопрогулка
            </button>}
            <span className="w-px bg-white/15 my-1 shrink-0" />
            {stops.map((s) => {
              const I = icon[s.kind]
              const on = stop?.id === s.id
              return (
                <button key={s.id} onClick={() => setStop(s)} title={s.label}
                  className={`shrink-0 h-9 px-3 rounded-xl text-[13px] inline-flex items-center gap-1.5 max-w-[240px] ${on ? 'bg-white text-ink' : 'bg-white/5 text-white/85 hover:bg-white/15'}`}>
                  <I size={13} className={s.kind === 'dorm' ? 'text-yellow-400' : s.kind === 'entrance' ? 'text-cyan-400' : 'text-sky-300'} />
                  <span className="truncate" dir="auto">{s.kind === 'dorm' ? `Общежитие · ${s.label}` : s.label}</span>
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

export default CampusTour
