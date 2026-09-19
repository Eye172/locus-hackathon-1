import { Routes, Route, Link, NavLink, Navigate, useLocation, useParams } from 'react-router-dom'
import { Logo } from './components/Logo'
import Home from './pages/Home'
import Globe from './pages/Globe'
import Profile from './pages/Profile'
import Compare from './pages/Compare'
import Saved from './pages/Saved'
import SearchSettings from './pages/SearchSettings'
import { HeaderMenu } from './components/HeaderMenu'

// one map for the whole app: old /map3d/:qid links open the campus scene of the main page
function ToMainMap() {
  const { qid = '' } = useParams()
  return <Navigate to={`/?u=${qid}`} replace />
}
import { setLang, useLang, useT, type Lang } from './lib/i18n'

function LangSelect({ dark }: { dark: boolean }) {
  const lang = useLang()
  return (
    <select value={lang} onChange={(e) => setLang(e.target.value as Lang)} aria-label="Язык интерфейса"
      className={`h-8 pl-2 pr-1 rounded-md bg-transparent text-[13px] font-medium cursor-pointer outline-none ${dark ? 'text-white/80 hover:text-white [&>option]:text-ink' : 'text-ink-2 hover:text-ink'}`}>
      <option value="ru">RU</option><option value="kk">KZ</option><option value="en">EN</option>
    </select>
  )
}

export default function App() {
  const t = useT()
  const loc = useLocation()
  const home = loc.pathname === '/'
  const link = ({ isActive }: { isActive: boolean }) => `transition-colors ${home
    ? (isActive ? 'text-white' : 'text-white/65 hover:text-white')
    : (isActive ? 'text-ink' : 'text-muted hover:text-ink')}`
  return (
    <div className={`min-h-full flex flex-col ${home ? 'bg-[#05070F]' : ''}`}>
      {/* home: transparent, laid over the globe; elsewhere: white with a hairline */}
      <header className={`top-0 z-40 ${home ? 'fixed inset-x-0 text-white' : 'sticky bg-white/92 backdrop-blur-md border-b border-line'}`}>
        {/* home: the full width, from the screen's left edge; elsewhere: aligned with the page's centred content */}
        {/* two equal flex-1 sides keep the middle (the 3D scene's university, #hdr-center) centred on the screen while
            they have room; when they do not, the middle moves over and truncates rather than overlapping them */}
        <div className={`${home ? 'px-4 sm:px-5' : 'mx-auto max-w-7xl px-4 sm:px-6'} h-14 flex items-center gap-6`}>
          <div className="flex-1 flex items-center gap-10 whitespace-nowrap">
            <div className="flex items-center gap-3">
              {/* the 3D campus scene portals its back arrow here */}
              <span id="hdr-back" className="contents" />
              <Link to="/" aria-label="CampusLense" className={`hdr-logo flex ${home ? 'text-white' : 'text-ink'}`}><Logo /></Link>
            </div>
            <nav className="hidden md:flex items-center gap-7 text-[14px] font-medium">
              <NavLink to="/" end className={link}>{t('lvl.planet')}</NavLink>
              <NavLink to="/saved" className={link}>{t('nav.saved')}</NavLink>
              <NavLink to="/compare" className={link}>{t('nav.compare')}</NavLink>
            </nav>
          </div>
          <span id="hdr-center" className="contents" />
          <div className="flex-1 flex items-center justify-end gap-2">
            <div className="hdr-lang hidden md:block"><LangSelect dark={home} /></div>
            <div className="md:hidden"><HeaderMenu dark={home} /></div>
          </div>
        </div>
        {/* the 3D campus scene portals its camera buttons here: a column hanging from the header's right end */}
        <div id="hdr-side" />
      </header>
      <main className="flex-1">
        <Routes>
          <Route path="/" element={<Globe />} />
          <Route path="/classic" element={<Home />} />
          <Route path="/u/:qid" element={<Profile />} />
          <Route path="/compare" element={<Compare />} />
          <Route path="/saved" element={<Saved />} />
          <Route path="/settings/search" element={<SearchSettings />} />
          <Route path="/map3d/:qid" element={<ToMainMap />} />
          <Route path="*" element={<div className="mx-auto max-w-7xl px-4 sm:px-6 py-16"><h1 className="text-3xl">{t('error.notFound')}</h1><p className="mt-3 text-muted">{t('error.notFoundHint')}</p><Link to="/" className="btn-primary mt-6">{t('lvl.planet')}</Link></div>} />
        </Routes>
      </main>
      <footer className={`border-t border-line py-8 text-[13px] text-muted ${home ? 'hidden' : ''}`}>
        <div className="mx-auto max-w-7xl px-4 sm:px-6 flex flex-col sm:flex-row gap-x-10 gap-y-3">
          <span className="inline-flex items-center gap-2 text-ink-2 shrink-0"><Logo size={18} className="[&>span:last-child]:text-[14px]" /></span>
          <span className="max-w-[80ch]">{t('footer.sources')}</span>
          <Link to="/settings/search" className="sm:ml-auto shrink-0 hover:text-ink">{t('nav.searchSettings')}</Link>
        </div>
      </footer>
    </div>
  )
}
