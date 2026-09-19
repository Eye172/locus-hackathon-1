import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Menu, X, Check, Bookmark, GitCompare, SlidersHorizontal } from 'lucide-react'
import { setLang, useLang, useT, type Lang } from '../lib/i18n'

const LANGS: { code: Lang; name: string }[] = [
  { code: 'ru', name: 'Русский' },
  { code: 'kk', name: 'Қазақша' },
  { code: 'en', name: 'English' },
]

export function HeaderMenu({ dark = false }: { dark?: boolean }) {
  const t = useT()
  const lang = useLang()
  const { pathname } = useLocation()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [open])

  const links = [
    { to: '/saved', label: t('nav.saved'), Icon: Bookmark },
    { to: '/compare', label: t('nav.compare'), Icon: GitCompare },
    { to: '/settings/search', label: t('nav.searchSettings'), Icon: SlidersHorizontal },
  ]
  const item = dark ? 'hover:bg-white/5 hover:text-white' : 'hover:bg-black/[0.04] hover:text-ink'
  const active = dark ? 'text-white' : 'text-ink'

  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)} aria-label="Menu" aria-expanded={open}
        className={`grid place-items-center p-1 cursor-pointer ${dark ? 'text-white/80 hover:text-white' : 'text-ink-2 hover:text-ink'}`}>
        {open ? <X size={22} /> : <Menu size={22} />}
      </button>
      {open && (
        <div className={`absolute right-0 mt-3 w-56 rounded-xl border p-1.5 text-[14px] backdrop-blur-xl ${dark
          ? 'bg-[#0B0D12]/95 border-white/10 text-white/70 shadow-2xl'
          : 'bg-white/95 border-line text-ink-2 shadow-[0_12px_40px_rgba(11,13,18,0.10)]'}`}>
          {links.map(({ to, label, Icon }) => (
            <Link key={to} to={to} onClick={() => setOpen(false)}
              className={`flex items-center gap-2.5 px-2.5 py-2 rounded-lg ${item} ${pathname === to ? active : ''}`}>
              <Icon size={15} className="opacity-60" /> {label}
            </Link>
          ))}
          <div className={`my-1 mx-2 border-t ${dark ? 'border-white/10' : 'border-line'}`} />
          {LANGS.map(({ code, name }) => (
            <button key={code} onClick={() => { setLang(code); setOpen(false) }}
              className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg cursor-pointer text-left ${item} ${lang === code ? active : ''}`}>
              <span className="w-[15px] text-[10px] uppercase opacity-50">{code}</span>
              <span className="flex-1">{name}</span>
              {lang === code && <Check size={14} className="opacity-70" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
