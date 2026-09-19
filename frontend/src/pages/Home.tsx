import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, ArrowRight, MapPin, Clock, Images, KeyRound } from 'lucide-react'
import { api } from '../lib/api'
import type { Candidate, RecentItem } from '../lib/types'
import { useT } from '../lib/i18n'

export default function Home() {
  const t = useT()
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [cands, setCands] = useState<Candidate[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [recent, setRecent] = useState<RecentItem[]>([])
  const [sources, setSources] = useState<Record<string, { enabled: boolean; needs_key: boolean; env?: string }>>({})
  const timer = useRef<number | undefined>(undefined)
  const seq = useRef(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.recent().then((r) => setRecent(r.recent)).catch(() => {})
    api.sources().then((r) => setSources(r.sources)).catch(() => {})
    inputRef.current?.focus({ preventScroll: true })
  }, [])

  useEffect(() => {
    window.clearTimeout(timer.current)
    if (q.trim().length < 2) { setCands(null); return }
    timer.current = window.setTimeout(async () => {
      const my = ++seq.current
      setLoading(true)
      try {
        const r = await api.search(q.trim())
        if (my === seq.current) setCands(r.candidates)
      } catch { if (my === seq.current) setCands([]) }
      finally { if (my === seq.current) setLoading(false) }
    }, 250)
    return () => window.clearTimeout(timer.current)
  }, [q])

  const go = (c: Candidate) => nav(`/u/${c.qid}`)
  const submit = async () => {
    if (!q.trim()) return
    let list = cands
    if (!list) { setLoading(true); list = (await api.search(q.trim())).candidates; setCands(list); setLoading(false) }
    if (list.length) go(list[0])
  }

  const steps = [1, 2, 3, 4, 5].map((i) => ({ title: t(`home.step${i}`), desc: t(`home.step${i}d`) }))

  return (
    <div className="mx-auto max-w-5xl px-4 py-10 sm:py-16">
      <div className="text-center max-w-3xl mx-auto">
        <h1 className="text-[32px] sm:text-[44px] leading-[1.1]">{t('home.title')}</h1>
        <p className="mt-5 text-muted text-[17px] leading-[1.6]">{t('home.subtitle')}</p>
      </div>

      <div className="relative mt-10 max-w-2xl mx-auto">
        <div className="flex items-center gap-2 p-2 pl-4 rounded-xl border border-line-2 bg-surface transition-colors focus-within:border-ink">
          <Search className="text-muted shrink-0" size={20} />
          <input
            value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && submit()}
            placeholder={t('home.placeholder')} ref={inputRef}
            className="flex-1 bg-transparent outline-none text-base py-2" />
          <button onClick={submit} className="btn-primary" disabled={!q.trim()}>{t('home.go')} <ArrowRight size={16} /></button>
        </div>
        {(cands || loading) && (
          <div className="card absolute left-0 right-0 mt-2 p-2 z-30 max-h-96 overflow-auto pop shadow-[0_12px_40px_rgba(11,13,18,0.10)]">
            {loading && !cands && <div className="p-3 text-sm text-muted">…</div>}
            {cands && cands.length === 0 && !loading && <div className="p-3 text-sm text-muted">{t('home.nothing')}</div>}
            {cands && cands.length > 0 && (
              <>
                <div className="px-3 pt-1 pb-2 text-[12px] text-muted">{t('home.didyoumean')}</div>
                {cands.map((c) => (
                  <button key={c.qid} onClick={() => go(c)} className="w-full flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-soft text-left cursor-pointer">
                    <div className="w-9 h-9 rounded-lg bg-soft grid place-items-center overflow-hidden shrink-0">
                      {c.logo_url ? <img src={c.logo_url} alt="" className="w-9 h-9 object-contain" loading="lazy" /> : <MapPin size={16} className="text-muted" />}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="font-medium truncate">{c.label}</div>
                      <div className="text-xs text-muted truncate">{[c.city, c.country].filter(Boolean).join(', ')}{c.description ? ` · ${c.description}` : ''}</div>
                    </div>
                    <span className="text-[11px] text-faint code">{c.qid}</span>
                  </button>
                ))}
              </>
            )}
          </div>
        )}
      </div>

      <section className="mt-20">
        <h2 className="h-sec">{t('home.how')}</h2>
        <ol className="mt-6 grid grid-cols-1 sm:grid-cols-5 gap-x-8 gap-y-6 border-t border-line pt-6">
          {steps.map((s, i) => (
            <li key={i}>
              <div className="text-[13px] text-faint">0{i + 1}</div>
              <div className="mt-2 text-[15px] font-medium">{s.title}</div>
              <p className="mt-1.5 text-[13px] text-muted leading-relaxed">{s.desc}</p>
            </li>
          ))}
        </ol>
      </section>

      {recent.length > 0 && (
        <section className="mt-16">
          <h2 className="h-sec">{t('home.recent')}</h2>
          <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-x-12">
            {recent.map((r) => (
              <button key={r.qid} onClick={() => nav(`/u/${r.qid}`)} className="row-link group min-w-0">
                <span className="min-w-0 flex-1">
                  <span className="block text-[15px] font-medium truncate">{r.name}</span>
                  <span className="mt-0.5 text-[13px] text-muted flex items-center gap-3">
                    {r.city && <span className="inline-flex items-center gap-1"><MapPin size={12} />{r.city}</span>}
                    <span className="inline-flex items-center gap-1"><Images size={12} />{r.photos}</span>
                    <span className="inline-flex items-center gap-1"><Clock size={12} />{(r.elapsed_ms / 1000).toFixed(1)} s</span>
                  </span>
                </span>
                <ArrowRight size={16} className="text-faint group-hover:text-ink transition-colors shrink-0 mr-1" />
              </button>
            ))}
          </div>
        </section>
      )}

      {Object.keys(sources).length > 0 && (
        <section className="mt-16">
          <h2 className="h-sec">{t('home.sources')}</h2>
          <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-[13px]">
            {Object.entries(sources).map(([k, s]) => (
              <span key={k} className={`inline-flex items-center gap-1.5 ${s.enabled ? 'text-ink-2' : 'text-faint'}`} title={s.env}>
                {s.enabled ? <i className="w-1.5 h-1.5 rounded-full bg-verified" /> : <KeyRound size={12} />}{k}{!s.enabled && ` · ${t('home.keyMissing')}`}
              </span>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
