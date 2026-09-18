import { Routes, Route, Link, Navigate, useLocation, useParams } from 'react-router-dom'
import { Aperture } from 'lucide-react'
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
import { useT } from './lib/i18n'

export default function App() {
  const t = useT()
  const loc = useLocation()
  return (
    <div className={`min-h-full flex flex-col ${loc.pathname === '/' ? 'bg-[#05070F]' : ''}`}>
      {/* home: transparent and laid over the globe; elsewhere: sticky frosted glass */}
      <header className={`top-0 z-40 ${loc.pathname === '/'
        ? 'fixed inset-x-0 text-white'
        : 'sticky backdrop-blur-xl backdrop-saturate-150 border-b bg-white/55 border-black/[0.06] shadow-[inset_0_1px_0_rgba(255,255,255,0.9),0_4px_20px_-10px_rgba(15,23,42,0.15)]'}`}>
        <div className="mx-auto max-w-7xl px-4 h-14 flex items-center gap-3">
          <Link to="/" className={`flex items-center gap-2 font-bold display ${loc.pathname === '/' ? 'text-white' : 'text-ink'}`}>
            <span className="grid place-items-center w-8 h-8 rounded-xl bg-brand text-white"><Aperture size={18} /></span>
            CampusLens
          </Link>
          <div className="ml-auto flex items-center gap-2">
            <HeaderMenu dark={loc.pathname === '/'} />
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
          <Route path="/settings/search" element={<SearchSettings />} />
          <Route path="/map3d/:qid" element={<ToMainMap />} />
        </Routes>
      </main>
      <footer className={`border-t py-6 text-xs ${loc.pathname === '/' ? 'hidden' : 'border-line text-muted'}`}>
        <div className="mx-auto max-w-7xl px-4 flex flex-wrap gap-x-6 gap-y-2">
          <span>CampusLens · LOCUS Startup Hackathon 2026 · кейс 1</span>
          <span>{t('footer.sources')}</span>
        </div>
      </footer>
    </div>
  )
}
