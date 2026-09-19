import { useState } from 'react'
import { Check, Loader2, MinusCircle, AlertTriangle, Circle, KeyRound, FileJson, Trash2 } from 'lucide-react'
import type { Profile, SourceStatus, Stage } from '../lib/types'
import { useLang, useT, type Lang } from '../lib/i18n'
import { store, useStoreVersion } from '../lib/store'

const ICON = {
  pending: <Circle size={12} className="text-line-2" />,
  running: <Loader2 size={12} className="text-ink animate-spin" />,
  done: <Check size={12} className="text-verified" />,
  skipped: <MinusCircle size={12} className="text-likely" />,
  error: <AlertTriangle size={12} className="text-unverified" />,
  fetching: <Loader2 size={12} className="text-ink animate-spin" />,
  disabled: <KeyRound size={12} className="text-faint" />,
}
const AGENT: Record<string, Record<Lang, string>> = {
  facts: { ru: 'Факты', en: 'Facts', kk: 'Деректер' }, campus: { ru: 'Кампус · OSM', en: 'Campus · OSM', kk: 'Кампус · OSM' },
  collect: { ru: 'Поиск фото', en: 'Scout', kk: 'Scout' }, fetch: { ru: 'Загрузка', en: 'Fetcher', kk: 'Fetcher' },
  inspect: { ru: 'ИИ-инспектор', en: 'AI inspector', kk: 'ЖИ-инспектор' },
  analyze: { ru: 'Отбор', en: 'Curator', kk: 'Curator' }, assemble: { ru: 'Описание', en: 'Writer', kk: 'Writer' },
}

/** The build, stage by stage (the «Проверка» tab). */
export function StageList({ stages }: { stages: Record<string, Stage> }) {
  const lang = useLang()
  const name = (s: Stage) => AGENT[s.key]?.[lang] ?? s.label
  return (
    <ol className="space-y-2.5 text-[14px]">
      {Object.values(stages).map((s) => (
        <li key={s.key} title={s.detail ?? ''}>
          <div className="flex items-center gap-2">
            {ICON[s.status]}
            <span className={s.status === 'pending' ? 'text-faint' : 'text-ink-2'}>{name(s)}</span>
            {s.ms != null && s.status !== 'running' && s.status !== 'pending' && <span className="tnum text-muted ml-auto">{(s.ms / 1000).toFixed(1)} с</span>}
          </div>
          {(s.status === 'skipped' || s.status === 'error') && s.detail && <div className="ml-5 mt-0.5 text-[12.5px] text-likely">{s.detail}</div>}
        </li>
      ))}
    </ol>
  )
}

/** Every source with how many candidates it gave. */
export function SourceList({ sources }: { sources: Record<string, SourceStatus> }) {
  const t = useT()
  const srcs = Object.entries(sources).sort((a, b) => (b[1].count ?? 0) - (a[1].count ?? 0))
  if (!srcs.length) return null
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-12 text-[14px]">
      {srcs.map(([k, s]) => (
        <div key={k} className="flex items-center gap-2 min-w-0 py-2.5 border-t border-line" title={s.detail ?? s.label}>
          {ICON[s.status as keyof typeof ICON]}
          <span className="text-ink-2 truncate">{s.label}</span>
          <span className="tnum text-muted ml-auto shrink-0">{s.status === 'done' ? s.count : s.status === 'disabled' ? t('home.keyMissing') : s.status === 'fetching' ? '…' : s.status}</span>
        </div>
      ))}
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
      <h3 className="text-[15px] font-semibold tracking-[-0.01em] pb-3 mb-4 border-b border-line">План визита</h3>
      <div className="flex gap-2">
        <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()}
          placeholder="Что посмотреть на кампусе" className="flex-1 min-w-0 rounded-lg border border-line-2 px-3 h-9 text-[14px] outline-none focus:border-ink placeholder:text-faint" />
        <button className="btn-ghost !h-9 !w-9 !px-0 justify-center text-[18px] leading-none" onClick={add} aria-label="Добавить">+</button>
      </div>
      {items.length === 0 && <div className="mt-2 text-[12.5px] text-faint">Добавляйте места сюда или кнопкой «В план визита» у фото.</div>}
      <ul className="mt-3 space-y-1.5">
        {items.map((i) => (
          <li key={i.id} className="flex items-center gap-2 text-[14px] group">
            <input type="checkbox" checked={i.done} onChange={() => store.togglePlan(qid, i.id)} className="accent-ink" />
            <span className={`flex-1 ${i.done ? 'line-through text-muted' : 'text-ink-2'}`}>{i.label}</span>
            <button onClick={() => store.removePlan(qid, i.id)} aria-label="Удалить" className="text-faint opacity-0 group-hover:opacity-100 hover:text-unverified cursor-pointer"><Trash2 size={14} /></button>
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
    <div className="space-y-10">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-12 gap-y-10">
        <div>
          <h3 className="text-[14px] font-semibold">{t('judge.stages')}</h3>
          <table className="mt-2 w-full text-xs">
            <thead><tr><Th>агент</Th><Th>статус</Th><Th>время</Th><Th>детали</Th></tr></thead>
            <tbody>{p.stages.map((s) => (
              <tr key={s.key} className="rule"><td className="py-1.5 pr-3">{AGENT[s.key]?.[lang] ?? s.label}</td><td className="py-1.5 pr-3 text-muted">{s.status}</td><td className="py-1.5 pr-3 mono text-right">{s.ms ?? '—'} ms</td><td className="py-1.5 text-muted">{s.detail}</td></tr>
            ))}</tbody>
          </table>
          <div className="mt-2 mono text-[11px] text-muted">итого {p.elapsed_ms} ms · {p.partial ? 'частичный' : 'полный'} · {p.generated_at}</div>
        </div>
        <div>
          <h3 className="text-[14px] font-semibold">{t('judge.sources')}</h3>
          <table className="mt-2 w-full text-xs">
            <thead><tr><Th>источник</Th><Th>статус</Th><Th>фото</Th><Th>время</Th></tr></thead>
            <tbody>{Object.entries(p.sources_status).map(([k, s]) => (
              <tr key={k} className="rule"><td className="py-1.5 pr-3 mono">{k}</td><td className="py-1.5 pr-3 text-muted">{s.status}{s.detail ? ` · ${s.detail}` : ''}</td><td className="py-1.5 pr-3 mono text-right">{s.count}</td><td className="py-1.5 mono text-right">{s.ms} ms</td></tr>
            ))}</tbody>
          </table>
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-x-12 gap-y-10">
        <div>
          <h3 className="text-[14px] font-semibold">{t('judge.inspector')}</h3>
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
          <h3 className="text-[14px] font-semibold mt-6">{t('judge.thresholds')}</h3>
          <div className="mt-2 text-xs text-ink-2 grid grid-cols-2 sm:grid-cols-4 gap-2 mono">
            <span>verified ≥ 0.65</span><span>likely ≥ 0.40</span><span>pHash ≤ 8</span><span>cosine ≥ 0.93</span>
            <span>strip &gt; 2.4:1</span><span>budget 25 s</span><span>source timeout 6 s</span><span>outdated &gt; 8 y</span>
          </div>
        </div>
        {p.reference && (
          <div className="lg:w-56">
            <h3 className="text-[14px] font-semibold">{t('judge.reference')}</h3>
            <img src={p.reference.url} alt="" className="mt-2 w-full aspect-[4/3] object-cover rounded-md" />
          </div>
        )}
      </div>
      <div>
        <div className="flex items-center justify-between">
          <h3 className="text-[14px] font-semibold">{t('judge.log')}</h3>
          <div className="flex gap-2">
            <a href="/api/schema" target="_blank" rel="noreferrer" className="btn-ghost !h-8 !px-3 text-xs">/api/schema</a>
            <button onClick={download} className="btn-ghost !h-8 !px-3 text-xs"><FileJson size={13} /> {t('judge.json')}</button>
          </div>
        </div>
        <pre className="mt-3 code text-[11px] leading-relaxed bg-soft text-ink-2 rounded-lg p-4 overflow-auto max-h-80">{p.log.join('\n')}</pre>
      </div>
    </div>
  )
}
