import { useEffect, useState } from 'react'
import { X, ExternalLink, Flag, Heart, ListPlus, Check, ChevronLeft, ChevronRight, ChevronDown, Move3d } from 'lucide-react'
import type { Photo } from '../lib/types'
import { CATEGORIES } from '../lib/types'
import { api, thumbUrl, API_BASE } from '../lib/api'
import { DepthPhoto } from './DepthPhoto'
import { catLabel, useLang, useT } from '../lib/i18n'
import { CoverageDot, OutdatedChip } from './Badges'
import { store, useStoreVersion } from '../lib/store'
import { SourceLink, sourceName } from './SourceLink'
import { Map3DInset } from './Map3DInset'

const levelBar = (l: Photo['level']) => (l === 'verified' ? 'conf-verified' : l === 'likely' ? 'conf-likely' : 'conf-unverified')

const levelDot = (l: Photo['level']) => (l === 'verified' ? 'bg-verified' : l === 'likely' ? 'bg-likely' : 'bg-unverified')

/** A photo with a quiet caption: where it came from, how sure we are. The category shows only in mixed grids. */
export function PhotoTile({ p, qid, onOpen, large, showCategory }: { p: Photo; qid: string; onOpen: (p: Photo) => void; large?: boolean; showCategory?: boolean }) {
  useStoreVersion()
  const lang = useLang()
  const t = useT()
  const fav = store.favorites(qid).includes(p.id)
  return (
    <figure className={`group relative m-0 min-w-0 ${large ? 'col-span-2 row-span-2' : ''}`}>
      <button onClick={() => onOpen(p)} className={`block w-full relative rounded-lg overflow-hidden bg-soft cursor-pointer aspect-[4/3]`}>
        <img src={thumbUrl(p)} alt={p.title ?? ''} loading="lazy" className="absolute inset-0 w-full h-full object-cover transition-[filter] duration-200 group-hover:brightness-[0.9]" />
        <div className="absolute top-2 left-2 flex gap-1">{p.outdated && <OutdatedChip />}{p.preliminary && <span className="chip bg-white/90 text-ink-2">предварительно</span>}</div>
        {p.similar.length > 0 && <span className="absolute top-2 right-2 chip bg-black/50 text-white" title="Похожие кадры скрыты">+{p.similar.length}</span>}
      </button>
      <figcaption className="mt-2 flex items-start gap-2 min-w-0">
        <div className="min-w-0 flex-1 leading-4">
          {showCategory && <div className="text-[12.5px] text-ink-2 truncate">{catLabel(p.category, lang)}</div>}
          <SourceLink p={p} />
        </div>
        <span className="inline-flex items-center gap-1.5 text-[11.5px] text-muted shrink-0 pt-px" title={t('level.' + p.level)}>
          <i className={`w-1.5 h-1.5 rounded-full ${levelDot(p.level)}`} />{Math.round(p.confidence * 100)}%
        </span>
        <button onClick={() => store.toggleFavorite(qid, p.id)} aria-label="В избранное"
          className={`shrink-0 cursor-pointer transition-opacity ${fav ? 'text-rose-500' : 'text-faint hover:text-rose-400 opacity-0 group-hover:opacity-100 focus:opacity-100'}`}>
          <Heart size={14} fill={fav ? 'currentColor' : 'none'} />
        </button>
      </figcaption>
    </figure>
  )
}

export function PhotoGrid({ photos, qid, onOpen, empty }: { photos: Photo[]; qid: string; onOpen: (p: Photo) => void; empty?: React.ReactNode }) {
  if (!photos.length) return <>{empty}</>
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-x-4 gap-y-5">
      {photos.map((p) => <PhotoTile key={p.id} p={p} qid={qid} onOpen={onOpen} showCategory />)}
    </div>
  )
}

/** Album view: one rail per category, first photo large. Shows the curator's picks; the rest unfolds on demand. */
export function PhotoAlbums({ photos, qid, onOpen, coverage }: { photos: Photo[]; qid: string; onOpen: (p: Photo) => void; coverage: Record<string, string> }) {
  const lang = useLang()
  const t = useT()
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const curated = photos.some((p) => p.featured)  // profiles built before the curator have no picks
  return (
    <div className="space-y-12">
      {CATEGORIES.map((c) => {
        const list = photos.filter((p) => p.category === c)
        if (!list.length) return null
        const picks = curated ? list.filter((p) => p.featured) : list.slice(0, 11)
        const shown = open[c] ? list : picks.length ? picks : list.slice(0, 6)
        const more = list.length - shown.length
        return (
          <section key={c}>
            <div className="flex items-baseline gap-2.5 mb-4">
              <h3 className="text-[18px] font-semibold tracking-[-0.01em]">{catLabel(c, lang)}</h3>
              <span className="text-[13px] text-muted">{list.length}</span>
              <span className="ml-auto inline-flex items-center gap-1.5 text-[12.5px] text-muted"><CoverageDot level={coverage[c] ?? 'none'} />{list.filter((p) => p.level === 'verified').length} {t('level.verified')}</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-x-3 gap-y-5 grid-flow-dense">
              {shown.map((p, i) => <PhotoTile key={p.id} p={p} qid={qid} onOpen={onOpen} large={i === 0} />)}
            </div>
            {(more > 0 || open[c]) && (
              <button onClick={() => setOpen((o) => ({ ...o, [c]: !o[c] }))} className="btn-text mt-4">
                {open[c] ? t('profile.showLess') : `${t('profile.showAll')} ${list.length}`}<ChevronDown size={15} className={open[c] ? 'rotate-180' : ''} />
              </button>
            )}
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
                        <div key={p.id} className="min-w-0">
                          <button onClick={() => onOpen(p)} className="relative block w-full aspect-[4/3] rounded-md overflow-hidden bg-slate-100 cursor-pointer">
                            <img src={thumbUrl(p)} alt="" loading="lazy" className="w-full h-full object-cover" />
                            <div className="absolute inset-x-0 bottom-0 conf-bar"><i className={levelBar(p.level)} style={{ width: `${Math.round(p.confidence * 100)}%` }} /></div>
                          </button>
                          <SourceLink p={p} />
                        </div>
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

const SRC_NAME: [RegExp, string][] = [
  [/^official/, 'сайт вуза'], [/^web_image|^google/, 'Google Картинки'], [/^commons|^openverse/, 'Wikimedia Commons'],
  [/^instagram/, 'Instagram'], [/^tiktok/, 'TikTok'], [/^youtube/, 'YouTube'], [/^vk/, 'ВКонтакте'], [/^telegram/, 'Telegram'],
  [/^map_review|^places/, 'Google Карты'], [/^mapillary/, 'Mapillary'], [/^flickr/, 'Flickr'], [/^wikipedia/, 'Википедия'],
  [/^city/, 'статья о городе'], [/^external/, 'внешний сборщик'],
]
const srcName = (code: string) => SRC_NAME.find(([rx]) => rx.test(code))?.[1] ?? code

function PassportLabel({ children }: { children: React.ReactNode }) {
  return <h3 className="text-[13px] font-medium text-muted mb-2.5">{children}</h3>
}

function AiBlock({ p }: { p: Photo }) {
  const t = useT()
  const v = p.ai
  if (!v) return <div className="text-[13px] text-muted">{t('passport.aiNone')}</div>
  const dots = (n: number) => <span className="inline-flex gap-1 align-middle">{[0, 1, 2].map((i) => <i key={i} className={`w-1.5 h-1.5 rounded-full ${i < n ? 'bg-ink' : 'bg-line-2'}`} />)}</span>
  const bad = v.place === 'other_place' || v.place === 'not_photo'
  return (
    <div>
      <PassportLabel>{t('passport.ai')} <span className="text-faint font-normal">· {v.model}</span></PassportLabel>
      <dl className="grid grid-cols-[9rem_1fr] gap-x-4 gap-y-1.5 text-[13.5px] items-center">
        <dt className="text-muted first-letter:uppercase">{t('passport.aiPlace')}</dt><dd className={bad ? 'text-unverified font-medium' : 'text-ink'}>{t('ai.place.' + v.place)}</dd>
        <dt className="text-muted first-letter:uppercase">{t('passport.aiSure')}</dt><dd>{dots(v.rel)}</dd>
        <dt className="text-muted first-letter:uppercase">{t('passport.aiUseful')}</dt><dd>{dots(v.q)}</dd>
        {v.era !== 'unknown' && <><dt className="text-muted first-letter:uppercase">{t('passport.aiEra')}</dt><dd>{t('era.' + v.era)}</dd></>}
        {p.ref_similarity != null && <><dt className="text-muted first-letter:uppercase">{t('passport.refSim')}</dt><dd>{Math.round(p.ref_similarity * 100)}%</dd></>}
      </dl>
      {v.flags.length > 0 && <div className="mt-2.5 flex flex-wrap gap-1.5">{v.flags.map((f) => <span key={f} className="text-[12px] px-2 py-0.5 rounded-full border border-line text-ink-2">{t('ai.flag.' + f)}</span>)}</div>}
      {v.why && <div className="mt-2 text-[13px] text-ink-2">«{v.why}»</div>}
    </div>
  )
}

export function PhotoPassport({ p, qid, all, onClose, onOpen, onPrev, onNext }: {
  p: Photo; qid: string; all: Photo[]; onClose: () => void; onOpen: (p: Photo) => void; onPrev?: () => void; onNext?: () => void
}) {
  const t = useT()
  const lang = useLang()
  const [flagged, setFlagged] = useState(false)
  const [planned, setPlanned] = useState(false)
  const [three, setThree] = useState(false)
  useEffect(() => { setThree(false); setFlagged(false); setPlanned(false) }, [p.id])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); if (e.key === 'ArrowLeft') onPrev?.(); if (e.key === 'ArrowRight') onNext?.() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, onPrev, onNext])
  const cats = Object.entries(p.category_scores).sort((a, b) => b[1] - a[1]).slice(0, 4)
  const similar = p.similar.map((s) => all.find((x) => x.id === s.id) ?? null)
  const Row = ({ k, v }: { k: string; v: React.ReactNode }) => <><dt className="text-muted">{k}</dt><dd className="text-ink-2 min-w-0 truncate">{v}</dd></>
  const date = p.date ? p.date : p.date_estimate && p.date_estimate !== 'unknown' ? `≈ ${t('era.' + p.date_estimate)}` : null
  const levelTone = p.level === 'verified' ? 'text-verified' : p.level === 'likely' ? 'text-likely' : 'text-unverified'
  return (
    <div className="fixed inset-0 z-50 bg-[#0B0D12]/80 backdrop-blur-sm flex items-center justify-center p-0 sm:p-6" onClick={onClose}>
      {onPrev && <button onClick={(e) => { e.stopPropagation(); onPrev() }} aria-label="Предыдущее фото" className="hidden sm:grid absolute left-4 top-1/2 -translate-y-1/2 w-11 h-11 place-items-center rounded-full bg-white/10 text-white hover:bg-white/20 cursor-pointer"><ChevronLeft /></button>}
      {onNext && <button onClick={(e) => { e.stopPropagation(); onNext() }} aria-label="Следующее фото" className="hidden sm:grid absolute right-4 top-1/2 -translate-y-1/2 w-11 h-11 place-items-center rounded-full bg-white/10 text-white hover:bg-white/20 cursor-pointer"><ChevronRight /></button>}
      <div className="bg-surface sm:rounded-2xl w-full max-w-6xl h-full sm:h-auto sm:max-h-full overflow-auto pop grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_400px]" onClick={(e) => e.stopPropagation()}>
        <div className="bg-[#0B0D12] flex items-center justify-center min-h-72 lg:min-h-[640px] relative">
          {three ? <DepthPhoto src={thumbUrl(p)} depthSrc={`${API_BASE}/api/depth/${p.id}.png`} alt={p.title ?? ''} className="w-full max-h-[80vh] overflow-hidden" />
            : <img src={thumbUrl(p)} alt={p.title ?? ''} className="max-h-[80vh] w-full object-contain" />}
          {!p.rejected && <button onClick={() => setThree(!three)} className={`absolute top-3 left-3 inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-[13px] font-medium cursor-pointer ${three ? 'bg-white text-ink' : 'bg-white/15 text-white hover:bg-white/25'}`} title="3D-фото: параллакс по карте глубины"><Move3d size={15} /> 3D</button>}
          <button onClick={onClose} aria-label="Закрыть" className="lg:hidden absolute top-3 right-3 grid place-items-center w-9 h-9 rounded-full bg-white/15 text-white"><X size={18} /></button>
        </div>
        <div className="p-6 space-y-7 text-[14px] min-w-0">
          <div>
            <div className="flex items-start gap-3">
              <div className="flex-1 min-w-0">
                <div className="text-[13px] text-muted">{catLabel(p.category, lang)}{p.secondary ? ` / ${catLabel(p.secondary, lang)}` : ''}</div>
                <h2 className="mt-1 text-[18px] font-semibold leading-snug tracking-[-0.01em] line-clamp-3">{p.title || p.source_label}</h2>
              </div>
              <button onClick={onClose} aria-label="Закрыть" className="hidden lg:grid btn-icon !h-9 !w-9 shrink-0"><X size={17} /></button>
            </div>
            <div className="mt-3 flex items-center gap-3 text-[13.5px]">
              <span className={`font-semibold ${levelTone}`}>{Math.round(p.confidence * 100)}% · {t('level.' + p.level)}</span>
              {date && <span className="text-muted">{date}</span>}
            </div>
            {p.reject_reason && <div className="mt-2 text-[13.5px] text-unverified">Отклонено: {p.reject_reason}</div>}
            <div className="mt-5 flex flex-wrap gap-2">
              <a href={p.page_url} target="_blank" rel="noreferrer" className="btn-primary max-w-full"><ExternalLink size={16} className="shrink-0" /> <span className="truncate">{sourceName(p) || t('passport.source')}</span></a>
              <button className="btn-ghost" disabled={planned} onClick={() => { store.addPlan(qid, p.building || p.title || catLabel(p.category, lang), p.id); setPlanned(true) }}>{planned ? <Check size={16} /> : <ListPlus size={16} />} В план</button>
              <button className="btn-ghost !text-unverified" disabled={flagged} title={t('passport.flag')} onClick={async () => { await api.flag(qid, p.id, 'not_this_university').catch(() => {}); setFlagged(true) }}>
                <Flag size={16} /> {flagged ? 'Учтём' : t('passport.flag')}
              </button>
            </div>
          </div>

          <div>
            <PassportLabel>{t('passport.why')}</PassportLabel>
            <ul className="space-y-1.5">
              {p.signals.map((s) => (
                <li key={s.key} className="grid grid-cols-[3rem_1fr] gap-3 leading-snug">
                  <span className={`tnum text-[13px] text-right ${s.weight >= 0 ? 'text-verified' : 'text-unverified'}`}>{s.weight >= 0 ? '+' : '−'}{Math.abs(s.weight).toFixed(2)}</span>
                  <span className="text-ink-2">{s.label}{s.value ? <span className="text-muted"> · {s.value}</span> : null}</span>
                </li>
              ))}
              <li className="grid grid-cols-[3rem_1fr] gap-3 border-t border-line pt-2 mt-2 font-semibold">
                <span className="tnum text-[13px] text-right">{p.confidence.toFixed(2)}</span><span>{t('level.' + p.level)}</span>
              </li>
            </ul>
          </div>

          <AiBlock p={p} />

          {p.lat != null && p.lon != null && (
            <div>
              <PassportLabel>{t('passport.geo')}{p.geo_distance_m != null ? ` · ${p.geo_distance_m} м от кампуса` : ''}</PassportLabel>
              {/* the app's one 3D map: the same scene as the arrival on the main page, the photo at its place */}
              <Map3DInset qid={qid} at={{ lat: p.lat, lng: p.lon }} range={650} className="h-56"
                marker={{ lat: p.lat, lng: p.lon, thumb: thumbUrl(p), level: p.level }} />
            </div>
          )}

          {similar.some(Boolean) && (
            <div>
              <PassportLabel>Похожие кадры, скрытые из сетки</PassportLabel>
              <div className="flex gap-2 overflow-auto">
                {similar.map((s, i) => s ? (
                  <button key={s.id} onClick={() => onOpen(s)} className="shrink-0 w-24 aspect-[4/3] rounded-md overflow-hidden bg-soft cursor-pointer" title={`${Math.round((p.similar[i].similarity ?? 0) * 100)}%`}><img src={thumbUrl(s)} alt="" className="w-full h-full object-cover" /></button>
                ) : (
                  <a key={i} href={p.similar[i].page_url} target="_blank" rel="noreferrer" className="shrink-0 w-24 aspect-[4/3] rounded-md overflow-hidden bg-soft"><img src={thumbUrl(p.similar[i])} alt="" className="w-full h-full object-cover" /></a>
                ))}
              </div>
            </div>
          )}

          <details className="group border-t border-line pt-4">
            <summary className="cursor-pointer list-none flex items-center gap-2 text-[13.5px] font-medium text-ink-2 hover:text-ink">
              Метаданные и категории<ChevronDown size={16} className="ml-auto text-muted group-open:rotate-180 transition-transform" />
            </summary>
            <dl className="mt-4 grid grid-cols-[7.5rem_1fr] gap-x-4 gap-y-1.5 text-[13px]">
              <Row k="Найдено в" v={[...new Set(p.sources.map(srcName))].join(', ')} />
              <Row k={t('passport.author')} v={p.author || '—'} />
              <Row k={t('passport.license')} v={p.license || '—'} />
              <Row k={t('passport.date')} v={p.date ? `${p.date} (${p.date_source})` : date ? `${date} (${t('passport.estimate')})` : '—'} />
              <Row k={t('passport.size')} v={`${p.width} × ${p.height}`} />
              {p.building && <Row k={t('passport.building')} v={`${p.building} (${p.building_kind})`} />}
              {p.lat != null && <Row k="Координаты" v={`${p.lat.toFixed(5)}, ${p.lon?.toFixed(5)}`} />}
              <Row k="Хэши" v={<span className="code text-[11px] text-muted" title={`sha1 ${p.sha1}`}>{p.phash} · {p.dhash}</span>} />
            </dl>
            <div className="mt-4 space-y-1.5">
              {cats.map(([c, v]) => (
                <div key={c} className="grid grid-cols-[7.5rem_1fr_2.5rem] items-center gap-3 text-[13px]"><span className="truncate text-ink-2">{catLabel(c, lang)}</span><span className="h-1 rounded-full bg-soft overflow-hidden"><i className="block h-full bg-ink" style={{ width: `${v * 100}%` }} /></span><span className="text-right text-muted">{Math.round(v * 100)}%</span></div>
              ))}
              {p.junk_score > 0.2 && <div className="text-[12.5px] text-muted">похоже на не-фото: {Math.round(p.junk_score * 100)}%</div>}
            </div>
          </details>
        </div>
      </div>
    </div>
  )
}
