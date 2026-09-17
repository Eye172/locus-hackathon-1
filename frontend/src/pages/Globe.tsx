import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, ChevronRight, Globe2, Loader2, MapPin, MousePointerClick, Sparkles, Undo2, X } from 'lucide-react'
import { Cutscene, hasCutscene } from '../components/Cutscene'
import { GlobeMap, type GlobeHandle, type LevelState, type UniSelection } from '../map/GlobeMap'
import { loadCountries, localName, type CountryInfo } from '../map/levels'
import { CloudDive } from '../components/CloudDive'
import { SearchBox } from '../components/SearchBox'
import { api, streamProfile, thumbUrl } from '../lib/api'
import type { Candidate, Photo } from '../lib/types'
import { CampusReveal, depthUrl, heroUrl, pickHero } from '../components/CampusReveal'
import { useLang, useT } from '../lib/i18n'
import { detectLiteGpu } from '../lib/gpu'

const QUICK = ['KZ', 'KG', 'UZ', 'RU', 'CN', 'US', 'GB', 'DE']
const FLAG: Record<string, string> = { KZ: '🇰🇿', KG: '🇰🇬', UZ: '🇺🇿', RU: '🇷🇺', CN: '🇨🇳', US: '🇺🇸', GB: '🇬🇧', DE: '🇩🇪' }
const CAMPUS_DIVE_MS = 3600  // cloud dive into the campus: clouds from the first frame, hidden jump at 0.74

type MiniInfo = { name?: string; city?: string | null; photos?: { id: string; thumb: string }[] }

export default function Globe() {
  const t = useT()
  const lang = useLang()
  const nav = useNavigate()
  const lite = useMemo(() => detectLiteGpu(), [])
  const globe = useRef<GlobeHandle>(null)
  const [lvl, setLvl] = useState<LevelState>({ level: 'planet', country: null, city: null })
  const [countries, setCountries] = useState<CountryInfo[]>([])
  const [hint, setHint] = useState<'deeper' | 'up' | null>(null)
  const [sel, setSel] = useState<UniSelection | null>(null)
  const [selInfo, setSelInfo] = useState<MiniInfo | null>(null)
  const [flying, setFlying] = useState<string | null>(null)
  // campus (level 4)
  const [campus, setCampus] = useState<UniSelection | null>(null)
  const [cutscene, setCutscene] = useState<string | null>(null)
  const [heroPhotos, setHeroPhotos] = useState<Photo[]>([])
  const [reveal, setReveal] = useState(false)
  const [revealArmed, setRevealArmed] = useState(false)
  const [diveRun, setDiveRun] = useState(0)
  const [progress, setProgress] = useState<{ sources: number; photos: number; done: boolean } | null>(null)
  const stopStream = useRef<(() => void) | null>(null)
  const heroFor = useRef<string | null>(null)
  const heroRef = useRef<Photo[]>([])
  const campusRef = useRef<UniSelection | null>(null)
  const peekSeq = useRef(0)
  useEffect(() => { heroRef.current = heroPhotos }, [heroPhotos])
  useEffect(() => { campusRef.current = campus }, [campus])
  useEffect(() => { loadCountries().then(setCountries) }, [])
  useEffect(() => () => stopStream.current?.(), [])

  // photos for the campus scene: the cached profile answers at once; `live` also starts collecting when there is none
  const prepareHero = useCallback((qid: string, live: boolean) => {
    if (heroFor.current === qid && (heroRef.current.length || !live)) return
    heroFor.current = qid
    stopStream.current?.(); stopStream.current = null
    setHeroPhotos([]); setProgress(null)
    api.profile(qid).then((p) => { if (heroFor.current === qid) setHeroPhotos(pickHero(p.photos)) }).catch(() => {
      if (!live || heroFor.current !== qid) return
      const pool = new Map<string, Photo>()
      let sources = 0
      setProgress({ sources: 0, photos: 0, done: false })
      stopStream.current = streamProfile(qid, false, {
        onSource: (_n, s) => { if (s.status === 'done' || s.status === 'skipped' || s.status === 'error') { sources++; setProgress((p) => p && { ...p, sources }) } },
        onPhotos: (_s, photos) => { for (const p of photos) pool.set(p.id, p); setHeroPhotos(pickHero([...pool.values()])); setProgress((p) => p && { ...p, photos: pool.size }) },
        onProfile: (p) => { setHeroPhotos(pickHero(p.photos)); setProgress((x) => x && { ...x, done: true }) },
        onError: () => setProgress((x) => x && { ...x, done: true }),
      })
    })
  }, [])
  useEffect(() => {  // warm the first frames so the scene opens without a blank
    const q = campus?.qid ?? sel?.qid
    if (!q) return
    for (const p of heroPhotos.slice(0, 2)) { new Image().src = heroUrl(q, p.id, p.url); new Image().src = depthUrl(p.id) }
  }, [heroPhotos, campus, sel])

  const onSelectUni = useCallback((u: UniSelection | null) => {
    setSel(u); setSelInfo(null)
    if (!u) return
    prepareHero(u.qid, false)
    api.mini(u.qid).then((m) => setSelInfo({ name: m.name, city: m.city, photos: m.profile?.photos })).catch(() => {})
  }, [prepareHero])

  // ---- level 4: cloud dive from the city into one campus
  const openCampus = useCallback((u: UniSelection) => {
    if (campusRef.current) return
    setSel(u)
    setCampus(u)
    prepareHero(u.qid, true)
    setReveal(false); setRevealArmed(false)
    setDiveRun((n) => n + 1)
    globe.current?.diveIntoCampus(u.lat, u.lon, CAMPUS_DIVE_MS * 0.74)
  }, [prepareHero])
  const onDiveMid = useCallback(() => {
    const u = campusRef.current
    if (u) globe.current?.landAt(u.lat, u.lon)
    if (heroRef.current.length > 0) setRevealArmed(true)  // mounted hidden: GL setup happens under the clouds
  }, [])
  const onDiveWhite = useCallback(() => {
    if (heroRef.current.length > 0) { setReveal(true); setRevealArmed(false) }
    else globe.current?.riseBuildings()
  }, [])
  const onDiveDone = useCallback(() => {
    setDiveRun(0)
    if (heroRef.current.length === 0) window.setTimeout(() => { if (heroRef.current.length) setReveal(true) }, 900)
  }, [])
  const exitCampus = () => {
    setCampus(null); setReveal(false); setRevealArmed(false); setDiveRun(0)
    globe.current?.exitCampus()
  }
  const openProfile = async (qid: string) => { const src = await hasCutscene(qid); if (src) setCutscene(src); else nav(`/u/${qid}`) }

  // ---- search: staged spiral flight to the university's city
  const onPick = async (c: Candidate) => {
    const m = await api.mini(c.qid).catch(() => null)
    if (!m || m.lat == null || m.lon == null) { nav(`/u/${c.qid}`); return }
    if (campusRef.current) exitCampus()
    setFlying(c.label)
    prepareHero(c.qid, true)  // a searched university is a strong intent: start collecting right away
    const s = await globe.current?.flyToUniversity({ qid: c.qid, name: m.name || c.label, lat: m.lat, lon: m.lon, city: m.city ?? c.city })
    setFlying(null)
    if (s) { setSel(s); setSelInfo({ name: m.name, city: m.city, photos: m.profile?.photos }) }
  }
  const onCandidates = (cs: Candidate[]) => {
    const top = cs[0]
    if (!top || lvl.level !== 'planet') return
    const my = ++peekSeq.current
    api.mini(top.qid).then((m) => { if (my === peekSeq.current && m.lat != null && m.lon != null) globe.current?.peekAt(m.lat, m.lon) }).catch(() => {})
  }

  const hintText = hint === 'deeper' ? t(lvl.level === 'planet' ? 'lvl.limitPlanet' : 'lvl.limitCountry')
    : hint === 'up' ? t('lvl.limitUp')
      : lvl.level === 'planet' ? t('lvl.dblCountry') : lvl.level === 'country' ? t('lvl.dblCity') : lvl.level === 'city' ? t('lvl.clickUni') : ''
  const countryName = lvl.country ? localName(lvl.country, lang) : ''
  const campusName = campus ? (selInfo?.name && sel?.qid === campus.qid ? selInfo.name : campus.name) : ''

  return (
    <div className="relative min-h-[560px] overflow-hidden text-white globe-page" style={{ height: 'calc(100vh - 56px)' }}>
      {cutscene && <Cutscene src={cutscene} skipLabel={t('globe.skip')} aiLabel={t('globe.aiTransition')} onDone={() => { setCutscene(null); if (campus) nav(`/u/${campus.qid}`) }} />}
      <GlobeMap ref={globe} lang={lang} t={t} lite={lite} onLevel={setLvl} onSelectUni={onSelectUni} onOpenCampus={openCampus} onHint={setHint} />
      <CloudDive run={diveRun} duration={CAMPUS_DIVE_MS} cloudStart={0} lite={lite} onMid={onDiveMid} onWhite={onDiveWhite} onDone={onDiveDone} />
      <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(ellipse_at_center,transparent_60%,rgba(5,7,15,0.5)_100%)]" />

      {/* breadcrumb: where am I, one click to go up */}
      {lvl.level !== 'planet' && !reveal && (
        <nav className="absolute top-4 left-4 z-30 flex items-center gap-1 rounded-xl bg-[#0B1222]/85 border border-white/10 backdrop-blur px-2 py-1.5 text-sm pop">
          <button onClick={() => { if (campus) setCampus(null); globe.current?.enterPlanet() }} className="crumb"><Globe2 size={14} /> {t('lvl.planet')}</button>
          {lvl.country && <><ChevronRight size={14} className="opacity-40" /><button onClick={() => { if (campus) setCampus(null); globe.current?.enterCountry(lvl.country!.iso) }} className="crumb">{countryName}</button></>}
          {lvl.city && <><ChevronRight size={14} className="opacity-40" /><button onClick={() => { if (campus) exitCampus(); else if (lvl.country && lvl.city!.id >= 0) globe.current?.enterCity(lvl.country.iso, lvl.city!.id) }} className="crumb">{lvl.city.name || '—'}</button></>}
          {campus && <><ChevronRight size={14} className="opacity-40" /><span className="crumb crumb-on">{t('lvl.campus')}</span></>}
        </nav>
      )}

      {lvl.level === 'planet' && !flying && (
        <div className="absolute top-8 left-0 right-0 flex flex-col items-center text-center px-4 pointer-events-none">
          <span className="chip bg-blue-500/15 text-blue-200 border border-blue-300/20 mb-4 pointer-events-auto"><Sparkles size={13} /> LOCUS Hackathon 2026 · кейс 1</span>
          <h1 className="text-3xl sm:text-5xl font-extrabold tracking-tight leading-tight max-w-3xl drop-shadow-[0_2px_16px_rgba(0,0,0,0.6)]">{t('home.title')}</h1>
          <p className="mt-3 text-blue-100/80 max-w-2xl text-sm sm:text-base">{t('home.subtitle')}</p>
        </div>
      )}

      {!campus && (
        <div className="absolute left-0 right-0 bottom-8 flex flex-col items-center px-4 gap-2 pointer-events-none">
          {hintText && !flying && (
            <div className={`pointer-events-none chip border backdrop-blur text-xs ${hint ? 'bg-[#1D4ED8]/80 border-blue-300/40 text-white' : 'bg-black/35 border-white/10 text-blue-100/85'}`}>
              <MousePointerClick size={13} /> {hintText}
            </div>
          )}
          {flying && <div className="chip bg-black/40 border border-white/10 text-blue-100 text-xs"><Loader2 size={12} className="animate-spin" /> {t('globe.fly')} · {flying}</div>}
          <div className="w-full max-w-2xl pointer-events-auto"><SearchBox onPick={onPick} onCandidates={onCandidates} dark autoFocus={lvl.level === 'planet'} direction="up" size={lvl.level === 'planet' ? 'lg' : 'md'} /></div>
          {lvl.level === 'planet' && (
            <div className="flex flex-wrap justify-center gap-2 pointer-events-auto">
              {QUICK.map((iso) => {
                const c = countries.find((x) => x.iso === iso)
                if (!c) return null
                return (
                  <button key={iso} onClick={() => globe.current?.enterCountry(iso)}
                    className="chip bg-white/10 border border-white/15 text-white hover:bg-white/20 cursor-pointer backdrop-blur">
                    {FLAG[iso]} {localName(c, lang)} <span className="opacity-60">{c.count}</span>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      )}
      {lvl.level === 'planet' && (
        <div className="absolute left-4 bottom-3 text-[11px] text-blue-100/55 hidden lg:block">
          {countries.reduce((a, c) => a + c.count, 0).toLocaleString('ru-RU')} {t('lvl.unis')} · {countries.length} {t('lvl.countries')}{lite ? ` · ${t('lvl.lite')}` : ''}
        </div>
      )}

      {/* selected university (city level) */}
      {sel && !campus && lvl.level === 'city' && (
        <div className="absolute left-4 top-20 z-30 w-[320px] max-w-[calc(100vw-2rem)] pop">
          <div className="rounded-2xl bg-[#0B1222]/92 border border-white/10 backdrop-blur-xl p-4 shadow-2xl">
            <div className="flex items-start gap-2">
              <div className="flex-1 min-w-0">
                <div className="font-bold leading-snug">{selInfo?.name ?? localName(sel, lang)}</div>
                <div className="text-xs text-blue-100/70 mt-0.5 flex items-center gap-1"><MapPin size={12} /> {selInfo?.city ?? sel.cityName}</div>
              </div>
              <button onClick={() => globe.current?.select(null)} className="btn-icon !border-white/15 !text-white !w-7 !h-7"><X size={14} /></button>
            </div>
            {selInfo?.photos && selInfo.photos.length > 0 && (
              <div className="mt-3 grid grid-cols-3 gap-1.5">
                {selInfo.photos.map((p) => <img key={p.id} src={thumbUrl(p)} alt="" className="aspect-[4/3] w-full object-cover rounded-lg" />)}
              </div>
            )}
            <div className="mt-3 flex gap-2">
              <button onClick={() => openCampus(sel)} className="btn-primary flex-1 justify-center">{t('lvl.toCampus')} <ArrowRight size={15} /></button>
              <button onClick={() => void openProfile(sel.qid)} className="btn-ghost !border-white/15 !text-white">{t('lvl.profile')}</button>
            </div>
            <div className="mt-2 text-[11px] text-blue-200/50">{t('lvl.dblCampus')}</div>
          </div>
        </div>
      )}

      {/* level 4: campus */}
      {((reveal && !!campus) || revealArmed) && campus && heroPhotos.length > 0 && (
        <CampusReveal visible={reveal} qid={campus.qid} name={campusName} city={campus.cityName}
          photos={heroPhotos} onOpen={() => void openProfile(campus.qid)} onMap={() => { setReveal(false); setRevealArmed(false); globe.current?.riseBuildings() }} />
      )}
      {campus && !reveal && diveRun === 0 && (
        <div className="absolute left-4 bottom-4 right-4 sm:right-auto sm:w-[400px] z-30 pop">
          <div className="rounded-2xl bg-[#0B1222]/92 border border-white/10 backdrop-blur-xl p-4 shadow-2xl">
            <div className="flex items-start gap-3">
              <div className="flex-1 min-w-0">
                <div className="text-[11px] uppercase tracking-wider text-blue-200/60">{t('globe.campus')}</div>
                <div className="font-bold text-lg leading-snug truncate">{campusName}</div>
                {campus.cityName && <div className="text-sm text-blue-100/70">{campus.cityName}</div>}
              </div>
              <button onClick={exitCampus} className="btn-ghost !border-white/15 !text-white !px-2.5" title={t('lvl.back')}><Undo2 size={15} /></button>
            </div>
            <div className="mt-3 flex items-center gap-2">
              <button onClick={() => void openProfile(campus.qid)} className="btn-primary flex-1 justify-center">{t('globe.open')} <ArrowRight size={16} /></button>
              {heroPhotos.length > 0 && <button onClick={() => setReveal(true)} className="btn-ghost !border-white/15 !text-white">{t('reveal.show')}</button>}
            </div>
            {progress && !progress.done && <div className="mt-2 text-[11px] text-blue-200/70 flex items-center gap-1.5"><Loader2 size={11} className="animate-spin" /> {t('reveal.collecting')} · {progress.photos} · {progress.sources}/13</div>}
            <div className="mt-2 text-[11px] text-blue-200/50">{t('globe.hint')}</div>
          </div>
        </div>
      )}
    </div>
  )
}
