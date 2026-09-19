import { useEffect, useMemo, useState } from 'react'
import { Sun, Cloud, CloudRain, CloudSnow, CloudFog, CloudLightning, CloudDrizzle, Wind, Droplets, ExternalLink, Map as MapIcon } from 'lucide-react'
import { api } from '../lib/api'
import type { ClimatePack, ClimateStory } from '../lib/types'
import { useLang, useT, type Lang } from '../lib/i18n'

type T = (key: string) => string
type SeasonKey = 'winter' | 'spring' | 'summer' | 'autumn'
const SEASON_KEYS: SeasonKey[] = ['winter', 'spring', 'summer', 'autumn']

/* Chart colours (DESIGN.md allows colour in charts only). How a day feels is one scale: two steps of blue for cold,
   mint for comfortable, two steps of orange for warm and hot; rainy days are grey with a dot, so rain is never
   told by colour alone. */
const FEEL = { freezing: '#1C5CAB', cool: '#9EC5F4', comfortable: '#1BAF7A', warm: '#F4A57C', hot: '#E0561F', rainy: '#C3C8D1' } as const
const CLS: Record<string, keyof typeof FEEL> = { f: 'freezing', k: 'cool', c: 'comfortable', w: 'warm', h: 'hot', r: 'rainy' }
const COMFORT_ORDER: (keyof typeof FEEL)[] = ['freezing', 'cool', 'comfortable', 'warm', 'hot', 'rainy']

/** Month bars: diverging cold blue - neutral grey - warm orange, by the month's mean of highs and lows. */
function tempColor(t: number): string {
  const stops: [number, [number, number, number]][] = [[-15, [28, 92, 171]], [-3, [85, 152, 231]], [8, [178, 185, 196]], [18, [242, 154, 110]], [28, [217, 72, 15]]]
  if (t <= stops[0][0]) return `rgb(${stops[0][1].join(',')})`
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i][0]) {
      const [t0, c0] = stops[i - 1], [t1, c1] = stops[i]
      const k = (t - t0) / (t1 - t0)
      return `rgb(${c0.map((c, j) => Math.round(c + (c1[j] - c) * k)).join(',')})`
    }
  }
  return `rgb(${stops[stops.length - 1][1].join(',')})`
}

const WMO_KEYS: Record<number, [string, typeof Sun]> = {
  0: ['climate.clear', Sun], 1: ['climate.mostlyClear', Sun], 2: ['climate.partlyCloudy', Cloud], 3: ['climate.cloudy', Cloud],
  45: ['climate.fog', CloudFog], 48: ['climate.rime', CloudFog], 51: ['climate.drizzle', CloudDrizzle], 53: ['climate.drizzle', CloudDrizzle], 55: ['climate.drizzle', CloudDrizzle],
  61: ['climate.rain', CloudRain], 63: ['climate.rain', CloudRain], 65: ['climate.heavyRain', CloudRain], 71: ['climate.snowWord', CloudSnow], 73: ['climate.snowWord', CloudSnow], 75: ['climate.heavySnow', CloudSnow],
  80: ['climate.showers', CloudRain], 81: ['climate.showers', CloudRain], 82: ['climate.showers', CloudRain], 95: ['climate.thunderstorm', CloudLightning], 96: ['climate.thunderstormHail', CloudLightning], 99: ['climate.thunderstormHail', CloudLightning],
}
function Weather({ code, t, size = 18 }: { code: number; t: T; size?: number }) {
  const [key, Icon] = WMO_KEYS[code] ?? ['', Cloud]
  return <Icon size={size} strokeWidth={1.75} aria-label={key ? t(key) : undefined} />
}
const weatherLabel = (code: number, t: T) => (WMO_KEYS[code] ? t(WMO_KEYS[code][0]) : '—')

/** +5°, −3°, 0° (a real minus sign; −0 is 0). */
function deg(v: number | null | undefined): string {
  if (v == null) return '—'
  const r = Math.round(v)
  return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r)}°`
}
const num = (v: number | null | undefined, lang: Lang) => (v == null ? '—' : v.toLocaleString(lang, { maximumFractionDigits: 1 }))
const fill = (s: string, vars: Record<string, string | number>) => s.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''))
const RU_MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
const monthName = (m: number, lang: Lang, style: 'short' | 'long' = 'short') =>
  style === 'short' && lang === 'ru' ? RU_MONTHS[m - 1]
    : new Intl.DateTimeFormat(lang, { month: style }).format(new Date(2025, m - 1, 15)).replace(/\.$/, '')

const WIND_FROM: Record<Lang, Record<string, string>> = {
  ru: { N: 'с севера', NE: 'с северо-востока', E: 'с востока', SE: 'с юго-востока', S: 'с юга', SW: 'с юго-запада', W: 'с запада', NW: 'с северо-запада' },
  en: { N: 'north', NE: 'north-east', E: 'east', SE: 'south-east', S: 'south', SW: 'south-west', W: 'west', NW: 'north-west' },
  kk: { N: 'солтүстіктен', NE: 'солтүстік-шығыстан', E: 'шығыстан', SE: 'оңтүстік-шығыстан', S: 'оңтүстіктен', SW: 'оңтүстік-батыстан', W: 'батыстан', NW: 'солтүстік-батыстан' },
}
const WIND_SHORT: Record<Lang, Record<string, string>> = {
  ru: { N: 'С', NE: 'СВ', E: 'В', SE: 'ЮВ', S: 'Ю', SW: 'ЮЗ', W: 'З', NW: 'СЗ' },
  en: { N: 'N', NE: 'NE', E: 'E', SE: 'SE', S: 'S', SW: 'SW', W: 'W', NW: 'NW' },
  kk: { N: 'С', NE: 'СШ', E: 'Ш', SE: 'ОШ', S: 'О', SW: 'ОБ', W: 'Б', NW: 'СБ' },
}

function SectionTitle({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h2 className="text-[20px] font-semibold tracking-[-0.015em]">{title}</h2>
      {sub && <span className="text-[13.5px] text-muted">{sub}</span>}
    </div>
  )
}

/** The year as a flower: one petal per month, colour = temperature, length = precipitation; the current month is
 *  outlined and the centre shows the temperature right now (the year's mean when there is no live reading). */
function YearRing({ c, t, lang }: { c: ClimatePack; t: T; lang: Lang }) {
  const R = 122, r0 = 62, cx = 150, cy = 150
  const maxP = Math.max(1, ...c.months.map((m) => m.precip_mm))
  const nowMonth = c.now?.time ? Number(c.now.time.slice(5, 7)) : null
  const arc = (i: number, rin: number, rout: number) => {
    const a0 = (i / 12) * Math.PI * 2 - Math.PI / 2 + 0.025, a1 = ((i + 1) / 12) * Math.PI * 2 - Math.PI / 2 - 0.025
    const p = (a: number, rr: number) => [cx + rr * Math.cos(a), cy + rr * Math.sin(a)]
    const [x0, y0] = p(a0, rout), [x1, y1] = p(a1, rout), [x2, y2] = p(a1, rin), [x3, y3] = p(a0, rin)
    return `M${x0} ${y0}A${rout} ${rout} 0 0 1 ${x1} ${y1}L${x2} ${y2}A${rin} ${rin} 0 0 0 ${x3} ${y3}Z`
  }
  return (
    <svg viewBox="0 0 300 300" className="w-full max-w-[280px] mx-auto" role="img" aria-label={t('climate.ringNote').replace('{year}', String(c.year))}>
      {c.months.map((m, i) => {
        const tm = m.t_hi != null && m.t_lo != null ? (m.t_hi + m.t_lo) / 2 : m.t_mean ?? 0
        const rout = r0 + 20 + (R - r0 - 20) * (m.precip_mm / maxP)
        const a = ((i + 0.5) / 12) * Math.PI * 2 - Math.PI / 2
        const current = m.m === nowMonth
        return (
          <g key={m.m}>
            <path d={arc(i, r0, rout)} fill={tempColor(tm)} stroke={current ? '#0B0D12' : '#fff'} strokeWidth={current ? 2 : 1.5}>
              <title>{`${monthName(m.m, lang, 'long')}: ${deg(m.t_mean)}, ${t('climate.precipWord')} ${m.precip_mm} ${t('climate.mmUnit')}`}</title>
            </path>
            <text x={cx + (R + 15) * Math.cos(a)} y={cy + (R + 15) * Math.sin(a)} textAnchor="middle" dominantBaseline="middle"
              fill={current ? '#0B0D12' : '#6B7180'} style={{ fontSize: 10.5, fontFamily: 'Onest', fontWeight: current ? 600 : 400 }}>{monthName(m.m, lang)}</text>
            <text x={cx + (r0 + 12) * Math.cos(a)} y={cy + (r0 + 12) * Math.sin(a)} textAnchor="middle" dominantBaseline="middle"
              fill={tm > 2 && tm < 21 ? '#0B0D12' : '#fff'} style={{ fontSize: 9.5, fontFamily: 'Onest' }}>{deg(m.t_mean)}</text>
          </g>
        )
      })}
      <text x={cx} y={cy - 4} textAnchor="middle" fill="#0B0D12" style={{ fontSize: 34, fontWeight: 600, letterSpacing: '-0.03em', fontFamily: 'Onest' }}>{deg(c.now ? c.now.t : c.annual.t_mean)}</text>
      <text x={cx} y={cy + 18} textAnchor="middle" fill="#6B7180" style={{ fontSize: 11, fontFamily: 'Onest' }}>{c.now ? t('climate.nowShort') : t('climate.yearAvg')}</text>
    </svg>
  )
}

function Now({ c, city, t, lang }: { c: ClimatePack; city?: string | null; t: T; lang: Lang }) {
  const n = c.now
  const weekday = new Intl.DateTimeFormat(lang, { weekday: 'short' })
  return (
    <section className="grid gap-8 md:grid-cols-[280px_minmax(0,1fr)] md:items-center">
      <YearRing c={c} t={t} lang={lang} />
      <div className="space-y-5">
        <div>
          <div className="text-[13.5px] text-muted">{t('climate.now')}{city ? ` · ${city}` : ''}</div>
          {n ? (
            <>
              <div className="mt-2 flex items-center gap-2.5 text-[22px] font-medium tracking-[-0.01em] text-ink"><Weather code={n.code} t={t} size={24} /><span className="first-letter:uppercase">{weatherLabel(n.code, t)}</span></div>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[14px] text-muted">
                <span>{t('climate.feelsWord')} <span className="mono text-ink-2">{deg(n.feels)}</span></span>
                <span className="inline-flex items-center gap-1"><Droplets size={14} strokeWidth={1.75} /><span className="mono">{Math.round(n.humidity)} %</span></span>
                <span className="inline-flex items-center gap-1"><Wind size={14} strokeWidth={1.75} /><span className="mono">{num(n.wind_ms, lang)} {t('climate.msUnit')}</span></span>
              </div>
            </>
          ) : <div className="mt-2 text-[15px] text-ink-2">{t('climate.yearAvg')}: <span className="mono">{deg(c.annual.t_mean)}</span></div>}
        </div>
        {c.forecast && c.forecast.length > 0 && (
        <ol className="grid grid-cols-6 rounded-xl border border-line divide-x divide-line overflow-hidden max-w-[560px]">
          {c.forecast.map((d, i) => (
            <li key={d.date} className="px-2 sm:px-3.5 py-2.5 text-center sm:min-w-[68px]" title={weatherLabel(d.code, t)}>
              <div className="text-[12.5px] text-muted first-letter:uppercase">{i === 0 ? t('climate.today') : weekday.format(new Date(`${d.date}T12:00`))}</div>
              <div className="my-1.5 flex justify-center text-ink-2"><Weather code={d.code} t={t} /></div>
              <div className="mono text-[13px] sm:text-[13.5px] flex flex-col sm:block sm:whitespace-nowrap"><span className="text-ink font-medium">{deg(d.t_max)}</span> <span className="text-muted">{deg(d.t_min)}</span></div>
            </li>
          ))}
        </ol>
        )}
        <p className="max-w-[560px] text-[12.5px] leading-relaxed text-muted">{fill(t('climate.ringNote'), { year: c.year })}</p>
      </div>
    </section>
  )
}

function Story({ c, story, t }: { c: ClimatePack; story: ClimateStory | null | 'error'; t: T }) {
  const s = story && story !== 'error' ? story : null
  const lead = s?.lead || (story === 'error' ? c.feels_text.text : '')
  const aside = s && (s.packing.length > 0 || s.best)
  return (
    <section className={`grid gap-x-12 gap-y-8 ${aside ? 'lg:grid-cols-[minmax(0,1fr)_300px]' : ''}`}>
      <div className="max-w-[68ch]">
        <SectionTitle title={t('climate.storyTitle')} />
        {story === null ? (
          <div className="mt-4 space-y-3" aria-busy="true">
            <div className="h-5 rounded shimmer w-full" /><div className="h-5 rounded shimmer w-11/12" /><div className="h-5 rounded shimmer w-3/5" />
            <div className="pt-1 text-[13px] text-muted">{t('climate.writing')}</div>
          </div>
        ) : (
          <>
            <p className="mt-4 text-[19px] leading-[1.55] tracking-[-0.005em] text-ink">{lead}</p>
            {s?.story && <p className="mt-4 text-[16px] leading-[1.7] text-ink-2">{s.story}</p>}
            <p className="mt-4 text-[12.5px] text-faint">{fill(t(s?.mode === 'ai' ? 'climate.aiNote' : 'climate.templateNote'), { year: c.year })}</p>
          </>
        )}
      </div>
      {aside && (
        <aside className="space-y-8 lg:pt-12">
          {s.packing.length > 0 && (
            <div>
              <h3 className="text-[13px] font-medium text-muted">{t('climate.packTitle')}</h3>
              <ul className="mt-3 space-y-2.5">
                {s.packing.map((x) => <li key={x} className="flex gap-3 text-[15px] leading-snug text-ink"><span className="mt-[10px] w-2 h-px bg-ink shrink-0" />{x}</li>)}
              </ul>
            </div>
          )}
          {s.best && (
            <div>
              <h3 className="text-[13px] font-medium text-muted">{t('climate.bestTitle')}</h3>
              <p className="mt-2 text-[15px] leading-relaxed text-ink-2">{s.best}</p>
            </div>
          )}
        </aside>
      )}
    </section>
  )
}

function YearChart({ c, t, lang }: { c: ClimatePack; t: T; lang: Lang }) {
  const [hover, setHover] = useState<number | null>(null)
  const months = c.months.map((m) => ({ ...m, hi: m.t_hi ?? m.t_max ?? m.t_mean ?? 0, lo: m.t_lo ?? m.t_min ?? m.t_mean ?? 0 }))
  const H = 190, PAD = 24
  const yMax = Math.ceil(Math.max(...months.map((m) => m.hi))) + 2
  const yMin = Math.floor(Math.min(...months.map((m) => m.lo))) - 2
  const y = (v: number) => ((yMax - v) / (yMax - yMin)) * H
  const rows: [string, (m: (typeof months)[number]) => string][] = [
    [t('climate.rowPrecip'), (m) => String(m.precip_days)],
    [t('climate.rowSnow'), (m) => String(m.snow_days)],
    [t('climate.rowSun'), (m) => num(m.sun_h_day, lang)],
  ]
  const h = hover != null ? months[hover] : null
  const col = (i: number) => `transition-colors ${hover === i ? 'bg-soft' : ''}`
  return (
    <section>
      <SectionTitle title={t('climate.yearTitle')} sub={fill(t('climate.yearSub'), { year: c.year })} />
      <div className="mt-6 grid grid-cols-[minmax(0,1fr)] sm:grid-cols-[140px_minmax(0,1fr)] sm:gap-x-4" onMouseLeave={() => setHover(null)}>
        <div className="hidden sm:flex items-end pb-3 text-[12.5px] leading-snug text-muted">{t('climate.chartLegend')}</div>
        <div className="relative" style={{ height: H + PAD * 2 }}>
          {yMin < 0 && yMax > 0 && (
            <div className="absolute inset-x-0 border-t border-dashed border-line-2" style={{ top: PAD + y(0) }}>
              <span className="absolute right-0 -top-[18px] text-[11px] text-faint mono">0°</span>
            </div>
          )}
          <div className="absolute inset-x-0 grid grid-cols-12" style={{ top: PAD, height: H }}>
            {months.map((m, i) => (
              <button key={m.m} type="button" className={`relative rounded-md cursor-default ${col(i)}`} style={{ marginTop: -PAD, marginBottom: -PAD }}
                onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onClick={() => setHover(hover === i ? null : i)}
                aria-label={`${monthName(m.m, lang, 'long')}: ${t('climate.day')} ${deg(m.hi)}, ${t('climate.night')} ${deg(m.lo)}`}>
                <span className="absolute left-1/2 -translate-x-1/2 w-[min(16px,50%)] rounded-full" style={{ top: PAD + y(m.hi), height: Math.max(4, y(m.lo) - y(m.hi)), background: tempColor((m.hi + m.lo) / 2) }} />
                <span className="absolute inset-x-0 text-center mono text-[11px] sm:text-[12.5px] font-medium text-ink" style={{ top: PAD + y(m.hi) - 19 }}>{deg(m.hi)}</span>
                <span className="absolute inset-x-0 text-center mono text-[11px] sm:text-[12.5px] text-muted" style={{ top: PAD + y(m.lo) + 3 }}>{deg(m.lo)}</span>
              </button>
            ))}
          </div>
          {h && hover != null && (
            <div className="pointer-events-none absolute z-10 -top-3 w-max max-w-[220px] rounded-lg bg-ink text-white px-3 py-2 text-[12.5px] leading-relaxed shadow-lg"
              style={{ left: `${((hover + 0.5) / 12) * 100}%`, transform: `translateX(${hover < 2 ? '-15%' : hover > 9 ? '-85%' : '-50%'}) translateY(-100%)` }}>
              <div className="font-medium first-letter:uppercase">{monthName(h.m, lang, 'long')}</div>
              <div>{t('climate.day')} <span className="mono">{deg(h.hi)}</span> · {t('climate.night')} <span className="mono">{deg(h.lo)}</span></div>
              <div className="text-white/70">{t('climate.feelsWord')} <span className="mono">{deg(h.feels)}</span> · {t('climate.precipWord')} <span className="mono">{h.precip_mm}</span> {t('climate.mmUnit')}</div>
            </div>
          )}
        </div>
        <div className="hidden sm:block" />
        <div className="grid grid-cols-12 border-t border-line text-center text-[12px] sm:text-[12.5px] text-muted">
          {months.map((m, i) => <div key={m.m} className={`py-2 rounded-b-md ${col(i)}`}>{monthName(m.m, lang)}</div>)}
        </div>
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <div className="pt-3 sm:pt-0 sm:h-10 sm:flex sm:items-center sm:border-t border-line text-[12.5px] sm:text-[13px] text-muted">{label}</div>
            <div className="grid grid-cols-12 border-t border-line text-center mono tnum text-[12.5px] sm:text-[13.5px] text-ink-2">
              {months.map((m, i) => <div key={m.m} className={`h-9 sm:h-10 flex items-center justify-center ${col(i)}`} onMouseEnter={() => setHover(i)}>{value(m)}</div>)}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function Seasons({ c, story, t, lang }: { c: ClimatePack; story: ClimateStory | null; t: T; lang: Lang }) {
  return (
    <section>
      <SectionTitle title={t('climate.seasonsHeading')} sub={c.hemisphere === 'south' ? t('climate.southNote') : undefined} />
      <div className="mt-6 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-x-8 gap-y-10">
        {SEASON_KEYS.map((k) => {
          const s = c.seasons[k]
          const ms = s.months
          const line = story?.seasons[k]
          return (
            <div key={k} className="border-t-2 border-ink pt-4">
              <div className="flex items-baseline justify-between gap-3">
                <h3 className="text-[16px] font-semibold">{t(`climate.${k}`)}</h3>
                {ms && <span className="text-[12.5px] text-muted">{monthName(ms[0], lang)} – {monthName(ms[ms.length - 1], lang)}</span>}
              </div>
              <div className="mt-3 flex items-end gap-3">
                <span className="mono text-[34px] leading-none font-semibold tracking-[-0.03em]">{deg(s.t_mean)}</span>
                <span className="pb-0.5 text-[13px] leading-tight text-muted">
                  {s.t_hi != null && s.t_lo != null && <>{t('climate.day')} <span className="mono text-ink-2">{deg(s.t_hi)}</span> · {t('climate.night')} <span className="mono text-ink-2">{deg(s.t_lo)}</span><br /></>}
                  {t('climate.feelsWord')} <span className="mono text-ink-2">{deg(s.feels)}</span>
                </span>
              </div>
              {line && <p className="mt-3 text-[14.5px] leading-relaxed text-ink-2">{line}</p>}
              <p className="mt-3 text-[13px] leading-relaxed text-muted">
                {t('climate.precipWord')} <span className="mono text-ink-2">{s.precip_days}</span> {t('climate.daysUnit')} · {t('climate.snowWord')} <span className="mono text-ink-2">{s.snow_days}</span> {t('climate.daysUnit')} · {t('climate.sunWord')} <span className="mono text-ink-2">{num(s.sun_h_day, lang)}</span> {t('climate.hDay')}
              </p>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function Calendar({ c, t, lang }: { c: ClimatePack; t: T; lang: Lang }) {
  const [hover, setHover] = useState<{ date: Date; cls: keyof typeof FEEL; feels: number | null; precip: number } | null>(null)
  const byMonth = useMemo(() => {
    const out: { date: Date; cls: keyof typeof FEEL | null; feels: number | null; precip: number }[][] = Array.from({ length: 12 }, () => [])
    const d = c.days
    if (!d) return out
    const start = new Date(`${d.start}T12:00`)
    for (let i = 0; i < d.cls.length; i++) {
      const date = new Date(start); date.setDate(start.getDate() + i)
      out[date.getMonth()].push({ date, cls: CLS[d.cls[i]] ?? null, feels: d.feels[i], precip: d.precip[i] })
    }
    return out
  }, [c.days])
  const dayFmt = new Intl.DateTimeFormat(lang, { day: 'numeric', month: 'long' })
  if (!c.days) return null
  return (
    <div className="mt-6 max-w-[620px]" onMouseLeave={() => setHover(null)}>
      <div className="space-y-[3px]">
        {byMonth.map((days, m) => (
          <div key={m} className="grid grid-cols-[34px_minmax(0,1fr)] items-center gap-2">
            <span className="text-[11.5px] text-muted">{monthName(m + 1, lang)}</span>
            <div className="grid gap-[3px]" style={{ gridTemplateColumns: 'repeat(31, minmax(0, 1fr))' }}>
              {days.map((d) => (
                <span key={d.date.getDate()} className="relative aspect-square rounded-[3px]"
                  style={{ background: d.cls ? FEEL[d.cls] : '#F4F5F7', outline: hover?.date.getTime() === d.date.getTime() ? '2px solid #0B0D12' : undefined, outlineOffset: 1 }}
                  onMouseEnter={() => d.cls && setHover({ ...d, cls: d.cls })}>
                  {d.cls === 'rainy' && <i className="absolute inset-0 m-auto w-[3px] h-[3px] rounded-full bg-ink-2" />}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-3 pl-[42px] min-h-[20px] text-[13px] text-ink-2" aria-live="polite">
        {hover ? (
          <span><span className="first-letter:uppercase">{dayFmt.format(hover.date)}</span> · {t(`climate.${hover.cls}`)} · {t('climate.feelsWord')} <span className="mono">{deg(hover.feels)}</span> · <span className="mono">{num(hover.precip, lang)}</span> {t('climate.mmUnit')}</span>
        ) : <span className="text-muted">{fill(t('climate.comfortCells'), { year: c.year })}</span>}
      </div>
    </div>
  )
}

function WindRose({ c, t, lang }: { c: ClimatePack; t: T; lang: Lang }) {
  const rose = c.wind_rose
  const cx = 90, cy = 90, R = 64
  const max = Math.max(...rose.map((r) => r.share), 0.01)
  const pts = rose.map((r, i) => { const a = (i / 8) * Math.PI * 2 - Math.PI / 2; const rr = 8 + (R - 8) * (r.share / max); return [cx + rr * Math.cos(a), cy + rr * Math.sin(a)] })
  const top = rose.reduce((a, b) => (b.share > a.share ? b : a), rose[0])
  const windiest = SEASON_KEYS.reduce((a, b) => ((c.seasons[b].wind_ms ?? 0) > (c.seasons[a].wind_ms ?? 0) ? b : a), SEASON_KEYS[0])
  return (
    <div>
      <h3 className="text-[16px] font-semibold">{t('climate.windRoseTitle')}</h3>
      <svg viewBox="0 0 180 180" className="mt-3 w-44 h-44" role="img" aria-label={t('climate.windRoseTitle')}>
        {[0.34, 0.67, 1].map((k) => <circle key={k} cx={cx} cy={cy} r={8 + (R - 8) * k} fill="none" stroke="#E9EBEF" />)}
        {rose.map((r, i) => { const a = (i / 8) * Math.PI * 2 - Math.PI / 2; return <text key={r.dir} x={cx + (R + 14) * Math.cos(a)} y={cy + (R + 14) * Math.sin(a)} textAnchor="middle" dominantBaseline="middle" fill="#6B7180" style={{ fontSize: 10, fontFamily: 'Onest' }}>{WIND_SHORT[lang][r.dir] ?? r.dir}</text> })}
        <polygon points={pts.map((p) => p.join(',')).join(' ')} fill="rgba(29,78,216,0.10)" stroke="#1D4ED8" strokeWidth="1.5" strokeLinejoin="round" />
        {pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r="2.5" fill="#1D4ED8"><title>{`${WIND_SHORT[lang][rose[i].dir] ?? rose[i].dir}: ${Math.round(rose[i].share * 100)} % · ${num(rose[i].speed, lang)} ${t('climate.msUnit')}`}</title></circle>)}
      </svg>
      <p className="mt-3 text-[14px] leading-relaxed text-ink-2">
        {fill(t('climate.windMost'), { dir: WIND_FROM[lang][top.dir] ?? top.dir, share: Math.round(top.share * 100), speed: num(top.speed, lang) })}{' '}
        {fill(t('climate.windiest'), { season: t(`climate.${windiest}`).toLowerCase(), speed: num(c.seasons[windiest].wind_ms, lang) })}
      </p>
    </div>
  )
}

function Comfort({ c, t, lang }: { c: ClimatePack; t: T; lang: Lang }) {
  return (
    <section className="grid gap-x-12 gap-y-10 lg:grid-cols-[minmax(0,1fr)_240px]">
      <div>
        <SectionTitle title={t('climate.comfortHeading')} />
        <div className="mt-5 flex flex-wrap gap-x-8 gap-y-4">
          {COMFORT_ORDER.filter((k) => c.comfort[k] != null).map((k) => (
            <div key={k}>
              <div className="mono text-[28px] leading-none font-semibold tracking-[-0.02em]">{c.comfort[k]}</div>
              <div className="mt-1.5 flex items-center gap-1.5 text-[13px] text-ink-2">
                <i className="relative w-2.5 h-2.5 rounded-[3px]" style={{ background: FEEL[k] }}>{k === 'rainy' && <i className="absolute inset-0 m-auto w-[3px] h-[3px] rounded-full bg-ink-2" />}</i>
                {t(`climate.${k}`)}
              </div>
            </div>
          ))}
        </div>
        <Calendar c={c} t={t} lang={lang} />
        <p className="mt-2 text-[12.5px] text-muted">{t('climate.comfortLegend')}</p>
      </div>
      <aside><WindRose c={c} t={t} lang={lang} /></aside>
    </section>
  )
}

function LiveMap({ lat, lon, t }: { lat: number; lon: number; t: T }) {
  const [open, setOpen] = useState(false)
  const [overlay, setOverlay] = useState<'wind' | 'clouds' | 'temp'>('wind')
  return (
    <section>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <SectionTitle title={t('climate.liveMapTitle')} sub={t('climate.liveMapSub')} />
        <div className="ml-auto flex gap-1">
          {open ? (['wind', 'clouds', 'temp'] as const).map((o) => (
            <button key={o} onClick={() => setOverlay(o)} className={`filter ${overlay === o ? 'filter-active' : ''}`}>
              {o === 'wind' ? t('climate.windWord') : o === 'clouds' ? t('climate.cloudsWord') : t('climate.temperatureWord')}
            </button>
          )) : <button className="btn-ghost" onClick={() => setOpen(true)}><MapIcon size={16} strokeWidth={1.75} />{t('climate.liveMapShow')}</button>}
        </div>
      </div>
      {open && (
        <iframe title="Windy" className="mt-4 w-full h-[420px] rounded-xl border border-line" loading="lazy"
          src={`https://embed.windy.com/embed2.html?lat=${lat}&lon=${lon}&detailLat=${lat}&detailLon=${lon}&zoom=7&level=surface&overlay=${overlay}&product=ecmwf&menu=&message=&marker=true&calendar=now&type=map&location=coordinates&metricWind=m%2Fs&metricTemp=%C2%B0C&radarRange=-1`} />
      )}
    </section>
  )
}

export function ClimateTab({ qid, lat, lon, city }: { qid: string; lat?: number | null; lon?: number | null; city?: string | null }) {
  const t = useT()
  const lang = useLang()
  const [c, setC] = useState<ClimatePack | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [story, setStory] = useState<ClimateStory | null | 'error'>(null)
  const load = () => { setC(null); setErr(null); api.climate(qid).then(setC).catch((e) => setErr(String(e))) }
  useEffect(load, [qid])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    let live = true
    setStory(null)
    api.climateStory(qid, lang).then((s) => { if (live) setStory(s) }).catch(() => { if (live) setStory('error') })
    return () => { live = false }
  }, [qid, lang])

  if (err) return (
    <div className="py-10 text-[15px] text-ink-2">
      {t('climate.unavailable')}. <button className="btn-text underline" onClick={load}>{t('climate.retry')}</button>
      <div className="mt-1 text-[12.5px] text-faint">{err}</div>
    </div>
  )
  if (!c) return (
    <div className="space-y-8" aria-busy="true">
      <div className="text-[13.5px] text-muted">{t('climate.loadingYear')}</div>
      <div className="h-20 rounded-xl shimmer" />
      <div className="h-64 rounded-xl shimmer" />
    </div>
  )
  const storyReady = story && story !== 'error' ? story : null
  return (
    <div className="space-y-14">
      <Now c={c} city={city} t={t} lang={lang} />
      <div className="border-t border-line pt-10"><Story c={c} story={story} t={t} /></div>
      <div className="border-t border-line pt-10"><YearChart c={c} t={t} lang={lang} /></div>
      <div className="border-t border-line pt-10"><Seasons c={c} story={storyReady} t={t} lang={lang} /></div>
      <div className="border-t border-line pt-10"><Comfort c={c} t={t} lang={lang} /></div>
      {lat != null && lon != null && <div className="border-t border-line pt-10"><LiveMap lat={lat} lon={lon} t={t} /></div>}
      <div className="text-[12px] text-muted">
        {t('climate.sourceLabel')}: <a href={c.source.url} target="_blank" rel="noreferrer" className="hover:text-brand inline-flex items-center gap-0.5">{c.source.label} <ExternalLink size={11} /></a> · {c.year}{c.timezone ? ` · ${t('climate.timezoneLabel')} ${c.timezone}` : ''}
      </div>
    </div>
  )
}
