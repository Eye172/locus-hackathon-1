import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { MapPin, Sparkles, ArrowRight, Undo2, Loader2 } from 'lucide-react'
import { GlobeMap, type GlobeHandle, type HoverInfo } from '../map/GlobeMap'
import { Clouds } from '../components/Clouds'
import { SearchBox } from '../components/SearchBox'
import { api, thumbUrl } from '../lib/api'
import type { Candidate } from '../lib/types'
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
    mini.then((m) => { if (m) setTarget((t) => (t && t.qid === qid ? { ...t, name: m.name || t.name, city: m.city ?? t.city, photos: m.profile?.photos } : t)) })
    await globe.current?.flyToUniversity(c[1], c[0])
    setPhase('arrived')
  }
  const onPick = (c: Candidate) => goTo(c.qid, c.label, c.city)
  const back = () => { window.clearTimeout(autoTimer.current); setPhase('idle'); setTarget(null); globe.current?.resetToGlobe() }

  return (
    <div className="relative min-h-[560px] overflow-hidden text-white globe-page" style={{ height: 'calc(100vh - 56px)' }}>
      <GlobeMap ref={globe} onHover={setHover} onSelect={(h) => goTo(h.qid, h.name, h.city)} onZoom={setZoom} />
      <Clouds zoom={zoom} />
      <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(ellipse_at_center,transparent_55%,rgba(5,7,15,0.55)_100%)]" />

      {phase === 'idle' && (
        <>
          <div className="absolute top-8 left-0 right-0 flex flex-col items-center text-center px-4 pointer-events-none">
            <span className="chip bg-blue-500/15 text-blue-200 border border-blue-300/20 mb-4 pointer-events-auto"><Sparkles size={13} /> LOCUS Hackathon 2026 · кейс 1</span>
            <h1 className="text-3xl sm:text-5xl font-extrabold tracking-tight leading-tight max-w-3xl drop-shadow-[0_2px_16px_rgba(0,0,0,0.6)]">{t('home.title')}</h1>
            <p className="mt-3 text-blue-100/80 max-w-2xl text-sm sm:text-base">{t('home.subtitle')}</p>
          </div>
          <div className="absolute left-0 right-0 bottom-10 flex flex-col items-center px-4 gap-3">
            <div className="w-full max-w-2xl"><SearchBox onPick={onPick} dark autoFocus direction="up" /></div>
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

      {phase !== 'idle' && target && (
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
              <button onClick={() => nav(`/u/${target.qid}`)} className="btn-primary flex-1 justify-center">{t('globe.open')} <ArrowRight size={16} /></button>
            </div>
            {phase === 'arrived' && <div className="mt-2 text-[11px] text-blue-200/50">{t('globe.hint')}</div>}
          </div>
        </div>
      )}
    </div>
  )
}
