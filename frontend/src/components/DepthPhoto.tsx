import { useEffect, useRef, useState } from 'react'

/**
 * "3D photo": fragment-shader parallax driven by a depth map. Pixels are only shifted, never invented.
 * Falls back to a plain <img> when WebGL or the depth map is unavailable.
 */
const VS = `attribute vec2 a_pos; varying vec2 v_uv; void main(){ v_uv = a_pos * 0.5 + 0.5; v_uv.y = 1.0 - v_uv.y; gl_Position = vec4(a_pos, 0.0, 1.0); }`
const FS = `precision mediump float; varying vec2 v_uv; uniform sampler2D u_img; uniform sampler2D u_depth; uniform vec2 u_off; uniform float u_zoom;
void main(){
  vec2 uv = (v_uv - 0.5) / u_zoom + 0.5;
  float d = texture2D(u_depth, uv).r;
  vec2 p = uv + u_off * (d - 0.5);
  float d2 = texture2D(u_depth, p).r;           // one refinement step reduces halos at depth edges
  vec2 p2 = uv + u_off * (d2 - 0.5);
  gl_FragColor = texture2D(u_img, clamp(p2, 0.002, 0.998));
}`

function load(src: string): Promise<HTMLImageElement> {
  return new Promise((res, rej) => { const im = new Image(); im.crossOrigin = 'anonymous'; im.onload = () => res(im); im.onerror = () => rej(new Error('img')); im.src = src })
}

export function DepthPhoto({ src, depthSrc, className, strength = 0.045, alt }: { src: string; depthSrc: string; className?: string; strength?: number; alt?: string }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [fallback, setFallback] = useState(false)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let raf = 0, alive = true
    const target = { x: 0, y: 0 }, cur = { x: 0, y: 0 }
    const c = canvas.current
    if (!c) return
    const gl = c.getContext('webgl', { premultipliedAlpha: false })
    if (!gl) { setFallback(true); return }
    let onMove: ((e: MouseEvent) => void) | null = null
    let onLeave: (() => void) | null = null
    let onOrient: ((e: DeviceOrientationEvent) => void) | null = null

    Promise.all([load(src), load(depthSrc)]).then(([img, dep]) => {
      if (!alive) return
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      const w = c.clientWidth || img.width, h = Math.round(w * img.height / img.width)
      c.width = w * dpr; c.height = h * dpr; c.style.height = `${h}px`
      gl.viewport(0, 0, c.width, c.height)
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
      const uOff = gl.getUniformLocation(prog, 'u_off'), uZoom = gl.getUniformLocation(prog, 'u_zoom')
      gl.uniform1f(uZoom, 1.06)
      const draw = () => {
        cur.x += (target.x - cur.x) * 0.08; cur.y += (target.y - cur.y) * 0.08
        gl.uniform2f(uOff, cur.x * strength, -cur.y * strength)
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
        raf = requestAnimationFrame(draw)
      }
      onMove = (e: MouseEvent) => { const r = c.getBoundingClientRect(); target.x = ((e.clientX - r.left) / r.width - 0.5) * 2; target.y = ((e.clientY - r.top) / r.height - 0.5) * 2 }
      onLeave = () => { target.x = 0; target.y = 0 }
      onOrient = (e: DeviceOrientationEvent) => { if (e.gamma != null && e.beta != null) { target.x = Math.max(-1, Math.min(1, e.gamma / 25)); target.y = Math.max(-1, Math.min(1, (e.beta - 45) / 25)) } }
      c.addEventListener('mousemove', onMove); c.addEventListener('mouseleave', onLeave); window.addEventListener('deviceorientation', onOrient)
      setReady(true)
      draw()
    }).catch(() => setFallback(true))
    return () => {
      alive = false; cancelAnimationFrame(raf)
      if (onMove) c.removeEventListener('mousemove', onMove)
      if (onLeave) c.removeEventListener('mouseleave', onLeave)
      if (onOrient) window.removeEventListener('deviceorientation', onOrient)
    }
  }, [src, depthSrc, strength])

  if (fallback) return <img src={src} alt={alt ?? ''} className={className} />
  return (
    <div className={`relative ${className ?? ''}`}>
      <canvas ref={canvas} className="block w-full" />
      {!ready && <img src={src} alt={alt ?? ''} className="absolute inset-0 w-full h-full object-cover" />}
    </div>
  )
}
