import { setLang, useLang, type Lang } from '../lib/i18n'

const LANGS: Lang[] = ['ru', 'kk', 'en']

export function LanguageSwitch() {
  const lang = useLang()
  return (
    <div className="flex rounded-xl border border-line bg-white p-0.5 text-xs font-semibold">
      {LANGS.map((l) => (
        <button key={l} onClick={() => setLang(l)}
          className={`px-2 py-1 rounded-lg uppercase cursor-pointer ${lang === l ? 'bg-brand text-white' : 'text-muted hover:text-ink'}`}>
          {l}
        </button>
      ))}
    </div>
  )
}
