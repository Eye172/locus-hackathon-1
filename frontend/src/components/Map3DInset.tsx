/**
 * A small window onto the app's one 3D map (Google photorealistic 3D): a point seen from the side, with an optional
 * photo marker at it. Used in the photo passport. The map is created only once the box is on screen; a click on
 * «open» goes to the full campus scene of the main page.
 */
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Maximize2 } from 'lucide-react'
import { GOOGLE_3D_KEY, groundHeight, loadGoogle3D } from '../lib/gmaps'
import type { LatLng } from '../lib/gmaps'
import { useLang } from '../lib/i18n'

interface Props {
  qid: string
  at: LatLng
  range?: number
  className?: string
  marker?: { lat: number; lng: number; thumb?: string | null; level?: string | null }
}

export function Map3DInset({ qid, at, range = 650, className = '', marker }: Props) {
  const lang = useLang()
  const host = useRef<HTMLDivElement>(null)
  const [seen, setSeen] = useState(false)
  const [failed, setFailed] = useState(!GOOGLE_3D_KEY)

  useEffect(() => {
    const el = host.current
    if (!el || seen) return
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) setSeen(true) })
    io.observe(el)
    return () => io.disconnect()
  }, [seen])

  useEffect(() => {
    const el = host.current
    if (!seen || failed || !el) return
    let alive = true
    let map: any = null
    Promise.all([loadGoogle3D(lang), groundHeight(at)]).then(([libs, h]) => {
      if (!alive) return
      map = new libs.maps3d.Map3DElement({
        center: { lat: at.lat, lng: at.lng, altitude: h ?? 0 }, range, tilt: 58, heading: 20, mode: 'HYBRID', language: lang,
      })
      map.style.cssText = 'display:block;width:100%;height:100%'
      map.addEventListener('gmp-error', () => setFailed(true))
      if (marker) {
        const mk = new libs.maps3d.MarkerElement({
          position: { lat: marker.lat, lng: marker.lng, altitude: 12 }, altitudeMode: 'RELATIVE_TO_GROUND',
          collisionBehavior: 'REQUIRED',
        })
        const ring = marker.level === 'verified' ? '#15803D' : marker.level === 'likely' ? '#B45309' : '#FFFFFF'
        const d = document.createElement('div')
        d.style.cssText = `width:44px;height:44px;border-radius:10px;overflow:hidden;border:3px solid ${ring};background:#111`
        if (marker.thumb) {
          const img = document.createElement('img')
          img.src = marker.thumb
          img.alt = ''
          img.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block'
          d.append(img)
        }
        mk.append(d)
        map.append(mk)
      }
      el.append(map)
    }).catch(() => { if (alive) setFailed(true) })
    return () => { alive = false; map?.remove() }
  }, [seen, failed, at.lat, at.lng, range, lang, marker?.lat, marker?.lng, marker?.thumb, marker?.level])  // eslint-disable-line react-hooks/exhaustive-deps

  if (failed) return null
  return (
    <div className={`relative rounded-lg overflow-hidden bg-[#0B0F1A] ${className}`}>
      <div ref={host} className="absolute inset-0" />
      <Link to={`/?u=${qid}`} className="absolute right-2 top-2 w-8 h-8 grid place-items-center rounded-lg bg-white/90 hover:bg-white text-ink shadow" aria-label="3D">
        <Maximize2 size={15} />
      </Link>
    </div>
  )
}
