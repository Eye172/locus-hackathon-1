import { useEffect, useRef } from 'react'

/**
 * Cinematic descent from orbit: the map keeps zooming underneath (the planet grows), thin cirrus wisps drift in,
 * then layered clouds rush past the camera in perspective and close into a white-out, inside which the map jumps
 * to the campus. Procedural fBm, no assets — works for any point on Earth.
 *
 * Timeline (fractions of `duration`):  0–0.34 pure approach (the planet grows to city scale, no clouds) ·
 * 0.34–0.55 wisps · 0.55–0.74 the deck closes · 0.74 onMid (map jump, hidden) · 0.95 onWhite · 1.0 onDone.
 */
const VS = `attribute vec2 a; void main(){ gl_Position = vec4(a, 0.0, 1.0); }`
const FS = `precision highp float;
uniform vec2 u_res; uniform float u_t; uniform float u_p0;
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y); }
float fbm(vec2 p){ float v = 0.0, a = 0.5; for (int i = 0; i < 5; i++) { v += a * noise(p); p = p * 2.02 + vec2(1.7, 9.2); a *= 0.5; } return v; }
void main(){
  float u_p = clamp((u_p0 - 0.34) / 0.66, 0.0, 1.0);   // cloud time starts after the pure approach
  float gate = smoothstep(0.30, 0.42, u_p0);          // nothing is drawn while the planet is still growing
  vec2 uv = (gl_FragCoord.xy - 0.5 * u_res) / u_res.y;
  float r = length(uv);
  vec2 sunp = vec2(0.38, 0.26);
  float sunD = length(uv - sunp);
  float sun = pow(max(0.0, 1.0 - sunD * 1.2), 6.0);
  float glow = exp(-sunD * 2.2);
  // background: sky above the horizon, brighter as we descend
  float descend = smoothstep(0.15, 0.75, u_p);
  vec3 sky = mix(vec3(0.16, 0.30, 0.62), vec3(0.52, 0.72, 0.98), clamp(0.5 - uv.y * 0.7, 0.0, 1.0) * 0.6 + descend * 0.5);
  vec3 col = sky + vec3(1.0, 0.94, 0.82) * (sun * 1.2 + glow * 0.25);
  float bgA = smoothstep(0.30, 0.62, u_p);            // the sky itself only closes in once we are below the cirrus
  // volumetric-looking cloud deck: 7 sheets at increasing depth flying towards the camera; near sheets are big and soft
  float speed = 0.06 + u_p * 0.55;
  float fly = u_t * speed;
  vec3 cloudsum = vec3(0.0); float asum = 0.0;
  for (int i = 0; i < 7; i++) {
    float fi = float(i);
    float z = fract(fly + fi / 7.0);                   // 0 = at the camera, 1 = far
    float nearFade = smoothstep(0.0, 0.12, z) * (1.0 - smoothstep(0.8, 1.0, z));
    if (nearFade < 0.002) continue;                     // sheet not visible: skip its noise entirely
    float depth = 0.08 + z * 1.6;
    vec2 q = uv / depth * 1.35 + vec2(fi * 23.1, fi * 11.7) + vec2(0.05, 0.02) * u_t * (1.0 + fi * 0.2);
    float d = fbm(q);
    float thick = 0.62 - u_p * 0.42;                   // threshold drops with progress: wisps → deck → white-out
    float dens = smoothstep(thick, thick + 0.22, d);
    float a = dens * nearFade * (0.55 + 0.45 * (1.0 - z));
    float lit = 0.62 + 0.55 * smoothstep(thick, thick + 0.35, d) + 0.25 * glow;
    vec3 c = mix(vec3(0.55, 0.62, 0.80), vec3(1.0, 0.99, 0.97), clamp(lit - 0.4, 0.0, 1.0));
    cloudsum += c * a * (1.0 - asum); asum += a * (1.0 - asum);
    if (asum > 0.985) break;                            // opaque: the sheets behind cannot show
  }
  vec3 outc = mix(col, cloudsum / max(asum, 0.001), clamp(asum, 0.0, 1.0));
  float alpha = max(bgA, clamp(asum, 0.0, 1.0));       // early: only the wisps are drawn over the planet
  float white = smoothstep(0.66, 0.9, u_p);
  outc = mix(outc, vec3(1.0), white);
  alpha = max(alpha, white) * gate;
  float vig = 1.0 - smoothstep(0.6, 1.2, r) * 0.3 * (1.0 - white);
  gl_FragColor = vec4(outc * vig, alpha);
}`

interface GLState { gl: WebGLRenderingContext; uRes: WebGLUniformLocation | null; uT: WebGLUniformLocation | null; uP: WebGLUniformLocation | null }

/**
 * Always mounted: the shader is compiled (and warmed with a 1×1 draw) when the page loads, so starting a dive costs
 * nothing — the first run used to freeze the page for ~0.6 s exactly when the approach began. `run` > 0 starts a dive;
 * a new value restarts it; 0 stops and hides it.
 */
const HOLD_AT = 0.8  // the deck is closed and still flying past, the white-out has not begun

export function CloudDive({ run, duration = 6200, hold = false, maxHoldMs = 6000, onMid, onWhite, onDone }:
  { run: number; duration?: number
    /** the scene behind is not ready: the dive stays inside the clouds (they keep flying) until this goes false, at most maxHoldMs */
    hold?: boolean; maxHoldMs?: number
    onMid?: () => void; onWhite?: () => void; onDone?: () => void }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const glRef = useRef<GLState | null>(null)
  const cb = useRef({ onMid, onWhite, onDone })
  cb.current = { onMid, onWhite, onDone }
  const holdRef = useRef(hold)
  holdRef.current = hold

  // one-time setup + warm-up
  useEffect(() => {
    const c = ref.current
    if (!c) return
    const gl = c.getContext('webgl', { antialias: false, premultipliedAlpha: false, alpha: true })
    if (!gl) return
    const sh = (t: number, s: string) => { const o = gl.createShader(t)!; gl.shaderSource(o, s); gl.compileShader(o); return o }
    const prog = gl.createProgram()!
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(prog)
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { console.warn('CloudDive shader:', gl.getProgramInfoLog(prog)); return }  // falls back to the white flash
    gl.useProgram(prog)
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
    const loc = gl.getAttribLocation(prog, 'a'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA)
    const st: GLState = { gl, uRes: gl.getUniformLocation(prog, 'u_res'), uT: gl.getUniformLocation(prog, 'u_t'), uP: gl.getUniformLocation(prog, 'u_p0') }
    // the first full-screen frame (buffer allocation + lazy driver work) froze the page for ~0.4 s when the clouds
    // appeared; do it once now, while the globe is still loading, and keep the buffer sized from then on
    const size = () => {
      const scale = 0.5  // soft clouds: a half-resolution buffer upscaled by the browser looks the same, costs 4-6× less GPU
      c.width = Math.max(1, Math.round(c.clientWidth * scale)); c.height = Math.max(1, Math.round(c.clientHeight * scale))
      gl.viewport(0, 0, c.width, c.height)
    }
    size()
    gl.uniform2f(st.uRes, c.width, c.height); gl.uniform1f(st.uT, 0); gl.uniform1f(st.uP, 0.8)
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4); gl.finish()
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT)
    const ro = 'ResizeObserver' in window ? new ResizeObserver(size) : null
    ro?.observe(c)
    glRef.current = st
    return () => ro?.disconnect()
  }, [])

  // a dive
  useEffect(() => {
    const c = ref.current, w = wrap.current
    if (!c || !w) return
    if (!run) { w.style.opacity = '0'; return }
    let raf = 0, midDone = false, whiteDone = false, doneDone = false, shown = false
    const t0 = performance.now()
    const finish = () => { if (!doneDone) { doneDone = true; w.style.opacity = '0'; cb.current.onDone?.() } }
    const st = glRef.current
    if (!st) {  // no WebGL: a plain white flash keeps the choreography intact
      w.style.background = '#fff'
      const a = window.setTimeout(() => { w.style.opacity = '1'; cb.current.onMid?.() }, duration * 0.74)
      const b = window.setTimeout(() => cb.current.onWhite?.(), duration * 0.95)
      const d = window.setTimeout(finish, duration + 800)
      return () => { window.clearTimeout(a); window.clearTimeout(b); window.clearTimeout(d) }
    }
    const { gl } = st
    let clock = 0, last = t0, held = 0   // the dive's own clock: it stops while the dive is held inside the clouds
    const draw = () => {
      const now = performance.now()
      const dt = Math.max(0, now - last)
      last = now
      if (holdRef.current && clock >= duration * HOLD_AT && held < maxHoldMs) held += dt
      else clock += dt
      const el = clock
      const p = Math.min(1, el / duration)
      if (p >= 0.26) {  // nothing is visible before the clouds arrive: do not spend GPU time during the approach
        if (!shown) { shown = true; w.style.opacity = '1' }
        const tail = Math.max(0, (el - duration) / 900)          // dissolve after the white-out
        w.style.opacity = String(Math.max(0, 1 - tail))
        gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT)
        gl.uniform2f(st.uRes, c.width, c.height); gl.uniform1f(st.uT, (now - t0) / 1000); gl.uniform1f(st.uP, p)
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
        if (tail >= 1) { finish(); return }
      }
      if (!midDone && p >= 0.74) { midDone = true; cb.current.onMid?.() }   // deck is opaque from ~0.7
      if (!whiteDone && p >= 0.95) { whiteDone = true; cb.current.onWhite?.() }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => { cancelAnimationFrame(raf); w.style.opacity = '0' }
  }, [run, duration, maxHoldMs])

  return (
    <div ref={wrap} className="absolute inset-0 z-30 pointer-events-none" style={{ opacity: 0 }}>
      <canvas ref={ref} className="block w-full h-full" />
    </div>
  )
}
