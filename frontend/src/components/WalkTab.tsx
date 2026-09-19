import { useEffect, useRef, useState } from 'react'
import { Footprints, KeyRound, Building2, Move3d } from 'lucide-react'
import type { Campus, Photo, University } from '../lib/types'
import { API_BASE, thumbUrl } from '../lib/api'
import { DepthPhoto } from './DepthPhoto'
import { SourceLink } from './SourceLink'
import { catLabel, useLang, useT } from '../lib/i18n'
import { GOOGLE_3D_KEY, loadStreetView } from '../lib/gmaps'

const KIND_LABEL_KEY: Record<string, string> = { dormitory: 'walk.kindDormitory', library: 'walk.kindLibrary', sports: 'walk.kindSports', academic: 'walk.kindAcademic', student_life: 'walk.kindStudentLife', other: 'walk.kindOther' }

/** Compass bearing from a to b, degrees: the panorama opens facing the building. */
function bearing(a: { lat: number; lng: number }, b: { lat: number; lng: number }): number {
  const r = Math.PI / 180
  const y = Math.sin((b.lng - a.lng) * r) * Math.cos(b.lat * r)
  const x = Math.cos(a.lat * r) * Math.sin(b.lat * r) - Math.sin(a.lat * r) * Math.cos(b.lat * r) * Math.cos((b.lng - a.lng) * r)
  return (Math.atan2(y, x) / r + 360) % 360
}

/** Street-level panorama: Google Street View through the same Maps JavaScript API and key as the 3D map. */
function Panorama({ lat, lon, t }: { lat: number; lon: number; t: (key: string) => string }) {
  const lang = useLang()
  const box = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<'idle' | 'ok' | 'none'>('idle')
  useEffect(() => {
    if (!GOOGLE_3D_KEY) return
    let alive = true
    let pano: { setVisible?: (v: boolean) => void } | null = null
    setState('idle')
    loadStreetView(lang).then(async (sv) => {
      const { data } = await new sv.StreetViewService().getPanorama({
        location: { lat, lng: lon }, radius: 250, preference: sv.StreetViewPreference.NEAREST, sources: [sv.StreetViewSource.OUTDOOR],
      })
      if (!alive || !box.current) return
      const at = data.location.latLng.toJSON()
      pano = new sv.StreetViewPanorama(box.current, {
        pano: data.location.pano, pov: { heading: bearing(at, { lat, lng: lon }), pitch: 4 }, zoom: 0,
        addressControl: false, motionTracking: false, motionTrackingControl: false, fullscreenControl: true,
      })
      setState('ok')
    }).catch(() => { if (alive) setState('none') })   // ZERO_RESULTS: no panorama within reach
    return () => { alive = false; pano?.setVisible?.(false) }
  }, [lat, lon, lang])

  if (!GOOGLE_3D_KEY) {
    return (
      <div className="card topo-soft p-8 text-center">
        <div className="relative">
          <KeyRound className="mx-auto text-muted" />
          <div className="mt-2 font-bold">{t('walk.panoramasDisabled')}</div>
          <div className="mt-1 text-sm text-muted max-w-xl mx-auto">{t('walk.addKey')}</div>
        </div>
      </div>
    )
  }
  return (
    <div className="relative">
      <div ref={box} className="w-full h-[420px] rounded-lg border border-line bg-black overflow-hidden" />
      {state === 'idle' && <div className="absolute inset-0 grid place-items-center text-white/70 text-sm">{t('walk.searching')}</div>}
      {state === 'none' && <div className="absolute inset-0 grid place-items-center text-white/70 text-sm">{t('walk.noPanorama')}</div>}
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
