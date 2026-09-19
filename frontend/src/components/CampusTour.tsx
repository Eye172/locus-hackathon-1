/**
 * «Обзор» — the campus up close, next to the 3D map everywhere it is: the best photos as depth-parallax "3D photos"
 * (CampusReveal) and a walk on Google Street View. The walk's stops are built with Google: the university's own
 * places around the campus (Places API text search by its name: buildings, faculties, libraries, halls), its
 * dormitories, and the campus entrance; each stop opens the nearest panorama facing the place. Street View's own arrows
 * walk on from there, and «Автопрогулка» keeps walking straight ahead by itself.
 */
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Box, Footprints, LoaderCircle, MapPin, Pause, Play, X } from 'lucide-react'
import { api } from '../lib/api'
import type { Map3DPack, Photo } from '../lib/types'
import { CampusReveal, pickHero } from './CampusReveal'
import { GOOGLE_3D_KEY, haversineM, loadGoogle3D, loadStreetView, textPlaces } from '../lib/gmaps'
import type { LatLng } from '../lib/gmaps'
import { uniName, useLang } from '../lib/i18n'

type Mode = 'photos' | 'walk'
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

  // the panorama: nearest to the stop (outdoor first, then any: photospheres often show the insides), facing it
  useEffect(() => {
    if (mode !== 'walk' || !stop || !GOOGLE_3D_KEY) return
    let alive = true
    setPano('idle')
    setAuto(false)
    loadStreetView(lang).then(async (sv) => {
      const svc = new sv.StreetViewService()
      const ask = (radius: number, source: any) => svc.getPanorama({ location: stop.at, radius, preference: sv.StreetViewPreference.NEAREST, sources: [source] })
      let data: any = null
      for (const [r, s] of [[80, sv.StreetViewSource.OUTDOOR], [80, sv.StreetViewSource.DEFAULT], [300, sv.StreetViewSource.OUTDOOR], [600, sv.StreetViewSource.DEFAULT]] as const) {
        try { data = (await ask(r, s)).data; break } catch { /* next radius */ }
      }
      if (!alive || !box.current) return
      if (!data) { setPano('none'); return }
      const at = data.location.latLng.toJSON()
      const pov = { heading: haversineM(at, stop.at) > 8 ? bearing(at, stop.at) : 0, pitch: 4 }
      if (panoRef.current) {
        panoRef.current.setPano(data.location.pano)
        panoRef.current.setPov(pov)
      } else {
        panoRef.current = new sv.StreetViewPanorama(box.current, {
          pano: data.location.pano, pov, zoom: 0, addressControl: false, motionTracking: false, motionTrackingControl: false,
          fullscreenControl: false, showRoadLabels: false,
        })
      }
      setPano('ok')
    }).catch(() => { if (alive) setPano('none') })
    return () => { alive = false }
  }, [stop, mode, lang])
  useEffect(() => () => { panoRef.current?.setVisible?.(false); panoRef.current = null }, [])
  useEffect(() => { if (mode !== 'walk') { panoRef.current?.setVisible?.(false); panoRef.current = null } }, [mode])

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
      {mode === 'walk' && (
        <>
          <div ref={box} className="absolute inset-0" />
          {pano === 'idle' && <div className="absolute inset-0 grid place-items-center text-white/75 text-sm pointer-events-none"><span className="inline-flex items-center gap-2"><LoaderCircle size={15} className="animate-spin" /> Ищем панораму рядом…</span></div>}
          {pano === 'none' && <div className="absolute inset-0 grid place-items-center text-white/75 text-sm px-6 text-center pointer-events-none">Google Street View не снимал это место. Выберите другую точку внизу.</div>}
          {!GOOGLE_3D_KEY && <div className="absolute inset-0 grid place-items-center text-white/75 text-sm">Нет ключа Google Maps</div>}
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
            <button onClick={() => setAuto((v) => !v)} disabled={pano !== 'ok'}
              className={`shrink-0 h-9 px-3 rounded-xl text-[13px] font-medium inline-flex items-center gap-1.5 disabled:opacity-50 ${auto ? 'bg-white text-ink' : 'bg-white/10 hover:bg-white/20'}`}>
              {auto ? <Pause size={14} /> : <Play size={14} />} Автопрогулка
            </button>
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
