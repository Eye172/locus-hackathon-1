import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ChevronDown, Plus, RotateCcw, Save, Trash2, RefreshCw } from 'lucide-react'
import { api } from '../lib/api'
import type { CustomQuery, Intent, Platform, SearchPlan, UniPlan, UniPlanView } from '../lib/types'

const NETS: { key: Platform; label: string }[] = [
  { key: 'tiktok', label: 'TikTok' }, { key: 'instagram', label: 'Instagram' }, { key: 'google', label: 'Google Картинки' },
  { key: 'maps', label: 'Google Карты' }, { key: 'youtube', label: 'YouTube' },
]
const lines = (s: string) => s.split('\n').map((x) => x.trim()).filter(Boolean)
const words = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean)

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-[12px] font-semibold text-ink-2">{label}</span>
      {hint && <span className="ml-1.5 text-[11px] text-muted">{hint}</span>}
      <div className="mt-1">{children}</div>
    </label>
  )
}
const input = 'w-full rounded-lg border border-line-2 bg-surface px-2.5 py-1.5 text-[13px] outline-none focus:border-brand'

function IntentRow({ it, onChange, onRemove }: { it: Intent; onChange: (x: Intent) => void; onRemove: () => void }) {
  const [open, setOpen] = useState(false)
  const set = (patch: Partial<Intent>) => onChange({ ...it, ...patch })
  return (
    <div className="border-t border-line first:border-t-0">
      <div className="flex items-center gap-3 px-4 py-2.5">
        <input type="checkbox" checked={it.enabled} onChange={(e) => set({ enabled: e.target.checked })} className="accent-ink" title="Искать эту тему" />
        <button onClick={() => setOpen(!open)} className="flex-1 min-w-0 flex items-center gap-2 text-left cursor-pointer">
          <span className={`font-semibold ${it.enabled ? '' : 'text-muted line-through'}`}>{it.label}</span>
          <span className="mono text-[11px] text-muted">{it.key}</span>
          {it.fast && <span className="text-[11px] text-brand">в первом профиле</span>}
          <span className="ml-auto text-[12px] text-muted truncate">{it.platforms.map((p) => NETS.find((n) => n.key === p)?.label ?? p).join(' · ')}</span>
          <ChevronDown size={15} className={`shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>
        <label className="flex items-center gap-1 text-[12px] text-muted" title="Сколько фото этой темы в коллаже">
          в коллаже <input type="number" min={0} max={60} value={it.target} onChange={(e) => set({ target: Number(e.target.value) })} className="w-14 rounded border border-line-2 px-1.5 py-0.5 text-[13px] text-ink" />
        </label>
      </div>
      {open && (
        <div className="px-4 pb-4 grid gap-3 md:grid-cols-2">
          <Field label="Название"><input className={input} value={it.label} onChange={(e) => set({ label: e.target.value })} /></Field>
          <Field label="Где искать">
            <div className="flex flex-wrap gap-3 pt-1">
              {NETS.map((n) => (
                <label key={n.key} className="flex items-center gap-1.5 text-[13px]">
                  <input type="checkbox" className="accent-ink" checked={it.platforms.includes(n.key)}
                    onChange={(e) => set({ platforms: e.target.checked ? [...it.platforms, n.key] : it.platforms.filter((p) => p !== n.key) })} />{n.label}
                </label>
              ))}
              <label className="flex items-center gap-1.5 text-[13px]"><input type="checkbox" className="accent-ink" checked={it.fast} onChange={(e) => set({ fast: e.target.checked })} />в первом профиле</label>
            </div>
          </Field>
          <Field label="Запросы на английском" hint="по одному в строке, {name} — название вуза">
            <textarea rows={3} className={input} value={(it.phrases.en ?? []).join('\n')} onChange={(e) => set({ phrases: { ...it.phrases, en: lines(e.target.value) } })} />
          </Field>
          <Field label="Запросы на русском" hint="для вузов СНГ; для других языков — перевод темы">
            <textarea rows={3} className={input} value={(it.phrases.ru ?? []).join('\n')} onChange={(e) => set({ phrases: { ...it.phrases, ru: lines(e.target.value) } })} />
          </Field>
          <Field label="Слова в подписи поста" hint="через запятую: пост с ними — про эту тему">
            <input className={input} value={it.caption_words.join(', ')} onChange={(e) => set({ caption_words: words(e.target.value) })} />
          </Field>
          <Field label="Слова в названии аккаунта" hint="аккаунт вуза с ними (library, dorm…) ведёт эту тему">
            <input className={input} value={it.account_words.join(', ')} onChange={(e) => set({ account_words: words(e.target.value) })} />
          </Field>
          <Field label="Места на Google Картах" hint="«<вуз> слово» рядом с кампусом">
            <input className={input} value={it.maps.join(', ')} onChange={(e) => set({ maps: words(e.target.value) })} />
          </Field>
          <Field label="Категории фото" hint="campus, dormitory, classroom, library, lab, sports, student_life; пусто — любые">
            <input className={input} value={it.categories.join(', ')} onChange={(e) => set({ categories: words(e.target.value) })} />
          </Field>
          <div className="md:col-span-2"><button onClick={onRemove} className="inline-flex items-center gap-1.5 text-[12px] text-unverified hover:underline cursor-pointer"><Trash2 size={13} /> Удалить тему</button></div>
        </div>
      )}
    </div>
  )
}

function CustomList({ items, intents, onChange }: { items: CustomQuery[]; intents: Intent[]; onChange: (x: CustomQuery[]) => void }) {
  return (
    <div className="space-y-2">
      {items.map((c, i) => (
        <div key={i} className="flex flex-wrap items-center gap-2">
          <select className={`${input} !w-auto`} value={c.intent} onChange={(e) => onChange(items.map((x, k) => k === i ? { ...x, intent: e.target.value } : x))}>
            {intents.map((it) => <option key={it.key} value={it.key}>{it.label}</option>)}
          </select>
          <input className={`${input} flex-1 min-w-[220px]`} placeholder="например: {name} Media Lab или MIT hackathon" value={c.query}
            onChange={(e) => onChange(items.map((x, k) => k === i ? { ...x, query: e.target.value } : x))} />
          {(['tiktok', 'instagram', 'google'] as Platform[]).map((p) => (
            <label key={p} className="flex items-center gap-1 text-[12px]"><input type="checkbox" className="accent-ink" checked={c.platforms.includes(p)}
              onChange={(e) => onChange(items.map((x, k) => k === i ? { ...x, platforms: e.target.checked ? [...x.platforms, p] : x.platforms.filter((y) => y !== p) } : x))} />{NETS.find((n) => n.key === p)?.label}</label>
          ))}
          <button onClick={() => onChange(items.filter((_, k) => k !== i))} className="text-muted hover:text-unverified cursor-pointer" aria-label="Удалить"><Trash2 size={14} /></button>
        </div>
      ))}
      <button onClick={() => onChange([...items, { intent: intents[0]?.key ?? 'atmosphere', query: '', platforms: ['tiktok', 'instagram', 'google'] }])}
        className="inline-flex items-center gap-1.5 text-[13px] text-brand hover:underline cursor-pointer"><Plus size={14} /> Добавить запрос</button>
    </div>
  )
}

export default function SearchSettings() {
  const [params] = useSearchParams()
  const qid = params.get('u')
  const [plan, setPlan] = useState<SearchPlan | null>(null)
  const [saved, setSaved] = useState<string>('')
  const [err, setErr] = useState<string | null>(null)
  const [uni, setUni] = useState<UniPlanView | null>(null)
  const [up, setUp] = useState<UniPlan | null>(null)

  useEffect(() => { api.searchPlan().then((r) => setPlan(r.plan)).catch((e) => setErr(String(e.message ?? e))) }, [])
  const loadUni = () => { if (qid) api.uniPlan(qid).then((r) => { setUni(r); setUp(r.uni) }).catch((e) => setErr(String(e.message ?? e))) }
  useEffect(loadUni, [qid]) // eslint-disable-line react-hooks/exhaustive-deps
  const snapshot = useMemo(() => JSON.stringify(plan), [plan])
  const [base, setBase] = useState('')
  useEffect(() => { if (plan && !base) setBase(snapshot) }, [plan, snapshot, base])
  const dirty = !!plan && base !== '' && base !== snapshot

  const save = async () => {
    if (!plan) return
    setErr(null)
    try { const r = await api.saveSearchPlan(plan); setPlan(r.plan); setBase(JSON.stringify(r.plan)); setSaved('Сохранено: применится при следующем открытии профиля'); loadUni() } catch (e) { setErr(String((e as Error).message)) }
  }
  const reset = async () => { const r = await api.resetSearchPlan(); setPlan(r.plan); setBase(JSON.stringify(r.plan)); setSaved('Сброшено к стандартным темам'); loadUni() }
  const saveUni = async () => { if (qid && up) { try { await api.saveUniPlan(qid, up); setSaved('Сохранено для этого вуза'); loadUni() } catch (e) { setErr(String((e as Error).message)) } } }
  const setP = (patch: Partial<SearchPlan>) => plan && setPlan({ ...plan, ...patch })

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 space-y-8">
      <div>
        {qid ? <Link to={`/u/${qid}`} className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-ink"><ArrowLeft size={13} /> К профилю</Link>
          : <Link to="/" className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-ink"><ArrowLeft size={13} /> Планета</Link>}
        <h1 className="mt-2 text-[36px] leading-tight">Настройки поиска</h1>
        <p className="mt-1 text-sm text-muted max-w-3xl">
          Фото ищутся по темам — так, как о вузе пишут студенты: «атмосфера», «один день из жизни», «кампус», «общежития», «ивенты»…
          Для каждой темы — запросы на английском и на языке страны вуза, сети, где искать, и сколько фото этой темы попадёт в коллаж «Какой это вуз».
          Найденные посты сначала ранжируются по тому, насколько явно они про этот вуз, потом каждое фото проверяет ИИ.
        </p>
      </div>
      {err && <div className="rounded-lg bg-unverified-soft text-unverified px-4 py-2 text-sm">{err}</div>}
      {saved && <div className="rounded-lg bg-verified-soft text-verified px-4 py-2 text-sm">{saved}</div>}

      {qid && uni && up && (
        <section className="rounded-xl border border-brand/30 bg-surface">
          <div className="px-4 py-3 border-b border-line flex items-center gap-2">
            <h2 className="font-bold">Только для «{uni.name}»</h2>
            <button onClick={saveUni} className="ml-auto btn-primary !h-8 !py-0 text-[13px]"><Save size={14} /> Сохранить</button>
            <Link to={`/u/${qid}?refresh=1`} className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-line-2 text-[13px] hover:border-brand hover:text-brand"><RefreshCw size={14} /> Пересобрать профиль</Link>
          </div>
          <div className="p-4 grid gap-4 md:grid-cols-2">
            <Field label="Другие названия вуза" hint="через запятую: бренд, сокращение, как его называют студенты">
              <input className={input} value={up.names.join(', ')} onChange={(e) => setUp({ ...up, names: words(e.target.value) })} />
            </Field>
            <Field label="Не искать для этого вуза">
              <div className="flex flex-wrap gap-3 pt-1">
                {(plan?.intents ?? []).map((it) => (
                  <label key={it.key} className="flex items-center gap-1.5 text-[13px]"><input type="checkbox" className="accent-ink" checked={up.disabled.includes(it.key)}
                    onChange={(e) => setUp({ ...up, disabled: e.target.checked ? [...up.disabled, it.key] : up.disabled.filter((k) => k !== it.key) })} />{it.label}</label>
                ))}
              </div>
            </Field>
            <div className="md:col-span-2"><Field label="Свои запросы для этого вуза"><CustomList items={up.custom} intents={plan?.intents ?? []} onChange={(custom) => setUp({ ...up, custom })} /></Field></div>
          </div>
          <details className="border-t border-line">
            <summary className="px-4 py-2.5 text-sm font-semibold cursor-pointer">Что будет отправлено в поиск</summary>
            <div className="px-4 pb-4 space-y-2 text-[13px]">
              <div className="text-muted">Название узнаётся в подписи как: {[...uni.names.full, ...uni.names.abbr].join(', ')}</div>
              {Object.entries(uni.preview).map(([k, nets]) => (
                <div key={k}><span className="font-semibold">{plan?.intents.find((i) => i.key === k)?.label ?? k}:</span>{' '}
                  {Object.entries(nets).map(([n, qs]) => qs.length ? <span key={n} className="text-muted"> {NETS.find((x) => x.key === n)?.label ?? n}: {qs.map((q) => `«${q}»`).join(', ')};</span> : null)}
                </div>
              ))}
              {uni.discovered && (
                <div className="pt-2 text-muted">
                  <div><span className="text-ink-2">Аккаунты вуза в Instagram:</span> {uni.discovered.ig_accounts.map((a) => `@${a.username} (${plan?.intents.find((i) => i.key === a.intent)?.label ?? a.intent})`).join(', ') || '—'}</div>
                  <div><span className="text-ink-2">Хэштеги:</span> {uni.discovered.ig_tags.map((t) => `#${t.tag} (${t.count})`).join(', ') || '—'}</div>
                  <div><span className="text-ink-2">Подсказки TikTok:</span> {uni.discovered.suggestions.map((s) => `«${s.query}»`).join(', ') || '—'}</div>
                </div>
              )}
            </div>
          </details>
        </section>
      )}

      {plan && (
        <>
          <section className="rounded-xl border border-line bg-surface">
            <div className="px-4 py-3 border-b border-line flex items-center gap-2">
              <h2 className="font-bold">Темы</h2>
              <span className="text-xs text-muted">для всех вузов</span>
              <button onClick={() => setP({ intents: [...plan.intents, { key: `custom_${plan.intents.length + 1}`, label: 'Новая тема', enabled: true, target: 6, categories: [], platforms: ['tiktok', 'instagram', 'google'], phrases: { en: ['{name} '], ru: [] }, caption_words: [], account_words: [], maps: [], fast: false }] })}
                className="ml-auto inline-flex items-center gap-1.5 text-[13px] text-brand hover:underline cursor-pointer"><Plus size={14} /> Тема</button>
            </div>
            {plan.intents.map((it, i) => (
              <IntentRow key={i} it={it} onChange={(x) => setP({ intents: plan.intents.map((y, k) => k === i ? x : y) })}
                onRemove={() => setP({ intents: plan.intents.filter((_, k) => k !== i) })} />
            ))}
          </section>

          <section className="rounded-xl border border-line bg-surface p-4 grid gap-4 md:grid-cols-3">
            <Field label="Языки запросов">
              <select className={input} value={plan.languages} onChange={(e) => setP({ languages: e.target.value as SearchPlan['languages'] })}>
                <option value="both">английский + язык страны</option><option value="en">только английский</option><option value="local">только язык страны</option>
              </select>
            </Field>
            <Field label="Запросов на тему и язык"><input type="number" min={1} max={5} className={input} value={plan.phrases_per_intent} onChange={(e) => setP({ phrases_per_intent: Number(e.target.value) })} /></Field>
            <Field label="Постов на тему и сеть" hint="лучшие после ранжирования"><input type="number" min={1} max={30} className={input} value={plan.posts_per_intent} onChange={(e) => setP({ posts_per_intent: Number(e.target.value) })} /></Field>
            <Field label="Из них видео — кадры изнутри"><input type="number" min={0} max={10} className={input} value={plan.videos_per_intent} onChange={(e) => setP({ videos_per_intent: Number(e.target.value) })} /></Field>
            <Field label="Порог релевантности поста" hint="1.5 — стандарт; выше — строже"><input type="number" step={0.5} min={0} max={8} className={input} value={plan.min_relevance} onChange={(e) => setP({ min_relevance: Number(e.target.value) })} /></Field>
            <div className="space-y-2 pt-5">
              <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" className="accent-ink" checked={plan.use_accounts} onChange={(e) => setP({ use_accounts: e.target.checked })} />аккаунты клубов, библиотеки, общежитий</label>
              <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" className="accent-ink" checked={plan.use_suggestions} onChange={(e) => setP({ use_suggestions: e.target.checked })} />подсказки поиска TikTok</label>
            </div>
            <div className="md:col-span-3"><Field label="Свои запросы для всех вузов" hint="{name} — название вуза"><CustomList items={plan.custom} intents={plan.intents} onChange={(custom) => setP({ custom })} /></Field></div>
          </section>

          <div className="flex items-center gap-2 sticky bottom-4 rounded-xl border border-line bg-surface/95 backdrop-blur p-2 shadow-sm">
            <button onClick={save} disabled={!dirty} className="btn-primary disabled:opacity-50"><Save size={15} /> Сохранить темы</button>
            <button onClick={reset} className="inline-flex items-center gap-1.5 h-10 px-3 rounded-lg border border-line-2 bg-surface text-[13px] hover:border-brand hover:text-brand cursor-pointer"><RotateCcw size={14} /> Стандартные темы</button>
            {dirty && <span className="text-xs text-likely">есть несохранённые изменения</span>}
          </div>
        </>
      )}
    </div>
  )
}
