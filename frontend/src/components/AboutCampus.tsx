import { useEffect, useState } from 'react'
import { RefreshCw, ExternalLink } from 'lucide-react'
import type { CampusFacts } from '../lib/types'
import { api } from '../lib/api'

const RATING: Record<string, { label: string; cls: string }> = {
  good: { label: 'хорошо', cls: 'bg-verified-soft text-verified' },
  mixed: { label: 'по-разному', cls: 'bg-likely-soft text-likely' },
  poor: { label: 'плохо', cls: 'bg-unverified-soft text-unverified' },
}
const KIND: Record<string, string> = { official: 'сайт вуза', wikipedia: 'Википедия', reviews: 'отзывы', web: 'веб' }

function Refs({ ids }: { ids: number[] }) {
  if (!ids.length) return null
  return <>{ids.map((k) => <a key={k} href={`#src-${k}`} className="ml-0.5 align-super text-[10px] text-brand hover:underline">[{k}]</a>)}</>
}

/** What is on the campus and what it is like - an LLM brief from texts found on the web (pipeline/campus_facts.py),
 *  every statement with its sources. Loaded on demand: the first time takes 20-60 s. */
export function AboutCampus({ qid }: { qid: string }) {
  const [facts, setFacts] = useState<CampusFacts | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const load = (refresh = false) => {
    setBusy(true); setError(null)
    api.facts(qid, refresh).then(setFacts).catch((e) => setError(String(e.message ?? e))).finally(() => setBusy(false))
  }
  useEffect(() => { setFacts(null); load() }, [qid]) // eslint-disable-line react-hooks/exhaustive-deps

  if (busy && !facts) {
    return (
      <div className="space-y-3">
        <div className="text-sm text-muted">Ищу в интернете тексты о корпусах, общежитиях, библиотеке, лабораториях, Wi-Fi и питании и собираю справку… обычно 20–60 секунд.</div>
        {Array.from({ length: 4 }).map((_, i) => <div key={i} className="shimmer h-20 rounded-lg" />)}
      </div>
    )
  }
  if (error) return <div className="text-sm text-unverified">Не удалось собрать справку: {error}</div>
  if (!facts) return null
  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <div className="min-w-0">
          <h2 className="text-xl font-extrabold">Что есть на кампусе</h2>
          {facts.summary && <p className="mt-1.5 text-[15px] leading-relaxed text-ink-2">{facts.summary}</p>}
          {facts.note && <p className="mt-1.5 text-sm text-likely">{facts.note}</p>}
        </div>
        <button onClick={() => load(true)} disabled={busy} className="ml-auto shrink-0 inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-line-2 bg-surface text-[13px] hover:border-brand hover:text-brand disabled:opacity-50" title="Собрать заново">
          <RefreshCw size={14} className={busy ? 'animate-spin' : ''} /> Обновить
        </button>
      </div>

      {facts.quick.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {facts.quick.map((q, i) => (
            <span key={i} className="inline-flex items-baseline gap-1 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[13px]">
              <span className="text-muted">{q.label}:</span> <span className="font-semibold">{q.value}</span><Refs ids={q.sources} />
            </span>
          ))}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {facts.sections.map((s) => (
          <div key={s.key} className="rounded-xl border border-line bg-surface p-4">
            <div className="flex items-center gap-2">
              <h3 className="font-bold">{s.title}</h3>
              {RATING[s.rating] && <span className={`ml-auto rounded-full px-2 py-0.5 text-[11px] font-semibold ${RATING[s.rating].cls}`} title={s.rating_note}>{RATING[s.rating].label}</span>}
            </div>
            <ul className="mt-2 space-y-1.5 text-[14px] leading-snug text-ink-2">
              {s.items.map((it, i) => <li key={i} className="pl-3 relative before:content-[''] before:absolute before:left-0 before:top-[0.55em] before:w-1 before:h-1 before:rounded-full before:bg-line-2">{it.text}<Refs ids={it.sources} /></li>)}
            </ul>
            {s.rating_note && <div className="mt-2 text-xs text-muted">{s.rating_note}</div>}
          </div>
        ))}
      </div>

      {facts.sources.length > 0 && (
        <div>
          <div className="text-xs uppercase tracking-wider font-semibold text-muted mb-1.5">Источники</div>
          <ol className="space-y-1 text-[12px] text-muted">
            {facts.sources.map((s) => (
              <li key={s.id} id={`src-${s.id}`} className="flex gap-1.5">
                <span className="mono">[{s.id}]</span>
                <span className="rounded bg-canvas px-1">{KIND[s.kind] ?? s.kind}</span>
                {s.url ? <a href={s.url} target="_blank" rel="noreferrer" className="truncate hover:text-brand inline-flex items-center gap-1">{s.title} <ExternalLink size={10} /></a> : <span className="truncate">{s.title}</span>}
              </li>
            ))}
          </ol>
          <div className="mt-2 text-[11px] text-muted">Справка составлена ИИ ({facts.model || '—'}) только по найденным текстам; где данных нет — так и написано.</div>
        </div>
      )}
    </div>
  )
}
