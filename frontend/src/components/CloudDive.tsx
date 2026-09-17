import { useEffect, useRef } from 'react'

/**
 * Cinematic dive from orbit through the cloud deck — a full-screen procedural cloud tunnel (fBm noise, no assets).
 * The clouds thicken into a white-out in which the map underneath silently jumps to the campus; then they clear.
 * Callbacks (fractions of the dive): onMid → position the map, onClear → start raising buildings, onDone → unmount.
 */
const VS = `attribute vec2 a; void main(){ gl_Position = vec4(a, 0.0, 1.0); }`
const FS = `precision highp float;
uniform vec2 u_res; uniform float u_t; uniform float u_p;
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y); }
float fbm(vec2 p){ float v = 0.0, a = 0.5; for (int i = 0; i < 5; i++) { v += a * noise(p); p = p * 2.03 + vec2(1.7, 9.2); a *= 0.5; } return v; }
void main(){
  vec2 uv = (gl_FragCoord.xy - 0.5 * u_res) / u_res.y;
  float r = length(uv);
  float speed = u_t * 0.55 + u_p * 5.5;
  // layered "tunnel": each cloud sheet scales up as it passes the camera, so we seem to fly forward through them
  float d = 0.0, wsum = 0.0;
  for (int i = 0; i < 5; i++) {
    float fi = float(i);
    float z = fract(speed * 0.28 + fi * 0.2);
    float sc = 1.0 / (0.18 + z * 2.2);
    vec2 q = uv * sc * 1.6 + vec2(fi * 17.0, -speed * 0.35 + fi * 3.0);
    float w = smoothstep(0.0, 0.25, z) * (1.0 - z);
    d += w * fbm(q); wsum += w;
  }
  d /= max(wsum, 0.001);
  float dens = smoothstep(0.47 - u_p * 0.34, 0.66 - u_p * 0.30, d);   // crisper cloud edges
  // sky: deep space above the horizon at the start, bright troposphere blue as we descend
  float descend = smoothstep(0.0, 0.7, u_p);
  vec3 space = vec3(0.015, 0.03, 0.09), sky = vec3(0.40, 0.62, 0.95);
  vec3 bg = mix(space, sky, clamp(uv.y * 0.6 + 0.5 + descend * 0.9, 0.0, 1.0));
  bg = mix(bg, sky * 1.05, descend);
  vec2 sunp = vec2(0.42, 0.28);
  float sun = pow(max(0.0, 1.0 - length(uv - sunp) * 1.3), 5.0);
  vec3 cloud = mix(vec3(0.58, 0.66, 0.82), vec3(1.0, 0.99, 0.97), smoothstep(0.35, 0.85, d)) * (0.78 + 0.4 * sun);   // shaded undersides, lit tops
  vec3 col = mix(bg + vec3(1.0, 0.95, 0.8) * sun * 0.9, cloud, dens);
  col += vec3(1.0, 0.97, 0.9) * sun * 0.35 * (1.0 - dens);
  float white = smoothstep(0.62, 0.9, u_p);              // white-out: the map jumps underneath, nobody sees it
  col = mix(col, vec3(1.0), white);
  float vig = 1.0 - smoothstep(0.55, 1.15, r) * 0.35 * (1.0 - white);
  gl_FragColor = vec4(col * vig, 1.0);
}`

export function CloudDive({ duration = 3600, onMid, onClear, onDone }:
  { duration?: number; onMid?: () => void; onClear?: () => void; onDone?: () => void }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const c = ref.current, w = wrap.current
    if (!c || !w) return
    const gl = c.getContext('webgl', { antialias: false, premultipliedAlpha: false })
    let raf = 0, midDone = false, clearDone = false, doneDone = false
    const t0 = performance.now()
    const finish = () => { if (!doneDone) { doneDone = true; onDone?.() } }
    if (!gl) {  // no WebGL: a plain white flash keeps the choreography intact
      w.style.background = '#fff'
      const id = window.setTimeout(() => { onMid?.(); onClear?.(); finish() }, duration * 0.6)
      return () => window.clearTimeout(id)
    }
    const sh = (t: number, s: string) => { const o = gl.createShader(t)!; gl.shaderSource(o, s); gl.compileShader(o); return o }
    const prog = gl.createProgram()!
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(prog); gl.useProgram(prog)
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
    const loc = gl.getAttribLocation(prog, 'a'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)
    const uRes = gl.getUniformLocation(prog, 'u_res'), uT = gl.getUniformLocation(prog, 'u_t'), uP = gl.getUniformLocation(prog, 'u_p')
    const size = () => {
      const dpr = Math.min(1.5, window.devicePixelRatio || 1)  // the shader is per-pixel heavy: cap the resolution
      c.width = Math.round(c.clientWidth * dpr); c.height = Math.round(c.clientHeight * dpr); gl.viewport(0, 0, c.width, c.height)
    }
    size()
    const ro = 'ResizeObserver' in window ? new ResizeObserver(size) : null
    ro?.observe(c)
    const draw = () => {
      const el = performance.now() - t0
      const p = Math.min(1, el / duration)
      // fade in over the first 14 %, hold, then dissolve over the last 800 ms after the white-out
      const fadeIn = Math.min(1, p / 0.14)
      const tail = Math.max(0, (el - duration) / 800)
      w.style.opacity = String(Math.max(0, fadeIn * (1 - tail)))
      gl.uniform2f(uRes, c.width, c.height); gl.uniform1f(uT, el / 1000); gl.uniform1f(uP, p)
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
      if (!midDone && p >= 0.5) { midDone = true; onMid?.() }  // clouds are opaque from here on: tiles get ~2 s to load
      if (!clearDone && p >= 0.98) { clearDone = true; onClear?.() }
      if (tail >= 1) { finish(); return }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => { cancelAnimationFrame(raf); ro?.disconnect() }
  }, [duration, onMid, onClear, onDone])
  return (
    <div ref={wrap} className="absolute inset-0 z-30 pointer-events-none" style={{ opacity: 0 }}>
      <canvas ref={ref} className="block w-full h-full" />
    </div>
  )
}
