import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { RefreshCw, Share2, GitCompare, Gavel, ExternalLink, Bookmark, BookmarkCheck, ArrowLeft, LayoutGrid, Rows3, Check } from 'lucide-react'
import { streamProfile, thumbUrl, API_BASE } from '../lib/api'
import type { Campus, Photo, Profile as ProfileT, SourceStatus, Stage, University } from '../lib/types'
import { CATEGORIES } from '../lib/types'
import { catLabel, useLang, useT } from '../lib/i18n'
import { CoverageDot, coverageLabel } from '../components/Badges'
import { AgentsStrip, CoverageMatrix, DescriptionBlock, ContextCards, Timeline, JudgePanel, VisitPlan } from '../components/ProfileParts'
import { PhotoGrid, PhotoAlbums, PhotoPassport, BrochureVsReality, EmptyState } from '../components/Photos'
import { CampusMap3D } from '../components/CampusMap3D'
import { ClimateTab } from '../components/ClimateTab'
import { CityTab } from '../components/CityTab'
import { WalkTab } from '../components/WalkTab'
import { api } from '../lib/api'
import type { ContextPack } from '../lib/types'
import { store, useStoreVersion } from '../lib/store'

type Tab = 'photos' | 'bvr' | 'map' | 'climate' | 'city' | 'timeline' | 'walk' | 'rejected' | 'judge'
const FILTERS: string[] = ['all', ...CATEGORIES]

export default function Profile() {
  const { qid = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const nav = useNavigate()
  const t = useT()
  const lang = useLang()
  useStoreVersion()

  const [stages, setStages] = useState<Record<string, Stage>>({})
  const [sources, setSources] = useState<Record<string, SourceStatus>>({})
  const [uni, setUni] = useState<University | null>(null)
  const [campus, setCampus] = useState<Campus | null>(null)
  const [prelim, setPrelim] = useState<Photo[]>([])
  const [profile, setProfile] = useState<ProfileT | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [cached, setCached] = useState(false)
  const [run, setRun] = useState(0)
  const refresh = params.get('refresh') === '1'

  const [tab, setTabState] = useState<Tab>((params.get('tab') as Tab) || 'photos')
  const setTab = (next: Tab) => { setTabState(next); setParams((prev) => { const n = new URLSearchParams(prev); if (next === 'photos') n.delete('tab'); else n.set('tab', next); return n }, { replace: true }) }
  const [view, setView] = useState<'grid' | 'album'>('album')
  const [filter, setFilter] = useState<string>('all')
  const [verifiedOnly, setVerifiedOnly] = useState(false)
  const [recent3, setRecent3] = useState(false)
  const [year, setYear] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(params.get('photo'))
  const [copied, setCopied] = useState(false)
  const [ctx, setCtx] = useState<ContextPack | null>(null)
  useEffect(() => { setCtx(null) }, [qid])
  useEffect(() => {
    if ((tab === 'map' || tab === 'city') && !ctx && profile) api.context(qid).then(setCtx).catch(() => {})
  }, [tab, ctx, profile, qid])

  useEffect(() => {
    setStages({}); setSources({}); setUni(null); setCampus(null); setPrelim([]); setProfile(null); setError(null); setElapsed(0); setCached(false)
    const close = streamProfile(qid, refresh, {
      onStage: (s, e) => { setStages((st) => ({ ...st, [s.key]: s })); setElapsed(e) },
      onUniversity: (u, c) => { setUni(u); setCampus(c) },
      onCampus: (c, u) => { setCampus(c); setUni(u) },
      onSource: (name, s, e) => { setSources((ss) => ({ ...ss, [name]: s })); setElapsed(e) },
      onPhotos: (_src, photos) => setPrelim((pp) => { const ids = new Set(pp.map((p) => p.id)); return [...pp, ...photos.filter((p) => !ids.has(p.id))] }),
      onProfile: (p, c) => {
        setProfile(p); setUni(p.university); setCampus(p.campus ?? null); setCached(c); setElapsed(p.elapsed_ms)
        setStages((st) => (Object.keys(st).length ? st : Object.fromEntries(p.stages.map((s) => [s.key, s]))))
        setSources((ss) => (Object.keys(ss).length ? ss : p.sources_status))
        if (refresh) setParams({})
      },
      onError: (m) => setError(m),
    })
    return close
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qid, run])

  const photos = profile ? profile.photos : prelim
  const shown = useMemo(() => photos.filter((p) =>
    (filter === 'all' || p.category === filter) &&
    (!verifiedOnly || p.level === 'verified') &&
    (!recent3 || (p.year != null && p.year >= new Date().getFullYear() - 3)) &&
    (!year || String(p.year) === year),
  ), [photos, filter, verifiedOnly, recent3, year])
  const counts = useMemo(() => Object.fromEntries(FILTERS.map((f) => [f, f === 'all' ? photos.length : photos.filter((p) => p.category === f).length])), [photos])
  const allForPassport = useMemo(() => (profile ? [...profile.photos, ...profile.rejected] : prelim), [profile, prelim])
  const open = openId ? allForPassport.find((p) => p.id === openId) ?? null : null
  const openPhoto = useCallback((p: Photo) => { setOpenId(p.id); setParams((prev) => { const n = new URLSearchParams(prev); n.set('photo', p.id); return n }, { replace: true }) }, [setParams])
  const closePhoto = useCallback(() => { setOpenId(null); setParams((prev) => { const n = new URLSearchParams(prev); n.delete('photo'); return n }, { replace: true }) }, [setParams])
  const idx = open ? shown.findIndex((p) => p.id === open.id) : -1
  const prev = idx > 0 ? () => openPhoto(shown[idx - 1]) : undefined
  const next = idx >= 0 && idx < shown.length - 1 ? () => openPhoto(shown[idx + 1]) : undefined

  // the share link goes through /s/{qid}: crawlers get Open Graph tags + a preview card, people get redirected here
  const share = async () => { try { const u = new URL(window.location.href); await navigator.clipboard.writeText(`${API_BASE || u.origin}/s/${qid}${u.search}`); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* ignore */ } }
  const saved = !!store.saved()[qid]
  const doRefresh = () => { setParams({ refresh: '1' }); setRun((r) => r + 1) }
  const doneSources = Object.entries(sources).filter(([, s]) => s.status === 'done').map(([, s]) => s.label)

  const tabs: { key: Tab; label: string; badge?: number; hide?: boolean }[] = [
    { key: 'photos', label: t('profile.photos'), badge: photos.length },
    { key: 'bvr', label: t('profile.bvr') },
    { key: 'map', label: t('profile.map') },
    { key: 'walk', label: t('profile.walk') },
    { key: 'climate', label: t('profile.climateTab') },
    { key: 'city', label: t('profile.cityTab') },
    { key: 'timeline', label: t('profile.timeline') },
    { key: 'rejected', label: t('profile.rejected'), badge: profile?.rejected.length },
    { key: 'judge', label: t('profile.judge') },
  ]
  const name = uni ? (uni.names[lang] || uni.name) : ''
  const hero = profile?.photos.find((p) => p.category === 'campus' && p.level === 'verified') ?? profile?.photos[0]

  return (
    <div>
      {/* header */}
      <div className="bg-surface border-b border-line topo-soft">
        <div className="relative mx-auto max-w-7xl px-4 pt-5 pb-4">
          <Link to="/" className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-ink"><ArrowLeft size={13} /> Планета</Link>
          <div className="mt-3 grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-6 items-start">
            <div className="min-w-0 flex gap-5">
              <div className="w-20 h-20 rounded-lg bg-slate-100 overflow-hidden shrink-0 border border-line grid place-items-center">
                {uni?.logo_url ? <img src={uni.logo_url} alt="" className="w-20 h-20 object-contain p-1.5" /> : hero ? <img src={thumbUrl(hero)} alt="" className="w-20 h-20 object-cover" /> : <div className="shimmer w-20 h-20" />}
              </div>
              <div className="min-w-0">
                {uni ? (
                  <>
                    <h1 className="text-[28px] sm:text-[34px] leading-[1.1] font-extrabold">{name}</h1>
                    <div className="mt-1 text-sm text-ink-2">{[uni.names.en && uni.names.en !== name ? uni.names.en : null, uni.description].filter(Boolean).join(' · ')}</div>
                    <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
                      {uni.founded && <div><dt className="caps text-muted">основан</dt><dd className="mono">{uni.founded}</dd></div>}
                      {uni.students && <div><dt className="caps text-muted">студентов</dt><dd className="mono">{uni.students.toLocaleString('ru-RU')}</dd></div>}
                      {uni.city && <div><dt className="caps text-muted">город</dt><dd>{uni.city}{uni.country ? `, ${uni.country}` : ''}</dd></div>}
                      {uni.website && <div><dt className="caps text-muted">сайт</dt><dd><a href={uni.website} target="_blank" rel="noreferrer" className="hover:text-brand inline-flex items-center gap-1">{uni.website.replace(/^https?:\/\//, '').replace(/\/$/, '')}<ExternalLink size={11} /></a></dd></div>}
                      <div><dt className="caps text-muted">wikidata</dt><dd><a href={`https://www.wikidata.org/wiki/${uni.qid}`} target="_blank" rel="noreferrer" className="mono hover:text-brand">{uni.qid}</a></dd></div>
                    </dl>
                  </>
                ) : <div className="space-y-2"><div className="shimmer h-8 w-2/3 rounded" /><div className="shimmer h-4 w-1/3 rounded" /><div className="shimmer h-4 w-1/2 rounded" /></div>}
              </div>
            </div>
            <div className="flex flex-col items-start lg:items-end gap-3">
              <div className="flex gap-2">
                <button className="btn-icon" onClick={doRefresh} title={t('profile.refresh')} disabled={!profile}><RefreshCw size={15} /></button>
                <button className="btn-icon" onClick={share} title={t('profile.share')}>{copied ? <Check size={15} className="text-verified" /> : <Share2 size={15} />}</button>
                <button className="btn-icon" title={saved ? 'Убрать из сохранённых' : 'Сохранить'} disabled={!profile}
                  onClick={() => saved ? store.unsave(qid) : store.save(qid, { name: uni?.name ?? qid, city: uni?.city, savedAt: new Date().toISOString(), photos: photos.length })}>
                  {saved ? <BookmarkCheck size={15} className="text-brand" /> : <Bookmark size={15} />}
                </button>
                <Link to={`/compare?a=${qid}`} className="btn-icon" title={t('profile.compare')}><GitCompare size={15} /></Link>
                <button className={`btn-icon ${tab === 'judge' ? '!bg-ink !text-white' : ''}`} onClick={() => setTab(tab === 'judge' ? 'photos' : 'judge')} title={t('profile.judge')}><Gavel size={15} /></button>
              </div>
              {profile && (
                <div className="text-xs text-ink-2 flex items-center gap-2">
                  <CoverageDot level={profile.coverage.overall} />
                  <span>{t('profile.coverage')}: {coverageLabel(profile.coverage.overall, lang)}</span>
                  <span className="mono text-muted">· {(profile.elapsed_ms / 1000).toFixed(1)} s{cached ? ` · ${t('profile.cached')}` : ''}</span>
                </div>
              )}
            </div>
          </div>
          <AgentsStrip stages={stages} sources={sources} elapsed={elapsed} />
        </div>
      </div>

      <div className="mx-auto max-w-7xl px-4">
        {profile?.partial && <div className="mt-4 rounded-lg bg-likely-soft text-likely px-4 py-2 text-sm">{t('profile.partial')}</div>}
        {error && (
          <div className="card p-6 mt-4 border-unverified/40">
            <div className="font-semibold text-unverified">{t('profile.error')}</div>
            <div className="text-sm text-muted mt-1">{error}</div>
            <button className="btn-primary mt-3" onClick={() => nav('/')}>← Назад к поиску</button>
          </div>
        )}

        <div className="sticky top-14 z-30 bg-canvas/95 backdrop-blur border-b border-line -mx-4 px-4">
          <div className="flex gap-6 overflow-auto">
            {tabs.filter((x) => !x.hide).map((x) => (
              <button key={x.key} onClick={() => setTab(x.key)} className={`tab ${tab === x.key ? 'tab-active' : ''}`}>
                {x.label}{x.badge != null ? <span className="ml-1.5 mono text-xs text-muted">{x.badge}</span> : null}
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-10 py-6">
          <div className="min-w-0 space-y-5">
            {tab === 'photos' && (
              <>
                <div className="flex flex-wrap items-center gap-1.5">
                  {FILTERS.map((f) => (
                    <button key={f} onClick={() => setFilter(f)} className={`filter ${filter === f ? 'filter-active' : ''}`}>
                      {f === 'all' ? t('profile.all') : catLabel(f, lang)} <span className={`mono text-[11px] ${filter === f ? 'text-white/70' : 'text-muted'}`}>{counts[f]}</span>
                    </button>
                  ))}
                  <span className="mx-2 h-5 border-l border-line" />
                  <label className="filter"><input type="checkbox" checked={verifiedOnly} onChange={(e) => setVerifiedOnly(e.target.checked)} className="accent-ink" />{t('profile.onlyVerified')}</label>
                  <label className="filter"><input type="checkbox" checked={recent3} onChange={(e) => setRecent3(e.target.checked)} className="accent-ink" />{t('profile.recent3')}</label>
                  {year && <button className="filter filter-active" onClick={() => setYear(null)}>{year} ×</button>}
                  <span className="ml-auto flex gap-1">
                    <button onClick={() => setView('album')} className={`btn-icon !h-8 !w-8 ${view === 'album' ? '!bg-ink !text-white' : ''}`} title="Альбомы"><Rows3 size={14} /></button>
                    <button onClick={() => setView('grid')} className={`btn-icon !h-8 !w-8 ${view === 'grid' ? '!bg-ink !text-white' : ''}`} title="Сетка"><LayoutGrid size={14} /></button>
                  </span>
                </div>
                {!profile && !error && photos.length === 0 && (
                  <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="aspect-[4/3] rounded-lg shimmer" />)}</div>
                )}
                {view === 'album' && filter === 'all' && !verifiedOnly && !recent3 && !year && profile
                  ? <PhotoAlbums photos={photos} qid={qid} onOpen={openPhoto} coverage={profile.coverage} />
                  : <PhotoGrid photos={shown} qid={qid} onOpen={openPhoto}
                      empty={photos.length > 0 || profile ? <EmptyState category={filter === 'all' ? undefined : filter} sources={doneSources} /> : null} />}
              </>
            )}
            {tab === 'bvr' && <BrochureVsReality photos={photos} onOpen={openPhoto} />}
            {tab === 'map' && uni && <CampusMap3D uni={uni} campus={campus} photos={photos} ctx={ctx} onOpen={openPhoto} />}
            {tab === 'climate' && uni && <ClimateTab qid={qid} lat={uni.lat} lon={uni.lon} />}
            {tab === 'city' && uni && <CityTab qid={qid} uni={uni} ctx={ctx} onCtx={setCtx} />}
            {tab === 'timeline' && profile && <Timeline timeline={profile.timeline} selected={year} onSelect={(y) => { setYear(y); if (y) setTab('photos') }} />}
            {tab === 'walk' && uni && <WalkTab uni={uni} campus={campus} photos={photos} walk={profile?.walk ?? []} onOpen={openPhoto} />}
            {tab === 'rejected' && profile && (
              <div className="space-y-3">
                <div className="text-sm text-muted">Отклонённые кандидаты остаются видимыми: это доказательство, что отбор автоматический, а не ручной.</div>
                <div className="card divide-y divide-line">
                  {profile.rejected.map((p) => (
                    <button key={p.id} onClick={() => openPhoto(p)} className="w-full flex items-center gap-4 p-3 text-left hover:bg-slate-50 cursor-pointer">
                      <div className="w-20 h-14 rounded-md bg-slate-100 overflow-hidden shrink-0 grayscale"><img src={thumbUrl(p)} alt="" loading="lazy" className="w-full h-full object-cover" /></div>
                      <div className="min-w-0 flex-1">
                        <div className="text-sm text-unverified truncate">{p.reject_reason}</div>
                        <div className="text-xs text-muted truncate">{p.source_label}{p.title ? ` · ${p.title}` : ''}</div>
                      </div>
                      <span className="mono text-xs text-muted shrink-0">{Math.round(p.confidence * 100)}%</span>
                    </button>
                  ))}
                  {profile.rejected.length === 0 && <div className="p-4 text-sm text-muted">Ничего не отклонено</div>}
                </div>
              </div>
            )}
            {tab === 'judge' && profile && <JudgePanel p={profile} />}
          </div>

          <aside className="space-y-8 lg:border-l lg:border-line lg:pl-8">
            {profile && <CoverageMatrix categories={profile.categories} overall={profile.coverage.overall} />}
            {profile?.description && <DescriptionBlock d={profile.description} />}
            {profile?.context && <ContextCards c={profile.context} />}
            {profile && <VisitPlan qid={qid} />}
            {!profile && !error && <div className="text-sm text-muted">{t('profile.generating')}</div>}
          </aside>
        </div>
      </div>

      {open && <PhotoPassport p={open} qid={qid} campus={campus} all={allForPassport} onClose={closePhoto} onOpen={openPhoto} onPrev={prev} onNext={next} />}
    </div>
  )
}
