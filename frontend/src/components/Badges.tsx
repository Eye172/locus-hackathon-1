import type { Lang } from '../lib/i18n'
import type { Level } from '../lib/types'
import { catLabel, useLang, useT } from '../lib/i18n'

/** Thin confidence bar + percentage. Colour carries the level; no icon, no pill. */
export function ConfidenceBar({ level, confidence, showLabel }: { level: Level; confidence: number; showLabel?: boolean }) {
  const t = useT()
  const cls = level === 'verified' ? 'conf-verified' : level === 'likely' ? 'conf-likely' : 'conf-unverified'
  const color = level === 'verified' ? 'text-verified' : level === 'likely' ? 'text-likely' : 'text-unverified'
  return (
    <div className="flex items-center gap-2">
      <div className="conf-bar flex-1 rounded-full overflow-hidden"><i className={cls} style={{ width: `${Math.round(confidence * 100)}%` }} /></div>
      <span className={`mono text-[11px] ${color}`}>{Math.round(confidence * 100)}%{showLabel ? ` · ${t('level.' + level)}` : ''}</span>
    </div>
  )
}

export function ConfidenceBadge({ level, confidence }: { level: Level; confidence: number }) {
  const t = useT()
  const cls = level === 'verified' ? 'bg-verified-soft text-verified' : level === 'likely' ? 'bg-likely-soft text-likely' : 'bg-unverified-soft text-unverified'
  return <span className={`chip ${cls}`}>{t('level.' + level)} · {Math.round(confidence * 100)}%</span>
}

export function CategoryChip({ cat }: { cat: string }) {
  const lang = useLang()
  return <span className="caps text-ink-2">{catLabel(cat, lang)}</span>
}

export function SourceChip({ source, label, brochure }: { source: string; label?: string; brochure?: boolean }) {
  const cls = brochure ? 'bg-brochure-soft text-brochure' : 'bg-reality-soft text-reality'
  return <span className={`chip ${cls}`}>{label ?? source}</span>
}

export function OutdatedChip() {
  const t = useT()
  return <span className="chip bg-ink text-white">{t('profile.outdated')}</span>
}

export function CoverageDot({ level }: { level: string }) {
  const cls = level === 'strong' ? 'bg-verified' : level === 'medium' ? 'bg-likely' : level === 'weak' ? 'bg-orange-400' : 'bg-slate-300'
  return <span className={`inline-block w-2 h-2 rounded-full ${cls}`} />
}

export const COVERAGE_LABEL: Record<string, string> = { strong: 'сильное', medium: 'среднее', weak: 'слабое', none: 'нет данных' }
const COVERAGE_I18N: Record<string, Record<Lang, string>> = {
  strong: { ru: 'сильное', en: 'strong', kk: 'күшті' }, medium: { ru: 'среднее', en: 'medium', kk: 'орташа' },
  weak: { ru: 'слабое', en: 'weak', kk: 'әлсіз' }, none: { ru: 'нет данных', en: 'no data', kk: 'дерек жоқ' },
}
export const coverageLabel = (level: string, lang: Lang) => COVERAGE_I18N[level]?.[lang] ?? COVERAGE_LABEL[level] ?? level
