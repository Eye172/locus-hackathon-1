import { useState } from 'react'
import { Check, Loader2, MinusCircle, AlertTriangle, Circle, KeyRound, ExternalLink, Thermometer, Bus, Navigation, FileJson, ListChecks, Trash2, ChevronDown } from 'lucide-react'
import type { CategoryStats, Context, Description, Profile, SourceStatus, Stage } from '../lib/types'
import { CATEGORIES } from '../lib/types'
import { catLabel, useLang, useT, type Lang } from '../lib/i18n'
import { CoverageDot, coverageLabel } from './Badges'
import { store, useStoreVersion } from '../lib/store'

const ICON = {
  pending: <Circle size={12} className="text-slate-300" />,
  running: <Loader2 size={12} className="text-brand animate-spin" />,
  done: <Check size={12} className="text-verified" />,
  skipped: <MinusCircle size={12} className="text-likely" />,
  error: <AlertTriangle size={12} className="text-unverified" />,
  fetching: <Loader2 size={12} className="text-brand animate-spin" />,
  disabled: <KeyRound size={12} className="text-slate-400" />,
}
const AGENT: Record<string, Record<Lang, string>> = {
  facts: { ru: 'Факты', en: 'Facts', kk: 'Деректер' }, campus: { ru: 'Кампус · OSM', en: 'Campus · OSM', kk: 'Кампус · OSM' },
  collect: { ru: 'Scout', en: 'Scout', kk: 'Scout' }, fetch: { ru: 'Fetcher', en: 'Fetcher', kk: 'Fetcher' },
  inspect: { ru: 'ИИ-инспектор', en: 'AI inspector', kk: 'ЖИ-инспектор' },
  analyze: { ru: 'Curator', en: 'Curator', kk: 'Curator' }, assemble: { ru: 'Writer', en: 'Writer', kk: 'Writer' },
}

/** One-line agent strip: dot, agent name, time. Sources unfold below. */
export function AgentsStrip({ stages, sources, elapsed }: { stages: Record<string, Stage>; sources: Record<string, SourceStatus>; elapsed: number }) {
  const [open, setOpen] = useState(false)
  const lang = useLang()
  const t = useT()
  const list = Object.values(stages)
  const problems = list.filter((s) => s.status === 'skipped' || s.status === 'error')
  const srcs = Object.entries(sources)
  return (
    <div className="rule pt-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs">
        {list.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-1.5" title={s.detail ?? ''}>
            {ICON[s.status]}
            <span className={s.status === 'pending' ? 'text-slate-400' : 'text-ink-2'}>{AGENT[s.key]?.[lang] ?? s.label}</span>
            {s.ms != null && s.status !== 'running' && s.status !== 'pending' && <span className="mono text-muted">{(s.ms / 1000).toFixed(1)}s</span>}
          </span>
        ))}
        {srcs.length > 0 && (
          <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1 text-ink-2 hover:text-ink cursor-pointer">
            {t('home.sources').toLowerCase()} <span className="mono">{srcs.filter(([, s]) => s.status === 'done').length}/{srcs.length}</span><ChevronDown size={12} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
          </button>
        )}
        <span className="ml-auto mono text-muted">{(elapsed / 1000).toFixed(1)} s</span>
      </div>
      {problems.length > 0 && <div className="mt-1.5 text-xs text-likely">{problems.map((s) => `${AGENT[s.key]?.[lang] ?? s.label}: ${s.detail}`).join(' · ')}</div>}
      {open && (
        <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-1 text-xs">
          {srcs.map(([k, s]) => (
            <div key={k} className="flex items-center gap-2" title={s.detail ?? ''}>
              {ICON[s.status as keyof typeof ICON]}
              <span className="text-ink-2 truncate">{s.label}</span>
              <span className="mono text-muted ml-auto">{s.status === 'done' ? `${s.count} · ${s.ms} ms` : s.status === 'disabled' ? 'нет ключа' : s.status}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Coverage matrix: one row per category, filled squares = verified (max 6), hollow = likely. */
export function CoverageMatrix({ categories, overall, compact }: { categories: Record<string, CategoryStats>; overall?: string; compact?: boolean }) {
  const t = useT()
  const lang = useLang()
  return (
    <div>
      {!compact && (
        <div className="flex items-baseline justify-between mb-2">
          <h3 className="caps text-muted">{t('profile.coverage')}</h3>
          {overall && <span className="inline-flex items-center gap-1.5 text-xs"><CoverageDot level={overall} />{coverageLabel(overall, lang)}</span>}
        </div>
      )}
      <div className="space-y-1.5">
        {CATEGORIES.map((c) => {
          const s = categories[c]
          if (!s) return null
          const v = Math.min(6, s.verified), l = Math.min(6 - v, s.likely)
          return (
            <div key={c} className="flex items-center gap-2 text-xs">
              <span className={`${compact ? 'w-24' : 'w-32'} truncate text-ink-2`}>{catLabel(c, lang)}</span>
              <span className="flex gap-[3px]">
                {Array.from({ length: 6 }).map((_, i) => (
                  <i key={i} className={`block w-2.5 h-2.5 rounded-[2px] ${i < v ? 'bg-verified' : i < v + l ? 'bg-likely/70' : 'bg-slate-200'}`} />
                ))}
              </span>
              <span className="mono text-muted ml-auto">{s.verified}{s.likely ? `+${s.likely}` : ''}{s.rejected ? <span className="text-slate-400"> / {s.rejected}</span> : null}</span>
            </div>
          )
        })}
      </div>
      {!compact && <div className="mt-2 text-[11px] text-muted">заполнено — подтверждено · полупрозрачно — вероятно · серым — отклонено</div>}
    </div>
  )
}

export function DescriptionBlock({ d }: { d: Description }) {
  const t = useT()
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <h3 className="caps text-muted">{t('profile.description')}</h3>
        <span className="mono text-[11px] text-muted" title={d.note ?? ''}>{d.mode === 'llm' ? 'LLM' : 'шаблон'}</span>
      </div>
      <p className="mt-2 text-[15px] leading-relaxed border-l-2 border-brand pl-3">
        {d.sentences.map((s, i) => (
          <span key={i}>{s.text}{s.sources.map((id) => {
            const src = d.sources.find((x) => x.id === id)
            return src ? <a key={id} href={src.url} target="_blank" rel="noreferrer" className="ml-0.5 text-[10px] align-super text-brand hover:underline mono" title={src.label}>{id}</a> : null
          })} </span>
        ))}
      </p>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] mono text-muted">
        {d.sources.map((s) => <a key={s.id} href={s.url} target="_blank" rel="noreferrer" className="hover:text-brand inline-flex items-center gap-0.5">{s.id} {s.label}<ExternalLink size={10} /></a>)}
      </div>
    </div>
  )
}

export function ContextCards({ c }: { c: Context }) {
  const t = useT()
  const items = [
    c.distance_km != null && { icon: <Navigation size={14} />, v: `${c.distance_km} км`, l: `${t('profile.distance')}${c.center_name ? ` · ${c.center_name}` : ''}` },
    c.transport_stops != null && { icon: <Bus size={14} />, v: `${c.transport_stops}`, l: t('profile.stops') },
    c.climate && { icon: <Thermometer size={14} />, v: `${c.climate.jan > 0 ? '+' : ''}${c.climate.jan}° / ${c.climate.jul > 0 ? '+' : ''}${c.climate.jul}°`, l: `${t('profile.climate')} · январь / июль`, href: c.climate_url ?? undefined, title: c.climate_note ?? '' },
  ].filter(Boolean) as { icon: React.ReactNode; v: string; l: string; href?: string; title?: string }[]
  if (!items.length) return null
  return (
    <div>
      <h3 className="caps text-muted">{t('profile.context')}</h3>
      <div className="mt-2 grid grid-cols-3 gap-3">
        {items.map((it, i) => (
          <a key={i} href={it.href} target={it.href ? '_blank' : undefined} rel="noreferrer" title={it.title} className="block">
            <div className="mono text-xl font-medium tracking-tight">{it.v}</div>
            <div className="text-[11px] text-muted leading-tight mt-0.5 flex items-center gap-1">{it.icon}{it.l}</div>
          </a>
        ))}
      </div>
    </div>
  )
}

export function Timeline({ timeline, selected, onSelect }: { timeline: Record<string, number>; selected: string | null; onSelect: (y: string | null) => void }) {
  const years = Object.keys(timeline).sort()
  if (!years.length) return <div className="text-sm text-muted">Даты не найдены</div>
  const max = Math.max(...Object.values(timeline))
  return (
    <div className="card p-5">
      <div className="flex items-end gap-1.5 h-36">
        {years.map((y) => (
          <button key={y} onClick={() => onSelect(selected === y ? null : y)} title={`${y}: ${timeline[y]}`}
            className="flex-1 min-w-4 flex flex-col items-center justify-end h-full cursor-pointer group">
            <span className="mono text-[10px] text-muted mb-1 opacity-0 group-hover:opacity-100">{timeline[y]}</span>
            <div className={`w-full rounded-t-sm transition-colors ${selected === y ? 'bg-brand' : 'bg-slate-300 group-hover:bg-slate-400'}`} style={{ height: `${(timeline[y] / max) * 80}%` }} />
            <span className="mono text-[10px] text-muted mt-1">{y.slice(2)}</span>
          </button>
        ))}
      </div>
      <div className="mt-2 text-xs text-muted">Клик по году фильтрует сетку. Фото старше 8 лет помечены как «старое фото».</div>
    </div>
  )
}

export function VisitPlan({ qid }: { qid: string }) {
  useStoreVersion()
  const items = store.plan(qid)
  const [text, setText] = useState('')
  const add = () => { if (text.trim()) { store.addPlan(qid, text.trim()); setText('') } }
  return (
    <div>
      <h3 className="caps text-muted flex items-center gap-1.5"><ListChecks size={13} /> План визита</h3>
      <div className="mt-2 flex gap-2">
        <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()}
          placeholder="Что посмотреть на кампусе…" className="flex-1 min-w-0 rounded-lg border border-line-2 px-3 h-9 text-sm outline-none focus:border-ink" />
        <button className="btn-ghost !h-9 !px-3" onClick={add}>+</button>
      </div>
      {items.length === 0 && <div className="mt-2 text-xs text-muted">Пусто. Добавляйте здания и фото из паспорта.</div>}
      <ul className="mt-2 space-y-1">
        {items.map((i) => (
          <li key={i.id} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={i.done} onChange={() => store.togglePlan(qid, i.id)} className="accent-ink" />
            <span className={`flex-1 ${i.done ? 'line-through text-muted' : ''}`}>{i.label}</span>
            <button onClick={() => store.removePlan(qid, i.id)} className="text-slate-300 hover:text-unverified cursor-pointer"><Trash2 size={13} /></button>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function JudgePanel({ p }: { p: Profile }) {
  const lang = useLang()
  const t = useT()
  const download = () => {
    const blob = new Blob([JSON.stringify(p, null, 2)], { type: 'application/json' })
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `campuslens-${p.university.qid}.json`; a.click()
  }
  const Th = ({ children }: { children: React.ReactNode }) => <th className="text-left caps text-muted font-medium py-1.5 pr-3">{children}</th>
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card p-5">
          <h3 className="caps text-muted">{t('judge.stages')}</h3>
          <table className="mt-2 w-full text-xs">
            <thead><tr><Th>агент</Th><Th>статус</Th><Th>время</Th><Th>детали</Th></tr></thead>
            <tbody>{p.stages.map((s) => (
              <tr key={s.key} className="rule"><td className="py-1.5 pr-3">{AGENT[s.key]?.[lang] ?? s.label}</td><td className="py-1.5 pr-3 text-muted">{s.status}</td><td className="py-1.5 pr-3 mono text-right">{s.ms ?? '—'} ms</td><td className="py-1.5 text-muted">{s.detail}</td></tr>
            ))}</tbody>
          </table>
          <div className="mt-2 mono text-[11px] text-muted">итого {p.elapsed_ms} ms · {p.partial ? 'частичный' : 'полный'} · {p.generated_at}</div>
        </div>
        <div className="card p-5">
          <h3 className="caps text-muted">{t('judge.sources')}</h3>
          <table className="mt-2 w-full text-xs">
            <thead><tr><Th>источник</Th><Th>статус</Th><Th>фото</Th><Th>время</Th></tr></thead>
            <tbody>{Object.entries(p.sources_status).map(([k, s]) => (
              <tr key={k} className="rule"><td className="py-1.5 pr-3 mono">{k}</td><td className="py-1.5 pr-3 text-muted">{s.status}{s.detail ? ` · ${s.detail}` : ''}</td><td className="py-1.5 pr-3 mono text-right">{s.count}</td><td className="py-1.5 mono text-right">{s.ms} ms</td></tr>
            ))}</tbody>
          </table>
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-6">
        <div className="card p-5">
          <h3 className="caps text-muted">{t('judge.inspector')}</h3>
          {p.inspector ? (
            <div className="mt-2 text-xs text-ink-2 grid grid-cols-2 sm:grid-cols-3 gap-2 mono">
              <span>{p.inspector.model}</span>
              <span>{p.inspector.photos} / {p.inspector.submitted} photos</span>
              <span>{p.inspector.cached} from cache</span>
              <span>{p.inspector.calls} calls · {p.inspector.ms} ms</span>
              <span>{p.inspector.tokens_in} + {p.inspector.tokens_out} tokens</span>
              <span>{p.inspector.errors} errors</span>
            </div>
          ) : <div className="mt-2 text-xs text-muted">—</div>}
          <h3 className="caps text-muted mt-4">{t('judge.thresholds')}</h3>
          <div className="mt-2 text-xs text-ink-2 grid grid-cols-2 sm:grid-cols-4 gap-2 mono">
            <span>verified ≥ 0.65</span><span>likely ≥ 0.40</span><span>pHash ≤ 8</span><span>cosine ≥ 0.93</span>
            <span>strip &gt; 2.4:1</span><span>budget 25 s</span><span>source timeout 6 s</span><span>outdated &gt; 8 y</span>
          </div>
        </div>
        {p.reference && (
          <div className="card p-3 lg:w-56">
            <h3 className="caps text-muted px-2 pt-1">{t('judge.reference')}</h3>
            <img src={p.reference.url} alt="" className="mt-2 w-full aspect-[4/3] object-cover rounded-md" />
          </div>
        )}
      </div>
      <div className="card p-5">
        <div className="flex items-center justify-between">
          <h3 className="caps text-muted">{t('judge.log')}</h3>
          <div className="flex gap-2">
            <a href="/api/schema" target="_blank" rel="noreferrer" className="btn-ghost !h-8 !px-3 text-xs">/api/schema</a>
            <button onClick={download} className="btn-ghost !h-8 !px-3 text-xs"><FileJson size={13} /> {t('judge.json')}</button>
          </div>
        </div>
        <pre className="mt-2 mono text-[11px] leading-relaxed bg-ink text-slate-100 rounded-lg p-3 overflow-auto max-h-80">{p.log.join('\n')}</pre>
      </div>
    </div>
  )
}
