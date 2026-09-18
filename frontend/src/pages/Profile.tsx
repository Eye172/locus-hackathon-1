import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { RefreshCw, Share2, GitCompare, Gavel, ExternalLink, Bookmark, BookmarkCheck, ArrowLeft, LayoutGrid, Rows3, Check, Map as MapIcon, CalendarDays, Users, MapPin, Globe, Database, SearchCheck, Camera, type LucideIcon } from 'lucide-react'
import { streamProfile, thumbUrl, API_BASE } from '../lib/api'
import type { Campus, Photo, Profile as ProfileT, SourceStatus, Stage, University } from '../lib/types'
import { CATEGORIES } from '../lib/types'
import { catLabel, useLang, useT } from '../lib/i18n'
import { AgentsStrip, CoverageMatrix, DescriptionBlock, ContextCards, Timeline, JudgePanel, VisitPlan } from '../components/ProfileParts'
import { PhotoGrid, PhotoAlbums, PhotoPassport, BrochureVsReality, EmptyState } from '../components/Photos'
import { ClimateTab } from '../components/ClimateTab'
import { CityTab } from '../components/CityTab'
import { WalkTab } from '../components/WalkTab'
import { CollageTab } from '../components/CollageTab'
import { AboutCampus } from '../components/AboutCampus'
import { SourceLink } from '../components/SourceLink'
import { api } from '../lib/api'
import type { ContextPack } from '../lib/types'
import { store, useStoreVersion } from '../lib/store'

type Tab = 'collage' | 'about' | 'photos' | 'bvr' | 'climate' | 'city' | 'timeline' | 'walk' | 'rejected' | 'judge'
const FILTERS: string[] = ['all', ...CATEGORIES]

const PHOTO_NETS = new Set(['telegram', 'youtube', 'vk', 'instagram', 'tiktok'])  // networks we actually fetch photos from; the rest are links
const SOCIAL_NAME: Record<string, string> = { instagram: 'Instagram', telegram: 'Telegram', youtube: 'YouTube', vk: 'VK', facebook: 'Facebook', tiktok: 'TikTok' }
type Fact = { key: string; icon: LucideIcon; label: string; value: ReactNode }

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

  // the collage ("what is this university like") opens first; every other view is one click away
  const [tab, setTabState] = useState<Tab>((params.get('tab') as Tab) || 'collage')
  const setTab = (next: Tab) => { setTabState(next); setParams((prev) => { const n = new URLSearchParams(prev); if (next === 'collage') n.delete('tab'); else n.set('tab', next); return n }, { replace: true }) }
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
    if (tab === 'city' && !ctx && profile) api.context(qid).then(setCtx).catch(() => {})
  }, [tab, ctx, profile, qid])

  useEffect(() => {
    setStages({}); setSources({}); setUni(null); setCampus(null); setPrelim([]); setProfile(null); setError(null); setElapsed(0); setCached(false)
    const close = streamProfile(qid, refresh, {
      onStage: (s, e) => { setStages((st) => ({ ...st, [s.key]: s })); setElapsed(e) },
      onUniversity: (u, c) => { setUni(u); setCampus(c) },
      onCampus: (c, u) => { setCampus(c); setUni(u) },
      onSource: (name, s, e) => { setSources((ss) => ({ ...ss, [name]: s })); setElapsed(e) },
      onPhotos: (_src, photos) => setPrelim((pp) => { const ids = new Set(pp.map((p) => p.id)); return [...pp, ...photos.filter((p) => !ids.has(p.id))] }),
      // the first live profile comes at ~24 s; the build then keeps collecting without a clock and sends it again
      // as it grows - silently: the status line stays "built in N s", only the photos and the details change
      onProfile: (p, c) => {
        setProfile(p); setUni(p.university); setCampus(p.campus ?? null); setCached(c); setElapsed(p.elapsed_ms)
        setStages((st) => (c && Object.keys(st).length ? st : Object.fromEntries(p.stages.map((s) => [s.key, s]))))
        setSources((ss) => (c && Object.keys(ss).length ? ss : p.sources_status))
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
    { key: 'collage', label: t('profile.collage'), badge: profile?.collage?.reduce((n, s) => n + s.photos.length, 0) },
    { key: 'about', label: t('profile.about') },
    { key: 'photos', label: t('profile.photos'), badge: photos.length },
    { key: 'bvr', label: t('profile.bvr') },
    { key: 'walk', label: t('profile.walk') },
    { key: 'climate', label: t('profile.climateTab') },
    { key: 'city', label: t('profile.cityTab') },
    { key: 'timeline', label: t('profile.timeline') },
    { key: 'rejected', label: t('profile.rejected'), badge: profile?.rejected.length },
    { key: 'judge', label: t('profile.judge') },
  ]
  const name = uni ? (uni.names[lang] || uni.name) : ''
  // a city view must never stand in for the university in the header
  const hero = profile?.photos.find((p) => p.category === 'campus' && p.level === 'verified') ?? profile?.photos.find((p) => p.category !== 'city')
  const subtitle = uni ? [uni.names.en && uni.names.en !== name ? uni.names.en : null, uni.description].filter(Boolean).join(' · ') : ''
  const facts: Fact[] = !uni ? [] : ([
    uni.founded ? { key: 'founded', icon: CalendarDays, label: t('profile.founded'), value: <span className="tabular-nums">{uni.founded}</span> } : null,
    uni.students ? { key: 'students', icon: Users, label: t('profile.students'), value: <span className="tabular-nums">{uni.students.toLocaleString('ru-RU')}</span> } : null,
    uni.city ? { key: 'city', icon: MapPin, label: t('profile.city'), value: `${uni.city}${uni.country ? `, ${uni.country}` : ''}` } : null,
    uni.website ? { key: 'site', icon: Globe, label: t('profile.site'), value: (
      <a href={uni.website} target="_blank" rel="noreferrer" title={uni.website} className="inline-flex items-center gap-1 max-w-full hover:text-brand">
        <span className="truncate">{uni.website.replace(/^https?:\/\//, '').replace(/\/$/, '')}</span><ExternalLink size={12} className="shrink-0 text-muted" />
      </a>) } : null,
    uni.qid.startsWith('Q')
      ? { key: 'wikidata', icon: Database, label: 'Wikidata', value: <a href={`https://www.wikidata.org/wiki/${uni.qid}`} target="_blank" rel="noreferrer" className="mono hover:text-brand">{uni.qid}</a> }
      : { key: 'via', icon: SearchCheck, label: t('profile.foundVia'), value: t('search.webLong') },
  ] as (Fact | null)[]).filter((f): f is Fact => f !== null)
  const social = Object.entries(uni?.social ?? {})

  return (
    <div>
      {/* header: who (name + actions) → key facts → how the profile was built */}
      <div className="bg-surface border-b border-line topo-soft">
        <div className="relative mx-auto max-w-7xl px-4 pt-5 pb-4">
          <Link to="/" className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-ink"><ArrowLeft size={13} /> Планета</Link>

          <div className="mt-3 flex flex-col lg:flex-row lg:items-center gap-4 lg:gap-8">
            <div className="min-w-0 flex-1 flex items-center gap-4">
              <div className="w-16 h-16 sm:w-[72px] sm:h-[72px] rounded-xl bg-slate-100 overflow-hidden shrink-0 border border-line grid place-items-center">
                {uni?.logo_url ? <img src={uni.logo_url} alt="" className="w-full h-full object-contain p-1.5" /> : hero ? <img src={thumbUrl(hero)} alt="" className="w-full h-full object-cover" /> : <div className="shimmer w-full h-full" />}
              </div>
              <div className="min-w-0">
                {uni ? (
                  <>
                    <h1 className="text-[26px] sm:text-[32px] leading-[1.1] font-extrabold">{name}</h1>
                    {subtitle && <div className="mt-1.5 text-sm text-muted">{subtitle}</div>}
                  </>
                ) : <div className="space-y-2 w-72 max-w-full"><div className="shimmer h-8 w-full rounded" /><div className="shimmer h-4 w-2/3 rounded" /></div>}
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0 w-full lg:w-auto">
              {/* one map for the whole app: the campus scene of the main page, not a map of its own here */}
              <Link to={`/?u=${qid}`} className="btn-primary !h-10 !py-0 whitespace-nowrap justify-center flex-1 lg:flex-none"><MapIcon size={15} /> {t('profile.map')}</Link>
              <div className="inline-flex h-10 rounded-lg border border-line-2 bg-surface divide-x divide-line-2 overflow-hidden">
                <button className="tool" onClick={doRefresh} title={t('profile.refresh')} aria-label={t('profile.refresh')} disabled={!profile}><RefreshCw size={15} /></button>
                <button className="tool" onClick={share} title={t('profile.share')} aria-label={t('profile.share')}>{copied ? <Check size={15} className="text-verified" /> : <Share2 size={15} />}</button>
                <button className="tool" title={saved ? 'Убрать из сохранённых' : 'Сохранить'} aria-label={saved ? 'Убрать из сохранённых' : 'Сохранить'} disabled={!profile}
                  onClick={() => saved ? store.unsave(qid) : store.save(qid, { name: uni?.name ?? qid, city: uni?.city, savedAt: new Date().toISOString(), photos: photos.length })}>
                  {saved ? <BookmarkCheck size={15} className="text-brand" /> : <Bookmark size={15} />}
                </button>
                <Link to={`/compare?a=${qid}`} className="tool" title={t('profile.compare')} aria-label={t('profile.compare')}><GitCompare size={15} /></Link>
                <button className={`tool ${tab === 'judge' ? 'tool-on' : ''}`} onClick={() => setTab(tab === 'judge' ? 'photos' : 'judge')} title={t('profile.judge')} aria-label={t('profile.judge')}><Gavel size={15} /></button>
              </div>
            </div>
          </div>

          {uni && (
            <div className="mt-5 rounded-xl border border-line bg-surface overflow-hidden">
              {/* -ml/-mt hide the outer edge of the cell borders, so only the lines between cells show at any column count */}
              <dl className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] -ml-px -mt-px">
                {facts.map((f) => (
                  <div key={f.key} className="min-w-0 px-4 py-3 border-l border-t border-line">
                    <dt className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider font-semibold text-muted"><f.icon size={12} />{f.label}</dt>
                    <dd className="mt-1 text-[15px] font-semibold text-ink">{f.value}</dd>
                  </div>
                ))}
              </dl>
              {social.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-t border-line bg-canvas/60">
                  <span className="mr-1 text-[11px] uppercase tracking-wider font-semibold text-muted">{t('profile.social')}</span>
                  {social.map(([net, url]) => (
                    <a key={net} href={url} target="_blank" rel="noreferrer" title={PHOTO_NETS.has(net) ? t('profile.socialLegend') : undefined}
                      className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full border border-line-2 bg-surface text-[13px] font-medium text-ink-2 hover:border-brand hover:text-brand transition-colors">
                      {SOCIAL_NAME[net] ?? net}{PHOTO_NETS.has(net) && <Camera size={12} className="text-brand" />}
                    </a>
                  ))}
                  {social.some(([net]) => PHOTO_NETS.has(net)) && (
                    <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-muted"><Camera size={12} className="text-brand" /> {t('profile.socialLegend')}</span>
                  )}
                </div>
              )}
            </div>
          )}

          <AgentsStrip stages={stages} sources={sources} elapsed={elapsed} coverage={profile?.coverage.overall} cached={cached} />
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
            {tab === 'collage' && (profile ? <CollageTab profile={profile} onOpen={openPhoto} />
              : !error && <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="aspect-[4/3] rounded-lg shimmer" />)}</div>)}
            {tab === 'about' && <AboutCampus qid={qid} />}
            {tab === 'bvr' && <BrochureVsReality photos={photos} onOpen={openPhoto} />}
            {tab === 'climate' && uni && <ClimateTab qid={qid} lat={uni.lat} lon={uni.lon} />}
            {tab === 'city' && uni && <CityTab qid={qid} uni={uni} ctx={ctx} onCtx={setCtx} />}
            {tab === 'timeline' && profile && <Timeline timeline={profile.timeline} selected={year} onSelect={(y) => { setYear(y); if (y) setTab('photos') }} />}
            {tab === 'walk' && uni && <WalkTab uni={uni} campus={campus} photos={photos} walk={profile?.walk ?? []} onOpen={openPhoto} />}
            {tab === 'rejected' && profile && (
              <div className="space-y-3">
                <div className="text-sm text-muted">Отклонённые кандидаты остаются видимыми: это доказательство, что отбор автоматический, а не ручной.</div>
                <div className="card divide-y divide-line">
                  {profile.rejected.map((p) => (
                    <div key={p.id} className="flex items-center gap-4 p-3 hover:bg-slate-50">
                      <button onClick={() => openPhoto(p)} className="w-20 h-14 rounded-md bg-slate-100 overflow-hidden shrink-0 grayscale cursor-pointer"><img src={thumbUrl(p)} alt="" loading="lazy" className="w-full h-full object-cover" /></button>
                      <div className="min-w-0 flex-1">
                        <button onClick={() => openPhoto(p)} className="block max-w-full text-left text-sm text-unverified truncate cursor-pointer">{p.reject_reason}</button>
                        <div className="text-xs text-muted truncate">{p.source_label}{p.title ? ` · ${p.title}` : ''}</div>
                        <SourceLink p={p} />
                      </div>
                      <span className="mono text-xs text-muted shrink-0">{Math.round(p.confidence * 100)}%</span>
                    </div>
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
