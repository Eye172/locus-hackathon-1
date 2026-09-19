import { useEffect, useRef, useState } from 'react'
import { Search, ArrowRight, MapPin } from 'lucide-react'
import { api } from '../lib/api'
import type { Candidate } from '../lib/types'
import { useT } from '../lib/i18n'

export function SearchBox({ onPick, onCandidates, dark, autoFocus, size = 'lg', direction = 'down' }: { onPick: (c: Candidate) => void; onCandidates?: (c: Candidate[]) => void; dark?: boolean; autoFocus?: boolean; size?: 'lg' | 'md'; direction?: 'down' | 'up' }) {
  const t = useT()
  const [q, setQ] = useState('')
  const [cands, setCands] = useState<Candidate[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [active, setActive] = useState(0)
  const timer = useRef<number | undefined>(undefined)
  const seq = useRef(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const suppress = useRef(false)

  useEffect(() => { if (autoFocus) inputRef.current?.focus({ preventScroll: true }) }, [autoFocus])
  useEffect(() => {
    window.clearTimeout(timer.current)
    if (suppress.current) { suppress.current = false; return }
    if (q.trim().length < 2) { setCands(null); return }
    timer.current = window.setTimeout(async () => {
      const my = ++seq.current
      setLoading(true)
      try { const r = await api.search(q.trim()); if (my === seq.current) { setCands(r.candidates); setActive(0); onCandidates?.(r.candidates) } }
      catch { if (my === seq.current) setCands([]) }
      finally { if (my === seq.current) setLoading(false) }
    }, 220)
    return () => window.clearTimeout(timer.current)
  }, [q])

  const pick = (c: Candidate) => { seq.current++; suppress.current = true; setCands(null); setQ(c.label); inputRef.current?.blur(); onPick(c) }
  const submit = async () => {
    if (!q.trim()) return
    let list = cands
    if (!list) { setLoading(true); list = (await api.search(q.trim())).candidates; setLoading(false) }
    if (list.length) pick(list[Math.min(active, list.length - 1)])
  }
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' && cands) { e.preventDefault(); setActive((a) => Math.min(a + 1, cands.length - 1)) }
    else if (e.key === 'ArrowUp' && cands) { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
    else if (e.key === 'Enter') submit()
    else if (e.key === 'Escape') setCands(null)
  }

  const box = dark ? 'bg-white/10 border-white/15 text-white backdrop-blur-xl focus-within:border-white/50 focus-within:bg-white/[0.13]' : 'bg-white border-line-2 text-ink focus-within:border-ink'
  const drop = dark ? 'bg-[#0B1222]/95 border-white/10 text-white backdrop-blur-xl' : 'bg-white border-line'
  const h = size === 'lg' ? 'h-14 text-base' : 'h-11 text-sm'
  return (
    <div className="relative w-full">
      <div className={`flex items-center gap-2 pl-4 pr-2 rounded-2xl border transition ${box} ${h}`}>
        <Search size={20} className={dark ? 'text-white/60' : 'text-muted'} />
        <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey}
          placeholder={t('home.placeholder')} className="flex-1 bg-transparent outline-none placeholder:text-current/50" />
        <button onClick={submit} disabled={!q.trim()} className="btn-primary !rounded-xl !py-2 !px-3 sm:!px-3.5 shrink-0"><span className="hidden sm:inline">{t('home.go')}</span> <ArrowRight size={16} /></button>
      </div>
      {(cands || loading) && (
        <div className={`absolute left-0 right-0 ${direction === 'up' ? 'bottom-full mb-2' : 'mt-2'} p-2 rounded-2xl border shadow-2xl z-40 max-h-[360px] overflow-auto pop ${drop}`}>
          {loading && !cands && <div className="p-3 text-sm opacity-60">…</div>}
          {cands && cands.length === 0 && !loading && <div className="p-3 text-sm opacity-70">{t('home.nothing')}</div>}
          {cands && cands.length > 0 && (
            <>
              <div className="px-3 pt-1 pb-2 text-[12px] opacity-60">{t('home.didyoumean')}</div>
              {cands.map((c, i) => (
                <button key={c.qid} onMouseEnter={() => setActive(i)} onClick={() => pick(c)}
                  className={`w-full flex items-center gap-3 px-3 py-2 rounded-xl text-left cursor-pointer ${i === active ? (dark ? 'bg-white/10' : 'bg-soft') : ''}`}>
                  <div className={`w-9 h-9 rounded-lg grid place-items-center overflow-hidden shrink-0 ${dark ? 'bg-white/10' : 'bg-soft'}`}>
                    {c.logo_url ? <img src={c.logo_url} alt="" className="w-9 h-9 object-contain" loading="lazy" /> : <MapPin size={16} className="opacity-60" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium truncate">{c.label}{c.origin === 'web' && <span className="ml-2 align-middle text-[11px] font-normal opacity-60">· {t('search.web')}</span>}</div>
                    <div className="text-xs opacity-60 truncate">{[c.city, c.country].filter(Boolean).join(', ')}{c.description ? ` · ${c.description}` : ''}</div>
                  </div>
                  <span className="text-[11px] opacity-40 code">{c.qid}</span>
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}
