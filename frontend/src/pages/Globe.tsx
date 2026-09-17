import { Cutscene, hasCutscene } from '../components/Cutscene'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { MapPin, Sparkles, ArrowRight, Undo2, Loader2 } from 'lucide-react'
import { GlobeMap, type GlobeHandle, type HoverInfo } from '../map/GlobeMap'
import { Clouds } from '../components/Clouds'
import { CloudDive } from '../components/CloudDive'
import { SearchBox } from '../components/SearchBox'
import { api, streamProfile, thumbUrl } from '../lib/api'
import type { Candidate, Photo } from '../lib/types'
import { CampusReveal, depthUrl, heroUrl, pickHero } from '../components/CampusReveal'
import { useLang, useT } from '../lib/i18n'

interface Country { qid: string; iso: string; ru: string; en: string; kk: string; count: number; center: number[]; bbox: number[] }
const QUICK = ['KZ', 'KG', 'UZ', 'RU', 'CN', 'US', 'GB', 'DE']
const FLAG: Record<string, string> = { KZ: '🇰🇿', KG: '🇰🇬', UZ: '🇺🇿', RU: '🇷🇺', CN: '🇨🇳', US: '🇺🇸', GB: '🇬🇧', DE: '🇩🇪' }

export default function Globe() {
  const t = useT()
  const lang = useLang()
  const nav = useNavigate()
  const globe = useRef<GlobeHandle>(null)
  const [zoom, setZoom] = useState(1.5)
  const [hover, setHover] = useState<HoverInfo | null>(null)
  const [countries, setCountries] = useState<Country[]>([])
  const [phase, setPhase] = useState<'idle' | 'flying' | 'arrived'>('idle')
  const [cutscene, setCutscene] = useState<string | null>(null)
  const [heroPhotos, setHeroPhotos] = useState<Photo[]>([])
  const [reveal, setReveal] = useState(false)
  const [revealArmed, setRevealArmed] = useState(false)
  const stopStream = useRef<(() => void) | null>(null)
  const [progress, setProgress] = useState<{ sources: number; photos: number; done: boolean } | null>(null)
  const peekSeq = useRef(0)
  const [dive, setDive] = useState(false)
  const [diveRun, setDiveRun] = useState(0)
  const diveTarget = useRef<[number, number] | null>(null)
  const revealTimer = useRef<number | undefined>(undefined)
  const openProfile = async () => { if (!target) return; const src = await hasCutscene(target.qid); if (src) setCutscene(src); else nav(`/u/${target.qid}`) }
  const [target, setTarget] = useState<{ qid: string; name: string; city?: string | null; photos?: { id: string; thumb: string }[] } | null>(null)
  const coords = useRef<Map<string, [number, number]>>(new Map())
  const autoTimer = useRef<number | undefined>(undefined)

  useEffect(() => {
    fetch('/countries.json').then((r) => r.json()).then(setCountries).catch(() => {})
    fetch('/universities.geojson').then((r) => r.json()).then((g: GeoJSON.FeatureCollection) => {
      for (const f of g.features) coords.current.set((f.properties as { qid: string }).qid, (f.geometry as GeoJSON.Point).coordinates as [number, number])
    }).catch(() => {})
    return () => window.clearTimeout(autoTimer.current)
  }, [])

  const goTo = async (qid: string, name: string, city?: string | null) => {
    let c = coords.current.get(qid)
    const mini = api.mini(qid).catch(() => null)
    if (!c) {
      const m = await mini
      if (m && m.lat != null && m.lon != null) c = [m.lon, m.lat]
    }
    if (!c) { nav(`/u/${qid}`); return }
    setTarget({ qid, name, city })
    setPhase('flying')
    setHover(null)
    // the arrival scene needs photos: the cached profile answers at once, otherwise the live stream feeds it as it goes
    stopStream.current?.(); setHeroPhotos([]); setReveal(false); window.clearTimeout(revealTimer.current)
    setProgress(null)
    api.profile(qid).then((p) => setHeroPhotos(pickHero(p.photos))).catch(() => {
      const pool = new Map<string, Photo>()
      let sources = 0
      setProgress({ sources: 0, photos: 0, done: false })
      stopStream.current = streamProfile(qid, false, {
        onSource: (_n, s) => { if (s.status === 'done' || s.status === 'skipped' || s.status === 'error') { sources++; setProgress((p) => p && { ...p, sources }) } },
        onPhotos: (_s, photos) => { for (const p of photos) pool.set(p.id, p); const hero = pickHero([...pool.values()]); setHeroPhotos(hero); setProgress((p) => p && { ...p, photos: pool.size }) },
        onProfile: (p) => { setHeroPhotos(pickHero(p.photos)); setProgress((x) => x && { ...x, done: true }) },
        onError: () => setProgress((x) => x && { ...x, done: true }),
      })
    })
    mini.then((m) => { if (m) setTarget((t) => (t && t.qid === qid ? { ...t, name: m.name || t.name, city: m.city ?? t.city, photos: m.profile?.photos } : t)) })
    // 1. the planet turns to the university (space view)  2. the cloud dive covers the screen while the map zooms
    // 3. inside the white-out the map jumps to the campus  4. clouds clear, buildings rise  5. the arrival scene
    diveTarget.current = [c[1], c[0]]
    // one continuous move: the turn decelerates while the approach already accelerates; the cloud dive starts with it
    globe.current?.spinAndApproach(c[1], c[0], { approachMs: 4500, zoom: 11.2, onApproach: () => { setDive(true); setDiveRun((n) => n + 1) } })
  }
  const heroRef = useRef<Photo[]>([])
  useEffect(() => { heroRef.current = heroPhotos }, [heroPhotos])
  const onDiveMid = useCallback(() => {
    const d = diveTarget.current
    if (d) globe.current?.landAt(d[0], d[1])
    if (heroRef.current.length > 0) setRevealArmed(true)  // mount the scene hidden now: GL setup happens under the clouds
  }, [])
  // white-out complete: the campus photo takes over directly (the map with its boxes stays behind the «3D map» button);
  // without photos yet, the clouds clear onto the 3D map while the profile keeps collecting
  const onDiveWhite = useCallback(() => {
    setPhase('arrived')
    if (heroRef.current.length > 0) { setReveal(true); setRevealArmed(false) }
    else globe.current?.riseBuildings()
  }, [])
  const onDiveDone = useCallback(() => {
    setDive(false)
    setDiveRun(0)
    if (heroRef.current.length === 0) revealTimer.current = window.setTimeout(() => setReveal(true), 900)
  }, [])
  // the planet turns towards the best match while typing — also for universities outside the local index
  const onCandidates = (cs: Candidate[]) => {
    const top = cs[0]
    if (!top) return
    const my = ++peekSeq.current
    const c = coords.current.get(top.qid)
    if (c) { globe.current?.peekAt(c[1], c[0]); return }
    api.mini(top.qid).then((m) => {
      if (my !== peekSeq.current || m.lat == null || m.lon == null) return
      coords.current.set(top.qid, [m.lon, m.lat])
      globe.current?.peekAt(m.lat, m.lon)
    }).catch(() => {})
  }
  // warm the first frames during the flight so the scene opens without a blank
  useEffect(() => {
    if (!target) return
    for (const p of heroPhotos.slice(0, 2)) { new Image().src = heroUrl(target.qid, p.id, p.url); new Image().src = depthUrl(p.id) }
  }, [heroPhotos, target])
  const onPick = (c: Candidate) => goTo(c.qid, c.label, c.city)
  const back = () => { window.clearTimeout(autoTimer.current); window.clearTimeout(revealTimer.current); stopStream.current?.(); setDive(false); setDiveRun(0); setReveal(false); setRevealArmed(false); setHeroPhotos([]); setPhase('idle'); setTarget(null); globe.current?.resetToGlobe() }

  return (
    <div className="relative min-h-[560px] overflow-hidden text-white globe-page" style={{ height: 'calc(100vh - 56px)' }}>
      {cutscene && target && <Cutscene src={cutscene} skipLabel={t('globe.skip')} aiLabel={t('globe.aiTransition')} onDone={() => { setCutscene(null); nav(`/u/${target.qid}`) }} />}
      <GlobeMap ref={globe} onHover={setHover} onSelect={(h) => goTo(h.qid, h.name, h.city)} onZoom={setZoom} />
      <Clouds zoom={dive ? 0 : zoom} />
      <CloudDive run={diveRun} onMid={onDiveMid} onWhite={onDiveWhite} onDone={onDiveDone} />
      <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(ellipse_at_center,transparent_55%,rgba(5,7,15,0.55)_100%)]" />

      {phase === 'idle' && (
        <>
          <div className="absolute top-8 left-0 right-0 flex flex-col items-center text-center px-4 pointer-events-none">
            <span className="chip bg-blue-500/15 text-blue-200 border border-blue-300/20 mb-4 pointer-events-auto"><Sparkles size={13} /> LOCUS Hackathon 2026 · кейс 1</span>
            <h1 className="text-3xl sm:text-5xl font-extrabold tracking-tight leading-tight max-w-3xl drop-shadow-[0_2px_16px_rgba(0,0,0,0.6)]">{t('home.title')}</h1>
            <p className="mt-3 text-blue-100/80 max-w-2xl text-sm sm:text-base">{t('home.subtitle')}</p>
          </div>
          <div className="absolute left-0 right-0 bottom-10 flex flex-col items-center px-4 gap-3">
            <div className="w-full max-w-2xl"><SearchBox onPick={onPick} onCandidates={onCandidates} dark autoFocus direction="up" /></div>
            <div className="flex flex-wrap justify-center gap-2">
              {QUICK.map((iso) => {
                const c = countries.find((x) => x.iso === iso)
                if (!c) return null
                return (
                  <button key={iso} onClick={() => globe.current?.flyToCountry(c.bbox)}
                    className="chip bg-white/10 border border-white/15 text-white hover:bg-white/20 cursor-pointer backdrop-blur">
                    {FLAG[iso]} {c[lang]} <span className="opacity-60">{c.count}</span>
                  </button>
                )
              })}
              <button onClick={() => globe.current?.resetToGlobe()} className="chip bg-white/10 border border-white/15 text-white hover:bg-white/20 cursor-pointer">🌍 {t('profile.all')}</button>
            </div>
          </div>
          <div className="absolute left-4 bottom-4 text-[11px] text-blue-100/60 hidden sm:block">
            14 467 вузов · 10 045 с координатами · Wikidata · OpenStreetMap · Wikimedia Commons
          </div>
        </>
      )}

      {hover && phase === 'idle' && (
        <div className="absolute z-30 pointer-events-none pop" style={{ left: Math.min(hover.x + 14, window.innerWidth - 300), top: hover.y + 14 }}>
          <div className="w-72 rounded-2xl bg-[#0B1222]/95 border border-white/10 backdrop-blur-xl p-3 shadow-2xl">
            <div className="font-semibold leading-snug">{lang === 'en' ? hover.name_en : hover.name}</div>
            <div className="text-xs text-blue-200/70 mt-0.5 flex items-center gap-1"><MapPin size={12} />{[hover.city, hover.c].filter(Boolean).join(' · ')}</div>
            <div className="text-[11px] text-blue-200/50 mt-2">{t('globe.click')}</div>
          </div>
        </div>
      )}

      {((reveal && phase === 'arrived') || revealArmed) && target && heroPhotos.length > 0 && (
        <CampusReveal visible={reveal && phase === 'arrived'} qid={target.qid} name={target.name} city={target.city} photos={heroPhotos} onOpen={openProfile} onMap={() => { setReveal(false); setRevealArmed(false); globe.current?.riseBuildings() }} />
      )}
      {phase !== 'idle' && target && !(reveal && heroPhotos.length > 0) && (
        <div className="absolute left-4 bottom-4 right-4 sm:right-auto sm:w-[420px] z-30 pop">
          <div className="rounded-2xl bg-[#0B1222]/92 border border-white/10 backdrop-blur-xl p-4 shadow-2xl">
            <div className="flex items-start gap-3">
              <div className="flex-1 min-w-0">
                <div className="text-[11px] uppercase tracking-wider text-blue-200/60 flex items-center gap-1.5">
                  {phase === 'flying' ? <><Loader2 size={12} className="animate-spin" /> {t('globe.fly')}</> : t('globe.campus')}
                </div>
                <div className="font-bold text-lg leading-snug truncate">{target.name}</div>
                {target.city && <div className="text-sm text-blue-100/70">{target.city}</div>}
              </div>
              <button onClick={back} className="btn-ghost !border-white/15 !text-white !px-2.5" title="Назад к планете"><Undo2 size={15} /></button>
            </div>
            {target.photos && target.photos.length > 0 && (
              <div className="mt-3 grid grid-cols-3 gap-1.5">
                {target.photos.map((p) => <img key={p.id} src={thumbUrl(p)} alt="" className="aspect-[4/3] w-full object-cover rounded-lg" />)}
              </div>
            )}
            <div className="mt-3 flex items-center gap-2">
              <button onClick={openProfile} className="btn-primary flex-1 justify-center">{t('globe.open')} <ArrowRight size={16} /></button>
            </div>
            {progress && !progress.done && <div className="mt-2 text-[11px] text-blue-200/70 flex items-center gap-1.5"><Loader2 size={11} className="animate-spin" /> {t('reveal.collecting')} · {progress.photos} · {progress.sources}/13</div>}
            {phase === 'arrived' && heroPhotos.length > 0 && <button onClick={() => setReveal(true)} className="mt-2 text-[11px] text-blue-200 hover:text-white underline underline-offset-2 cursor-pointer">{t('reveal.show')}</button>}
            {phase === 'arrived' && <div className="mt-2 text-[11px] text-blue-200/50">{t('globe.hint')}</div>}
          </div>
        </div>
      )}
    </div>
  )
}
