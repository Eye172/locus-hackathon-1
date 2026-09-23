import { Fragment, memo, useEffect, useRef, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { ArrowRight, ArrowUp, Box, Loader2, RotateCcw, Sparkles, Square } from 'lucide-react'
import { api, compareSheets, streamAdvisor, thumbUrl, type AdvisorSheet } from '../lib/api'
import { sourceName } from '../components/SourceLink'
import { pickHero } from '../components/CampusReveal'
import type { Candidate, Photo, Profile } from '../lib/types'
import { CATEGORIES } from '../lib/types'
import { catLabel, uniName, useLang, useT, type Lang } from '../lib/i18n'
import { SearchBox } from '../components/SearchBox'
import { store, useStoreVersion } from '../lib/store'

type T = (key: string) => string
type Msg = { role: 'user' | 'assistant'; content: string }

const deg = (v: number | null | undefined) => {
  if (v == null) return '—'
  const r = Math.round(v)
  return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r)}°`
}
const money = (v: number, cur: string, lang: Lang) => `${v.toLocaleString(lang)} ${cur === 'KZT' ? '₸' : cur}`

/* ---------- a small Markdown renderer for the advisor: ### headings, - lists, **bold** ---------- */
function inline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') && part.length > 4 ? <strong key={i} className="font-semibold text-ink">{part.slice(2, -2)}</strong> : <Fragment key={i}>{part}</Fragment>)
}
// memoised: while an answer streams only its own bubble is parsed again, not every earlier message
const Markdown = memo(function Markdown({ text, names }: { text: string; names?: [string, string] }) {
  const blocks: React.ReactNode[] = []
  // a heading naming one of the two universities gets its A / B mark, as on the cards
  const mark = (h: string) => {
    const i = names ? names.findIndex((n) => n && h.toLowerCase().includes(n.toLowerCase())) : -1
    return i < 0 ? null : <span className="mr-2 inline-flex items-center justify-center w-5 h-5 rounded-full bg-ink text-white text-[11px] align-[2px]">{i ? 'B' : 'A'}</span>
  }
  let list: string[] = []
  const flush = () => {
    if (!list.length) return
    blocks.push(<ul key={`u${blocks.length}`} className="my-2 space-y-1.5">{list.map((li, i) => <li key={i} className="flex gap-2.5"><span className="mt-[11px] w-1.5 h-px bg-ink-2 shrink-0" /><span>{inline(li)}</span></li>)}</ul>)
    list = []
  }
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    const li = /^\s*[-*•]\s+(.*)$/.exec(line)
    if (li) { list.push(li[1]); continue }
    flush()
    const h = /^#{1,4}\s+(.*)$/.exec(line)
    if (h) { blocks.push(<h3 key={`h${blocks.length}`} className="mt-6 first:mt-0 mb-1 text-[17px] font-semibold tracking-[-0.01em] text-ink">{mark(h[1])}{inline(h[1])}</h3>); continue }
    if (line.trim()) blocks.push(<p key={`p${blocks.length}`} className="my-2">{inline(line)}</p>)
  }
  flush()
  return <div className="text-[15px] leading-[1.65] text-ink-2">{blocks}</div>
}, (a, b) => a.text === b.text && a.names?.[0] === b.names?.[0] && a.names?.[1] === b.names?.[1])

function Picker({ label, value, onPick, t }: { label: string; value: Candidate | null; onPick: (c: Candidate | null) => void; t: T }) {
  useStoreVersion()
  const saved = Object.entries(store.saved())
  return (
    <div className="min-w-0">
      <div className="lbl mb-2">{label}</div>
      {value ? (
        <div className="flex items-center justify-between gap-2 h-11 px-3.5 rounded-lg border border-line-2 bg-surface">
          <span className="font-medium truncate">{value.label}</span>
          <button className="btn-text !text-[13px]" onClick={() => onPick(null)}>{t('adv.change')}</button>
        </div>
      ) : (
        <>
          <SearchBox onPick={onPick} size="md" />
          {saved.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{saved.slice(0, 6).map(([qid, s]) => <button key={qid} onClick={() => onPick({ qid, label: s.name, city: s.city, score: 1, origin: 'index' })} className="filter border-line">{s.name}</button>)}</div>}
        </>
      )}
    </div>
  )
}

function Fact({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex items-baseline justify-between gap-4 py-2.5 border-t border-line text-[14px]"><span className="text-muted">{k}</span><span className="text-right text-ink mono">{v}</span></div>
}

function PhotoTile({ p, className = '' }: { p: Photo; className?: string }) {
  return (
    <a href={p.page_url} target="_blank" rel="noreferrer" title={`${sourceName(p)} ↗`} className={`group relative block overflow-hidden rounded-lg bg-soft ${className}`}>
      <img src={thumbUrl(p)} alt={p.title ?? ''} loading="lazy" className="w-full h-full object-cover transition-[filter] duration-150 group-hover:brightness-90" />
    </a>
  )
}

function UniColumn({ side, p, s, t, lang }: { side: 'A' | 'B'; p: Profile; s?: AdvisorSheet; t: T; lang: Lang }) {
  const u = p.university
  const photos = pickHero(p.photos, 4, (p as Profile & { cover?: string[] }).cover)
  const around = s?.around_campus
  const center = around?.city_center
  const dorms = around?.dorms_nearby
  const cl = s?.climate
  return (
    <article className="min-w-0">
      <div className="flex items-center gap-2 text-[12.5px] font-medium text-muted"><span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-ink text-white text-[11px]">{side}</span>{[u.city, u.country].filter(Boolean).join(', ')}</div>
      <Link to={`/u/${u.qid}`} className="mt-2 block font-display text-[24px] leading-[1.15] text-ink hover:underline decoration-line-2 underline-offset-4">{uniName(u, lang)}</Link>
      {photos.length > 0 && (
        <div className="mt-4 space-y-2">
          <PhotoTile p={photos[0]} className="aspect-[4/3]" />
          {photos.length > 1 && <div className="grid grid-cols-3 gap-2">{photos.slice(1, 4).map((x) => <PhotoTile key={x.id} p={x} className="aspect-square" />)}</div>}
        </div>
      )}
      <div className="mt-4 flex gap-2">
        <Link to={`/?u=${u.qid}`} className="btn-ghost !h-9 !px-3 !text-[13.5px]"><Box size={15} strokeWidth={1.75} />{t('adv.map3d')}</Link>
        <Link to={`/u/${u.qid}`} className="btn-ghost !h-9 !px-3 !text-[13.5px]">{t('adv.profile')}<ArrowRight size={14} strokeWidth={1.75} /></Link>
      </div>
      <div className="mt-5">
        <Fact k={t('adv.f.founded')} v={u.founded ?? '—'} />
        <Fact k={t('adv.f.students')} v={u.students ? u.students.toLocaleString(lang) : '—'} />
        {center?.distance_km != null && <Fact k={t('adv.f.center')} v={<>{center.distance_km.toLocaleString(lang)} км{center.walk_min ? <span className="text-muted"> · {t('adv.walk')} ≈{center.walk_min} мин</span> : null}</>} />}
        {dorms && <Fact k={t('adv.f.dorms')} v={<>{dorms.count}{dorms.nearest[0] ? <span className="text-muted"> · {t('adv.nearest')} {dorms.nearest[0].distance_m} м</span> : null}</>} />}
        {cl && <Fact k={t('adv.f.climate')} v={`${deg(cl.seasons.winter.t_mean)} / ${deg(cl.seasons.summer.t_mean)}`} />}
        {cl && <Fact k={t('adv.f.comfort')} v={cl.comfort_days.comfortable} />}
        {cl?.sun_hours != null && <Fact k={t('adv.f.sun')} v={`${cl.sun_hours.toLocaleString(lang)} ч`} />}
        {s?.budget && <Fact k={t('adv.f.budget')} v={money(s.budget.total_with_dorm, s.budget.currency, lang)} />}
        {!s && <div className="mt-2 space-y-2"><div className="h-9 rounded shimmer" /><div className="h-9 rounded shimmer" /></div>}
      </div>
    </article>
  )
}

function Advisor({ a, b, names, t, lang }: { a: string; b: string; names: [string, string]; t: T; lang: Lang }) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [run, setRun] = useState(0)
  const box = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const ctrl = useRef<AbortController | null>(null)
  const stick = useRef(true)   // follow the text while it streams, unless the reader scrolled up

  const send = (history: Msg[]) => {
    ctrl.current?.abort()
    const c = new AbortController(); ctrl.current = c
    setErr(null); setBusy(true); stick.current = true
    setMsgs([...history, { role: 'assistant', content: '' }])
    // tokens come every ~12 ms: they are gathered and drawn once per frame, not one render (and one scroll) per token
    let pending = '', frame = 0
    const flush = () => {
      frame = 0
      if (c.signal.aborted || !pending) return
      const add = pending; pending = ''
      setMsgs((m) => { const n = [...m]; n[n.length - 1] = { role: 'assistant', content: n[n.length - 1].content + add }; return n })
    }
    void streamAdvisor({ a, b, lang, messages: history }, (tok) => {
      if (c.signal.aborted) return
      pending += tok
      if (!frame) frame = requestAnimationFrame(flush)
    }, () => { if (!c.signal.aborted) { cancelAnimationFrame(frame); flush(); setBusy(false) } }, (e) => {
      if (c.signal.aborted) return
      cancelAnimationFrame(frame); flush()
      setErr(e); setBusy(false)
      setMsgs((m) => (m.length && m[m.length - 1].role === 'assistant' && !m[m.length - 1].content ? m.slice(0, -1) : m))
    }, c.signal)
  }
  const stop = () => {
    ctrl.current?.abort(); setBusy(false)
    setMsgs((m) => (m.length && m[m.length - 1].role === 'assistant' && !m[m.length - 1].content ? m.slice(0, -1) : m))
  }
  useEffect(() => { send([]); return () => ctrl.current?.abort() }, [a, b, lang, run])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const el = box.current; if (el && stick.current) el.scrollTop = el.scrollHeight }, [msgs])
  useEffect(() => {   // the input grows with the text, up to ~6 lines
    const el = input.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 168)}px`
  }, [q])

  const ask = (text: string) => {
    const v = text.trim()
    if (!v || busy) return
    setQ('')
    send([...msgs.filter((m) => m.content), { role: 'user', content: v }])
    input.current?.focus()
  }
  const asked = msgs.some((m) => m.role === 'user')
  const waiting = busy && msgs.length > 0 && !msgs[msgs.length - 1].content

  return (
    <section className="flex flex-col min-h-[600px] lg:h-[calc(100vh-7rem)] overflow-hidden rounded-xl bg-soft border border-line">
      <header className="flex items-center gap-3 px-4 sm:px-5 py-3.5 bg-surface border-b border-line">
        <span className="inline-flex items-center justify-center w-9 h-9 rounded-full bg-ink text-white shrink-0"><Sparkles size={17} strokeWidth={1.75} /></span>
        <div className="min-w-0">
          <h2 className="text-[15.5px] font-semibold leading-tight">{t('adv.advisor')}</h2>
          <div className="text-[12.5px] text-muted truncate">{busy ? t('adv.typing') : t('adv.advisorSub')}</div>
        </div>
        <button className="btn-text ml-auto !text-[13px] shrink-0 rounded-lg px-2.5 h-8 hover:bg-soft" onClick={() => setRun((n) => n + 1)} disabled={busy && !asked}>
          <RotateCcw size={14} strokeWidth={1.75} />{t('adv.restart')}
        </button>
      </header>

      <div ref={box} className="flex-1 overflow-y-auto px-3 sm:px-5 py-5 space-y-4"
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60 }}>
        {msgs.map((m, i) => m.role === 'user' ? (
          <div key={i} className="flex justify-end pl-10">
            <div className="max-w-[85%] rounded-2xl rounded-br-md bg-ink text-white px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap">{m.content}</div>
          </div>
        ) : m.content ? (
          <div key={i} className="pr-2 sm:pr-8">
            <div className="rounded-2xl rounded-tl-md bg-surface px-5 sm:px-6 py-4 sm:py-5 border border-line">
              <Markdown text={m.content} names={names} />
            </div>
          </div>
        ) : null)}
        {waiting && (
          <div className="pr-2 sm:pr-8" aria-busy="true">
            <div className="rounded-2xl rounded-tl-md bg-surface px-5 sm:px-6 py-5 border border-line space-y-3">
              {!asked && <div className="text-[13.5px] text-muted flex items-center gap-2"><Loader2 size={14} className="animate-spin" />{t('adv.preparing')}</div>}
              <div className="h-4 rounded shimmer w-2/5" /><div className="h-4 rounded shimmer w-full" /><div className="h-4 rounded shimmer w-11/12" /><div className="h-4 rounded shimmer w-3/4" />
            </div>
          </div>
        )}
        {err && (
          <div className="rounded-2xl bg-surface px-5 py-4 border border-line text-[14px] text-ink-2">
            {t('adv.error')}. <button className="btn-text underline" onClick={() => send(msgs.filter((m) => m.content))}>{t('climate.retry')}</button>
            <div className="text-[12px] text-faint mt-1">{err}</div>
          </div>
        )}
      </div>

      <div className="px-3 sm:px-5 pt-1 pb-4">
        {!asked && !busy && msgs.length > 0 && (
          <div className="-mx-3 sm:-mx-5 px-3 sm:px-5 mb-2.5 flex gap-1.5 overflow-x-auto no-scrollbar">
            {['adv.s1', 'adv.s2', 'adv.s3', 'adv.s4', 'adv.s5'].map((k) => (
              <button key={k} onClick={() => ask(t(k))} className="shrink-0 h-8 px-3 rounded-full bg-surface border border-line-2 text-[13px] text-ink-2 hover:text-ink hover:border-ink cursor-pointer transition-colors">{t(k)}</button>
            ))}
          </div>
        )}
        <div className="flex items-end gap-2 rounded-xl bg-surface px-3.5 py-2.5 border border-line-2 focus-within:border-ink transition-colors">
          <textarea ref={input} value={q} rows={1} onChange={(e) => setQ(e.target.value)} placeholder={t('adv.placeholder')}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(q) } }}
            className="flex-1 min-w-0 resize-none bg-transparent outline-none text-[15px] leading-[1.5] py-1.5" />
          {busy && asked ? (
            <button className="btn-ghost !h-9 !w-9 !p-0 justify-center shrink-0 !rounded-full" onClick={stop} aria-label={t('adv.stop')} title={t('adv.stop')}><Square size={13} fill="currentColor" /></button>
          ) : (
            <button className="btn-primary !h-9 !w-9 !p-0 justify-center shrink-0 !rounded-full" onClick={() => ask(q)} disabled={busy || !q.trim()} aria-label="send"><ArrowUp size={17} /></button>
          )}
        </div>
        <div className="mt-2 px-1 flex flex-wrap justify-between gap-x-4 gap-y-1 text-[11.5px] text-faint">
          <span>{t('adv.note')}</span>
          <span className="hidden sm:inline">{t('adv.keys')}</span>
        </div>
      </div>
    </section>
  )
}

function SideBySide({ pa, pb, t, lang }: { pa: Profile; pb: Profile; t: T; lang: Lang }) {
  const good = (p: Profile, c: string) => p.photos.filter((x) => x.category === c && (x.level === 'verified' || x.level === 'likely'))
    .sort((x, y) => (y.confidence ?? 0) - (x.confidence ?? 0)).slice(0, 3)
  const rows = CATEGORIES.map((c) => ({ c, a: good(pa, c), b: good(pb, c) })).filter((r) => r.a.length || r.b.length)
  if (!rows.length) return null
  return (
    <section className="mt-16 border-t border-line pt-10">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="h-sec">{t('adv.photosTitle')}<small>{t('adv.photosSub')}</small></h2>
      </div>
      <div className="mt-6 grid grid-cols-2 gap-x-6 sm:gap-x-10 relative">
        <div className="absolute inset-y-0 left-1/2 w-px bg-line hidden sm:block" />
        {[pa, pb].map((p, i) => (
          <div key={p.university.qid} className="flex items-center gap-2 pb-3 text-[14px] font-medium text-ink">
            <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-ink text-white text-[11px]">{i ? 'B' : 'A'}</span>
            <span className="truncate">{uniName(p.university, lang)}</span>
          </div>
        ))}
        {rows.map((r) => (
          <Fragment key={r.c}>
            <div className="col-span-2 pt-8 pb-3 text-[15px] font-semibold tracking-[-0.01em]">{catLabel(r.c, lang)}</div>
            {[r.a, r.b].map((list, i) => (
              <div key={i} className="grid grid-cols-3 gap-2 relative z-[1]">
                {list.map((p) => <PhotoTile key={p.id} p={p} className="aspect-[4/3]" />)}
                {list.length === 0 && <div className="col-span-3 flex items-center h-full min-h-16 text-[13px] text-faint">{t('adv.noPhotos')}</div>}
              </div>
            ))}
          </Fragment>
        ))}
      </div>
    </section>
  )
}

export default function Compare() {
  const t = useT()
  const lang = useLang()
  const [params, setParams] = useSearchParams()
  const [a, setA] = useState<Candidate | null>(params.get('a') ? { qid: params.get('a')!, label: params.get('a')!, score: 1, origin: 'index' } : null)
  const [b, setB] = useState<Candidate | null>(params.get('b') ? { qid: params.get('b')!, label: params.get('b')!, score: 1, origin: 'index' } : null)
  const [data, setData] = useState<{ a: Profile; b: Profile } | null>(null)
  const [sheets, setSheets] = useState<{ a: AdvisorSheet; b: AdvisorSheet } | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [pair, setPair] = useState<{ a: string; b: string } | null>(null)

  const go = async (qa = a?.qid, qb = b?.qid) => {
    if (!qa || !qb) return
    setLoading(true); setErr(null); setData(null); setSheets(null); setPair({ a: qa, b: qb })
    setParams({ a: qa, b: qb })
    compareSheets(qa, qb, lang).then(setSheets).catch(() => {})
    try {
      const d = await api.compare(qa, qb)
      setData(d)
      setA({ qid: qa, label: uniName(d.a.university, lang), score: 1, origin: 'index' }); setB({ qid: qb, label: uniName(d.b.university, lang), score: 1, origin: 'index' })
    } catch (e) { setErr(String(e)) } finally { setLoading(false) }
  }
  useEffect(() => { if (params.get('a') && params.get('b')) void go(params.get('a')!, params.get('b')!) /* eslint-disable-line react-hooks/exhaustive-deps */ }, [])

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-10">
      <h1 className="text-[32px] sm:text-[44px] leading-[1.08]">{t('adv.title')}</h1>
      <p className="mt-3 max-w-[70ch] text-[15px] leading-[1.6] text-muted">{t('adv.subtitle')}</p>
      <div className="mt-8 grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] gap-4 items-end">
        <Picker label={t('compare.a')} value={a?.qid ? a : null} onPick={setA} t={t} />
        <button className="btn-primary" onClick={() => go()} disabled={!a?.qid || !b?.qid || loading}>{loading ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />} {t('compare.go')}</button>
        <Picker label={t('compare.b')} value={b?.qid ? b : null} onPick={setB} t={t} />
      </div>
      {loading && <div className="mt-6 text-[14px] text-muted flex items-center gap-2"><Loader2 size={14} className="animate-spin" /> {t('compare.loading')}</div>}
      {err && <div className="mt-6 text-[14px] text-unverified">{err}</div>}

      {data && pair && (
        <>
          <div className="mt-10 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-[minmax(0,300px)_minmax(0,1fr)_minmax(0,300px)] gap-x-8 gap-y-10 items-start">
            <div className="lg:sticky lg:top-20"><UniColumn side="A" p={data.a} s={sheets?.a} t={t} lang={lang} /></div>
            <div className="order-last md:col-span-2 lg:order-none lg:col-span-1 lg:sticky lg:top-20"><Advisor a={pair.a} b={pair.b} names={[data.a.university.name, data.b.university.name]} t={t} lang={lang} /></div>
            <div className="lg:sticky lg:top-20"><UniColumn side="B" p={data.b} s={sheets?.b} t={t} lang={lang} /></div>
          </div>
          <SideBySide pa={data.a} pb={data.b} t={t} lang={lang} />
        </>
      )}
    </div>
  )
}
