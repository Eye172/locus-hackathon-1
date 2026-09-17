import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Box, ChevronLeft, ChevronRight, ExternalLink, Map as MapIcon } from 'lucide-react'
import { Link } from 'react-router-dom'
import { DepthPhoto } from './DepthPhoto'
import { API_BASE } from '../lib/api'
import type { Photo } from '../lib/types'
import { catLabel, useLang, useT } from '../lib/i18n'

/**
 * The arrival scene: after the fly-through, the campus itself instead of a map — the best verified photos with a
 * depth-map parallax and a slow camera drift, cross-fading like a title sequence. Real photos only; each one keeps
 * its source link. `photos` come from the cached profile or from the live stream while it is still collecting.
 */
const HERO_CATS = new Set(['campus', 'dormitory', 'library', 'sports', 'student_life', 'classroom', 'lab'])
const HOLD_MS = 5200

export function pickHero(photos: Photo[], limit = 6): Photo[] {
  const seen = new Set<string>()
  const ok = photos.filter((p) => !(p as { rejected?: boolean }).rejected && HERO_CATS.has(p.category)
    && (p.level === 'verified' || p.level === 'likely') && !p.outdated && p.width >= 500)
  // exteriors of the campus first, then dorms/libraries/sport; interiors and street-level car shots last
  const score = (p: Photo) => p.confidence
    + ({ campus: 0.35, dormitory: 0.12, library: 0.1, sports: 0.06, student_life: 0.0, classroom: -0.18, lab: -0.2 } as Record<string, number>)[p.category]
    + (p.source === 'official' || p.source.startsWith('commons') || p.source === 'wikipedia' ? 0.08 : 0) + (p.source === 'mapillary' ? -0.2 : 0)
    + (p.width > p.height ? 0.1 : -0.12) + (p.width >= 1000 ? 0.08 : 0) + (p.level === 'verified' ? 0.1 : 0) - Math.min(0.2, p.junk_score)
    + (p.quality != null ? (p.quality - 1.5) * 0.12 : 0) + (p.featured ? 0.05 : 0) + (p.ai?.flags.length ? -0.15 : 0)
  return ok.sort((a, b) => score(b) - score(a)).filter((p) => !seen.has(p.id) && seen.add(p.id)).slice(0, limit)
}

export const heroUrl = (qid: string, id: string, src?: string) => `${API_BASE}/api/hero/${qid}/${id}.jpg${src ? `?src=${encodeURIComponent(src)}` : ''}`
export const depthUrl = (id: string) => `${API_BASE}/api/depth/${id}.png`

export function CampusReveal({ qid, name, city, photos, onOpen, onMap, visible = true, inScene = false }:
  { qid: string; name: string; city?: string | null; photos: Photo[]; onOpen: () => void; onMap: () => void; visible?: boolean
    /** opened from the Google 3D scene: «3D map» returns there, no link to the separate page */
    inScene?: boolean }) {
  const t = useT()
  const lang = useLang()
  const [idx, setIdx] = useState(0)
  const [prev, setPrev] = useState<number | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const n = photos.length
  const cur = photos[Math.min(idx, n - 1)]

  const go = (next: number) => {
    if (n < 2) return
    setPrev(idx)
    setIdx(((next % n) + n) % n)
    window.setTimeout(() => setPrev(null), 1300)
  }
  useEffect(() => {
    window.clearTimeout(timer.current)
    if (n > 1 && visible) timer.current = window.setTimeout(() => go(idx + 1), HOLD_MS)
    return () => window.clearTimeout(timer.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idx, n, visible])
  useEffect(() => {  // warm the next frame so the cross-fade never waits for the network
    const nx = photos[(idx + 1) % n]
    if (nx) { new Image().src = heroUrl(qid, nx.id, nx.url); new Image().src = depthUrl(nx.id) }
  }, [idx, n, photos, qid])
  useEffect(() => {
    if (!visible) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'ArrowRight') go(idx + 1); else if (e.key === 'ArrowLeft') go(idx - 1); else if (e.key === 'Escape') onMap() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idx, n, visible])

  if (!cur) return null
  return (
    <div className={`absolute inset-0 z-40 bg-black select-none ${visible ? '' : 'opacity-0 pointer-events-none'}`} aria-hidden={!visible}>
      {visible && <div className="absolute inset-0 z-[60] bg-white pointer-events-none reveal-white" />}
      {prev != null && photos[prev] && (
        <div className="absolute inset-0">
          <DepthPhoto key={`p-${photos[prev].id}`} src={heroUrl(qid, photos[prev].id, photos[prev].url)} depthSrc={depthUrl(photos[prev].id)} cover auto strength={0.06} className="absolute inset-0 w-full h-full" />
        </div>
      )}
      <div className={`absolute inset-0 ${prev != null ? 'reveal-fade' : ''}`}>
        <DepthPhoto key={`c-${cur.id}`} src={heroUrl(qid, cur.id, cur.url)} depthSrc={depthUrl(cur.id)} cover auto strength={0.06} className="absolute inset-0 w-full h-full" alt={cur.title ?? name} />
      </div>
      {/* legibility gradients, no blur over the photo itself */}
      <div className="absolute inset-x-0 top-0 h-40 bg-gradient-to-b from-black/70 to-transparent pointer-events-none" />
      <div className="absolute inset-x-0 bottom-0 h-56 bg-gradient-to-t from-black/85 via-black/40 to-transparent pointer-events-none" />

      <div className="absolute top-6 left-6 right-6 flex items-start justify-between gap-4 text-white">
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-[0.18em] text-blue-200/80">{t('reveal.title')}</div>
          <div className="mt-1 text-2xl sm:text-4xl font-extrabold leading-tight drop-shadow-[0_2px_14px_rgba(0,0,0,0.6)] truncate">{name}</div>
          {city && <div className="text-sm text-white/75">{city}</div>}
        </div>
        <div className="flex gap-2 shrink-0">
          {!inScene && <Link to={`/map3d/${qid}`} className="btn-ghost !border-white/20 !text-white bg-black/30 backdrop-blur" title={t('m3d.open')}><Box size={15} /> <span className="max-sm:hidden">{t('m3d.open')}</span></Link>}
          <button onClick={onMap} className="btn-ghost !border-white/20 !text-white bg-black/30 backdrop-blur" title={t('reveal.map')}><MapIcon size={15} /> <span className="max-sm:hidden">{t('reveal.map')}</span></button>
        </div>
      </div>

      <div className="absolute left-6 right-6 bottom-6 flex flex-col sm:flex-row sm:items-end justify-between gap-4 text-white">
        <div className="min-w-0 max-w-xl">
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-white/70">
            <span className="rounded px-1.5 py-0.5 bg-white/15">{catLabel(cur.category, lang)}</span>
            <span>{cur.source_label}{cur.date ? ` · ${cur.date}` : ''}</span>
          </div>
          {cur.title && <div className="mt-1 text-sm text-white/85 truncate">{cur.title}</div>}
          <a href={cur.page_url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs text-blue-200 hover:text-white">
            {t('reveal.source')} <ExternalLink size={11} />
          </a>
          <div className="mt-2 text-[11px] text-white/55">{t('reveal.real')}</div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {n > 1 && (
            <div className="flex items-center gap-1 mr-1">
              <button onClick={() => go(idx - 1)} className="btn-icon !border-white/20 !text-white bg-black/30"><ChevronLeft size={15} /></button>
              <span className="mono text-xs text-white/70 w-12 text-center">{idx + 1} / {n}</span>
              <button onClick={() => go(idx + 1)} className="btn-icon !border-white/20 !text-white bg-black/30"><ChevronRight size={15} /></button>
            </div>
          )}
          <button onClick={onOpen} className="btn-primary">{t('globe.open')} <ArrowRight size={16} /></button>
        </div>
      </div>
      {n > 1 && (
        <div className="absolute left-0 right-0 bottom-0 h-0.5 bg-white/10">
          <div key={idx} className="h-full bg-white/70 reveal-progress" style={{ animationDuration: `${HOLD_MS}ms` }} />
        </div>
      )}
    </div>
  )
}
