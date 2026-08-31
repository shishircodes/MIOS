import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

// ----- Inline monoline icons -----
function Icon({ d, size = 14, sw = 1.4 }: { d: string; size?: number; sw?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="square"
      strokeLinejoin="miter"
    >
      <path d={d} />
    </svg>
  )
}

export const Icons: Record<string, ReactNode> = {
  monitor: <Icon d="M3 5h18v12H3zM3 21h18M9 17v4M15 17v4" />,
  push: <Icon d="M4 12h12M11 7l5 5-5 5M20 5v14" />,
  publish: <Icon d="M6 4h9l5 5v11H6zM15 4v5h5M9 13h7M9 17h5" />,
  watch: <Icon d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z M12 9a3 3 0 100 6 3 3 0 000-6z" />,
  dash: <Icon d="M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z" />,
  src: <Icon d="M4 6h16M4 12h16M4 18h16M8 4v16M16 4v16" />,
  tokens: <Icon d="M5 7h14v10H5zM5 12h14M9 7v10M15 7v10" />,
  search: <Icon d="M10 4a6 6 0 100 12 6 6 0 000-12zM20 20l-6-6" />,
  arrow: <Icon d="M5 12h14M13 6l6 6-6 6" />,
  spark: <Icon d="M3 13l4-6 4 4 4-8 4 6 2-2" />,
  ext: <Icon d="M14 4h6v6M20 4l-9 9M10 6H4v14h14v-6" />,
  check: <Icon d="M5 12l5 5 9-11" sw={1.8} />,
  alert: <Icon d="M12 3L1 21h22L12 3zM12 10v5M12 18h.01" sw={1.6} />,
  panel: <Icon d="M3 4h18v16H3zM9 4v16" sw={1.6} />,
  info: <Icon d="M12 3a9 9 0 100 18 9 9 0 000-18zM12 11v6M12 7h.01" sw={1.6} />,
  x: <Icon d="M6 6l12 12M18 6l-12 12" />,
  filter: <Icon d="M3 5h18l-7 9v6l-4-2v-4z" />,
  sort: <Icon d="M7 4v16M3 16l4 4 4-4M17 20V4M13 8l4-4 4 4" />,
  archive: <Icon d="M3 4h18v4H3zM5 8v12h14V8M10 12h4" />,
  people: <Icon d="M9 11a4 4 0 100-8 4 4 0 000 8zM2 21v-2a5 5 0 015-5h4a5 5 0 015 5v2M17 3.5a4 4 0 010 7.75M22 21v-2a5 5 0 00-3.5-4.75" />,
  lock: <Icon d="M5 11h14v10H5zM8 11V7a4 4 0 018 0v4M12 15v2" />,
}

/**
 * When a signal was collected, as "25 Aug" — short enough to sit inline on a
 * row. The full timestamp goes in the tooltip rather than the label, because
 * within a seven-day window the day is the part that distinguishes one signal
 * from another; the minute never is.
 */
export function CapturedAt({ at }: { at: string | null }) {
  if (!at) return null
  const d = new Date(at)
  if (Number.isNaN(d.getTime())) return null
  return (
    <span className="captured" title={d.toLocaleString()}>
      {d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}
    </span>
  )
}

// ----- Trend arrow -----
export function Trend({ dir, value, unit = '' }: { dir: string; value: number | string; unit?: string }) {
  if (dir === 'up') return <span className="up mono tnum">↑ {value}{unit}</span>
  if (dir === 'down') return <span className="down mono tnum">↓ {value}{unit}</span>
  return <span className="flat mono tnum">— {value}{unit}</span>
}

// ----- Tier chip -----
export function TierChip({ tier }: { tier: string | null | undefined }) {
  if (!tier) return <span className="chip new">NEW</span>
  return <span className={`chip tier-${tier.toLowerCase()}`}>TIER {tier}</span>
}

export function RegionChip({ region }: { region: string }) {
  return <span className="chip">{region}</span>
}

// ----- Sparkbar -----
export function SparkBar({ data, color = 'var(--ink-3)', height = 24 }: { data: number[]; color?: string; height?: number }) {
  const max = Math.max(...data, 1)
  return (
    <div className="spark" style={{ height }}>
      {data.map((v, i) => (
        <span key={i} style={{ background: color, height: `${(v / max) * 100}%` }} />
      ))}
    </div>
  )
}

// ----- Section card -----
export function Section({ title, tools, children }: { title?: string; tools?: ReactNode; children: ReactNode }) {
  return (
    <div className="section">
      {title && (
        <div className="section-h">
          <h2>{title}</h2>
          {tools && <div className="tools">{tools}</div>}
        </div>
      )}
      <div>{children}</div>
    </div>
  )
}

// ----- Bar block -----
export function BarBlock({ label, value, max, suffix = '' }: { label: string; value: number; max: number; suffix?: string }) {
  return (
    <div className="bar-block">
      <div style={{ color: 'var(--ink-2)' }}>{label}</div>
      <div className="bar-rail"><div className="bar-fill" style={{ width: `${(value / max) * 100}%` }} /></div>
      <div className="bar-num">{value}{suffix}</div>
    </div>
  )
}

// ----- Drawer -----

/** Must match the exit animation in app.css, or the panel is torn out of the
 *  DOM part-way through sliding away. */
const DRAWER_EXIT_MS = 180

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  // Stays mounted for the length of the exit animation, so closing slides out
  // instead of vanishing. `open` alone cannot express that: it is already false
  // while the panel is still on screen.
  const [mounted, setMounted] = useState(open)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (open) {
      setMounted(true)
      return
    }
    const t = setTimeout(() => setMounted(false), DRAWER_EXIT_MS)
    return () => clearTimeout(t)
  }, [open])

  // Escape closes it. A modal overlay that can only be dismissed by finding the
  // right pixel is a trap for keyboard users.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // Move focus into the panel when it opens, so the next Tab lands inside it
  // rather than continuing from wherever the trigger was in the page.
  // `mounted` is in the deps because the render that flips `open` still returns
  // null — the button does not exist until the following render.
  useEffect(() => {
    if (open && mounted) closeRef.current?.focus()
  }, [open, mounted])

  // The caller renders content from the same state that drives `open`
  // (`{signal && <Detail/>}`), so both are already empty by the time the exit
  // animation runs. Holding the last non-empty pair keeps the panel populated
  // on its way out.
  const last = useRef({ title, children })
  if (open) last.current = { title, children }
  const shown = open ? { title, children } : last.current

  if (!mounted) return null
  const closing = !open

  return (
    <>
      <div className={closing ? 'drawer-mask closing' : 'drawer-mask'} onClick={onClose} />
      <div
        className={closing ? 'drawer closing' : 'drawer'}
        role="dialog"
        aria-modal="true"
        aria-label={shown.title || 'Details'}
      >
        <div className="drawer-h">
          <div style={{ fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--muted)' }}>
            {shown.title}
          </div>
          <button ref={closeRef} className="close" onClick={onClose} aria-label="Close">
            {Icons.x}
          </button>
        </div>
        <div className="drawer-body">{shown.children}</div>
      </div>
    </>
  )
}

/**
 * Loading state: five bars rising and falling, plus a line of text.
 *
 * Bars rather than a spinner because the app is already full of them — the
 * sparklines in Hiring Velocity and the magnitude bars in the rail — so the
 * wait looks like the product is drawing something rather than stalling.
 *
 * `lines` rotates when loading outlasts one message. Most loads finish on the
 * first, so it reads as a single sentence rather than a slideshow.
 */
export function Loading({ lines }: { lines: string[] }) {
  const [i, setI] = useState(0)

  useEffect(() => {
    if (lines.length < 2) return
    const id = setInterval(() => setI((v) => (v + 1) % lines.length), 2400)
    return () => clearInterval(id)
  }, [lines.length])

  return (
    // One live region announcing the text. The bars are decoration and would
    // otherwise be read as five empty elements.
    <div className="loading" role="status" aria-live="polite">
      <div className="loading-bars" aria-hidden="true">
        <span /><span /><span /><span /><span />
      </div>
      <p className="loading-text">{lines[i] ?? lines[0]}</p>
    </div>
  )
}
