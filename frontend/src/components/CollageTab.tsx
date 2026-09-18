import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { SlidersHorizontal, ChevronDown } from 'lucide-react'
import type { Photo, Profile } from '../lib/types'
import { thumbUrl } from '../lib/api'

const NET: Record<string, string> = { tiktok: 'TikTok', instagram: 'Instagram', google: 'Google Картинки', youtube: 'YouTube' }

/** "What is this university like": the best photos of every theme of the search plan (atmosphere, a day in the
 *  life, campus, dorms, events…) - an overview mosaic first, then theme by theme. Built by pipeline/collage.py. */
export function CollageTab({ profile, onOpen }: { profile: Profile; onOpen: (p: Photo) => void }) {
  const byId = useMemo(() => new Map(profile.photos.map((p) => [p.id, p])), [profile.photos])
  const sections = (profile.collage ?? []).map((s) => ({ ...s, items: s.photos.map((id) => byId.get(id)).filter((p): p is Photo => !!p) }))
  const filled = sections.filter((s) => s.items.length > 0)
  const total = filled.reduce((n, s) => n + s.items.length, 0)
  // the overview: the best two or three of every theme, interleaved, so the first screen already shows all sides
  const overview = useMemo(() => {
    const out: Photo[] = []
    for (let round = 0; round < 3; round++) for (const s of filled) if (s.items[round] && out.length < 18) out.push(s.items[round])
    return out
  }, [filled])
  const [showQueries, setShowQueries] = useState(false)

  if (!profile.collage || total === 0) {
    return <div className="text-sm text-muted">Коллаж собирается: фото появятся здесь по мере того, как их найдут и проверят.</div>
  }
  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h2 className="text-xl font-extrabold">Какой это вуз</h2>
          <div className="text-sm text-muted mt-0.5">
            {total} фото по {filled.length} темам — снято студентами и гостями, найдено поиском по соцсетям и Google, проверено ИИ
          </div>
        </div>
        <Link to={`/settings/search?u=${profile.university.qid}`} className="ml-auto inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-line-2 bg-surface text-[13px] font-medium hover:border-brand hover:text-brand">
          <SlidersHorizontal size={14} /> Настроить поиск
        </Link>
      </div>

      {overview.length > 0 && (
        <div className="grid grid-cols-6 grid-rows-3 gap-1.5 h-[420px] sm:h-[520px] rounded-xl overflow-hidden">
          {overview.slice(0, 11).map((p, i) => (
            <button key={p.id} onClick={() => onOpen(p)}
              className={`relative overflow-hidden bg-slate-100 group cursor-pointer ${i === 0 ? 'col-span-3 row-span-2' : i === 1 ? 'col-span-3 row-span-1' : i < 5 ? 'col-span-1 row-span-1' : 'col-span-1 row-span-1'}`}>
              <img src={thumbUrl(p)} alt={p.title ?? ''} loading="lazy" className="absolute inset-0 w-full h-full object-cover group-hover:scale-[1.03] transition-transform duration-500" />
              <span className="absolute left-1.5 bottom-1.5 px-1.5 py-0.5 rounded bg-black/55 text-white text-[10px] font-medium opacity-0 group-hover:opacity-100 transition-opacity">
                {sections.find((s) => s.key === p.collage)?.label}
              </span>
            </button>
          ))}
        </div>
      )}

      {sections.map((s) => (
        <section key={s.key}>
          <div className="flex items-baseline gap-2 mb-2">
            <h3 className="text-[17px] font-bold">{s.label}</h3>
            <span className="mono text-xs text-muted">{s.items.length}/{s.target}</span>
            {s.found > s.items.length && <span className="text-xs text-muted">· подходящих найдено {s.found}</span>}
          </div>
          {s.items.length === 0
            ? <div className="text-sm text-muted rounded-lg border border-dashed border-line p-4">Проверенных фото по этой теме не нашлось. Можно добавить свой запрос в настройках поиска.</div>
            : (
              <div className="columns-2 sm:columns-3 lg:columns-4 gap-2 [&>*]:mb-2">
                {s.items.map((p) => (
                  <button key={p.id} onClick={() => onOpen(p)} className="relative block w-full overflow-hidden rounded-lg bg-slate-100 break-inside-avoid group cursor-pointer" title={p.title ?? ''}>
                    <img src={thumbUrl(p)} alt={p.title ?? ''} loading="lazy" className="w-full h-auto block group-hover:scale-[1.02] transition-transform duration-500"
                      style={{ aspectRatio: `${p.width} / ${p.height}` }} />
                    <span className="absolute left-1.5 bottom-1.5 max-w-[92%] truncate px-1.5 py-0.5 rounded bg-black/55 text-white text-[10px] opacity-0 group-hover:opacity-100 transition-opacity">
                      {p.source_label}{p.query ? ` · «${p.query}»` : ''}
                    </span>
                  </button>
                ))}
              </div>
            )}
        </section>
      ))}

      {profile.plan && (
        <div className="rounded-xl border border-line">
          <button onClick={() => setShowQueries(!showQueries)} className="w-full flex items-center gap-2 px-4 py-3 text-sm font-semibold cursor-pointer">
            Что искали <ChevronDown size={15} className={`transition-transform ${showQueries ? 'rotate-180' : ''}`} />
            <span className="ml-auto text-xs font-normal text-muted">запросы по темам и сетям</span>
          </button>
          {showQueries && (
            <div className="border-t border-line divide-y divide-line">
              {profile.plan.intents.filter((i) => i.enabled).map((i) => (
                <div key={i.key} className="px-4 py-2.5 text-[13px]">
                  <div className="font-semibold">{i.label}</div>
                  {Object.entries(i.queries).map(([net, qs]) => qs.length > 0 && (
                    <div key={net} className="mt-0.5 text-muted"><span className="text-ink-2">{NET[net] ?? net}:</span> {qs.map((q) => `«${q}»`).join(', ')}</div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
