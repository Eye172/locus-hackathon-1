import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { GitCompare, Trash2, Download, Upload, Heart, ListChecks } from 'lucide-react'
import { api } from '../lib/api'
import { store, useStoreVersion, KEYS } from '../lib/store'

export default function Saved() {
  useStoreVersion()
  const nav = useNavigate()
  const saved = store.saved()
  const entries = Object.entries(saved).sort((a, b) => b[1].savedAt.localeCompare(a[1].savedAt))
  // names are stored as they were at saving: refresh them to today's names
  useEffect(() => { for (const qid of Object.keys(store.saved())) api.mini(qid).then((m) => store.rename(qid, m.name)).catch(() => {}) }, [])
  const [sel, setSel] = useState<string[]>([])
  const toggle = (qid: string) => setSel((s) => (s.includes(qid) ? s.filter((x) => x !== qid) : [...s, qid].slice(-2)))
  const exportJson = () => {
    const data: Record<string, unknown> = {}
    for (const k of Object.values(KEYS)) { try { data[k] = JSON.parse(localStorage.getItem(k) ?? 'null') } catch { /* skip */ } }
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })); a.download = 'campuslens-saved.json'; a.click()
  }
  const importJson = (f: File) => {
    f.text().then((txt) => { const data = JSON.parse(txt) as Record<string, unknown>; for (const [k, v] of Object.entries(data)) if (v != null) localStorage.setItem(k, JSON.stringify(v)); window.location.reload() }).catch(() => alert('Не удалось прочитать файл'))
  }
  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-10">
      <div className="flex flex-wrap items-end gap-4">
        <div><h1 className="text-[32px] sm:text-[44px] leading-[1.08]">Мои вузы</h1><div className="text-[15px] text-muted mt-2">Сохранённые профили, избранные фото и планы визита. Хранится в этом браузере.</div></div>
        <div className="ml-auto flex gap-2">
          <button className="btn-ghost" onClick={exportJson}><Download size={15} /> Экспорт</button>
          <label className="btn-ghost cursor-pointer"><Upload size={15} /> Импорт<input type="file" accept="application/json" className="hidden" onChange={(e) => e.target.files?.[0] && importJson(e.target.files[0])} /></label>
          <button className="btn-primary" disabled={sel.length !== 2} onClick={() => nav(`/compare?a=${sel[0]}&b=${sel[1]}`)}><GitCompare size={15} /> Сравнить {sel.length ? `(${sel.length}/2)` : ''}</button>
        </div>
      </div>
      {entries.length === 0 ? (
        <div className="mt-10 border-t border-line pt-16 pb-10 text-center"><div className="text-[17px] font-medium">Пока пусто</div><div className="text-[15px] text-muted mt-1.5">Откройте профиль вуза и нажмите «Сохранить» в шапке.</div><Link to="/" className="btn-ghost mt-6">К планете</Link></div>
      ) : (
        <div className="mt-10 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {entries.map(([qid, p]) => {
            const favs = store.favorites(qid).length
            const plan = store.plan(qid)
            return (
              <div key={qid} className={`card p-5 transition-colors ${sel.includes(qid) ? 'border-ink' : 'hover:border-line-2'}`}>
                <div className="flex items-start gap-3">
                  <input type="checkbox" checked={sel.includes(qid)} onChange={() => toggle(qid)} className="mt-1.5 accent-ink" title="Выбрать для сравнения" />
                  <div className="min-w-0 flex-1">
                    <Link to={`/u/${qid}`} className="display text-[20px] leading-snug hover:underline decoration-line-2 underline-offset-4">{p.name}</Link>
                    <div className="text-[13px] text-muted mt-1">{p.city ?? ''} · сохранено {p.savedAt.slice(0, 10)} · <span className="mono">{p.photos}</span> фото</div>
                    <div className="mt-4 flex gap-5 text-[13px] text-ink-2">
                      <span className="inline-flex items-center gap-1"><Heart size={13} className="text-muted" /> {favs} избранных</span>
                      <span className="inline-flex items-center gap-1"><ListChecks size={13} className="text-muted" /> {plan.filter((i) => i.done).length}/{plan.length} в плане</span>
                    </div>
                    {plan.length > 0 && <ul className="mt-3 text-[13px] text-muted space-y-1">{plan.slice(0, 3).map((i) => <li key={i.id} className={i.done ? 'line-through' : ''}>· {i.label}</li>)}</ul>}
                  </div>
                  <button onClick={() => store.unsave(qid)} className="text-faint hover:text-unverified cursor-pointer transition-colors" title="Убрать" aria-label="Убрать"><Trash2 size={15} /></button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
