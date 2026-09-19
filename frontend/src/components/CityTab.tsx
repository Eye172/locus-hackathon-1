import { useEffect, useState } from 'react'
import { Coffee, Utensils, ShoppingCart, Pill, Landmark, Stethoscope, Film, Trees, Dumbbell, BookOpen, Bus, TrainFront, Plane, Navigation, Footprints, Car, Users, ExternalLink } from 'lucide-react'
import { api } from '../lib/api'
import type { ContextPack, CostPack, University } from '../lib/types'
import { useT } from '../lib/i18n'

const ICON: Record<string, React.ReactNode> = {
  cafe: <Coffee size={16} />, restaurant: <Utensils size={16} />, supermarket: <ShoppingCart size={16} />, pharmacy: <Pill size={16} />, bank: <Landmark size={16} />,
  hospital: <Stethoscope size={16} />, cinema: <Film size={16} />, park: <Trees size={16} />, sports: <Dumbbell size={16} />, library: <BookOpen size={16} />, bus: <Bus size={16} />, rail: <TrainFront size={16} />,
}
const fmt = (n: number, cur: string, t: (key: string) => string) => `${n.toLocaleString('ru-RU')} ${cur === 'KZT' ? '₸' : cur === 'KGS' ? t('city.somCurrency') : cur === 'UZS' ? t('city.sumCurrency') : cur}`

export function CityTab({ qid, uni, ctx, onCtx }: { qid: string; uni: University; ctx: ContextPack | null; onCtx: (c: ContextPack) => void }) {
  const t = useT()
  const [err, setErr] = useState<string | null>(null)
  const [cost, setCost] = useState<CostPack | null | 'none'>(null)
  useEffect(() => {
    if (!ctx) api.context(qid).then(onCtx).catch((e) => setErr(String(e)))
    if (uni.city_qid) api.cost(uni.city_qid).then(setCost).catch(() => setCost('none')); else setCost('none')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qid])
  if (err) return <div className="text-sm text-muted">{t('city.unavailable')}: {err}</div>
  if (!ctx) return <div className="grid grid-cols-2 md:grid-cols-4 gap-4">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="h-24 rounded-lg shimmer" />)}</div>
  const r = ctx.route_center
  const Num = ({ icon, v, l }: { icon: React.ReactNode; v: React.ReactNode; l: string }) => (
    <div className="min-w-0"><div className="lbl flex items-center gap-1.5 truncate"><span className="text-faint shrink-0">{icon}</span><span className="truncate">{l}</span></div><div className="val-lg mt-1.5">{v}</div></div>
  )
  return (
    <div className="space-y-14 max-w-5xl">
      <section>
        <h2 className="h-sec">{t('city.distanceTransportTitle')}</h2>
        <div className="mt-6 grid grid-cols-2 md:grid-cols-4 gap-x-8 gap-y-7">
          <Num icon={<Navigation size={13} />} v={r.distance_km != null ? `${r.distance_km} ${t('city.km')}` : '—'} l={`${t('city.toCenterPrefix')}${ctx.center.name ? ` · ${ctx.center.name}` : ''}`} />
          <Num icon={<Footprints size={13} />} v={r.walk_min != null ? `${r.walk_min} ${t('city.min')}` : '—'} l={t('city.walkToCenter')} />
          <Num icon={<Car size={13} />} v={r.drive_min != null ? `${r.drive_min} ${t('city.min')}` : '—'} l={`${t('city.byCarPrefix')} · ${r.source}`} />
          <Num icon={<Bus size={13} />} v={ctx.transit.stops_800m} l={t('city.stopsWithin800')} />
          {ctx.railway && <Num icon={<TrainFront size={13} />} v={`${ctx.railway.distance_m} ${t('city.meters')}`} l={`${ctx.railway.name ?? t('city.stationFallback')} · ${ctx.railway.kind === 'subway_entrance' ? t('city.metro') : ctx.railway.kind === 'tram_stop' ? t('city.tram') : t('city.rail')}`} />}
          {ctx.airport && <Num icon={<Plane size={13} />} v={ctx.airport.drive_min != null ? `${ctx.airport.drive_min} ${t('city.min')}` : `${ctx.airport.distance_km} ${t('city.km')}`} l={`${t('city.airportPrefix')} ${ctx.airport.iata ?? ''} · ${ctx.airport.road_km ?? ctx.airport.distance_km} ${t('city.km')}`} />}
          {ctx.center.population && <Num icon={<Users size={13} />} v={ctx.center.population.toLocaleString('ru-RU')} l={t('city.population')} />}
        </div>
      </section>

      <section>
        <h2 className="h-sec">{t('city.nearbyTitle')}<small>{t('city.nearbySubtitle')}</small></h2>
        {ctx.poi.length === 0 ? <div className="mt-4 text-sm text-muted">{t('city.noOsmObjects')}</div> : (
          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-x-12">
            {ctx.poi.map((g) => (
              <div key={g.kind} className="flex items-baseline gap-3 py-3 border-t border-line min-w-0">
                <span className="text-muted self-center shrink-0">{ICON[g.kind]}</span>
                <span className="text-[15px] first-letter:uppercase shrink-0">{g.label}</span>
                <span className="text-[13px] text-muted truncate min-w-0 flex-1">{g.items.filter((i) => i.name).slice(0, 3).map((i) => i.name).join(' · ')}</span>
                <span className="mono text-[15px] font-medium shrink-0">{g.count}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="h-sec">{t('city.budgetTitle')}</h2>
        {cost === null && <div className="mt-4 h-32 rounded-lg shimmer" />}
        {cost === 'none' && <div className="mt-3 text-sm text-muted">{t('city.noBudgetData')}</div>}
        {cost && cost !== 'none' && (
          <div className="mt-4 grid grid-cols-1 md:grid-cols-[1fr_280px] gap-x-12 gap-y-6">
            <table className="w-full text-[15px] self-start">
              <tbody>
                {([['dorm', t('city.itemDorm')], ['rent_1room', t('city.itemRent')], ['transport_pass', t('city.itemTransportPass')], ['canteen_lunch', t('city.itemLunch')], ['groceries_month', t('city.itemGroceries')]] as const).map(([k, l]) => (
                  <tr key={k} className="rule"><td className="py-3 text-ink-2">{l}</td><td className="py-3 mono text-right">{fmt(cost.items[k], cost.currency, t)}</td></tr>
                ))}
              </tbody>
            </table>
            <div className="md:border-l md:border-line md:pl-8 space-y-6">
              <div><div className="lbl">{t('city.withDorm')}</div><div className="val-lg mt-1.5">{fmt(cost.total_student_month_dorm, cost.currency, t)}</div></div>
              <div><div className="lbl">{t('city.withRent')}</div><div className="val-lg mt-1.5 text-ink-2">{fmt(cost.total_student_month_rent, cost.currency, t)}</div></div>
              <div className="text-[12px] text-faint leading-relaxed">{t('city.approx')} · {cost.as_of} · {cost.sources.map((s, i) => <a key={i} href={s.url} target="_blank" rel="noreferrer" className="hover:text-ink">{s.label}{i < cost.sources.length - 1 ? ', ' : ''}</a>)}</div>
            </div>
          </div>
        )}
      </section>
      <div className="text-[12px] text-faint">{t('city.sourcesLabel')}: {ctx.sources.map((s, i) => <a key={i} href={s.url} target="_blank" rel="noreferrer" className="hover:text-ink inline-flex items-center gap-0.5">{s.label}<ExternalLink size={10} />{i < ctx.sources.length - 1 ? ' · ' : ''}</a>)} · {ctx.generated_at.slice(0, 10)}</div>
    </div>
  )
}
