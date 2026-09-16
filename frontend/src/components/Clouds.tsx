/** Cloud layer shown while the camera dives from orbit to the campus. Pure CSS, no assets. */
export function Clouds({ zoom }: { zoom: number }) {
  // bell curve between zoom 3.5 and 9.5, peak at 6.5
  const o = zoom < 3.5 || zoom > 9.5 ? 0 : Math.max(0, 1 - Math.abs(zoom - 6.5) / 3) * 0.92
  return (
    <div className="absolute inset-0 pointer-events-none overflow-hidden transition-opacity duration-150" style={{ opacity: o }}>
      <div className="clouds clouds-a" />
      <div className="clouds clouds-b" />
      <div className="absolute inset-0 bg-white/40" style={{ opacity: o > 0.8 ? (o - 0.8) * 3 : 0 }} />
    </div>
  )
}
