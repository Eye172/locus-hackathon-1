import { useEffect, useState } from 'react'
import { Sun, Cloud, CloudRain, CloudSnow, CloudFog, CloudLightning, CloudDrizzle, Wind, Droplets, ExternalLink } from 'lucide-react'
import { api } from '../lib/api'
import type { ClimatePack } from '../lib/types'
import { useT } from '../lib/i18n'

type T = (key: string) => string

/** Temperature → colour (cold blue → warm red), piecewise in RGB. */
export function tempColor(t: number | null): string {
  if (t == null) return '#E3E6EC'
  const stops: [number, [number, number, number]][] = [[-30, [30, 58, 138]], [-10, [96, 165, 250]], [0, [224, 242, 254]], [10, [253, 230, 138]], [22, [249, 115, 22]], [34, [185, 28, 28]]]
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

const WMO_KEYS: Record<number, [string, React.ReactNode]> = {
  0: ['climate.clear', <Sun size={18} />], 1: ['climate.mostlyClear', <Sun size={18} />], 2: ['climate.partlyCloudy', <Cloud size={18} />], 3: ['climate.cloudy', <Cloud size={18} />],
  45: ['climate.fog', <CloudFog size={18} />], 48: ['climate.rime', <CloudFog size={18} />], 51: ['climate.drizzle', <CloudDrizzle size={18} />], 53: ['climate.drizzle', <CloudDrizzle size={18} />], 55: ['climate.drizzle', <CloudDrizzle size={18} />],
  61: ['climate.rain', <CloudRain size={18} />], 63: ['climate.rain', <CloudRain size={18} />], 65: ['climate.heavyRain', <CloudRain size={18} />], 71: ['climate.snowWord', <CloudSnow size={18} />], 73: ['climate.snowWord', <CloudSnow size={18} />], 75: ['climate.heavySnow', <CloudSnow size={18} />],
  80: ['climate.showers', <CloudRain size={18} />], 81: ['climate.showers', <CloudRain size={18} />], 82: ['climate.showers', <CloudRain size={18} />], 95: ['climate.thunderstorm', <CloudLightning size={18} />], 96: ['climate.thunderstormHail', <CloudLightning size={18} />], 99: ['climate.thunderstormHail', <CloudLightning size={18} />],
}
const wmo = (c: number, t: T): [string, React.ReactNode] => {
  const e = WMO_KEYS[c]
  return e ? [t(e[0]), e[1]] : ['—', <Cloud size={18} />]
}
const sign = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Math.round(v)}°`)

function YearRing({ c, t }: { c: ClimatePack; t: T }) {
  const R = 118, r0 = 58, cx = 150, cy = 150
  const maxP = Math.max(1, ...c.months.map((m) => m.precip_mm))
  const arc = (i: number, rin: number, rout: number) => {
    const a0 = (i / 12) * Math.PI * 2 - Math.PI / 2 + 0.02, a1 = ((i + 1) / 12) * Math.PI * 2 - Math.PI / 2 - 0.02
    const p = (a: number, rr: number) => [cx + rr * Math.cos(a), cy + rr * Math.sin(a)]
    const [x0, y0] = p(a0, rout), [x1, y1] = p(a1, rout), [x2, y2] = p(a1, rin), [x3, y3] = p(a0, rin)
    return `M${x0} ${y0}A${rout} ${rout} 0 0 1 ${x1} ${y1}L${x2} ${y2}A${rin} ${rin} 0 0 0 ${x3} ${y3}Z`
  }
  return (
    <svg viewBox="0 0 300 300" className="w-full max-w-[300px] mx-auto">
      {c.months.map((m, i) => {
        const rout = r0 + 18 + (R - r0 - 18) * (m.precip_mm / maxP)
        const a = ((i + 0.5) / 12) * Math.PI * 2 - Math.PI / 2
        return (
          <g key={m.m}>
            <path d={arc(i, r0, rout)} fill={tempColor(m.t_mean)} stroke="#fff" strokeWidth="1.5"><title>{`${m.label}: ${sign(m.t_mean)}, ${t('climate.precipWord')} ${m.precip_mm} ${t('climate.mmUnit')}, ${m.precip_days} ${t('climate.daysUnit')}`}</title></path>
            <text x={cx + (R + 14) * Math.cos(a)} y={cy + (R + 14) * Math.sin(a)} textAnchor="middle" dominantBaseline="middle" className="fill-[#6B7280]" style={{ fontSize: 10, fontFamily: 'JetBrains Mono' }}>{m.label}</text>
            <text x={cx + (r0 + 12) * Math.cos(a)} y={cy + (r0 + 12) * Math.sin(a)} textAnchor="middle" dominantBaseline="middle" style={{ fontSize: 9, fontFamily: 'JetBrains Mono', fill: (m.t_mean ?? 0) > 5 && (m.t_mean ?? 0) < 18 ? '#0A0A0A' : '#fff' }}>{sign(m.t_mean)}</text>
          </g>
        )
      })}
      <text x={cx} y={cy - 10} textAnchor="middle" style={{ fontSize: 22, fontFamily: 'JetBrains Mono', fontWeight: 600 }}>{sign(c.annual.t_mean)}</text>
      <text x={cx} y={cy + 8} textAnchor="middle" className="fill-[#6B7280]" style={{ fontSize: 9, letterSpacing: 1 }}>{t('climate.annualAvgLabel')}</text>
      <text x={cx} y={cy + 24} textAnchor="middle" className="fill-[#3A3F4B]" style={{ fontSize: 10, fontFamily: 'JetBrains Mono' }}>{c.annual.precip_days} {t('climate.annualPrecipSuffix')} · {c.annual.sun_hours} {t('climate.annualSunSuffix')}</text>
    </svg>
  )
}

function WindRose({ rose, t }: { rose: ClimatePack['wind_rose']; t: T }) {
  const cx = 90, cy = 90, R = 70
  const max = Math.max(...rose.map((r) => r.share), 0.01)
  const pts = rose.map((r, i) => { const a = (i / 8) * Math.PI * 2 - Math.PI / 2; const rr = 10 + (R - 10) * (r.share / max); return [cx + rr * Math.cos(a), cy + rr * Math.sin(a)] })
  return (
    <svg viewBox="0 0 180 180" className="w-40 h-40">
      {[0.33, 0.66, 1].map((k) => <circle key={k} cx={cx} cy={cy} r={10 + (R - 10) * k} fill="none" stroke="#E3E6EC" />)}
      {rose.map((r, i) => { const a = (i / 8) * Math.PI * 2 - Math.PI / 2; return <text key={r.dir} x={cx + (R + 12) * Math.cos(a)} y={cy + (R + 12) * Math.sin(a)} textAnchor="middle" dominantBaseline="middle" className="fill-[#6B7280]" style={{ fontSize: 9, fontFamily: 'JetBrains Mono' }}>{r.dir}</text> })}
      <polygon points={pts.map((p) => p.join(',')).join(' ')} fill="rgba(29,78,216,0.18)" stroke="#1D4ED8" strokeWidth="1.5" />
      {pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r="2.5" fill="#1D4ED8"><title>{`${rose[i].dir}: ${Math.round(rose[i].share * 100)}% ${t('climate.daysOfPercent')}, ${rose[i].speed} ${t('climate.msUnit')}`}</title></circle>)}
    </svg>
  )
}

const SEASON_BG: Record<string, string> = { winter: 'from-sky-50 to-white', spring: 'from-emerald-50 to-white', summer: 'from-amber-50 to-white', autumn: 'from-orange-50 to-white' }

export function ClimateTab({ qid, lat, lon }: { qid: string; lat?: number | null; lon?: number | null }) {
  const t = useT()
  const [c, setC] = useState<ClimatePack | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [overlay, setOverlay] = useState<'wind' | 'clouds' | 'temp'>('wind')
  useEffect(() => { setC(null); setErr(null); api.climate(qid).then(setC).catch((e) => setErr(String(e))) }, [qid])
  if (err) return <div className="card p-6 text-sm text-muted">{t('climate.unavailable')}: {err}. <button className="underline cursor-pointer" onClick={() => { setErr(null); api.climate(qid).then(setC).catch((e) => setErr(String(e))) }}>{t('climate.retry')}</button></div>
  if (!c) return <div className="grid grid-cols-1 md:grid-cols-2 gap-4">{Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-56 rounded-lg shimmer" />)}</div>
  const comfortTotal = Object.values(c.comfort).reduce((a, b) => a + b, 0) || 1
  const comfortItems = [['comfortable', t('climate.comfortable'), '#15803D'], ['cool', t('climate.cool'), '#60A5FA'], ['freezing', t('climate.freezing'), '#1E3A8A'], ['hot', t('climate.hot'), '#B91C1C'], ['rainy', t('climate.rainy'), '#64748B']] as const
  return (
    <div className="space-y-8">
      <div className="grid grid-cols-1 md:grid-cols-[320px_1fr] gap-8 items-start">
        <div>
          <h3 className="caps text-muted mb-2">{t('climate.yearRingTitle')} <span className="normal-case tracking-normal font-normal">· {t('climate.yearRingSubtitle')}</span></h3>
          <YearRing c={c} t={t} />
        </div>
        <div className="space-y-6">
          {c.now && (
            <div>
              <h3 className="caps text-muted">{t('climate.now')}</h3>
              <div className="mt-2 flex items-end gap-5">
                <div className="mono text-4xl font-medium">{sign(c.now.t)}</div>
                <div className="text-sm text-ink-2 pb-1 flex items-center gap-2">{wmo(c.now.code, t)[1]} {wmo(c.now.code, t)[0]} · {t('climate.feelsWord')} {sign(c.now.feels)}</div>
                <div className="text-xs text-muted pb-1 flex items-center gap-3"><span className="inline-flex items-center gap-1"><Droplets size={12} />{c.now.humidity}%</span><span className="inline-flex items-center gap-1"><Wind size={12} />{c.now.wind_ms} {t('climate.msUnit')}</span></div>
              </div>
              {c.forecast && (
                <div className="mt-3 grid grid-cols-6 gap-2">
                  {c.forecast.map((d) => (
                    <div key={d.date} className="rounded-lg border border-line p-2 text-center">
                      <div className="mono text-[10px] text-muted">{d.date.slice(5).replace('-', '.')}</div>
                      <div className="my-1 flex justify-center text-ink-2">{wmo(d.code, t)[1]}</div>
                      <div className="mono text-xs">{sign(d.t_max)} <span className="text-muted">{sign(d.t_min)}</span></div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <div>
            <h3 className="caps text-muted">{t('climate.feelsHeading')}</h3>
            <p className="mt-2 text-[15px] leading-relaxed border-l-2 border-brand pl-3">{c.feels_text.text}</p>
          </div>
        </div>
      </div>

      <div>
        <h3 className="caps text-muted mb-2">{t('climate.seasonsHeading')} · {c.year}</h3>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {(['winter', 'spring', 'summer', 'autumn'] as const).map((k) => {
            const s = c.seasons[k]
            return (
              <div key={k} className={`rounded-lg border border-line p-4 bg-gradient-to-b ${SEASON_BG[k]}`}>
                <div className="flex items-baseline justify-between"><span className="font-bold">{s.label}</span><span className="mono text-2xl" style={{ color: tempColor(s.t_mean) === '#E3E6EC' ? undefined : undefined }}>{sign(s.t_mean)}</span></div>
                <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-ink-2">
                  <span>{t('climate.feelsWord')}</span><span className="mono text-right">{sign(s.feels)}</span>
                  <span>{t('climate.minMax')}</span><span className="mono text-right">{sign(s.t_min)} / {sign(s.t_max)}</span>
                  <span>{t('climate.humidity')}</span><span className="mono text-right">{s.humidity ?? '—'}%</span>
                  <span>{t('climate.precipWord')}</span><span className="mono text-right">{s.precip_days} {t('climate.daysUnit')}</span>
                  <span>{t('climate.snowWord')}</span><span className="mono text-right">{s.snow_days} {t('climate.daysUnit')}</span>
                  <span>{t('climate.sunWord')}</span><span className="mono text-right">{s.sun_h_day} {t('climate.hDay')}</span>
                  <span>{t('climate.windWord')}</span><span className="mono text-right">{s.wind_ms} {t('climate.msUnit')}</span>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-[1fr_200px] gap-8 items-start">
        <div>
          <h3 className="caps text-muted mb-2">{t('climate.comfortTitle')} <span className="normal-case tracking-normal font-normal">· {t('climate.comfortSubtitle')}</span></h3>
          <div className="flex h-4 rounded-full overflow-hidden">
            {comfortItems.map(([k, , col]) => <div key={k} style={{ width: `${(c.comfort[k] / comfortTotal) * 100}%`, background: col }} title={`${k}: ${c.comfort[k]}`} />)}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs">
            {comfortItems.map(([k, label, col]) => <span key={k} className="inline-flex items-center gap-1.5"><i className="w-2.5 h-2.5 rounded-sm" style={{ background: col }} />{label} <span className="mono text-muted">{c.comfort[k]}</span></span>)}
          </div>
          <div className="mt-2 text-[11px] text-muted">{t('climate.comfortLegend')}</div>
        </div>
        <div>
          <h3 className="caps text-muted mb-2">{t('climate.windRoseTitle')}</h3>
          <WindRose rose={c.wind_rose} t={t} />
        </div>
      </div>

      {lat != null && lon != null && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <h3 className="caps text-muted">{t('climate.liveMapTitle')}</h3>
            <div className="ml-2 flex gap-1">{(['wind', 'clouds', 'temp'] as const).map((o) => <button key={o} onClick={() => setOverlay(o)} className={`filter !h-7 ${overlay === o ? 'filter-active' : ''}`}>{o === 'wind' ? t('climate.windWord') : o === 'clouds' ? t('climate.cloudsWord') : t('climate.temperatureWord')}</button>)}</div>
            <span className="ml-auto text-[11px] text-muted">Windy.com</span>
          </div>
          <iframe title="windy" className="w-full h-[380px] rounded-lg border border-line" loading="lazy"
            src={`https://embed.windy.com/embed2.html?lat=${lat}&lon=${lon}&detailLat=${lat}&detailLon=${lon}&zoom=7&level=surface&overlay=${overlay}&product=ecmwf&menu=&message=&marker=true&calendar=now&type=map&location=coordinates&metricWind=m%2Fs&metricTemp=%C2%B0C&radarRange=-1`} />
        </div>
      )}
      <div className="text-[11px] text-muted mono">{t('climate.sourceLabel')}: <a href={c.source.url} target="_blank" rel="noreferrer" className="hover:text-brand inline-flex items-center gap-0.5">{c.source.label} <ExternalLink size={10} /></a> · {c.year} · {t('climate.timezoneLabel')} {c.timezone}</div>
    </div>
  )
}
