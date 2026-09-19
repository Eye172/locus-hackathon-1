import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { RefreshCw, Share2, GitCompare, Bookmark, BookmarkCheck, ArrowLeft, Check, Map as MapIcon, Footprints, Box } from 'lucide-react'
import { streamProfile, API_BASE } from '../lib/api'
import type { Campus, Photo, Profile as ProfileT, SourceStatus, Stage, University } from '../lib/types'
import { CATEGORIES } from '../lib/types'
import { catLabel, uniName, useLang, useT } from '../lib/i18n'
import { PhotoGrid, PhotoAlbums, PhotoPassport, BrochureVsReality, EmptyState } from '../components/Photos'
import { ClimateTab } from '../components/ClimateTab'
import { CityTab } from '../components/CityTab'
import { WalkTab } from '../components/WalkTab'
import { pickHero } from '../components/CampusReveal'
import { AboutCampus } from '../components/AboutCampus'
import { BuildStatus, FactsRow, HeroGallery, OverviewTab, VerifyTab, heroPicks } from '../components/ProfileSections'
import type { ContextPack } from '../lib/types'
import { store, useStoreVersion } from '../lib/store'

const CampusTour = lazy(() => import('../components/CampusTour'))

type Tab = 'overview' | 'photos' | 'campus' | 'city' | 'climate' | 'verify'
type View = 'album' | 'grid' | 'bvr'
const FILTERS: string[] = ['all', ...CATEGORIES]
// old links (?tab=collage, ?tab=judge…) land on the tab that holds that content now
const LEGACY: Record<string, Tab> = { collage: 'overview', about: 'campus', walk: 'campus', bvr: 'photos', timeline: 'photos', rejected: 'verify', judge: 'verify' }
const TABS: Tab[] = ['overview', 'photos', 'campus', 'city', 'climate', 'verify']
const asTab = (v: string | null): Tab => (v && (TABS as string[]).includes(v) ? v as Tab : v && LEGACY[v] ? LEGACY[v] : 'overview')

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

  const [tab, setTabState] = useState<Tab>(asTab(params.get('tab')))
  const setTab = useCallback((next: Tab) => {
    setTabState(next)
    setParams((prev) => { const n = new URLSearchParams(prev); if (next === 'overview') n.delete('tab'); else n.set('tab', next); return n }, { replace: true })
  }, [setParams])
  const [view, setView] = useState<View>(params.get('tab') === 'bvr' ? 'bvr' : 'album')
  const [filter, setFilter] = useState<string>('all')
  const [verifiedOnly, setVerifiedOnly] = useState(false)
  const [recent3, setRecent3] = useState(false)
  const [year, setYear] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(params.get('photo'))
  const [copied, setCopied] = useState(false)
  const [walk, setWalk] = useState(params.get('tab') === 'walk')
  const [ctx, setCtx] = useState<ContextPack | null>(null)
  const [tourOpen, setTourOpen] = useState(false)
  useEffect(() => { setCtx(null); setWalk(false) }, [qid])

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
        if (refresh) setParams((prev) => { const n = new URLSearchParams(prev); n.delete('refresh'); return n }, { replace: true })
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
  const hero = useMemo(() => heroPicks(photos, 5, profile?.cover), [photos, profile?.cover])
  const open = openId ? allForPassport.find((p) => p.id === openId) ?? null : null
  const openPhoto = useCallback((p: Photo) => { setOpenId(p.id); setParams((prev) => { const n = new URLSearchParams(prev); n.set('photo', p.id); return n }, { replace: true }) }, [setParams])
  const closePhoto = useCallback(() => { setOpenId(null); setParams((prev) => { const n = new URLSearchParams(prev); n.delete('photo'); return n }, { replace: true }) }, [setParams])
  // arrows walk through what the viewer came from: the filtered grid, else every photo
  const seq = tab === 'photos' ? shown : allForPassport
  const idx = open ? seq.findIndex((p) => p.id === open.id) : -1
  const prev = idx > 0 ? () => openPhoto(seq[idx - 1]) : undefined
  const next = idx >= 0 && idx < seq.length - 1 ? () => openPhoto(seq[idx + 1]) : undefined

  // the share link goes through /s/{qid}: crawlers get Open Graph tags + a preview card, people get redirected here
  const share = async () => { try { const u = new URL(window.location.href); await navigator.clipboard.writeText(`${API_BASE || u.origin}/s/${qid}${u.search}`); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* ignore */ } }
  const saved = !!store.saved()[qid]
  useEffect(() => { if (uni?.name) store.rename(qid, uni.name) }, [qid, uni?.name])
  const doRefresh = () => { setParams((prev) => { const n = new URLSearchParams(prev); n.set('refresh', '1'); return n }, { replace: true }); setRun((r) => r + 1) }
  const doneSources = Object.entries(sources).filter(([, s]) => s.status === 'done').map(([, s]) => s.label)
  const showCategory = (c: string) => { setFilter(c); setView('grid'); setTab('photos'); window.scrollTo({ top: 0 }) }
  const years = Object.keys(profile?.timeline ?? {}).sort().reverse()

  const tabs: { key: Tab; label: string; badge?: number }[] = [
    { key: 'overview', label: 'Обзор' },
    { key: 'photos', label: t('profile.photos'), badge: photos.length || undefined },
    { key: 'campus', label: 'Кампус' },
    { key: 'city', label: t('profile.cityTab') },
    { key: 'climate', label: t('profile.climateTab') },
    { key: 'verify', label: 'Проверка' },
  ]
  const name = uni ? uniName(uni, lang) : ''
  const nameEn = uni ? uniName(uni, 'en') : ''
  const subtitle = uni ? [nameEn !== name ? nameEn : null, uni.description].filter(Boolean).join(' · ') : ''
  const loading = !profile && !error

  return (
    <div className="pb-24">
      {tourOpen && <Suspense fallback={null}><CampusTour qid={qid} name={uni ? name : undefined} photos={photos.length ? pickHero(photos, 8, profile?.cover ?? undefined) : undefined} onClose={() => setTourOpen(false)} /></Suspense>}
      <div className="mx-auto max-w-7xl px-4 sm:px-6">
        <nav className="pt-6 flex items-center gap-1.5 text-[13.5px] text-muted min-w-0">
          <Link to="/" className="inline-flex items-center gap-1.5 hover:text-ink shrink-0"><ArrowLeft size={15} /> Планета</Link>
          {uni?.country && <><span className="text-faint">/</span><span className="truncate">{uni.country}</span></>}
          {uni?.city && <><span className="text-faint">/</span><span className="truncate">{uni.city}</span></>}
        </nav>

        <div className="mt-5 flex flex-col lg:flex-row lg:items-end gap-6">
          <div className="min-w-0 flex-1 flex items-start gap-5">
            {uni?.logo_url && (
              <div className="hidden sm:grid w-16 h-16 rounded-xl border border-line bg-white place-items-center overflow-hidden shrink-0 mt-1.5">
                <img src={uni.logo_url} alt="" className="w-full h-full object-contain p-2" />
              </div>
            )}
            <div className="min-w-0">
              {uni ? (
                <>
                  <h1 className={`leading-[1.08] text-ink ${name.length > 56 ? 'text-[26px] sm:text-[32px]' : name.length > 34 ? 'text-[28px] sm:text-[38px]' : 'text-[32px] sm:text-[44px]'}`}>{name}</h1>
                  {subtitle && <p className="mt-2 text-[15px] text-muted max-w-[70ch]">{subtitle}</p>}
                </>
              ) : <div className="space-y-3 w-96 max-w-full"><div className="shimmer h-11 w-full rounded-lg" /><div className="shimmer h-4 w-2/3 rounded" /></div>}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 shrink-0 w-full lg:w-auto">
            {/* one map for the whole app: the campus scene of the main page */}
            <Link to={`/?u=${qid}`} className="btn-primary flex-1 sm:flex-none justify-center"><MapIcon size={17} /> 3D-карта кампуса</Link>
            <button className="btn-ghost flex-1 sm:flex-none justify-center" onClick={() => setTourOpen(true)}><Box size={17} /> Обзор</button>
            <button className="btn-ghost flex-1 sm:flex-none justify-center" disabled={!profile}
              onClick={() => saved ? store.unsave(qid) : store.save(qid, { name: uni?.name ?? qid, city: uni?.city, savedAt: new Date().toISOString(), photos: photos.length })}>
              {saved ? <BookmarkCheck size={17} /> : <Bookmark size={17} />} {saved ? 'Сохранено' : 'Сохранить'}
            </button>
            <Link to={`/compare?a=${qid}`} className="btn-ghost flex-1 sm:flex-none justify-center"><GitCompare size={17} /> Сравнить</Link>
            <button className="btn-icon" onClick={share} title={t('profile.share')} aria-label={t('profile.share')}>{copied ? <Check size={17} className="text-verified" /> : <Share2 size={17} />}</button>
            <button className="btn-icon max-sm:!hidden" onClick={doRefresh} title={t('profile.refresh')} aria-label={t('profile.refresh')} disabled={!profile}><RefreshCw size={17} /></button>
          </div>
        </div>

        {uni && <FactsRow uni={uni} />}
        <HeroGallery photos={hero} total={photos.length} loading={loading} onOpen={openPhoto} onAll={() => { setFilter('all'); setTab('photos') }} />
        <BuildStatus profile={profile} photos={photos} stages={stages} sources={sources} elapsed={elapsed} cached={cached} onVerify={() => setTab('verify')} />

        {profile?.partial && <div className="mt-4 text-[13.5px] text-likely">{t('profile.partial')}</div>}
        {error && (
          <div className="mt-6 border-t border-line pt-6">
            <div className="flex items-center gap-2 font-semibold text-ink"><i className="w-1.5 h-1.5 rounded-full bg-unverified" />{t('profile.error')}</div>
            <div className="text-sm text-muted mt-1">{error}</div>
            <button className="btn-ghost mt-3" onClick={() => nav('/')}><ArrowLeft size={16} /> К поиску</button>
          </div>
        )}
      </div>

      <div className="sticky top-14 z-30 mt-12 bg-white/92 backdrop-blur-md border-b border-line">
        <div role="tablist" className="mx-auto max-w-7xl px-4 sm:px-6 flex gap-7 overflow-x-auto no-scrollbar">
          {tabs.map((x) => (
            <button key={x.key} role="tab" aria-selected={tab === x.key} onClick={() => setTab(x.key)} className={`tab ${tab === x.key ? 'tab-active' : ''}`}>
              {x.label}{x.badge != null && <span className="ml-1.5 text-muted font-normal mono">{x.badge}</span>}
            </button>
          ))}
        </div>
      </div>

      <div className="mx-auto max-w-7xl px-4 sm:px-6 pt-12">
        {tab === 'overview' && <OverviewTab profile={profile} uni={uni} qid={qid} onOpen={openPhoto} onTab={setTab} onCategory={showCategory} />}

        {tab === 'photos' && (
          <div className="space-y-8">
            <div className="flex flex-wrap items-center gap-1 -ml-3">
              {FILTERS.map((f) => counts[f] > 0 || f === 'all' ? (
                <button key={f} onClick={() => setFilter(f)} className={`filter ${filter === f ? 'filter-active' : ''}`}>
                  {f === 'all' ? t('profile.all') : catLabel(f, lang)} <span className={`mono text-[12px] ${filter === f ? 'text-white/60' : 'text-muted'}`}>{counts[f]}</span>
                </button>
              ) : null)}
            </div>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-3 text-[13.5px] text-ink-2 border-b border-line pb-6">
              <div className="seg">
                <button className={view === 'album' ? 'on' : ''} onClick={() => setView('album')}>Альбомы</button>
                <button className={view === 'grid' ? 'on' : ''} onClick={() => setView('grid')}>Сетка</button>
                <button className={view === 'bvr' ? 'on' : ''} onClick={() => setView('bvr')}>{t('profile.bvr')}</button>
              </div>
              <label className="inline-flex items-center gap-2 cursor-pointer"><input type="checkbox" checked={verifiedOnly} onChange={(e) => setVerifiedOnly(e.target.checked)} className="accent-ink" />Только подтверждённые</label>
              <label className="inline-flex items-center gap-2 cursor-pointer"><input type="checkbox" checked={recent3} onChange={(e) => setRecent3(e.target.checked)} className="accent-ink" />Снято за 3 года</label>
              {years.length > 0 && (
                <select value={year ?? ''} onChange={(e) => setYear(e.target.value || null)} className="h-8 rounded-lg border border-line-2 bg-white px-2 text-[13.5px] cursor-pointer outline-none focus:border-ink">
                  <option value="">Любой год</option>
                  {years.map((y) => <option key={y} value={y}>{y} · {profile!.timeline[y]} фото</option>)}
                </select>
              )}
            </div>
            {loading && photos.length === 0 && (
              <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="aspect-[4/3] rounded-lg shimmer" />)}</div>
            )}
            {view === 'bvr' ? <BrochureVsReality photos={shown} onOpen={openPhoto} />
              : view === 'album' && filter === 'all' && !verifiedOnly && !recent3 && !year && profile
                ? <PhotoAlbums photos={photos} qid={qid} onOpen={openPhoto} coverage={profile.coverage} />
                : <PhotoGrid photos={shown} qid={qid} onOpen={openPhoto}
                    empty={photos.length > 0 || profile ? <EmptyState category={filter === 'all' ? undefined : filter} sources={doneSources} /> : null} />}
          </div>
        )}

        {tab === 'campus' && (
          <div className="space-y-16">
            <AboutCampus qid={qid} />
            {uni && (
              <section className="border-t border-line pt-10">
                <div className="flex flex-wrap items-baseline gap-3 mb-5">
                  <h2 className="h-sec">Прогулка по кампусу<small>уличные панорамы и снимки с геометкой рядом с корпусами</small></h2>
                </div>
                {walk ? <WalkTab uni={uni} campus={campus} photos={photos} walk={profile?.walk ?? []} onOpen={openPhoto} />
                  : <button className="btn-ghost" onClick={() => setWalk(true)}><Footprints size={17} /> Открыть прогулку</button>}
              </section>
            )}
          </div>
        )}
        {tab === 'city' && uni && <CityTab qid={qid} uni={uni} ctx={ctx} onCtx={setCtx} />}
        {tab === 'climate' && uni && <ClimateTab qid={qid} lat={uni.lat} lon={uni.lon} city={uni.city} />}
        {tab === 'verify' && (profile ? <VerifyTab profile={profile} stages={stages} sources={sources} onOpen={openPhoto} />
          : <div className="text-[15px] text-muted">{t('profile.generating')}</div>)}
      </div>

      {open && <PhotoPassport p={open} qid={qid} all={allForPassport} onClose={closePhoto} onOpen={openPhoto} onPrev={prev} onNext={next} />}
    </div>
  )
}

