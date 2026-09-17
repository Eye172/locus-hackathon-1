/** Software WebGL (no GPU, or acceleration switched off) makes the full-screen effects crawl at ~5 fps.
 *  Such machines get the "lite" globe: no cloud shader, no star twinkle, 1× pixel ratio. `?lite=1` / `?lite=0` override. */
export function detectLiteGpu(): boolean {
  try {
    const q = new URLSearchParams(window.location.search).get('lite')
    if (q === '1') return true
    if (q === '0') return false
    const gl = document.createElement('canvas').getContext('webgl')
    if (!gl) return true
    const ext = gl.getExtension('WEBGL_debug_renderer_info')
    const renderer = ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : ''
    gl.getExtension('WEBGL_lose_context')?.loseContext()
    return /swiftshader|llvmpipe|softpipe|software|basic render/i.test(renderer)
  } catch {
    return true
  }
}
