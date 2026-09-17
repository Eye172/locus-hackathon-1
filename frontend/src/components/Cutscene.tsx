import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'

/** Optional cinematic between the fly-through and the profile (Higgsfield branch, SPEC §10):
 *  drop `public/cutscenes/{qid}.mp4` and the globe plays it here; without a file nothing changes. */
export function Cutscene({ src, onDone, skipLabel, aiLabel }: { src: string; onDone: () => void; skipLabel: string; aiLabel: string }) {
  const ref = useRef<HTMLVideoElement>(null)
  useEffect(() => {
    const v = ref.current
    if (!v) return
    const t = window.setTimeout(onDone, 20000) // never trap the user if the file stalls
    v.play().catch(onDone)
    return () => window.clearTimeout(t)
  }, [onDone])
  return (
    <div className="fixed inset-0 z-50 bg-black flex items-center justify-center">
      <video ref={ref} src={src} muted playsInline onEnded={onDone} onError={onDone} className="w-full h-full object-cover" />
      <div className="absolute bottom-5 left-5 chip bg-black/50 !text-white/85 backdrop-blur">{aiLabel}</div>
      <button onClick={onDone} className="absolute top-4 right-4 btn-ghost !text-white/80 hover:!text-white bg-black/40 backdrop-blur"><X size={16} /> {skipLabel}</button>
    </div>
  )
}

/** True when a cutscene file exists for this university (a SPA fallback page is not a video). */
export async function hasCutscene(qid: string): Promise<string | null> {
  const src = `/cutscenes/${qid}.mp4`
  try {
    const r = await fetch(src, { method: 'HEAD' })
    return r.ok && (r.headers.get('content-type') || '').startsWith('video') ? src : null
  } catch { return null }
}
