import { useEffect, useState } from 'react'
import { X, ExternalLink, Flag, Heart, Layers, MapPin, ListPlus, Check, ChevronLeft, ChevronRight, Move3d } from 'lucide-react'
import { MapContainer, TileLayer, CircleMarker, Polygon } from 'react-leaflet'
import type { Campus, Photo } from '../lib/types'
import { CATEGORIES } from '../lib/types'
import { api, thumbUrl, API_BASE } from '../lib/api'
import { DepthPhoto } from './DepthPhoto'
import { catLabel, useLang, useT } from '../lib/i18n'
import { ConfidenceBar, CoverageDot, OutdatedChip, SourceChip } from './Badges'
import { store, useStoreVersion } from '../lib/store'

const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, '') } catch { return '' } }
const levelText = (l: Photo['level']) => (l === 'verified' ? 'text-verified' : l === 'likely' ? 'text-likely' : 'text-unverified')
const levelBar = (l: Photo['level']) => (l === 'verified' ? 'conf-verified' : l === 'likely' ? 'conf-likely' : 'conf-unverified')

export function PhotoTile({ p, qid, onOpen, large }: { p: Photo; qid: string; onOpen: (p: Photo) => void; large?: boolean }) {
  useStoreVersion()
  const lang = useLang()
  const fav = store.favorites(qid).includes(p.id)
  return (
    <figure className={`group relative m-0 pop ${large ? 'col-span-2 row-span-2' : ''}`}>
      <button onClick={() => onOpen(p)} className="block w-full relative aspect-[4/3] rounded-lg overflow-hidden bg-slate-100 cursor-pointer">
        <img src={thumbUrl(p)} alt={p.title ?? ''} loading="lazy" className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-[1.03]" />
        <div className="absolute inset-x-0 bottom-0 conf-bar"><i className={levelBar(p.level)} style={{ width: `${Math.round(p.confidence * 100)}%` }} /></div>
        <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/0 to-black/0 opacity-0 group-hover:opacity-100 transition-opacity" />
        <div className="absolute left-2.5 right-2.5 bottom-2.5 text-white text-xs opacity-0 group-hover:opacity-100 transition-opacity">
          <div className="truncate font-medium">{p.title || p.source_label}</div>
          <div className="flex items-center gap-2 opacity-80 mono text-[11px]">{p.date && <span>{p.date}</span>}{p.sources_count > 1 && <span className="inline-flex items-center gap-1"><Layers size={11} />{p.sources_count}</span>}{p.geo_inside && <span className="inline-flex items-center gap-1"><MapPin size={11} />кампус</span>}</div>
        </div>
        <div className="absolute top-2 left-2 flex gap-1">{p.outdated && <OutdatedChip />}{p.preliminary && <span className="chip bg-white/90 text-brand">предв.</span>}</div>
        {p.similar.length > 0 && <span className="absolute top-2 right-2 chip bg-black/55 text-white mono">+{p.similar.length}</span>}
      </button>
      <figcaption className="mt-1.5 flex items-center justify-between gap-2">
        <span className="caps text-ink-2 truncate">{catLabel(p.category, lang)} <span className={`ml-1 ${p.is_brochure ? 'text-brochure' : 'text-reality'}`}>·</span> <span className="mono normal-case tracking-normal text-muted font-normal">{host(p.page_url)}</span></span>
        <span className="flex items-center gap-2 shrink-0">
          <span className={`mono text-[11px] ${levelText(p.level)}`}>{Math.round(p.confidence * 100)}%</span>
          <button onClick={() => store.toggleFavorite(qid, p.id)} className={`cursor-pointer ${fav ? 'text-rose-500' : 'text-slate-300 hover:text-rose-400'}`} title="В избранное"><Heart size={14} fill={fav ? 'currentColor' : 'none'} /></button>
        </span>
      </figcaption>
    </figure>
  )
}

export function PhotoGrid({ photos, qid, onOpen, empty }: { photos: Photo[]; qid: string; onOpen: (p: Photo) => void; empty?: React.ReactNode }) {
  if (!photos.length) return <>{empty}</>
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-x-4 gap-y-5">
      {photos.map((p) => <PhotoTile key={p.id} p={p} qid={qid} onOpen={onOpen} />)}
    </div>
  )
}

/** Album view: one rail per category, first photo large. */
export function PhotoAlbums({ photos, qid, onOpen, coverage }: { photos: Photo[]; qid: string; onOpen: (p: Photo) => void; coverage: Record<string, string> }) {
  const lang = useLang()
  return (
    <div className="space-y-8">
      {CATEGORIES.map((c) => {
        const list = photos.filter((p) => p.category === c)
        if (!list.length) return null
        return (
          <section key={c}>
            <div className="flex items-baseline gap-3 mb-3">
              <h3 className="text-lg font-bold">{catLabel(c, lang)}</h3>
              <span className="mono text-xs text-muted">{list.length}</span>
              <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-muted"><CoverageDot level={coverage[c] ?? 'none'} />{list.filter((p) => p.level === 'verified').length} подтверждено</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-x-4 gap-y-5 grid-flow-dense">
              {list.slice(0, 11).map((p, i) => <PhotoTile key={p.id} p={p} qid={qid} onOpen={onOpen} large={i === 0} />)}
            </div>
          </section>
        )
      })}
    </div>
  )
}

export function EmptyState({ category, sources }: { category?: string; sources: string[] }) {
  const t = useT()
  const lang = useLang()
  return (
    <div className="card topo-soft p-10 text-center">
      <div className="relative">
        <div className="text-lg font-bold">{t('profile.empty')}{category ? `: ${catLabel(category, lang).toLowerCase()}` : ''}</div>
        <div className="mt-2 text-sm text-muted">{t('profile.emptyHint')} <span className="mono">{sources.length ? sources.join(' · ') : '—'}</span></div>
        <div className="mt-1 text-xs text-muted">{t('profile.emptyHonest')}</div>
      </div>
    </div>
  )
}

export function BrochureVsReality({ photos, onOpen }: { photos: Photo[]; onOpen: (p: Photo) => void }) {
  const t = useT()
  const lang = useLang()
  const cats = CATEGORIES.filter((c) => photos.some((p) => p.category === c))
  if (!cats.length) return <EmptyState sources={[]} />
  return (
    <div className="space-y-8">
      <div className="grid grid-cols-2 gap-4 text-sm">
        <div className="border-l-2 border-brochure pl-3"><div className="caps text-brochure">{t('bvr.brochure')}</div><div className="text-muted text-xs mt-0.5">{t('bvr.brochureHint')}</div></div>
        <div className="border-l-2 border-reality pl-3"><div className="caps text-reality">{t('bvr.reality')}</div><div className="text-muted text-xs mt-0.5">{t('bvr.realityHint')}</div></div>
      </div>
      {cats.map((c) => {
        const bro = photos.filter((p) => p.category === c && p.is_brochure)
        const rea = photos.filter((p) => p.category === c && !p.is_brochure)
        return (
          <section key={c}>
            <h3 className="font-bold mb-2 flex items-baseline gap-2">{catLabel(c, lang)} <span className="mono text-xs text-muted">{bro.length} / {rea.length}</span></h3>
            <div className="grid grid-cols-2 gap-4">
              {[bro, rea].map((list, i) => (
                <div key={i} className={`rounded-lg p-2 ${i === 0 ? 'bg-brochure-soft/50' : 'bg-reality-soft/50'}`}>
                  {list.length === 0 ? <div className="text-xs text-muted p-3">нет фото</div> : (
                    <div className="grid grid-cols-2 gap-2">
                      {list.slice(0, 6).map((p) => (
                        <button key={p.id} onClick={() => onOpen(p)} className="relative aspect-[4/3] rounded-md overflow-hidden bg-slate-100 cursor-pointer">
                          <img src={thumbUrl(p)} alt="" loading="lazy" className="w-full h-full object-cover" />
                          <div className="absolute inset-x-0 bottom-0 conf-bar"><i className={levelBar(p.level)} style={{ width: `${Math.round(p.confidence * 100)}%` }} /></div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}

export function PhotoPassport({ p, qid, campus, all, onClose, onOpen, onPrev, onNext }: {
  p: Photo; qid: string; campus?: Campus | null; all: Photo[]; onClose: () => void; onOpen: (p: Photo) => void; onPrev?: () => void; onNext?: () => void
}) {
  const t = useT()
  const lang = useLang()
  const [flagged, setFlagged] = useState(false)
  const [planned, setPlanned] = useState(false)
  const [three, setThree] = useState(false)
  useEffect(() => { setThree(false) }, [p.id])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); if (e.key === 'ArrowLeft') onPrev?.(); if (e.key === 'ArrowRight') onNext?.() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, onPrev, onNext])
  const cats = Object.entries(p.category_scores).sort((a, b) => b[1] - a[1]).slice(0, 4)
  const similar = p.similar.map((s) => all.find((x) => x.id === s.id) ?? null)
  const Row = ({ k, v }: { k: string; v: React.ReactNode }) => <><dt className="caps text-muted font-medium">{k}</dt><dd className="text-sm truncate">{v}</dd></>
  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-3 sm:p-6" onClick={onClose}>
      {onPrev && <button onClick={(e) => { e.stopPropagation(); onPrev() }} className="hidden sm:grid absolute left-4 top-1/2 -translate-y-1/2 w-10 h-10 place-items-center rounded-full bg-white/10 text-white hover:bg-white/20 cursor-pointer"><ChevronLeft /></button>}
      {onNext && <button onClick={(e) => { e.stopPropagation(); onNext() }} className="hidden sm:grid absolute right-4 top-1/2 -translate-y-1/2 w-10 h-10 place-items-center rounded-full bg-white/10 text-white hover:bg-white/20 cursor-pointer"><ChevronRight /></button>}
      <div className="bg-surface rounded-xl w-full max-w-5xl max-h-full overflow-auto pop" onClick={(e) => e.stopPropagation()}>
        <div className="grid grid-cols-1 lg:grid-cols-5">
          <div className="lg:col-span-3 bg-black flex items-center justify-center min-h-64 relative">
            {three ? <DepthPhoto src={thumbUrl(p)} depthSrc={`${API_BASE}/api/depth/${p.id}.png`} alt={p.title ?? ''} className="w-full max-h-[72vh] overflow-hidden" />
              : <img src={thumbUrl(p)} alt={p.title ?? ''} className="max-h-[72vh] w-full object-contain" />}
            {!p.rejected && <button onClick={() => setThree(!three)} className={`absolute top-3 left-3 btn-ghost !h-8 !px-2.5 text-xs ${three ? '!bg-white' : '!bg-white/80'}`} title="3D-фото: параллакс по карте глубины"><Move3d size={13} /> 3D</button>}
          </div>
          <div className="lg:col-span-2 p-5 space-y-5 text-sm">
            <div className="flex items-start gap-2">
              <div className="flex-1 min-w-0">
                <div className="caps text-ink-2">{catLabel(p.category, lang)}{p.secondary ? <span className="text-muted"> / {catLabel(p.secondary, lang)}</span> : null}</div>
                <h2 className="mt-1 text-lg font-bold leading-snug">{p.title || p.source_label}</h2>
                <div className="mt-2"><ConfidenceBar level={p.level} confidence={p.confidence} showLabel /></div>
              </div>
              <button onClick={onClose} className="btn-icon !h-9 !w-9"><X size={16} /></button>
            </div>

            <div>
              <h3 className="caps text-muted">{t('passport.why')}</h3>
              <ul className="mt-2 space-y-1.5">
                {p.signals.map((s) => (
                  <li key={s.key} className="flex items-start gap-3">
                    <span className={`mono text-xs w-12 shrink-0 text-right ${s.weight >= 0 ? 'text-verified' : 'text-unverified'}`}>{s.weight >= 0 ? '+' : ''}{s.weight.toFixed(2)}</span>
                    <span className="flex-1 leading-snug">{s.label}{s.value ? <span className="text-muted"> · {s.value}</span> : null}</span>
                  </li>
                ))}
                <li className="flex items-start gap-3 rule pt-1.5 mt-1.5 font-semibold"><span className="mono text-xs w-12 shrink-0 text-right">= {p.confidence.toFixed(2)}</span><span>{t('level.' + p.level)}</span></li>
              </ul>
              {p.reject_reason && <div className="mt-2 text-xs text-unverified">Отклонено: {p.reject_reason}</div>}
            </div>

            <div>
              <h3 className="caps text-muted">{t('passport.categories')}</h3>
              <div className="mt-2 space-y-1.5">
                {cats.map(([c, v]) => (
                  <div key={c} className="flex items-center gap-2 text-xs"><span className="w-28 truncate">{catLabel(c, lang)}</span><div className="flex-1 conf-bar rounded-full overflow-hidden"><i className="bg-ink" style={{ width: `${v * 100}%` }} /></div><span className="w-9 text-right mono text-muted">{Math.round(v * 100)}%</span></div>
                ))}
                {p.junk_score > 0.2 && <div className="text-xs text-muted">не-фото: {Math.round(p.junk_score * 100)}%</div>}
              </div>
            </div>

            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 items-baseline">
              <Row k="источники" v={<span className="flex flex-wrap gap-1">{p.sources.map((s) => <SourceChip key={s} source={s} brochure={s === 'official'} />)}</span>} />
              <Row k={t('passport.author')} v={p.author || '—'} />
              <Row k={t('passport.license')} v={p.license || '—'} />
              <Row k={t('passport.date')} v={p.date ? <span className="mono">{p.date} <span className="text-muted">({p.date_source})</span></span> : '—'} />
              <Row k={t('passport.size')} v={<span className="mono">{p.width}×{p.height}</span>} />
              {p.building && <Row k={t('passport.building')} v={`${p.building} (${p.building_kind})`} />}
              {p.lat != null && <Row k={t('passport.geo')} v={<span className="mono">{p.lat.toFixed(5)}, {p.lon?.toFixed(5)}{p.geo_distance_m != null ? ` · ${p.geo_distance_m} м` : ''}</span>} />}
              <Row k="хэши" v={<span className="mono text-[10px] text-muted" title={`sha1 ${p.sha1}`}>{p.phash} · {p.dhash}</span>} />
            </dl>

            {p.lat != null && p.lon != null && (
              <div className="h-40 rounded-lg overflow-hidden border border-line">
                <MapContainer center={[p.lat, p.lon]} zoom={15} scrollWheelZoom={false} style={{ height: '100%' }} attributionControl={false}>
                  <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                  {campus?.polygon && <Polygon positions={campus.polygon as [number, number][]} pathOptions={{ color: '#1D4ED8', weight: 2, fillOpacity: 0.08 }} />}
                  <CircleMarker center={[p.lat, p.lon]} radius={7} pathOptions={{ color: '#fff', fillColor: p.geo_inside ? '#15803D' : '#B45309', fillOpacity: 1, weight: 2 }} />
                </MapContainer>
              </div>
            )}

            {similar.some(Boolean) && (
              <div>
                <h3 className="caps text-muted">Похожие, скрытые из сетки</h3>
                <div className="mt-2 flex gap-2 overflow-auto">
                  {similar.map((s, i) => s ? (
                    <button key={s.id} onClick={() => onOpen(s)} className="shrink-0 w-24 aspect-[4/3] rounded-md overflow-hidden bg-slate-100 cursor-pointer" title={`${Math.round((p.similar[i].similarity ?? 0) * 100)}%`}><img src={thumbUrl(s)} alt="" className="w-full h-full object-cover" /></button>
                  ) : (
                    <a key={i} href={p.similar[i].page_url} target="_blank" rel="noreferrer" className="shrink-0 w-24 aspect-[4/3] rounded-md overflow-hidden bg-slate-100"><img src={thumbUrl(p.similar[i])} alt="" className="w-full h-full object-cover" /></a>
                  ))}
                </div>
              </div>
            )}

            <div className="flex flex-wrap gap-2 pt-1">
              <a href={p.page_url} target="_blank" rel="noreferrer" className="btn-primary"><ExternalLink size={15} /> {t('passport.source')}</a>
              <button className="btn-ghost" disabled={planned} onClick={() => { store.addPlan(qid, p.building || p.title || catLabel(p.category, lang), p.id); setPlanned(true) }}>{planned ? <Check size={15} /> : <ListPlus size={15} />} В план визита</button>
              <button className="btn-ghost text-unverified" disabled={flagged} onClick={async () => { await api.flag(qid, p.id, 'not_this_university').catch(() => {}); setFlagged(true) }}>
                <Flag size={15} /> {flagged ? t('passport.flagged') : t('passport.flag')}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
