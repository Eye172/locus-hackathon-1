import { Cutscene, hasCutscene } from '../components/Cutscene'
import { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { MapPin, ArrowRight, Undo2, Loader2 } from 'lucide-react'
import { GlobeMap, type GlobeHandle, type HoverInfo } from '../map/GlobeMap'
import { Clouds } from '../components/Clouds'
import { CloudDive } from '../components/CloudDive'
import { SearchBox } from '../components/SearchBox'
import { api, streamProfile, thumbUrl } from '../lib/api'
import type { Candidate, Photo } from '../lib/types'
import { CampusReveal, depthUrl, heroUrl, pickHero } from '../components/CampusReveal'
import { uniName, useLang, useT } from '../lib/i18n'
import { GOOGLE_3D_KEY, loadGoogle3D } from '../lib/gmaps'

// after the cloud dive: Google photorealistic 3D, an orbit of the campus, then the map locked to the city
const Campus3D = lazy(() => import('../components/Campus3D'))
const CampusTour = lazy(() => import('../components/CampusTour'))

interface Country { qid: string; iso: string; ru: string; en: string; kk: string; count: number; center: number[]; bbox: number[] }
const QUICK = ['KZ', 'KG', 'UZ', 'RU', 'CN', 'US', 'GB', 'DE']

export default function Globe() {
  const t = useT()
  const lang = useLang()
  const nav = useNavigate()
  const [params, setParams] = useSearchParams()
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
  const [diveHold, setDiveHold] = useState(false)
  const [tour, setTour] = useState(false)   // «Обзор»: 3D photos + Street View walk over the scene   // the Google scene under the clouds is still loading
  const [diveRun, setDiveRun] = useState(0)
  const diveTarget = useRef<[number, number] | null>(null)
  const revealTimer = useRef<number | undefined>(undefined)
  const openProfile = async () => { if (!target) return; const src = await hasCutscene(target.qid); if (src) setCutscene(src); else nav(`/u/${target.qid}`) }
  const [target, setTarget] = useState<{ qid: string; name: string; city?: string | null; photos?: { id: string; thumb: string }[] } | null>(null)
  const coords = useRef<Map<string, [number, number]>>(new Map())
  const [g3d, setG3d] = useState<{ qid: string; active: boolean; at?: { lat: number; lng: number } } | null>(null)
  const g3dReadyFor = useRef<string | null>(null)
  const landed = useRef(false)  // the planet map has jumped to the campus (only needed when the Google scene is not used)
  const g3dRef = useRef(g3d)
  useEffect(() => { g3dRef.current = g3d }, [g3d])
  const autoTimer = useRef<number | undefined>(undefined)
  const pageRef = useRef<HTMLDivElement>(null)
  const titleRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLDivElement>(null)
  const [inset, setInset] = useState<{ top: number; bottom: number }>()
  const [titleHidden, setTitleHidden] = useState(false)   // the planet has grown into the title: fade it out

  // the planet is centred in the free band between the title and the search block
  useLayoutEffect(() => {
    const page = pageRef.current, title = titleRef.current, search = searchRef.current
    if (phase !== 'idle' || !page || !title || !search) return
    const GAP = 16
    const measure = () => {
      const r = page.getBoundingClientRect()
      const top = Math.round(title.getBoundingClientRect().bottom - r.top) + GAP
      const bottom = Math.round(r.bottom - search.getBoundingClientRect().top) + GAP
      setInset((o) => (o && o.top === top && o.bottom === bottom ? o : { top, bottom }))
    }
    measure()
    const ro = new ResizeObserver(measure)
    for (const el of [page, title, search]) ro.observe(el)
    return () => ro.disconnect()
  }, [phase])

  useEffect(() => {
    fetch('/countries.json').then((r) => r.json()).then(setCountries).catch(() => {})
    fetch('/universities.geojson').then((r) => r.json()).then((g: GeoJSON.FeatureCollection) => {
      for (const f of g.features) coords.current.set((f.properties as { qid: string }).qid, (f.geometry as GeoJSON.Point).coordinates as [number, number])
    }).catch(() => {})
    return () => window.clearTimeout(autoTimer.current)
  }, [])

  // the Google 3D scene's code and libraries are fetched ahead of the first pick (after the opening shot, or at once
  // when the page opens straight into a flight): the 3D map can then start loading the moment a university is chosen,
  // not ~2 s into the flight. Loading the script is free; a map is billed only when one is created.
  useEffect(() => {
    if (!GOOGLE_3D_KEY) return
    const warm = () => { loadGoogle3D(lang).catch(() => {}); import('../components/Campus3D').catch(() => {}) }
    const timer = window.setTimeout(warm, params.get('u') ? 0 : 4200)
    return () => window.clearTimeout(timer)
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

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
    // the Google 3D scene mounts hidden right away: its tiles load during the spin and the clouds
    const next = GOOGLE_3D_KEY ? { qid, active: false, at: { lat: c[1], lng: c[0] } } : null
    landed.current = false
    g3dRef.current = next
    setG3d(next)
    setDiveHold(!!next && g3dReadyFor.current !== qid)   // /?u= mounts the scene before the flight: it may be ready already
    setProgress(null)
    api.profile(qid).then((p) => setHeroPhotos(pickHero(p.photos, 6, p.cover))).catch(() => {
      const pool = new Map<string, Photo>()
      let sources = 0
      setProgress({ sources: 0, photos: 0, done: false })
      stopStream.current = streamProfile(qid, false, {
        onSource: (_n, s) => { if (s.status === 'done' || s.status === 'skipped' || s.status === 'error') { sources++; setProgress((p) => p && { ...p, sources }) } },
        onPhotos: (_s, photos) => { for (const p of photos) pool.set(p.id, p); const hero = pickHero([...pool.values()]); setHeroPhotos(hero); setProgress((p) => p && { ...p, photos: pool.size }) },
        onProfile: (p, _cached, final) => { setHeroPhotos(pickHero(p.photos, 6, p.cover)); setProgress((x) => x && { ...x, photos: Math.max(x.photos, p.photos.length), done: final }) },
        onError: () => setProgress((x) => x && { ...x, done: true }),
      })
    })
    mini.then((m) => { if (m) setTarget((t) => (t && t.qid === qid ? { ...t, name: uniName(m, lang) || t.name, city: m.city ?? t.city, photos: m.profile?.photos } : t)) })
    // 1. the planet turns to the university (space view)  2. the cloud dive covers the screen while the map zooms
    // 3. inside the white-out the map jumps to the campus  4. clouds clear, buildings rise  5. the arrival scene
    diveTarget.current = [c[1], c[0]]
    // one continuous spiral: the planet turns and grows at once; the cloud dive is timed so its hidden jump lands at the end
    globe.current?.spinAndApproach(c[1], c[0], { approachMs: 4588 /* 0.74 × 6.2 s: the dive's hidden jump */, zoom: 11.2, onApproach: () => {
      setDive(true); setDiveRun((n) => n + 1)
    } })
  }
  // the profile's «campus map» (/?u=qid) is this same map: the same flight as after a search, once the planet has loaded
  useEffect(() => {
    const u = params.get('u')
    if (!u) return
    setPhase('flying')  // no title and search box on the way
    // the Google scene starts loading now, not after the planet's style and the first camera move
    if (GOOGLE_3D_KEY) { const early = { qid: u, active: false }; g3dRef.current = early; setG3d(early) }
    const mini = api.mini(u).catch(() => null)
    let timer = 0, dead = false
    const go = async () => {
      // the style is in (its layers are added on style.load, ~0.8 s cold). Not isStyleLoaded(): that also waits for
      // the 2.4 MB universities layer and every tile, ~4 s cold, all spent looking at a still planet
      if (!globe.current?.getMap()?.getLayer('unis-point')) { timer = window.setTimeout(go, 100); return }
      const m = await mini
      if (!dead) goTo(u, m ? uniName(m, lang) : u, m?.city)
    }
    go()
    return () => { dead = true; window.clearTimeout(timer) }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  const heroRef = useRef<Photo[]>([])
  useEffect(() => { heroRef.current = heroPhotos }, [heroPhotos])
  const landPlanet = () => {
    const d = diveTarget.current
    if (!d || landed.current) return
    landed.current = true
    globe.current?.landAt(d[0], d[1])
  }
  const onDiveMid = useCallback(() => {
    // with the Google scene on its way the planet map stays where it is: its jump to the campus (street-level imagery,
    // vector tiles, 3D buildings) was ~300 requests and a 150 ms stall spent on a view nobody sees - it happens only if
    // Google turns out to be unavailable
    if (!g3dRef.current) landPlanet()
    if (heroRef.current.length > 0 && !g3dRef.current) setRevealArmed(true)  // mount the scene hidden now: GL setup happens under the clouds
  }, [])
  // white-out complete: the campus photo takes over directly (the map with its boxes stays behind the «3D map» button);
  // without photos yet, the clouds clear onto the 3D map while the profile keeps collecting
  const onDiveWhite = useCallback(() => {
    setPhase('arrived')
    if (g3dRef.current) {  // the Google 3D scene takes over: orbit, then the city map
      const on = { ...g3dRef.current, active: true }
      g3dRef.current = on
      setG3d(on)
      return
    }
    if (heroRef.current.length > 0) { setReveal(true); setRevealArmed(false) }
    else globe.current?.riseBuildings()
  }, [])
  const onDiveDone = useCallback(() => {
    setDive(false)
    setDiveRun(0)
    if (g3dRef.current) return
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
  const onG3dReady = useCallback(() => { g3dReadyFor.current = g3dRef.current?.qid ?? null; setDiveHold(false) }, [])
  const onG3dUnavailable = useCallback(() => {
    setDiveHold(false)
    const was = g3dRef.current
    g3dRef.current = null
    setG3d(null)
    landPlanet()
    if (was?.active) {
      if (heroRef.current.length > 0) setReveal(true)
      else globe.current?.riseBuildings()
    }
  }, [])
  const onPick = (c: Candidate) => goTo(c.qid, c.label, c.city)
  const back = () => { window.clearTimeout(autoTimer.current); window.clearTimeout(revealTimer.current); stopStream.current?.(); setDive(false); setDiveRun(0); setReveal(false); setRevealArmed(false); setHeroPhotos([]); setPhase('idle'); setTarget(null); setTour(false); setG3d(null); g3dRef.current = null; g3dReadyFor.current = null; setDiveHold(false); globe.current?.resetToGlobe(); if (params.has('u')) setParams({}, { replace: true }) }

  return (
    // the transparent header is fixed over the globe, so the page is the full viewport: one pixel more and it scrolls,
    // and the scrollbar that appears narrows the map by 15 px mid-load. dvh: on phones 100vh includes the address bar
    <div ref={pageRef} className="relative min-h-[560px] overflow-hidden text-white globe-page" style={{ height: '100dvh' }}>
      {cutscene && target && <Cutscene src={cutscene} skipLabel={t('globe.skip')} aiLabel={t('globe.aiTransition')} onDone={() => { setCutscene(null); nav(`/u/${target.qid}`) }} />}
      {/* under the Google scene the planet, its stars and clouds are not painted at all */}
      <div className="absolute inset-0" style={{ visibility: g3d?.active ? 'hidden' : 'visible' }}>
      <GlobeMap ref={globe} onHover={setHover} onSelect={(h) => goTo(h.qid, lang === 'en' ? h.name_en : h.name, h.city)} onZoom={setZoom} inset={inset}
        onTitleOverlap={setTitleHidden} />
      <Clouds zoom={dive ? 0 : zoom} />
      </div>
      {g3d && (
        <Suspense fallback={null}>
          <Campus3D qid={g3d.qid} variant="arrival" active={g3d.active} start={g3d.at} onOpenProfile={openProfile} onBack={back}
            onPhotos={() => setTour(true)} onUnavailable={onG3dUnavailable}
            collecting={progress && !progress.done ? { photos: progress.photos } : null} onSceneReady={onG3dReady} />
        </Suspense>
      )}
      {tour && target && (
        <Suspense fallback={null}>
          <CampusTour qid={target.qid} name={target.name} photos={heroPhotos} onClose={() => setTour(false)} />
        </Suspense>
      )}
      <CloudDive run={diveRun} hold={diveHold} onMid={onDiveMid} onWhite={onDiveWhite} onDone={onDiveDone} />
      <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(ellipse_at_center,transparent_55%,rgba(5,7,15,0.55)_100%)]" />

      {phase === 'idle' && (
        <>
          <div ref={titleRef} className="absolute top-[5.5rem] left-0 right-0 flex flex-col items-center text-center px-4 pointer-events-none"
            style={{ opacity: titleHidden ? 0 : 1, transition: 'opacity 800ms ease' }}>
            <h1 className="font-sans text-[40px] sm:text-[64px] font-semibold tracking-[-0.03em] leading-none drop-shadow-[0_2px_16px_rgba(0,0,0,0.6)]">CampusLense</h1>
          </div>
          <div ref={searchRef} className="absolute left-0 right-0 bottom-10 flex flex-col items-center px-4 gap-3">
            <div className="w-full max-w-2xl"><SearchBox onPick={onPick} onCandidates={onCandidates} dark autoFocus direction="up" /></div>
            <div className="flex flex-wrap justify-center gap-2">
              {QUICK.map((iso) => {
                const c = countries.find((x) => x.iso === iso)
                if (!c) return null
                return (
                  <button key={iso} onClick={() => globe.current?.flyToCountry(c.bbox)}
                    className="inline-flex items-center gap-1.5 h-8 px-3 rounded-full bg-white/10 border border-white/15 text-[13px] text-white/90 hover:bg-white/20 hover:text-white cursor-pointer backdrop-blur transition-colors">
                    {c[lang]} <span className="opacity-50">{c.count}</span>
                  </button>
                )
              })}
              <button onClick={() => globe.current?.resetToGlobe()} className="inline-flex items-center h-8 px-3 rounded-full bg-white/10 border border-white/15 text-[13px] text-white/90 hover:bg-white/20 hover:text-white cursor-pointer backdrop-blur transition-colors">{t('lvl.wholePlanet')}</button>
            </div>
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
        <CampusReveal visible={reveal && phase === 'arrived'} qid={target.qid} name={target.name} city={target.city} photos={heroPhotos} onOpen={openProfile}
          onMap={() => { setReveal(false); setRevealArmed(false); if (!g3dRef.current) globe.current?.riseBuildings() }} />
      )}
      {phase !== 'idle' && target && !(reveal && heroPhotos.length > 0) && !g3d?.active && (
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
