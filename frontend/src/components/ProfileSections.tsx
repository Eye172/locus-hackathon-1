import { useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, Camera, Check, ChevronDown, Images, Loader2, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import type { CategoryStats, Context, Description, Photo, Profile, SourceStatus, Stage, University } from '../lib/types'
import { CATEGORIES } from '../lib/types'
import { thumbUrl } from '../lib/api'
import { catLabel, useLang, useT } from '../lib/i18n'
import { coverageLabel } from './Badges'
import { StageList, SourceList, VisitPlan, JudgePanel } from './ProfileParts'
import { SourceLink } from './SourceLink'

/* ---------------------------------------------------------------- hero */

// flags that make a photo a poor first impression even when it is the right place
const HERO_BAD = new Set(['banner', 'text', 'logo', 'screenshot', 'collage', 'illustration', 'portrait', 'official_meeting', 'crop', 'stock'])
const HERO_ORDER = ['campus', 'student_life', 'library', 'dormitory', 'sports', 'lab', 'classroom']

/** Five photos for the header. The cover editor's choice when the profile has one (backend pipeline/cover.py: the
 *  campus from outside, beautifully, then four different sides of life), topped up by the rule below; otherwise the
 *  rule: the best campus view first, then one per side of university life - verified, landscape, high-resolution
 *  frames the AI rated useful; never a city view unless nothing else exists. */
export function heroPicks(photos: Photo[], n = 5, cover?: string[]): Photo[] {
  const picked = coverPhotos(photos, cover)
  if (picked.length >= Math.min(n, 3)) {
    const rest = heroPicksByRule(photos.filter((p) => !picked.includes(p)), n)
    return [...picked, ...rest].slice(0, n)
  }
  return heroPicksByRule(photos, n)
}

/** The editor's ids that are still in the list, in its order. */
export function coverPhotos(photos: Photo[], cover?: string[]): Photo[] {
  if (!cover?.length) return []
  const by = new Map(photos.map((p) => [p.id, p]))
  return cover.map((id) => by.get(id)).filter((p): p is Photo => !!p && !p.rejected)
}

function heroPicksByRule(photos: Photo[], n: number): Photo[] {
  const score = (p: Photo) => (p.level === 'verified' ? 2 : 0) + (p.ai?.q ?? 1) * 0.8 + (p.featured ? 1 : 0) + p.confidence
    + Math.min(p.width, 1600) / 1600 + (p.width / p.height >= 1.2 ? 0.6 : 0)
  const good = photos.filter((p) => !p.rejected && p.level !== 'unverified' && p.width >= 480 && p.width / p.height >= 0.95
    && !(p.ai?.flags ?? []).some((f) => HERO_BAD.has(f)))
  const pool = (good.length >= 3 ? good : photos.filter((p) => !p.rejected)).slice().sort((a, b) => score(b) - score(a))
  const out: Photo[] = []
  const take = (p?: Photo) => { if (p && !out.includes(p) && out.length < n) out.push(p) }
  for (const c of HERO_ORDER) take(pool.find((p) => p.category === c))
  for (const p of pool) if (p.category !== 'city') take(p)
  for (const p of pool) take(p)
  return out
}

export function HeroGallery({ photos, total, loading, onOpen, onAll }: {
  photos: Photo[]; total: number; loading: boolean; onOpen: (p: Photo) => void; onAll: () => void
}) {
  if (!photos.length) {
    if (!loading) return null
    return (
      <div className="mt-8 grid grid-cols-4 grid-rows-2 gap-2 h-[260px] sm:h-[420px] lg:h-[460px] rounded-2xl overflow-hidden">
        <div className="shimmer col-span-4 row-span-2 sm:col-span-2" />
        {[0, 1, 2, 3].map((i) => <div key={i} className="shimmer hidden sm:block" />)}
      </div>
    )
  }
  const n = photos.length
  // 1 big + up to 4 small; with fewer photos the small ones widen so the block never has holes
  const cell = (i: number) => {
    if (n === 1) return 'col-span-4 row-span-2'
    if (n === 2) return 'col-span-4 row-span-2 sm:col-span-2'
    if (i === 0) return 'col-span-4 row-span-2 sm:col-span-2'
    if (n === 3 || (n === 4 && i === 1)) return 'hidden sm:block sm:col-span-2'
    return 'hidden sm:block'
  }
  return (
    <div className="relative mt-8">
      <div className="grid grid-cols-4 grid-rows-2 gap-2 rounded-2xl overflow-hidden h-[260px] sm:h-[420px] lg:h-[460px]">
        {photos.map((p, i) => (
          <button key={p.id} onClick={() => onOpen(p)} className={`relative overflow-hidden bg-soft cursor-pointer group ${cell(i)}`}>
            <img src={thumbUrl(p)} alt={p.title ?? ''} className="absolute inset-0 w-full h-full object-cover transition-[filter] duration-200 group-hover:brightness-[0.92]" />
          </button>
        ))}
      </div>
      {total > n && (
        <button onClick={onAll} className="absolute right-3 bottom-3 inline-flex items-center gap-2 h-9 px-3.5 rounded-lg bg-white/95 text-ink text-[13.5px] font-medium shadow-[0_1px_2px_rgba(0,0,0,0.12),0_4px_14px_rgba(0,0,0,0.12)] hover:bg-white cursor-pointer">
          <Images size={16} /> Все {total} фото
        </button>
      )}
    </div>
  )
}

/* ---------------------------------------------------------------- status line */

const AGENT: Record<string, string> = { facts: 'факты', campus: 'контур кампуса', collect: 'поиск фото', fetch: 'загрузка', inspect: 'ИИ-инспектор', analyze: 'отбор', assemble: 'описание' }

/** One line under the gallery: what is proven, and how the build is going. Everything else lives in «Проверка». */
export function BuildStatus({ profile, photos, stages, sources, elapsed, cached, onVerify }: {
  profile: Profile | null; photos: Photo[]; stages: Record<string, Stage>; sources: Record<string, SourceStatus>
  elapsed: number; cached: boolean; onVerify: () => void
}) {
  const list = Object.values(stages)
  const busy = !profile || list.some((s) => s.status === 'running' || s.status === 'pending')
  const running = list.filter((s) => s.status === 'running').map((s) => AGENT[s.key] ?? s.label)
  const problems = list.filter((s) => s.status === 'skipped' || s.status === 'error').length
  const srcDone = Object.values(sources).filter((s) => s.status === 'done').length
  const verified = photos.filter((p) => p.level === 'verified').length
  const secs = (elapsed / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
  return (
    <div className="mt-4 flex flex-col sm:flex-row sm:items-center gap-x-6 gap-y-2 text-[13.5px]">
      {photos.length > 0 ? (
        <div className="flex items-center gap-2 text-ink-2 min-w-0">
          <ShieldCheck size={16} className="text-verified shrink-0" />
          <span><span className="font-semibold text-ink mono">{verified}</span> из <span className="mono">{photos.length}</span> фото подтверждены, у каждого есть ссылка на источник</span>
          <button onClick={onVerify} className="shrink-0 text-ink underline decoration-line-2 underline-offset-[3px] hover:decoration-ink cursor-pointer">Как проверяли</button>
        </div>
      ) : <div className="text-muted">Ищем фото в открытых источниках и соцсетях…</div>}
      <div className="sm:ml-auto flex items-center flex-wrap gap-x-2 gap-y-1 text-muted sm:whitespace-nowrap">
        {busy ? <Loader2 size={14} className="animate-spin text-ink-2" /> : <Check size={14} className="text-verified" />}
        {busy
          ? <span>Собираем{running.length ? `: ${running.slice(0, 2).join(', ')}` : ''} · <span className="mono">{secs} с</span></span>
          : <span>Собрано за <span className="mono">{secs} с</span> · {srcDone} источников{cached ? ' · прошлый сбор, обновляем' : ''}</span>}
        {problems > 0 && <button onClick={onVerify} className="text-likely hover:underline cursor-pointer">· замечаний: {problems}</button>}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- facts row */

/** The facts row's place while the university is not known yet: the gallery below must not jump when it arrives. */
export function FactsRowSkeleton() {
  return (
    <dl className="mt-6 flex gap-x-10" aria-hidden>
      <div><dt className="text-[13px]">&nbsp;</dt><dd className="mt-0.5 text-[16px] font-medium"><span className="shimmer inline-block w-40 h-4 rounded align-middle" /></dd></div>
    </dl>
  )
}

export function FactsRow({ uni }: { uni: University }) {
  const t = useT()
  const facts: { k: string; v: ReactNode }[] = []
  if (uni.city) facts.push({ k: t('profile.city'), v: `${uni.city}${uni.country ? `, ${uni.country}` : ''}` })
  if (uni.founded) facts.push({ k: 'Основан', v: <span className="mono">{uni.founded}</span> })
  if (uni.students) facts.push({ k: 'Студентов', v: <span className="mono">{uni.students.toLocaleString('ru-RU')}</span> })
  if (uni.website) facts.push({ k: 'Сайт', v: (
    <a href={uni.website} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 hover:underline decoration-line-2 underline-offset-4">
      {uni.website.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')}<ArrowUpRight size={14} className="text-faint" />
    </a>) })
  if (!facts.length) return null
  return (
    <dl className="mt-6 flex flex-wrap gap-x-10 gap-y-4">
      {facts.map((f) => (
        <div key={f.k} className="min-w-0">
          <dt className="text-[13px] text-muted first-letter:uppercase">{f.k}</dt>
          <dd className="mt-0.5 text-[16px] font-medium text-ink truncate max-w-[22rem]">{f.v}</dd>
        </div>
      ))}
    </dl>
  )
}

/* ---------------------------------------------------------------- overview */

function Section({ title, aside, children, className = '' }: { title: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={className}>
      <div className="flex items-baseline gap-3 mb-5">
        <h2 className="h-sec">{title}</h2>
        {aside && <div className="ml-auto text-[13.5px] text-muted">{aside}</div>}
      </div>
      {children}
    </section>
  )
}

export function DescriptionText({ d }: { d: Description }) {
  return (
    <div>
      <p className="text-[17px] leading-[1.65] text-ink-2 max-w-[65ch]">
        {d.sentences.map((s, i) => (
          <span key={i}>{s.text}{s.sources.map((id) => {
            const src = d.sources.find((x) => x.id === id)
            return src ? <a key={id} href={src.url} target="_blank" rel="noreferrer" title={src.label} className="ml-px align-super text-[11px] text-faint hover:text-ink">{id}</a> : null
          })} </span>
        ))}
      </p>
      {d.sources.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-muted">
          {d.sources.map((s) => <a key={s.id} href={s.url} target="_blank" rel="noreferrer" className="hover:text-ink"><span className="mono">{s.id}</span> {s.label}</a>)}
          <span className="text-faint">{d.mode === 'llm' ? 'текст составлен ИИ по этим источникам' : 'текст по шаблону из этих источников'}</span>
        </div>
      )}
    </div>
  )
}

// order inside a theme: clean, useful frames first; video covers with captions and posters sink to the end
const look = (p: Photo) => (p.ai?.q ?? 1) + (p.level === 'verified' ? 0.5 : 0) - ((p.ai?.flags ?? []).some((f) => HERO_BAD.has(f)) ? 2.5 : 0)

/** "What is this university like": every theme of the search plan as a tidy grid (two rows, the rest on demand). */
export function CollageThemes({ profile, onOpen }: { profile: Profile; onOpen: (p: Photo) => void }) {
  const byId = useMemo(() => new Map(profile.photos.map((p) => [p.id, p])), [profile.photos])
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const sections = (profile.collage ?? []).map((s) => ({ ...s, items: s.photos.map((id) => byId.get(id)).filter((p): p is Photo => !!p).sort((a, b) => look(b) - look(a)) }))
  const filled = sections.filter((s) => s.items.length > 0)
  const empty = sections.filter((s) => s.items.length === 0)
  if (!filled.length) return <div className="text-[15px] text-muted">Подборка по темам появится, когда фото найдут и проверят.</div>
  return (
    <div className="space-y-14">
      {filled.map((s) => {
        const shown = open[s.key] ? s.items : s.items.slice(0, 8)
        return (
          <div key={s.key}>
            <div className="flex items-baseline gap-2.5 mb-3">
              <h3 className="text-[16px] font-medium tracking-[-0.01em]">{s.label}</h3>
              <span className="text-[13px] text-faint mono">{s.items.length}</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
              {shown.map((p) => (
                <button key={p.id} onClick={() => onOpen(p)} title={p.title ?? ''}
                  className="relative aspect-[4/3] overflow-hidden rounded-lg bg-soft cursor-pointer group">
                  <img src={thumbUrl(p)} alt={p.title ?? ''} loading="lazy" className="absolute inset-0 w-full h-full object-cover transition-[filter] duration-200 group-hover:brightness-[0.9]" />
                </button>
              ))}
            </div>
            {s.items.length > 8 && (
              <button onClick={() => setOpen((o) => ({ ...o, [s.key]: !o[s.key] }))} className="btn-text mt-3">
                {open[s.key] ? 'Свернуть' : `Показать ещё ${s.items.length - 8}`}<ChevronDown size={15} className={open[s.key] ? 'rotate-180' : ''} />
              </button>
            )}
          </div>
        )
      })}
      {empty.length > 0 && (
        <p className="text-[13.5px] text-muted">Проверенных фото не нашлось: {empty.map((s) => s.label.toLowerCase()).join(', ')}. Пустые темы мы не заполняем чужими снимками.</p>
      )}
    </div>
  )
}

function AsideBlock({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <div>
      <div className="flex items-baseline justify-between pb-3 mb-1 border-b border-line">
        <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">{title}</h3>
        {action}
      </div>
      {children}
    </div>
  )
}

const SOCIAL_NAME: Record<string, string> = { instagram: 'Instagram', telegram: 'Telegram', youtube: 'YouTube', vk: 'VK', facebook: 'Facebook', tiktok: 'TikTok' }
const PHOTO_NETS = new Set(['telegram', 'youtube', 'vk', 'instagram', 'tiktok'])

export function Surroundings({ c, onCity, onClimate }: { c: Context; onCity: () => void; onClimate: () => void }) {
  const row = (k: string, v: ReactNode, go?: () => void) => (
    <button onClick={go} className="w-full flex items-baseline justify-between gap-4 py-2.5 border-t border-line first:border-t-0 text-left cursor-pointer group">
      <span className="text-[14px] text-ink-2">{k}</span>
      <span className="text-[15px] font-medium text-ink mono text-right group-hover:underline decoration-line-2 underline-offset-4">{v}</span>
    </button>
  )
  const sign = (x: number) => `${x > 0 ? '+' : ''}${x.toLocaleString('ru-RU', { maximumFractionDigits: 0 })}°`
  return (
    <AsideBlock title="Вокруг кампуса">
      <div>
        {c.distance_km != null && row(`До центра${c.center_name ? ` · ${c.center_name}` : ''}`, `${c.distance_km.toLocaleString('ru-RU')} км`, onCity)}
        {c.transport_stops != null && row('Остановок рядом', c.transport_stops, onCity)}
        {c.climate && row('Январь · июль', `${sign(c.climate.jan)} · ${sign(c.climate.jul)}`, onClimate)}
      </div>
    </AsideBlock>
  )
}

export function SocialLinks({ social }: { social: Record<string, string> }) {
  const list = Object.entries(social)
  if (!list.length) return null
  return (
    <AsideBlock title="Соцсети вуза" action={<span className="inline-flex items-center gap-1 text-[12px] text-faint"><Camera size={12} /> берём фото</span>}>
      <div className="pt-3 flex flex-wrap gap-1.5">
        {list.map(([net, url]) => (
          <a key={net} href={url} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1.5 h-8 px-3 rounded-full border border-line-2 text-[13px] text-ink-2 hover:border-ink hover:text-ink transition-colors">
            {SOCIAL_NAME[net] ?? net}{PHOTO_NETS.has(net) && <Camera size={12} className="text-faint" />}
          </a>
        ))}
      </div>
    </AsideBlock>
  )
}

/** Photos per section of the case: a thin bar per category, verified solid, likely lighter. */
export function CoverageBars({ categories, overall, onPhotos }: { categories: Record<string, CategoryStats>; overall?: string; onPhotos?: (c: string) => void }) {
  const lang = useLang()
  const max = Math.max(6, ...CATEGORIES.map((c) => (categories[c]?.verified ?? 0) + (categories[c]?.likely ?? 0)))
  return (
    <AsideBlock title="Фото по разделам" action={overall && <span className="text-[12px] text-faint">покрытие {coverageLabel(overall, lang)}</span>}>
      <div className="pt-3 space-y-2.5">
        {CATEGORIES.map((c) => {
          const s = categories[c]
          if (!s) return null
          const v = s.verified, l = s.likely
          return (
            <button key={c} onClick={() => onPhotos?.(c)} className="w-full grid grid-cols-[7.5rem_1fr_2.5rem] items-center gap-3 text-left cursor-pointer group">
              <span className="text-[13.5px] text-ink-2 truncate group-hover:text-ink">{catLabel(c, lang)}</span>
              <span className="h-1.5 rounded-full bg-soft overflow-hidden flex">
                <i className="h-full bg-ink" style={{ width: `${Math.min(100, (v / max) * 100)}%` }} />
                <i className="h-full bg-faint/60" style={{ width: `${Math.min(100 - (v / max) * 100, (l / max) * 100)}%` }} />
              </span>
              <span className="text-[13px] text-muted mono text-right">{v + l}</span>
            </button>
          )
        })}
      </div>
      <div className="mt-3 flex gap-4 text-[12px] text-faint">
        <span className="inline-flex items-center gap-1.5"><i className="w-2.5 h-1.5 rounded-full bg-ink" />подтверждено</span>
        <span className="inline-flex items-center gap-1.5"><i className="w-2.5 h-1.5 rounded-full bg-faint/60" />вероятно</span>
      </div>
    </AsideBlock>
  )
}

export function OverviewTab({ profile, uni, qid, onOpen, onTab, onCategory }: {
  profile: Profile | null; uni: University | null; qid: string; onOpen: (p: Photo) => void
  onTab: (t: 'photos' | 'city' | 'climate' | 'verify') => void; onCategory: (c: string) => void
}) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_300px] gap-x-16 gap-y-12">
      <div className="min-w-0 space-y-16">
        {profile?.description && <Section title="О вузе"><DescriptionText d={profile.description} /></Section>}
        <Section title="Какой это вуз" aside={profile && <Link to={`/settings/search?u=${qid}`} className="inline-flex items-center gap-1.5 hover:text-ink"><SlidersHorizontal size={14} /> Настроить поиск</Link>}>
          {profile ? <CollageThemes profile={profile} onOpen={onOpen} />
            : <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="aspect-[4/3] rounded-lg shimmer" />)}</div>}
        </Section>
      </div>
      <aside className="space-y-12 lg:sticky lg:top-32 lg:self-start">
        {profile?.context && <Surroundings c={profile.context} onCity={() => onTab('city')} onClimate={() => onTab('climate')} />}
        {profile && <CoverageBars categories={profile.categories} overall={profile.coverage.overall} onPhotos={onCategory} />}
        {uni?.social && <SocialLinks social={uni.social} />}
        {profile && <VisitPlan qid={qid} />}
      </aside>
    </div>
  )
}

/* ---------------------------------------------------------------- verification */

function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: string }) {
  return (
    <div>
      <div className="text-[13px] text-muted">{label}</div>
      <div className={`mt-2 text-[32px] leading-none font-semibold tracking-[-0.03em] mono ${tone ?? 'text-ink'}`}>{value}</div>
    </div>
  )
}

export function VerifyTab({ profile, stages, sources, onOpen }: {
  profile: Profile; stages: Record<string, Stage>; sources: Record<string, SourceStatus>; onOpen: (p: Photo) => void
}) {
  const [allRejected, setAllRejected] = useState(false)
  const [tech, setTech] = useState(false)
  const [queries, setQueries] = useState(false)
  const verified = profile.photos.filter((p) => p.level === 'verified').length
  const likely = profile.photos.filter((p) => p.level === 'likely').length
  const rejected = profile.rejected
  return (
    <div className="space-y-14 max-w-5xl">
      <div>
        <div className="flex flex-wrap gap-x-14 gap-y-6 border-y border-line py-6">
          <Stat label="Подтверждено" value={verified} tone="text-verified" />
          <Stat label="Вероятно" value={likely} tone="text-likely" />
          <Stat label="Отклонено" value={rejected.length} tone="text-unverified" />
          <Stat label="Первый профиль" value={`${(profile.elapsed_ms / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} с`} />
        </div>
        <p className="mt-6 text-[15px] leading-relaxed text-ink-2 max-w-[68ch]">
          Каждое фото получает оценку из независимых сигналов: вердикт ИИ-инспектора, тип источника, геометка внутри контура
          кампуса, сходство с эталонным снимком, название вуза в подписи, повторы в других источниках и дата. От 0,65 фото
          считается подтверждённым, от 0,40 вероятным, ниже отклоняется. Отклонённые кандидаты не прячем: ниже видно, почему
          каждый из них не прошёл. Разбор любого фото открывается кликом по нему.
        </p>
      </div>

      <div className="grid gap-x-16 gap-y-12 sm:grid-cols-2 lg:grid-cols-[320px_300px]">
        <CoverageBars categories={profile.categories} overall={profile.coverage.overall} />
        <div>
          <h3 className="text-[15px] font-semibold tracking-[-0.01em] pb-3 mb-3 border-b border-line">Как собран профиль</h3>
          <StageList stages={stages} />
        </div>
      </div>

      <div>
        <h2 className="h-sec mb-5">Источники<small>сколько кандидатов дал каждый</small></h2>
        <SourceList sources={sources} />
      </div>

      <section>
        <div className="flex items-baseline gap-3 mb-4">
          <h2 className="h-sec">Отклонённые<small className="mono">{rejected.length}</small></h2>
        </div>
        {rejected.length === 0 ? <div className="text-[15px] text-muted">Ничего не отклонено.</div> : (
          <div className="divide-y divide-line border-y border-line">
            {(allRejected ? rejected : rejected.slice(0, 12)).map((p) => (
              <div key={p.id} className="flex items-center gap-4 py-3">
                <button onClick={() => onOpen(p)} className="w-20 h-14 rounded-lg bg-soft overflow-hidden shrink-0 cursor-pointer">
                  <img src={thumbUrl(p)} alt="" loading="lazy" className="w-full h-full object-cover grayscale opacity-80" />
                </button>
                <div className="min-w-0 flex-1">
                  <button onClick={() => onOpen(p)} className="block max-w-full text-left text-[14px] text-ink truncate cursor-pointer hover:underline">{p.reject_reason}</button>
                  <SourceLink p={p} />
                </div>
                <span className="text-[13px] text-muted mono shrink-0">{Math.round(p.confidence * 100)}%</span>
              </div>
            ))}
          </div>
        )}
        {rejected.length > 12 && (
          <button onClick={() => setAllRejected(!allRejected)} className="btn-text mt-3">
            {allRejected ? 'Свернуть' : `Показать все ${rejected.length}`}<ChevronDown size={15} className={allRejected ? 'rotate-180' : ''} />
          </button>
        )}
      </section>

      {profile.plan && (
        <Disclosure open={queries} onToggle={() => setQueries(!queries)} title="Что искали" hint="запросы по темам и сетям">
          <div className="divide-y divide-line">
            {profile.plan.intents.filter((i) => i.enabled).map((i) => (
              <div key={i.key} className="py-3 text-[13.5px]">
                <div className="font-medium text-ink">{i.label}</div>
                {Object.entries(i.queries).map(([net, qs]) => qs.length > 0 && (
                  <div key={net} className="mt-0.5 text-muted"><span className="text-ink-2">{NET[net] ?? net}:</span> {qs.map((q) => `«${q}»`).join(', ')}</div>
                ))}
              </div>
            ))}
          </div>
          <Link to={`/settings/search?u=${profile.university.qid}`} className="btn-ghost mt-4 !h-9"><SlidersHorizontal size={15} /> Настроить поиск</Link>
        </Disclosure>
      )}

      <Disclosure open={tech} onToggle={() => setTech(!tech)} title="Технические детали" hint="этапы, источники, ИИ-инспектор, журнал, JSON">
        <JudgePanel p={profile} />
      </Disclosure>
    </div>
  )
}

const NET: Record<string, string> = { tiktok: 'TikTok', instagram: 'Instagram', google: 'Google Картинки', youtube: 'YouTube', maps: 'Google Карты' }

function Disclosure({ open, onToggle, title, hint, children }: { open: boolean; onToggle: () => void; title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="border-t border-line">
      <button onClick={onToggle} aria-expanded={open} className="w-full flex items-center gap-3 py-5 text-left cursor-pointer group">
        <span className="text-[17px] font-semibold tracking-[-0.01em]">{title}</span>
        {hint && <span className="text-[13px] text-muted">{hint}</span>}
        <ChevronDown size={18} className={`ml-auto text-muted group-hover:text-ink transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && <div className="pb-6">{children}</div>}
    </section>
  )
}
