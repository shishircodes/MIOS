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
  x: <Icon d="M6 6l12 12M18 6l-12 12" />,
  filter: <Icon d="M3 5h18l-7 9v6l-4-2v-4z" />,
  sort: <Icon d="M7 4v16M3 16l4 4 4-4M17 20V4M13 8l4-4 4 4" />,
  archive: <Icon d="M3 4h18v4H3zM5 8v12h14V8M10 12h4" />,
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
export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  if (!open) return null
  return (
    <>
      <div className="drawer-mask" onClick={onClose} />
      <div className="drawer">
        <div className="drawer-h">
          <div style={{ fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--muted)' }}>
            {title}
          </div>
          <button className="close" onClick={onClose}>{Icons.x}</button>
        </div>
        <div className="drawer-body">{children}</div>
      </div>
    </>
  )
}
