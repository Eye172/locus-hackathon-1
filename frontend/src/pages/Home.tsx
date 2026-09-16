import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, ArrowRight, MapPin, Clock, Images, KeyRound, Sparkles } from 'lucide-react'
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
        <span className="chip bg-brand-soft text-brand mb-4"><Sparkles size={13} /> LOCUS Hackathon 2026 · кейс 1</span>
        <h1 className="text-3xl sm:text-5xl font-extrabold tracking-tight leading-tight">{t('home.title')}</h1>
        <p className="mt-4 text-muted text-base sm:text-lg">{t('home.subtitle')}</p>
      </div>

      <div className="relative mt-8 max-w-2xl mx-auto">
        <div className="card flex items-center gap-2 p-2 pl-4 focus-within:ring-2 focus-within:ring-brand/30">
          <Search className="text-muted shrink-0" size={20} />
          <input
            value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && submit()}
            placeholder={t('home.placeholder')} ref={inputRef}
            className="flex-1 bg-transparent outline-none text-base py-2" />
          <button onClick={submit} className="btn-primary" disabled={!q.trim()}>{t('home.go')} <ArrowRight size={16} /></button>
        </div>
        {(cands || loading) && (
          <div className="card absolute left-0 right-0 mt-2 p-2 z-30 max-h-96 overflow-auto pop">
            {loading && !cands && <div className="p-3 text-sm text-muted">…</div>}
            {cands && cands.length === 0 && !loading && <div className="p-3 text-sm text-muted">{t('home.nothing')}</div>}
            {cands && cands.length > 0 && (
              <>
                <div className="px-3 pt-1 pb-2 text-[11px] uppercase tracking-wide text-muted font-semibold">{t('home.didyoumean')}</div>
                {cands.map((c) => (
                  <button key={c.qid} onClick={() => go(c)} className="w-full flex items-center gap-3 px-3 py-2 rounded-xl hover:bg-slate-50 text-left cursor-pointer">
                    <div className="w-9 h-9 rounded-lg bg-slate-100 grid place-items-center overflow-hidden shrink-0">
                      {c.logo_url ? <img src={c.logo_url} alt="" className="w-9 h-9 object-contain" loading="lazy" /> : <MapPin size={16} className="text-muted" />}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="font-medium truncate">{c.label}</div>
                      <div className="text-xs text-muted truncate">{[c.city, c.country].filter(Boolean).join(', ')}{c.description ? ` · ${c.description}` : ''}</div>
                    </div>
                    <span className="text-[11px] text-muted font-mono">{c.qid}</span>
                  </button>
                ))}
              </>
            )}
          </div>
        )}
      </div>

      <section className="mt-14">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">{t('home.how')}</h2>
        <div className="mt-4 grid grid-cols-1 sm:grid-cols-5 gap-3">
          {steps.map((s, i) => (
            <div key={i} className="card p-4">
              <div className="flex items-center gap-2">
                <span className="w-6 h-6 rounded-full bg-brand text-white text-xs font-bold grid place-items-center">{i + 1}</span>
                <span className="font-semibold">{s.title}</span>
              </div>
              <p className="mt-2 text-xs text-muted leading-relaxed">{s.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {recent.length > 0 && (
        <section className="mt-12">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">{t('home.recent')}</h2>
          <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {recent.map((r) => (
              <button key={r.qid} onClick={() => nav(`/u/${r.qid}`)} className="card p-4 text-left hover:border-brand/40 cursor-pointer">
                <div className="font-medium truncate">{r.name}</div>
                <div className="mt-1 text-xs text-muted flex items-center gap-3">
                  {r.city && <span className="inline-flex items-center gap-1"><MapPin size={12} />{r.city}</span>}
                  <span className="inline-flex items-center gap-1"><Images size={12} />{r.photos}</span>
                  <span className="inline-flex items-center gap-1"><Clock size={12} />{(r.elapsed_ms / 1000).toFixed(1)} s</span>
                </div>
              </button>
            ))}
          </div>
        </section>
      )}

      {Object.keys(sources).length > 0 && (
        <section className="mt-12">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">{t('home.sources')}</h2>
          <div className="mt-3 flex flex-wrap gap-2">
            {Object.entries(sources).map(([k, s]) => (
              <span key={k} className={`chip ${s.enabled ? 'bg-verified-soft text-verified' : 'bg-slate-100 text-muted'}`} title={s.env}>
                {!s.enabled && <KeyRound size={12} />}{k}{!s.enabled && ` · ${t('home.keyMissing')}`}
              </span>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
