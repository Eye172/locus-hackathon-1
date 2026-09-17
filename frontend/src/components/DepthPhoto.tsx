import { useEffect, useRef, useState } from 'react'

/**
 * "3D photo": fragment-shader parallax driven by a depth map. Pixels are only shifted, never invented.
 * Falls back to a plain <img> when WebGL or the depth map is unavailable.
 *  - `cover`: fill the container (like object-fit: cover) instead of following the image aspect;
 *  - `auto`:  a slow camera drift + breathing zoom when the pointer is not over the photo (cinematic mode).
 */
const VS = `attribute vec2 a_pos; varying vec2 v_uv; void main(){ v_uv = a_pos * 0.5 + 0.5; v_uv.y = 1.0 - v_uv.y; gl_Position = vec4(a_pos, 0.0, 1.0); }`
const FS = `precision mediump float; varying vec2 v_uv; uniform sampler2D u_img; uniform sampler2D u_depth; uniform vec2 u_off; uniform float u_zoom; uniform vec2 u_scale;
void main(){
  vec2 uv0 = (v_uv - 0.5) * u_scale + 0.5;       // cover crop
  vec2 uv = (uv0 - 0.5) / u_zoom + 0.5;
  float d = texture2D(u_depth, uv).r;
  vec2 p = uv + u_off * (d - 0.5);
  float d2 = texture2D(u_depth, p).r;           // one refinement step reduces halos at depth edges
  vec2 p2 = uv + u_off * (d2 - 0.5);
  gl_FragColor = texture2D(u_img, clamp(p2, 0.002, 0.998));
}`

function load(src: string): Promise<HTMLImageElement> {
  return new Promise((res, rej) => { const im = new Image(); im.crossOrigin = 'anonymous'; im.onload = () => res(im); im.onerror = () => rej(new Error('img')); im.src = src })
}

export function DepthPhoto({ src, depthSrc, className, strength = 0.045, alt, cover = false, auto = false, onReady }:
  { src: string; depthSrc: string; className?: string; strength?: number; alt?: string; cover?: boolean; auto?: boolean; onReady?: () => void }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [fallback, setFallback] = useState(false)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let raf = 0, alive = true, manual = false
    const target = { x: 0, y: 0 }, cur = { x: 0, y: 0 }
    const c = canvas.current
    if (!c) return
    const gl = c.getContext('webgl', { premultipliedAlpha: false })
    if (!gl) { setFallback(true); return }
    let onMove: ((e: MouseEvent) => void) | null = null
    let onLeave: (() => void) | null = null
    let onOrient: ((e: DeviceOrientationEvent) => void) | null = null
    let ro: ResizeObserver | null = null

    Promise.all([load(src), load(depthSrc)]).then(([img, dep]) => {
      if (!alive) return
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      const sh = (type: number, s: string) => { const o = gl.createShader(type)!; gl.shaderSource(o, s); gl.compileShader(o); return o }
      const prog = gl.createProgram()!
      gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(prog); gl.useProgram(prog)
      const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf)
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
      const loc = gl.getAttribLocation(prog, 'a_pos'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)
      const tex = (im: HTMLImageElement, unit: number, name: string) => {
        const t = gl.createTexture(); gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, t)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, im); gl.uniform1i(gl.getUniformLocation(prog, name), unit)
      }
      tex(img, 0, 'u_img'); tex(dep, 1, 'u_depth')
      const uOff = gl.getUniformLocation(prog, 'u_off'), uZoom = gl.getUniformLocation(prog, 'u_zoom'), uScale = gl.getUniformLocation(prog, 'u_scale')
      const size = () => {
        const w = c.clientWidth || img.width
        const h = cover ? (c.clientHeight || Math.round(w * 9 / 16)) : Math.round(w * img.height / img.width)
        c.width = w * dpr; c.height = h * dpr
        if (!cover) c.style.height = `${h}px`
        gl.viewport(0, 0, c.width, c.height)
        const ia = img.width / img.height, ca = w / h
        if (cover) gl.uniform2f(uScale, ca > ia ? 1 : ca / ia, ca > ia ? ia / ca : 1)
        else gl.uniform2f(uScale, 1, 1)
      }
      size()
      if (cover && 'ResizeObserver' in window) { ro = new ResizeObserver(size); ro.observe(c) }
      gl.uniform1f(uZoom, 1.06)
      const t0 = performance.now()
      const draw = () => {
        if (auto && !manual) {
          const tt = (performance.now() - t0) / 1000
          target.x = Math.sin(tt * 0.33) * 0.9
          target.y = Math.cos(tt * 0.24) * 0.55
          gl.uniform1f(uZoom, 1.07 + 0.035 * Math.sin(tt * 0.16))
        }
        cur.x += (target.x - cur.x) * (auto && !manual ? 0.03 : 0.08); cur.y += (target.y - cur.y) * (auto && !manual ? 0.03 : 0.08)
        gl.uniform2f(uOff, cur.x * strength, -cur.y * strength)
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
        raf = requestAnimationFrame(draw)
      }
      onMove = (e: MouseEvent) => { manual = true; const r = c.getBoundingClientRect(); target.x = ((e.clientX - r.left) / r.width - 0.5) * 2; target.y = ((e.clientY - r.top) / r.height - 0.5) * 2 }
      onLeave = () => { manual = false; target.x = 0; target.y = 0 }
      onOrient = (e: DeviceOrientationEvent) => { if (e.gamma != null && e.beta != null) { manual = true; target.x = Math.max(-1, Math.min(1, e.gamma / 25)); target.y = Math.max(-1, Math.min(1, (e.beta - 45) / 25)) } }
      c.addEventListener('mousemove', onMove); c.addEventListener('mouseleave', onLeave); window.addEventListener('deviceorientation', onOrient)
      setReady(true); onReady?.()
      draw()
    }).catch(() => setFallback(true))
    return () => {
      alive = false; cancelAnimationFrame(raf)
      ro?.disconnect()
      if (onMove) c.removeEventListener('mousemove', onMove)
      if (onLeave) c.removeEventListener('mouseleave', onLeave)
      if (onOrient) window.removeEventListener('deviceorientation', onOrient)
    }
  }, [src, depthSrc, strength, cover, auto, onReady])

  if (fallback) return <img src={src} alt={alt ?? ''} className={`${className ?? ''} ${cover ? 'object-cover w-full h-full' : ''}`} />
  return (
    <div className={`relative ${className ?? ''}`}>
      <canvas ref={canvas} className={cover ? 'block w-full h-full' : 'block w-full'} />
      {!ready && <img src={src} alt={alt ?? ''} className="absolute inset-0 w-full h-full object-cover" />}
    </div>
  )
}
