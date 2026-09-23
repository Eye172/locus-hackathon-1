import { forwardRef, useImperativeHandle, useRef } from 'react'

export interface CloudsHandle { setZoom: (zoom: number) => void }

/** Cloud layer shown while the camera dives from orbit to the campus. Pure CSS, no assets. The map's move event sets
 *  its opacity directly: through React state it re-rendered the whole page on every camera frame. Out of its band the
 *  layer is hidden and its drift paused, so the two blurred sheets cost nothing on the idle planet. */
export const Clouds = forwardRef<CloudsHandle>(function Clouds(_, ref) {
  const box = useRef<HTMLDivElement>(null)
  const white = useRef<HTMLDivElement>(null)
  const last = useRef(-1)
  useImperativeHandle(ref, () => ({
    setZoom: (zoom) => {
      // bell curve between zoom 3.5 and 9.5, peak at 6.5
      const o = zoom < 3.5 || zoom > 9.5 ? 0 : Math.max(0, 1 - Math.abs(zoom - 6.5) / 3) * 0.92
      if (Math.abs(o - last.current) < 0.004 && (o > 0) === (last.current > 0)) return
      last.current = o
      if (box.current) { box.current.style.opacity = String(o); box.current.dataset.on = o > 0 ? '1' : '' }
      if (white.current) white.current.style.opacity = String(o > 0.8 ? (o - 0.8) * 3 : 0)
    },
  }), [])
  return (
    <div ref={box} className="clouds-box absolute inset-0 pointer-events-none overflow-hidden transition-opacity duration-150" style={{ opacity: 0 }}>
      <div className="clouds clouds-a" />
      <div className="clouds clouds-b" />
      <div ref={white} className="absolute inset-0 bg-white/40" style={{ opacity: 0 }} />
    </div>
  )
})
