import { useEffect, useState } from 'react'
import { RefreshCw, ExternalLink } from 'lucide-react'
import type { CampusFacts } from '../lib/types'
import { api } from '../lib/api'

const RATING: Record<string, { label: string; dot: string }> = {
  good: { label: 'хорошо', dot: 'bg-verified' },
  mixed: { label: 'по-разному', dot: 'bg-likely' },
  poor: { label: 'плохо', dot: 'bg-unverified' },
}
const KIND: Record<string, string> = { official: 'сайт вуза', wikipedia: 'Википедия', reviews: 'отзывы', web: 'веб' }

function Refs({ ids }: { ids: number[] }) {
  if (!ids.length) return null
  return <sup className="ml-0.5 text-[10.5px] text-faint whitespace-nowrap">{ids.map((k, i) => <a key={k} href={`#src-${k}`} className="hover:text-ink">{i ? ',' : ''}{k}</a>)}</sup>
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
      <div className="space-y-4 max-w-3xl">
        <div className="text-[15px] text-muted">Читаем сайт вуза, Википедию и отзывы о корпусах, общежитиях, библиотеке, лабораториях, Wi-Fi и питании. Первый раз это занимает 20–60 секунд.</div>
        {Array.from({ length: 4 }).map((_, i) => <div key={i} className="shimmer h-16 rounded-lg" />)}
      </div>
    )
  }
  if (error) return <div className="text-[15px] text-unverified">Не удалось собрать справку: {error}</div>
  if (!facts) return null
  return (
    <div className="space-y-12">
      <div className="flex items-start gap-6">
        <div className="min-w-0">
          <h2 className="text-[20px] font-semibold tracking-[-0.015em]">Что есть на кампусе</h2>
          {facts.summary && <p className="mt-3 text-[17px] leading-[1.65] text-ink-2 max-w-[68ch]">{facts.summary}</p>}
          {facts.note && <p className="mt-2 text-[14px] text-likely">{facts.note}</p>}
        </div>
        <button onClick={() => load(true)} disabled={busy} className="btn-ghost !h-9 ml-auto shrink-0" title="Собрать справку заново">
          <RefreshCw size={15} className={busy ? 'animate-spin' : ''} /> Обновить
        </button>
      </div>

      {facts.quick.length > 0 && (
        <dl className="grid grid-cols-2 md:grid-cols-3 gap-x-10 gap-y-5 max-w-5xl">
          {facts.quick.map((q, i) => (
            <div key={i} className="min-w-0">
              <dt className="text-[13px] text-muted">{q.label}</dt>
              <dd className="mt-0.5 text-[16px] font-medium text-ink">{q.value}<Refs ids={q.sources} /></dd>
            </div>
          ))}
        </dl>
      )}

      <div className="grid gap-x-16 gap-y-10 md:grid-cols-2 max-w-6xl">
        {facts.sections.map((s) => (
          <section key={s.key} className="border-t border-line pt-5">
            <div className="flex items-baseline gap-3">
              <h3 className="text-[16px] font-semibold">{s.title}</h3>
              {RATING[s.rating] && (
                <span className="ml-auto inline-flex items-center gap-1.5 text-[12.5px] text-muted" title={s.rating_note}>
                  <i className={`w-1.5 h-1.5 rounded-full ${RATING[s.rating].dot}`} />{RATING[s.rating].label}
                </span>
              )}
            </div>
            <ul className="mt-3 space-y-2 text-[14.5px] leading-[1.55] text-ink-2">
              {s.items.map((it, i) => <li key={i} className="pl-4 relative before:content-[''] before:absolute before:left-0 before:top-[0.7em] before:w-1.5 before:h-px before:bg-faint">{it.text}<Refs ids={it.sources} /></li>)}
            </ul>
            {s.rating_note && <div className="mt-3 text-[13px] text-muted">{s.rating_note}</div>}
          </section>
        ))}
      </div>

      {facts.sources.length > 0 && (
        <details className="group max-w-5xl border-t border-line pt-4">
          <summary className="cursor-pointer list-none text-[14px] font-medium text-ink-2 hover:text-ink">Источники справки · {facts.sources.length}<span className="ml-2 text-muted font-normal">составлена ИИ ({facts.model || '—'}) только по найденным текстам</span></summary>
          <ol className="mt-3 space-y-1.5 text-[13px] text-muted">
            {facts.sources.map((s) => (
              <li key={s.id} id={`src-${s.id}`} className="flex gap-2 min-w-0">
                <span className="tnum w-6 shrink-0 text-right">{s.id}</span>
                <span className="shrink-0 text-faint w-20">{KIND[s.kind] ?? s.kind}</span>
                {s.url ? <a href={s.url} target="_blank" rel="noreferrer" className="truncate hover:text-ink inline-flex items-center gap-1">{s.title} <ExternalLink size={11} className="shrink-0" /></a> : <span className="truncate">{s.title}</span>}
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  )
}
