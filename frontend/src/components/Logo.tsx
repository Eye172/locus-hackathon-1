/** CampusLense mark: a lens with a classical portico (grid 64, lens centre 26/26, r 21, handle at 45° behind a
 *  uniform 2.5 gap). Two cuts of the same drawing:
 *  - solid: one colour; the portico is cut out of the lens - the pediment, the beam under it (as wide as the
 *    pediment, a little over the colonnade), the spaces between the columns and the step. The default everywhere,
 *    it holds down to 16 px.
 *  - line: the same geometry as strokes, for large sizes and loading states.
 *  Source files and the construction sheet: docs/design/logo. */

const HANDLE = 'M39.88 44.97L53.29 58.39A3.6 3.6 0 0 0 58.39 53.29L44.97 39.88A23.5 23.5 0 0 1 39.88 44.97z'
const SOLID = 'M5 26A21 21 0 1 1 47 26A21 21 0 1 1 5 26zM26 11.9L38.2 17H13.8zM13.8 19.2H38.2V22H13.8zM14.8 24.2H18.2V34.6H14.8zM21.13 24.2H24.53V34.6H21.13zM27.47 24.2H30.87V34.6H27.47zM33.8 24.2H37.2V34.6H33.8zM11.4 36.9H40.6V39.3H11.4z'

export function LogoMark({ variant = 'solid', size = 28, className = '', title }: {
  variant?: 'solid' | 'line'; size?: number; className?: string; title?: string
}) {
  const a11y = title ? { role: 'img', 'aria-label': title } : { 'aria-hidden': true }
  if (variant === 'line') {
    return (
      <svg viewBox="0 0 64 64" width={size} height={size} className={className} fill="none" stroke="currentColor" {...a11y}>
        <circle cx="26" cy="26" r="19.5" strokeWidth="3" />
        <path d={HANDLE} fill="currentColor" stroke="none" />
        <g strokeWidth="2.6">
          <path d="M14.4 19.2L26 13.2L37.6 19.2z" strokeLinejoin="miter" />
          <path d="M13.2 22.6H38.8M17.9 25.6V33.4M23.3 25.6V33.4M28.7 25.6V33.4M34.1 25.6V33.4M14 36.2H38M12.22 39.8H39.78" />
        </g>
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} className={className} fill="currentColor" {...a11y}>
      <path fillRule="evenodd" d={SOLID} />
      <path d={HANDLE} />
    </svg>
  )
}

/** Mark + name. The name is set in the UI face (Onest 600, -2 %), the mark sits 2 px low
 *  so the lens (not the handle) lines up with the letters. */
export function Logo({ className = '', size = 30 }: { className?: string; size?: number }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <LogoMark size={size} className="translate-y-[2px]" />
      <span className="text-[17px] font-semibold tracking-[-0.02em] leading-none">CampusLense</span>
    </span>
  )
}
