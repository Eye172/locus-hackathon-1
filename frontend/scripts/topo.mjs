// Generates public/topo.svg: subtle topographic contour lines from fractal simplex noise.
import { contours } from 'd3-contour'
import { createNoise2D } from 'simplex-noise'
import { writeFileSync } from 'node:fs'

const W = 120, H = 120, SIZE = 1800, LEVELS = 10
const noise = createNoise2D(() => 0.4242) // deterministic
const values = new Float64Array(W * H)
for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
  let v = 0, amp = 1, freq = 1 / 48
  for (let o = 0; o < 4; o++) { v += amp * noise(x * freq, y * freq); amp *= 0.5; freq *= 2 }
  values[y * W + x] = v
}
const min = Math.min(...values), max = Math.max(...values)
const thresholds = Array.from({ length: LEVELS }, (_, i) => min + ((max - min) * (i + 1)) / (LEVELS + 1))
const polys = contours().size([W, H]).thresholds(thresholds)(values)
const k = SIZE / W
let paths = ''
for (const c of polys) {
  let d = ''
  for (const poly of c.coordinates) for (const ring of poly) {
    d += ring.filter((_, i) => i % 2 === 0).map(([x, y], i) => `${i ? "L" : "M"}${Math.round(x * k)} ${Math.round(y * k)}`).join("") + 'Z'
  }
  paths += `<path d="${d}"/>`
}
const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${SIZE} ${SIZE}" width="${SIZE}" height="${SIZE}"><g fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round">${paths}</g></svg>`
writeFileSync(new URL('../public/topo.svg', import.meta.url), svg)
console.log('topo.svg', (svg.length / 1024).toFixed(0), 'KB', polys.length, 'levels')
