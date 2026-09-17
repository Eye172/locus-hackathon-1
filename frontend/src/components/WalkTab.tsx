import { useEffect, useRef, useState } from 'react'
import { Footprints, KeyRound, Building2, Move3d } from 'lucide-react'
import type { Campus, Photo, University } from '../lib/types'
import { API_BASE, thumbUrl } from '../lib/api'
import { DepthPhoto } from './DepthPhoto'
import { SourceLink } from './SourceLink'
import { catLabel, useLang, useT } from '../lib/i18n'

const GKEY = (import.meta.env.VITE_GOOGLE_MAPS_KEY as string | undefined) || ''
const MTOKEN = (import.meta.env.VITE_MAPILLARY_TOKEN as string | undefined) || ''
const KIND_LABEL_KEY: Record<string, string> = { dormitory: 'walk.kindDormitory', library: 'walk.kindLibrary', sports: 'walk.kindSports', academic: 'walk.kindAcademic', student_life: 'walk.kindStudentLife', other: 'walk.kindOther' }

/** Street-level panorama: Google Street View Embed (free) or MapillaryJS (free token). */
function Panorama({ lat, lon, t }: { lat: number; lon: number; t: (key: string) => string }) {
  const box = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<'idle' | 'ok' | 'none'>('idle')
  useEffect(() => {
    if (!MTOKEN || GKEY) return
    let viewer: { remove?: () => void; moveTo?: (id: string) => Promise<unknown> } | null = null
    let alive = true
    ;(async () => {
      try {
        const d = 0.004
        const r = await fetch(`https://graph.mapillary.com/images?access_token=${MTOKEN}&fields=id,computed_geometry&bbox=${lon - d},${lat - d},${lon + d},${lat + d}&limit=30`)
        const j = await r.json()
        const imgs: { id: string; computed_geometry?: { coordinates: [number, number] } }[] = j.data ?? []
        if (!imgs.length) { setState('none'); return }
        imgs.sort((a, b) => { const da = a.computed_geometry ? Math.hypot(a.computed_geometry.coordinates[0] - lon, a.computed_geometry.coordinates[1] - lat) : 9; const db = b.computed_geometry ? Math.hypot(b.computed_geometry.coordinates[0] - lon, b.computed_geometry.coordinates[1] - lat) : 9; return da - db })
        const mod = await import('mapillary-js')
        await import('mapillary-js/dist/mapillary.css')
        if (!alive || !box.current) return
        viewer = new mod.Viewer({ accessToken: MTOKEN, container: box.current, imageId: imgs[0].id, component: { cover: false } })
        setState('ok')
      } catch { setState('none') }
    })()
    return () => { alive = false; viewer?.remove?.() }
  }, [lat, lon])

  if (GKEY) {
    return <iframe title="streetview" className="w-full h-[420px] rounded-lg border border-line" loading="lazy" allowFullScreen
      src={`https://www.google.com/maps/embed/v1/streetview?key=${GKEY}&location=${lat},${lon}&heading=0&pitch=0&fov=90`} />
  }
  if (MTOKEN) {
    return (
      <div className="relative">
        <div ref={box} className="w-full h-[420px] rounded-lg border border-line bg-black overflow-hidden" />
        {state === 'idle' && <div className="absolute inset-0 grid place-items-center text-white/70 text-sm">{t('walk.searchingMapillary')}</div>}
        {state === 'none' && <div className="absolute inset-0 grid place-items-center text-white/70 text-sm">{t('walk.noMapillaryNearby')}</div>}
      </div>
    )
  }
  return (
    <div className="card topo-soft p-8 text-center">
      <div className="relative">
        <KeyRound className="mx-auto text-muted" />
        <div className="mt-2 font-bold">{t('walk.panoramasDisabled')}</div>
        <div className="mt-1 text-sm text-muted max-w-xl mx-auto">{t('walk.addKey1')} <span className="mono">VITE_GOOGLE_MAPS_KEY</span> {t('walk.addKey2')} <span className="mono">VITE_MAPILLARY_TOKEN</span> {t('walk.addKey3')} <span className="mono">frontend/.env</span> {t('walk.addKey4')}</div>
      </div>
    </div>
  )
}

export function WalkTab({ uni, campus, photos, walk, onOpen }: { uni: University; campus?: Campus | null; photos: Photo[]; walk: Photo[]; onOpen: (p: Photo) => void }) {
  const lang = useLang()
  const t = useT()
  const stops = [{ id: 'entrance', label: t('walk.entranceLabel'), lat: uni.lat!, lon: uni.lon! },
    ...(campus?.buildings ?? []).filter((b) => b.kind !== 'other' && (b.name || b.name_en)).slice(0, 8).map((b) => ({ id: b.osm_id, label: `${b.name_en || b.name} · ${t(KIND_LABEL_KEY[b.kind])}`, lat: b.lat, lon: b.lon }))]
  const [stop, setStop] = useState(stops[0])
  const three = photos.filter((p) => p.level === 'verified').slice(0, 8)
  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <h3 className="caps text-muted flex items-center gap-1.5"><Footprints size={13} /> {t('walk.title')}</h3>
          <div className="flex flex-wrap gap-1 ml-2">{stops.map((s) => <button key={s.id} onClick={() => setStop(s)} className={`filter !h-7 ${stop.id === s.id ? 'filter-active' : ''}`}><Building2 size={12} />{s.label}</button>)}</div>
        </div>
        {uni.lat != null && uni.lon != null && <Panorama key={stop.id} lat={stop.lat} lon={stop.lon} t={t} />}
        <div className="mt-2 text-[11px] text-muted">{t('walk.panoramaCaption')}</div>
      </div>

      <div>
        <h3 className="caps text-muted flex items-center gap-1.5 mb-3"><Move3d size={13} /> {t('walk.threeDTitle')} <span className="normal-case tracking-normal font-normal">· {t('walk.threeDSubtitle')}</span></h3>
        {three.length === 0 ? <div className="text-sm text-muted">{t('walk.no3dPhotos')}</div> : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {three.map((p) => (
              <figure key={p.id} className="m-0">
                <button onClick={() => onOpen(p)} className="block w-full rounded-lg overflow-hidden bg-slate-100 cursor-pointer"><DepthPhoto src={thumbUrl(p)} depthSrc={`${API_BASE}/api/depth/${p.id}.png`} alt={p.title ?? ''} /></button>
                <SourceLink p={p} />
                <figcaption className="mt-1.5 flex justify-between text-xs"><span className="caps text-ink-2">{catLabel(p.category, lang)}</span><span className="mono text-verified">{Math.round(p.confidence * 100)}%</span></figcaption>
              </figure>
            ))}
          </div>
        )}
        <div className="mt-2 text-[11px] text-muted">{t('walk.parallaxCaption')}</div>
      </div>

      {walk.length > 0 && (
        <div>
          <h3 className="caps text-muted mb-3">{t('walk.mapillaryInsideTitle')}</h3>
          <div className="flex gap-3 overflow-auto pb-2">{walk.map((p) => (
            <div key={p.id} className="shrink-0 w-64">
              <button onClick={() => onOpen(p)} className="block w-full aspect-[4/3] rounded-lg overflow-hidden bg-slate-100 cursor-pointer"><img src={thumbUrl(p)} alt="" className="w-full h-full object-cover" /></button>
              <SourceLink p={p} />
            </div>
          ))}</div>
        </div>
      )}
    </div>
  )
}
