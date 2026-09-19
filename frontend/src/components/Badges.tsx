import type { Lang } from '../lib/i18n'
import { useT } from '../lib/i18n'

export function OutdatedChip() {
  const t = useT()
  return <span className="chip bg-ink text-white">{t('profile.outdated')}</span>
}

export function CoverageDot({ level }: { level: string }) {
  const cls = level === 'strong' ? 'bg-verified' : level === 'medium' ? 'bg-likely' : level === 'weak' ? 'bg-unverified' : 'bg-line-2'
  return <span className={`inline-block w-1.5 h-1.5 rounded-full ${cls}`} />
}

export const COVERAGE_LABEL: Record<string, string> = { strong: 'сильное', medium: 'среднее', weak: 'слабое', none: 'нет данных' }
const COVERAGE_I18N: Record<string, Record<Lang, string>> = {
  strong: { ru: 'сильное', en: 'strong', kk: 'күшті' }, medium: { ru: 'среднее', en: 'medium', kk: 'орташа' },
  weak: { ru: 'слабое', en: 'weak', kk: 'әлсіз' }, none: { ru: 'нет данных', en: 'no data', kk: 'дерек жоқ' },
}
export const coverageLabel = (level: string, lang: Lang) => COVERAGE_I18N[level]?.[lang] ?? COVERAGE_LABEL[level] ?? level
