import { useEffect, useRef, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { ArrowRight, MapPin, Calendar, Users, Navigation, Thermometer, Sun, Wallet, Bus, Sparkles, Send, Bot, Loader2 } from 'lucide-react'
import { api, streamChat, thumbUrl, type CompareAi, type FactSheet } from '../lib/api'
import type { Candidate, Profile } from '../lib/types'
import { CATEGORIES } from '../lib/types'
import { catLabel, useLang, useT } from '../lib/i18n'
import { CoverageDot, coverageLabel } from '../components/Badges'
import { SearchBox } from '../components/SearchBox'
import { store, useStoreVersion } from '../lib/store'

const PREFS: [string, string][] = [['budget', 'бюджет'], ['climate', 'климат'], ['dorm', 'общежитие'], ['city', 'город']]
const QUICK = ['Где теплее зимой?', 'Где дешевле жить с общежитием?', 'Где больше подтверждённых фото общежитий?', 'Какой кампус ближе к центру города?']
const sign = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Math.round(v)}°`)
const money = (v: number, cur: string) => `${v.toLocaleString('ru-RU')} ${cur === 'KZT' ? '₸' : cur}`

function Picker({ label, value, onPick }: { label: string; value: Candidate | null; onPick: (c: Candidate | null) => void }) {
  useStoreVersion()
  const saved = Object.entries(store.saved())
  return (
    <div>
      <div className="caps text-muted mb-1.5">{label}</div>
      {value ? (
        <div className="flex items-center justify-between gap-2 h-11 px-3 rounded-lg border border-line bg-surface"><span className="font-medium truncate">{value.label}</span><button className="text-xs text-brand cursor-pointer" onClick={() => onPick(null)}>изменить</button></div>
      ) : (
        <>
          <SearchBox onPick={onPick} size="md" />
          {saved.length > 0 && <div className="mt-1.5 flex flex-wrap gap-1">{saved.slice(0, 6).map(([qid, s]) => <button key={qid} onClick={() => onPick({ qid, label: s.name, city: s.city, score: 1, origin: 'index' })} className="chip bg-slate-100 text-ink-2 hover:bg-slate-200 cursor-pointer">{s.name}</button>)}</div>}
        </>
      )}
    </div>
  )
}

function Card({ p, f }: { p: Profile; f?: FactSheet }) {
  const lang = useLang()
  const u = p.university
  const Row = ({ icon, k, v }: { icon: React.ReactNode; k: string; v: React.ReactNode }) => (
    <div className="flex items-center gap-2 text-sm py-1.5 rule first:border-0"><span className="text-muted">{icon}</span><span className="text-ink-2 flex-1">{k}</span><span className="mono">{v}</span></div>
  )
  const hero = p.photos.find((x) => x.category === 'campus') ?? p.photos[0]
  return (
    <div className="card overflow-hidden">
      {hero && <div className="aspect-[16/9] bg-slate-100"><img src={thumbUrl(hero)} alt="" className="w-full h-full object-cover" /></div>}
      <div className="p-4">
        <Link to={`/u/${u.qid}`} className="font-bold text-lg leading-snug hover:text-brand">{u.names[lang] || u.name}</Link>
        <div className="text-xs text-muted mt-0.5 flex items-center gap-1"><MapPin size={11} />{[u.city, u.country].filter(Boolean).join(', ')}</div>
        <div className="mt-3">
          <Row icon={<Calendar size={13} />} k="основан" v={u.founded ?? '—'} />
          <Row icon={<Users size={13} />} k="студентов" v={u.students ? u.students.toLocaleString('ru-RU') : '—'} />
          <Row icon={<CoverageDot level={p.coverage.overall} />} k="покрытие фото" v={`${coverageLabel(p.coverage.overall, 'ru')} · ${p.photos.length}`} />
          {f?.climate && <Row icon={<Thermometer size={13} />} k="зима / лето" v={`${sign(f.climate.winter.t_mean)} / ${sign(f.climate.summer.t_mean)}`} />}
          {f?.climate && <Row icon={<Sun size={13} />} k="комфортных дней" v={f.climate.comfort_days.comfortable} />}
          {f?.city_context && <Row icon={<Navigation size={13} />} k="до центра" v={f.city_context.distance_to_center_km != null ? `${f.city_context.distance_to_center_km} км` : '—'} />}
          {f?.city_context && <Row icon={<Bus size={13} />} k="остановок в 800 м" v={f.city_context.stops_800m} />}
          {f?.budget && <Row icon={<Wallet size={13} />} k="месяц с общежитием" v={money(f.budget.total_with_dorm, f.budget.currency)} />}
        </div>
        <div className="mt-3 grid grid-cols-4 gap-1">
          {CATEGORIES.map((c) => { const s = p.categories[c]; return <div key={c} className="text-center" title={catLabel(c, lang)}><div className={`h-1.5 rounded-full ${s?.verified ? 'bg-verified' : s?.likely ? 'bg-likely' : 'bg-slate-200'}`} /><div className="mono text-[10px] text-muted mt-0.5">{s?.verified ?? 0}</div></div> })}
        </div>
      </div>
    </div>
  )
}

export default function Compare() {
  const t = useT()
  const lang = useLang()
  const [params, setParams] = useSearchParams()
  const [a, setA] = useState<Candidate | null>(params.get('a') ? { qid: params.get('a')!, label: params.get('a')!, score: 1, origin: 'index' } : null)
  const [b, setB] = useState<Candidate | null>(params.get('b') ? { qid: params.get('b')!, label: params.get('b')!, score: 1, origin: 'index' } : null)
  const [data, setData] = useState<{ a: Profile; b: Profile } | null>(null)
  const [ai, setAi] = useState<CompareAi | null>(null)
  const [prefs, setPrefs] = useState<Record<string, boolean>>({ budget: true, climate: true, dorm: true, city: false })
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [chat, setChat] = useState<{ role: 'user' | 'assistant'; content: string }[]>([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const chatEnd = useRef<HTMLDivElement>(null)

  const go = async (qa = a?.qid, qb = b?.qid) => {
    if (!qa || !qb) return
    setLoading(true); setErr(null); setData(null); setAi(null); setChat([])
    setParams({ a: qa, b: qb })
    try {
      const [d, r] = await Promise.all([api.compare(qa, qb), api.compareAi(qa, qb, prefs)])
      setData(d); setAi(r)
      setA({ qid: qa, label: d.a.university.name, score: 1, origin: 'index' }); setB({ qid: qb, label: d.b.university.name, score: 1, origin: 'index' })
    } catch (e) { setErr(String(e)) } finally { setLoading(false) }
  }
  useEffect(() => { if (params.get('a') && params.get('b')) void go(params.get('a')!, params.get('b')!) /* eslint-disable-line react-hooks/exhaustive-deps */ }, [])
  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: 'smooth' }) }, [chat])

  const ask = async (text: string) => {
    if (!a?.qid || !b?.qid || !text.trim() || busy) return
    const msgs = [...chat, { role: 'user' as const, content: text.trim() }]
    setChat([...msgs, { role: 'assistant', content: '' }]); setQ(''); setBusy(true)
    await streamChat({ a: a.qid, b: b.qid, prefs, messages: msgs },
      (tok) => setChat((c) => { const n = [...c]; n[n.length - 1] = { role: 'assistant', content: n[n.length - 1].content + tok }; return n }),
      () => setBusy(false),
      (m) => { setChat((c) => { const n = [...c]; n[n.length - 1] = { role: 'assistant', content: `Ошибка: ${m}` }; return n }); setBusy(false) })
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-8">
      <h1 className="text-3xl font-extrabold">{t('compare.title')}</h1>
      <div className="mt-1 text-sm text-muted">Слева и справа — карточки по фактам профилей, посередине — ИИ-разбор и чат, которые отвечают только по этим данным.</div>
      <div className="mt-6 grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] gap-4 items-end">
        <Picker label={t('compare.a')} value={a?.qid ? a : null} onPick={setA} />
        <button className="btn-primary" onClick={() => go()} disabled={!a?.qid || !b?.qid || loading}>{loading ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />} {t('compare.go')}</button>
        <Picker label={t('compare.b')} value={b?.qid ? b : null} onPick={setB} />
      </div>
      {loading && <div className="mt-6 text-sm text-muted flex items-center gap-2"><Loader2 size={14} className="animate-spin" /> {t('compare.loading')}</div>}
      {err && <div className="mt-6 card p-4 text-sm text-unverified">{err}</div>}

      {data && (
        <div className="mt-8 grid grid-cols-1 lg:grid-cols-[300px_1fr_300px] gap-6 items-start">
          <Card p={data.a} f={ai?.facts.a} />
          <div className="space-y-6">
            <div className="card p-5">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="font-bold flex items-center gap-2"><Sparkles size={16} className="text-brand" /> ИИ-сравнение</h2>
                <span className="mono text-[11px] text-muted">{ai?.provider === 'rules' ? 'по правилам · без LLM-ключа' : ai?.provider}</span>
                <div className="ml-auto flex gap-1">{PREFS.map(([k, l]) => <button key={k} onClick={() => setPrefs((p) => ({ ...p, [k]: !p[k] }))} className={`filter !h-7 ${prefs[k] ? 'filter-active' : ''}`}>{l}</button>)}</div>
                <button className="btn-ghost !h-7 !px-2.5 text-xs" onClick={() => go()}>обновить</button>
              </div>
              {ai ? (
                <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-5">
                  {[['pros_a', data.a.university.name], ['pros_b', data.b.university.name]].map(([k, name]) => (
                    <div key={k}>
                      <div className="caps text-muted mb-1.5">плюсы · {name}</div>
                      <ul className="space-y-1.5 text-sm">{(ai[k as 'pros_a' | 'pros_b']).map((p, i) => <li key={i} className="flex gap-2"><span className="text-verified">+</span><span>{p.text} {p.refs.map((r) => <span key={r} className="mono text-[10px] text-muted">{r} </span>)}</span></li>)}{ai[k as 'pros_a' | 'pros_b'].length === 0 && <li className="text-muted text-sm">нет явных преимуществ по доступным данным</li>}</ul>
                    </div>
                  ))}
                  <div className="md:col-span-2">
                    <div className="caps text-muted mb-1.5">на что обратить внимание</div>
                    <ul className="space-y-1.5 text-sm">{ai.watch_out.map((p, i) => <li key={i} className="flex gap-2"><span className="text-likely">!</span><span>{p.text}</span></li>)}</ul>
                  </div>
                  <p className="md:col-span-2 text-sm border-l-2 border-brand pl-3">{ai.summary}</p>
                </div>
              ) : <div className="mt-4 h-24 shimmer rounded-lg" />}
            </div>

            <div className="card p-5">
              <h2 className="font-bold flex items-center gap-2"><Bot size={16} className="text-brand" /> Спросить про эти два вуза</h2>
              <div className="mt-2 flex flex-wrap gap-1.5">{QUICK.map((s) => <button key={s} onClick={() => ask(s)} className="chip bg-slate-100 text-ink-2 hover:bg-slate-200 cursor-pointer" disabled={busy}>{s}</button>)}</div>
              <div className="mt-3 space-y-3 max-h-96 overflow-auto pr-1">
                {chat.map((m, i) => (
                  <div key={i} className={`text-sm leading-relaxed ${m.role === 'user' ? 'text-ink font-medium' : 'text-ink-2 border-l-2 border-line pl-3 whitespace-pre-wrap'}`}>{m.content || (busy && i === chat.length - 1 ? '…' : '')}</div>
                ))}
                <div ref={chatEnd} />
              </div>
              <div className="mt-3 flex gap-2">
                <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && ask(q)} placeholder="Например: где ближе аэропорт и сколько ехать?" className="flex-1 min-w-0 rounded-lg border border-line-2 px-3 h-10 text-sm outline-none focus:border-ink" />
                <button className="btn-primary !px-3" onClick={() => ask(q)} disabled={busy || !q.trim()}><Send size={15} /></button>
              </div>
              <div className="mt-2 text-[11px] text-muted">Отвечает только по данным двух профилей, климату, городу и бюджету. Без гарантий поступления.</div>
            </div>
          </div>
          <Card p={data.b} f={ai?.facts.b} />
        </div>
      )}

      {data && (
        <div className="mt-10 space-y-6">
          {CATEGORIES.map((c) => {
            const la = data.a.photos.filter((p) => p.category === c).slice(0, 4)
            const lb = data.b.photos.filter((p) => p.category === c).slice(0, 4)
            if (!la.length && !lb.length) return null
            return (
              <div key={c}>
                <h3 className="font-bold mb-2 flex items-center gap-2">{catLabel(c, lang)}<span className="mono text-xs text-muted">{la.length} · {lb.length}</span></h3>
                <div className="grid grid-cols-2 gap-6">
                  {[la, lb].map((list, i) => (
                    <div key={i} className="grid grid-cols-4 gap-2">
                      {list.map((p) => <a key={p.id} href={p.page_url} target="_blank" rel="noreferrer" className="aspect-[4/3] rounded-md overflow-hidden bg-slate-100"><img src={thumbUrl(p)} alt="" loading="lazy" className="w-full h-full object-cover" /></a>)}
                      {list.length === 0 && <div className="col-span-4 text-xs text-muted p-3">нет подтверждённых фото</div>}
                    </div>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
