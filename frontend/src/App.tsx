import { lazy, Suspense } from 'react'
import { Routes, Route, Link, useLocation } from 'react-router-dom'
import { Aperture, GitCompare, Code2 } from 'lucide-react'
import Home from './pages/Home'
import Globe from './pages/Globe'
import Profile from './pages/Profile'
import Compare from './pages/Compare'
import Saved from './pages/Saved'
import { LanguageSwitch } from './components/LanguageSwitch'

// Google Maps 3D page: its own chunk, loaded only when opened
const Map3D = lazy(() => import('./pages/Map3D'))
import { useT } from './lib/i18n'

export default function App() {
  const t = useT()
  const loc = useLocation()
  return (
    <div className={`min-h-full flex flex-col ${loc.pathname === '/' ? 'bg-[#05070F]' : ''}`}>
      <header className={`sticky top-0 z-40 backdrop-blur border-b ${loc.pathname === '/' ? 'bg-[#05070F]/70 border-white/10 text-white' : 'bg-white/80 border-line'}`}>
        <div className="mx-auto max-w-7xl px-4 h-14 flex items-center gap-3">
          <Link to="/" className={`flex items-center gap-2 font-bold display ${loc.pathname === '/' ? 'text-white' : 'text-ink'}`}>
            <span className="grid place-items-center w-8 h-8 rounded-xl bg-brand text-white"><Aperture size={18} /></span>
            CampusLens
          </Link>
          <nav className="ml-4 hidden sm:flex items-center gap-1 text-sm">
            <Link to="/saved" className={`px-2 py-1 rounded-md text-sm ${loc.pathname === '/' ? 'text-white/80 hover:text-white' : 'text-ink-2 hover:text-ink'} ${loc.pathname === '/saved' ? 'font-semibold' : ''}`}>{t('nav.saved')}</Link>
            <Link to="/compare" className={`px-2 py-1 rounded-md text-sm inline-flex items-center gap-1.5 ${loc.pathname === '/' ? 'text-white/80 hover:text-white' : 'text-ink-2 hover:text-ink'} ${loc.pathname === '/compare' ? 'font-semibold' : ''}`}>
              <GitCompare size={15} /> {t('nav.compare')}
            </Link>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <a href="/api/schema" target="_blank" rel="noreferrer" className={`btn-icon !h-9 !w-9 hidden sm:inline-flex ${loc.pathname === '/' ? '!border-white/20 !text-white' : ''}`} title="API schema">
              <Code2 size={16} />
            </a>
            <LanguageSwitch />
          </div>
        </div>
      </header>
      <main className="flex-1">
        <Routes>
          <Route path="/" element={<Globe />} />
          <Route path="/classic" element={<Home />} />
          <Route path="/u/:qid" element={<Profile />} />
          <Route path="/compare" element={<Compare />} />
          <Route path="/saved" element={<Saved />} />
          <Route path="/map3d/:qid" element={<Suspense fallback={<div className="h-[calc(100dvh-3.5rem)] bg-[#0B0F1A]" />}><Map3D /></Suspense>} />
        </Routes>
      </main>
      <footer className={`border-t py-6 text-xs ${loc.pathname === '/' || loc.pathname.startsWith('/map3d/') ? 'hidden' : 'border-line text-muted'}`}>
        <div className="mx-auto max-w-7xl px-4 flex flex-wrap gap-x-6 gap-y-2">
          <span>CampusLens · LOCUS Startup Hackathon 2026 · кейс 1</span>
          <span>{t('footer.sources')}</span>
        </div>
      </footer>
    </div>
  )
}
