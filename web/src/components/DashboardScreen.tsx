import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useRef, useState } from 'react'
import { Explainer, Loading, Section } from '~/components/ui'
import { dashboardQueryOptions } from '~/lib/api'
import type { DashboardFilters } from '~/lib/api'
import { useDrawPath, useFigure, useGrowSlices, useReveal } from '~/lib/motion'
import type { Breakdown, CollectionPoint } from '~/lib/types'

/** The landing page: what the last collection found, and how it compares.
 *
 *  Every figure here is counted from the signals table. That is worth stating
 *  because the page this descends from was built entirely on invented numbers —
 *  a hardcoded series, fabricated sector totals, a literal 20 for the watchlist,
 *  and an "↑ trending" delta on every tile with nothing compared. Anything that
 *  cannot be measured is absent rather than estimated, which is why several
 *  panels below say "no earlier collection" instead of showing a movement.
 */

const REGION_LABEL: Record<string, string> = { AU: 'Australia', PNG: 'Papua New Guinea' }

/** Colours for a composition bar, in a fixed order.
 *
 *  Fixed rather than assigned by rank, so a sector keeps its colour between
 *  weeks. A palette that reshuffles when the ordering changes makes two weeks
 *  impossible to compare at a glance, which is the one thing the bar is for.
 */
const SLICE_VARS = [
  'var(--teal)', 'var(--moss)', 'var(--navy)', 'var(--primary-2)',
  'var(--amber)', 'var(--ink-3)', 'var(--line-3)',
]

function sliceColour(index: number): string {
  return SLICE_VARS[index % SLICE_VARS.length]!
}

/** A movement, or an explicit absence of one.
 *
 *  `null` means there was no earlier collection to compare against. The version
 *  this replaces printed "↑ trending" on every tile regardless.
 */
function Delta({ pct, quiet }: { pct: number | null | undefined; quiet?: boolean }) {
  if (pct === null || pct === undefined) {
    return <span className="delta flat">{quiet ? 'no comparison' : 'no earlier collection'}</span>
  }
  if (pct === 0) return <span className="delta flat">level</span>
  const up = pct > 0
  return (
    <span className={`delta ${up ? 'up' : 'down'}`}>
      {up ? '↑' : '↓'} {Math.abs(pct)}%
    </span>
  )
}

/** A bare sparkline. Deliberately unlabelled — it shows shape, and the figure
 *  beside it carries the value. */
function Spark({ points, colour = 'var(--teal)' }: { points: number[]; colour?: string }) {
  if (points.length < 2) return <div className="spark-empty">one collection so far</div>
  const max = Math.max(...points, 1)
  const w = 100
  const h = 26
  const step = w / (points.length - 1)
  const path = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${(h - (p / max) * h).toFixed(1)}`)
    .join(' ')
  const area = `${path} L${w},${h} L0,${h} Z`
  return (
    <svg className="kpi-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={area} fill={colour} opacity="0.12" />
      <path d={path} fill="none" stroke={colour} strokeWidth="1.6"
            strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

function Kpi({
  label, value, suffix, delta, points, colour, foot,
}: {
  label: string
  value: number | string
  suffix?: string
  delta?: number | null
  points?: number[]
  colour?: string
  foot?: string
}) {
  // The value comes from the prop, not from reading the rendered text.
  //
  // The shared count-up read its target out of the DOM and wrote back to it,
  // which was fine while the data changed once on load. With filters it changes
  // repeatedly, and the animation and React ended up writing the same node with
  // different ideas of what belonged there — the hero settled on "0" while the
  // API had returned 11. Driving it from the prop makes both writers agree on
  // the final value by construction.
  const numeric = typeof value === 'number' ? value : Number(String(value).replace(/[^0-9.-]/g, ''))
  const ref = useFigure(Number.isFinite(numeric) ? numeric : 0)

  return (
    <div className="kpi2">
      <div className="kpi2-label">{label}</div>
      <div className="kpi2-value">
        {/* The number stays alone in .val: the count-up rewrites its text
            content, so anything sharing the node would be overwritten. */}
        <span className="val tnum" ref={ref}>{value}</span>
        {suffix && <span className="kpi2-suffix">{suffix}</span>}
      </div>
      <div className="kpi2-foot">
        {delta !== undefined && <Delta pct={delta} quiet />}
        {foot && <span className="muted">{foot}</span>}
      </div>
      {points && <Spark points={points} colour={colour} />}
    </div>
  )
}

const SERIES = [
  { key: 'au' as const, label: 'Australia', colour: 'var(--teal-2)', dash: undefined },
  { key: 'png' as const, label: 'Papua New Guinea', colour: 'var(--moss)', dash: '5 3' },
]

/** Two series over the same collections, drawn together and inspectable.
 *
 *  One chart rather than two stacked ones: the question is whether the two
 *  markets move together, and that cannot be read from charts with independent
 *  vertical scales.
 *
 *  Hand-drawn SVG rather than a charting library. Two series over a dozen
 *  points does not need one, and a library would arrive with its own type
 *  scale, palette and tooltip to be overridden back into this one — plus a few
 *  hundred kilobytes for a path and some circles.
 *
 *  **Every interaction has a keyboard path.** The readout follows arrow keys as
 *  well as the pointer, and the legend toggles are real buttons. A hover-only
 *  chart puts its figures out of reach of anyone not using a mouse, and the
 *  numbers here are the whole point of it.
 */
function TrendChart({ points, mode }: { points: CollectionPoint[]; mode: 'line' | 'bar' }) {
  // Hover and keyboard keep separate cursors, and the keyboard one wins.
  //
  // Sharing one piece of state looked simpler and was wrong: `mouseleave`
  // cleared it, and focusing the chart can scroll it under a stationary
  // pointer — which fires `mouseleave` and wiped the cursor that focus had
  // just set. Tabbing to the chart showed nothing until an arrow key was
  // pressed, so the one affordance a keyboard user needs was invisible.
  const [hoverAt, setHoverAt] = useState<number | null>(null)
  const [keyAt, setKeyAt] = useState<number | null>(null)
  const cursor = keyAt ?? hoverAt
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const auPath = useRef<SVGPathElement>(null)
  const pngPath = useRef<SVGPathElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  const shown = SERIES.filter((s) => !hidden.has(s.key))
  const drawKey = `${mode}-${[...hidden].sort().join()}-${points.length}`
  useDrawPath(auPath, { key: drawKey })
  useDrawPath(pngPath, { key: drawKey, delay: 0.12 })

  if (points.length < 2) {
    return (
      <div className="center-empty">
        One collection so far. A trend needs at least two — the next weekly run
        will give this a shape.
      </div>
    )
  }

  const w = 720
  const h = 200
  const pad = { l: 36, r: 14, t: 14, b: 26 }
  // Scaled to the visible series only, so hiding the larger one actually
  // reveals the shape of the smaller rather than leaving it flat at the axis.
  const max = Math.max(
    ...points.flatMap((p) => shown.map((s) => p[s.key])), 1,
  )
  const x = (i: number) => pad.l + (i * (w - pad.l - pad.r)) / (points.length - 1)
  const y = (v: number) => pad.t + (1 - v / max) * (h - pad.t - pad.b)
  const line = (key: 'au' | 'png') =>
    points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(' ')

  // Gridlines at fractions of the maximum rather than round numbers: the scale
  // is signals per collection, which has no natural unit to round to.
  const grid = [0, 0.25, 0.5, 0.75, 1]
  const active = cursor === null ? null : points[cursor]
  const barW = Math.max(4, (w - pad.l - pad.r) / (points.length * 2.6))

  function toggle(key: string) {
    setHidden((prev) => {
      const next = new Set(prev)
      // Never hide the last visible series: an empty chart is not a view of
      // anything, and the control would have no obvious way back.
      if (next.has(key)) next.delete(key)
      else if (next.size < SERIES.length - 1) next.add(key)
      return next
    })
  }

  function nearest(clientX: number): number | null {
    const svg = svgRef.current
    if (!svg) return null
    const rect = svg.getBoundingClientRect()
    const rel = ((clientX - rect.left) / rect.width) * w
    let best = 0
    for (let i = 1; i < points.length; i += 1) {
      if (Math.abs(x(i) - rel) < Math.abs(x(best) - rel)) best = i
    }
    return best
  }

  return (
    <div className="trend">
      {/* The focusable element is this div, not the <svg> inside it.
          SVG focus is inconsistent across engines — Chromium will set
          `document.activeElement` to a focused <svg> without dispatching a
          single focus event — so hanging the keyboard affordance off it means
          the readout may never open for a keyboard user, silently and only in
          some browsers. A div focuses the same way everywhere. */}
      <div
        className="trend-plot"
        tabIndex={0}
        role="group"
        aria-label={`Signals per collection across ${points.length} collections. ${
          shown.map((s) => `${s.label}: ${points.map((p) => p[s.key]).join(', ')}`).join('. ')
        }. Use the left and right arrow keys to read each collection.`}
        onMouseMove={(e) => setHoverAt(nearest(e.clientX))}
        onMouseLeave={() => setHoverAt(null)}
        onTouchStart={(e) => e.touches[0] && setHoverAt(nearest(e.touches[0].clientX))}
        onTouchMove={(e) => e.touches[0] && setHoverAt(nearest(e.touches[0].clientX))}
        // Focus opens the readout at the most recent collection, so tabbing
        // here shows something rather than requiring a guess that arrows work.
        onFocus={() => setKeyAt((c) => c ?? points.length - 1)}
        onBlur={() => setKeyAt(null)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            e.preventDefault()
            setKeyAt((c) => {
              const from = c ?? points.length - 1
              return Math.min(points.length - 1, Math.max(0, from + (e.key === 'ArrowLeft' ? -1 : 1)))
            })
          }
          if (e.key === 'Escape') setKeyAt(null)
        }}
      >
        <svg
          ref={svgRef}
          viewBox={`0 0 ${w} ${h}`}
          className="trend-svg"
          aria-hidden="true"
        >
          {grid.map((f) => (
            <g key={f}>
              <line x1={pad.l} x2={w - pad.r} y1={y(max * f)} y2={y(max * f)}
                    stroke="var(--line)" strokeWidth="1" />
              <text x={pad.l - 7} y={y(max * f) + 3} textAnchor="end" className="trend-tick">
                {Math.round(max * f)}
              </text>
            </g>
          ))}

          {/* The cursor rule sits behind the data, so it never crosses a point. */}
          {cursor !== null && (
            <line className="trend-cursor" x1={x(cursor)} x2={x(cursor)}
                  y1={pad.t} y2={h - pad.b} />
          )}

          {mode === 'line' ? (
            <>
              {!hidden.has('au') && (
                <path d={`${line('au')} L${x(points.length - 1)},${y(0)} L${x(0)},${y(0)} Z`}
                      fill="var(--teal)" opacity="0.10" className="trend-area" />
              )}
              {!hidden.has('au') && (
                <path ref={auPath} d={line('au')} fill="none" stroke="var(--teal-2)"
                      strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
              )}
              {!hidden.has('png') && (
                <path ref={pngPath} d={line('png')} fill="none" stroke="var(--moss)"
                      strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"
                      strokeDasharray="5 3" />
              )}
              {points.map((p, i) => (
                <g key={p.date} className={cursor === i ? 'pt on' : 'pt'}>
                  {shown.map((s) => (
                    <circle key={s.key} cx={x(i)} cy={y(p[s.key])} r={cursor === i ? 4.2 : 2.6}
                            fill={s.colour} />
                  ))}
                </g>
              ))}
            </>
          ) : (
            points.map((p, i) => (
              <g key={p.date} className={cursor === i ? 'bars on' : 'bars'}>
                {shown.map((s, si) => {
                  const offset = shown.length === 1 ? 0 : (si === 0 ? -barW / 2 - 1 : barW / 2 + 1)
                  return (
                    <rect key={s.key} className="trend-bar"
                          x={x(i) + offset - barW / 2} width={barW}
                          y={y(p[s.key])} height={Math.max(0, y(0) - y(p[s.key]))}
                          fill={s.colour} rx="1.5" />
                  )
                })}
              </g>
            ))
          )}
        </svg>

        {/* The readout. Positioned along the plot rather than following the
            pointer: a tooltip that chases the cursor is unreadable on touch and
            impossible to reach with a keyboard. */}
        {active && (
          <div
            className="trend-read"
            style={{
              left: `${(x(cursor!) / w) * 100}%`,
              transform: cursor! > points.length / 2 ? 'translateX(-100%)' : 'none',
            }}
            role="status"
          >
            <div className="trend-read-date">{active.date}</div>
            {shown.map((s) => (
              <div key={s.key} className="trend-read-row">
                <i className="swatch" style={{ background: s.colour }} />
                <span>{s.label}</span>
                <b className="tnum">{active[s.key]}</b>
              </div>
            ))}
            <div className="trend-read-total">
              <span>Total</span><b className="tnum">{active.total}</b>
            </div>
          </div>
        )}
      </div>

      <div className="trend-axis">
        <span>{points[0]!.date}</span>
        <span className="trend-hint">
          Hover, or focus the chart and use ← →
        </span>
        <span>{points[points.length - 1]!.date}</span>
      </div>

      <div className="legend">
        {SERIES.map((s) => {
          const off = hidden.has(s.key)
          return (
            <button
              key={s.key}
              type="button"
              className={`legend-item toggle${off ? ' off' : ''}`}
              aria-pressed={!off}
              onClick={() => toggle(s.key)}
              title={off ? `Show ${s.label}` : `Hide ${s.label}`}
            >
              <i className={`swatch${s.dash ? ' dashed' : ''}`}
                 style={{ background: off ? 'var(--line-3)' : s.colour }} />
              {s.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/** A composition as one bar plus a legend, rather than a pie.
 *
 *  A stacked bar reads left to right at any size and stays legible at five
 *  slices; a pie needs a legend anyway and compares angles badly.
 */
function Composition({ items, total }: { items: Breakdown[]; total: number }) {
  const scope = useRef<HTMLDivElement>(null)
  const [lit, setLit] = useState<string | null>(null)
  // Keyed on the shape of the data, so a bar re-grows when the collection
  // changes rather than only on first mount.
  useGrowSlices(scope, '.comp-slice', { key: items.map((i) => i.count).join() })

  if (!items.length) return <div className="muted comp-empty">Nothing classified in this collection.</div>
  return (
    <div className={`comp${lit ? ' has-lit' : ''}`} ref={scope}>
      <div className="comp-bar" role="img"
           aria-label={items.map((s) => `${s.label} ${s.count}`).join(', ')}>
        {items.map((s, i) => (
          // `title` as an attribute, not a <title> child. A <title> element is
          // only valid in <head> or inside SVG; nested in a span the browser
          // hoists it and it replaces the document title, which turned the
          // browser tab into "Leadership: 2 of 80".
          <span key={s.key}
                className={`comp-slice${lit === s.key ? ' lit' : ''}`}
                title={`${s.label}: ${s.count} of ${total}`}
                onMouseEnter={() => setLit(s.key)}
                onMouseLeave={() => setLit(null)}
                style={{ width: `${(s.count / Math.max(total, 1)) * 100}%`, background: sliceColour(i) }} />
        ))}
      </div>
      <ul className="comp-legend">
        {items.map((s, i) => (
          <li key={s.key}
              className={lit === s.key ? 'lit' : undefined}
              onMouseEnter={() => setLit(s.key)}
              onMouseLeave={() => setLit(null)}>
            <i className="swatch" style={{ background: sliceColour(i) }} />
            <span className="comp-name">{s.label}</span>
            <span className="comp-num tnum">{s.count}</span>
            <span className="comp-share tnum">{s.share}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function DashboardScreen() {
  const [filters, setFilters] = useState<DashboardFilters>({})
  const { data, isPending, isFetching, error } = useQuery(dashboardQueryOptions(filters))
  const scope = useRef<HTMLDivElement>(null)
  const [mode, setMode] = useState<'line' | 'bar'>('line')

  function narrow(next: Partial<DashboardFilters>) {
    setFilters((f) => ({ ...f, ...next }))
  }

  // Before the early returns: hooks cannot be called conditionally.
  // Keyed on what is being shown, not just the date: changing region keeps the
  // date but changes every figure.
  const dataKey = `${data?.selected ?? 'none'}-${data?.region ?? 'all'}`
  const heroRef = useFigure(data?.latest?.total ?? 0, { delay: 0.05 })
  // The panels arrive in reading order rather than all at once. Capped low:
  // this page has a dozen, and staggering all of them delays the last one past
  // the point where the movement still reads as arrival.
  useReveal(scope, '.section, .kpi2', { key: dataKey, delay: 0.05, stagger: 0.04, y: 8, max: 8 })

  if (isPending) {
    return (
      <div className="page">
        <Loading lines={['Counting what the last run found', 'Lining up the collections']} />
      </div>
    )
  }
  if (error) {
    return (
      <div className="page">
        <div className="notice err">Could not load the dashboard. {(error as Error).message}</div>
      </div>
    )
  }

  const {
    collections, latest, change, sectors, categories, groups, sources,
    companies, newNames, run, watchlist, coverage, trendWindow,
    available, selected, region, isLatest, trendChoices,
  } = data

  if (!latest || collections.length === 0) {
    return (
      <div className="page">
        <div className="page-header">
          <div>
            <div className="kicker">Overview</div>
            <h1>Dashboard</h1>
          </div>
        </div>
        <div className="center-empty">
          Nothing has been collected and classified yet, so there is nothing to show.
          The weekly run will fill this in.
        </div>
      </div>
    )
  }

  // Windows shorter than the history, then "All". `All` maps to the coverage
  // itself rather than a fixed ceiling, so the button that says "everything"
  // asks for exactly everything.
  const windowChoices = [
    ...trendChoices
      .filter((n) => n < coverage.collections)
      .map((n) => ({ value: n, label: String(n) })),
    { value: coverage.collections, label: 'All' },
  ]

  // The decorative trace behind the hero. Built from the same collections the
  // chart draws, so it cannot show a different history from the one below it.
  const traceMax = Math.max(...collections.map((c) => c.total), 1)
  const heroTrace = collections
    .map((c, i) => {
      const x = collections.length === 1 ? 0 : (i / (collections.length - 1)) * 300
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${(60 - (c.total / traceMax) * 46 - 7).toFixed(1)}`
    })
    .join(' ')

  const auPoints = collections.map((c) => c.au)
  const pngPoints = collections.map((c) => c.png)
  const totalPoints = collections.map((c) => c.total)
  const acting = groups.find((g) => g.key === 'acting')

  return (
    <div className="page" ref={scope}>
      <div className="page-header">
        <div>
          <div className="kicker">Overview</div>
          {/* The heading follows the selection. It claimed "the last collection"
              whatever was being shown, which is exactly the kind of stale label
              the rest of this page goes out of its way to avoid. */}
          <h1>
            {isLatest ? 'What the last collection found' : `The collection of ${selected}`}
            {region && <span className="h1-qualifier"> · {REGION_LABEL[region] ?? region}</span>}
          </h1>
        </div>
        {/* The filters live in the header rather than in a band of their own.
            A full-width control panel between the title and the first figure
            was the most prominent thing on a page whose job is to show
            numbers, and it pushed the data below the fold. Here they are
            available without being addressed first. */}
        <div className="meta hdr-meta">
        <div className={`hdr-controls${isFetching ? ' busy' : ''}`}>
          <label className="ctl">
            <span className="ctl-label">Collection</span>
            <span className="ctl-select">
              <select
                value={selected ?? ''}
                onChange={(e) => narrow({ collection: e.target.value || null })}
              >
                {available.map((c) => (
                  <option key={c.date} value={c.date}>
                    {c.date} — {c.total} signal{c.total === 1 ? '' : 's'}
                  </option>
                ))}
              </select>
              <svg className="ctl-caret" viewBox="0 0 10 6" aria-hidden="true">
                <path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5"
                      strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
          </label>

          <div className="ctl">
            <span className="ctl-label">Market</span>
            <div className="seg" role="group" aria-label="Market">
              {[
                { v: null, label: 'Both' },
                { v: 'AU', label: 'Australia' },
                { v: 'PNG', label: 'PNG' },
              ].map((o) => (
                <button key={o.label} type="button"
                        className={`seg-btn${(region ?? null) === o.v ? ' on' : ''}`}
                        aria-pressed={(region ?? null) === o.v}
                        onClick={() => narrow({ region: o.v })}>
                  {o.label}
                </button>
              ))}
            </div>
          </div>

          {/* Only the windows that are actually shorter than the history, plus
              one "All". Offering 4 / 8 / 12 / 26 against six collections gave
              three buttons that all said "All" and all did the same thing. */}
          {windowChoices.length > 1 && (
            <div className="ctl">
              <span className="ctl-label">Chart covers</span>
              <div className="seg" role="group" aria-label="Collections in the chart">
                {windowChoices.map((c) => (
                  <button key={c.label} type="button"
                          className={`seg-btn${trendWindow === c.value ? ' on' : ''}`}
                          aria-pressed={trendWindow === c.value}
                          onClick={() => narrow({ trend: c.value })}>
                    {c.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {(selected !== available[0]?.date || region) && (
            <button className="btn sm ghost ctl-reset"
                    onClick={() => setFilters({})}>
              Reset
            </button>
          )}
        </div>
          <div className="hdr-coverage">
            {coverage.collections} collection{coverage.collections === 1 ? '' : 's'}
            {coverage.collections > trendWindow && ` · last ${trendWindow} charted`}
            {' · '}{coverage.from} to {coverage.to}
          </div>
        </div>
      </div>

      {/* A figure from three weeks ago shown without comment reads as current. */}
      {!isLatest && (
        <div className="notice warn viewing-past" role="status">
          <strong>Showing the collection of {selected}.</strong> This is not the most
          recent — {available[0]?.date} is.{' '}
          <button className="linkish" onClick={() => narrow({ collection: null })}>
            Jump to the latest →
          </button>
        </div>
      )}

      {/* ---------- the hero band ---------- */}
      <div className="hero">
        {/* The run of collections, drawn faintly across the band. Decorative:
            the same series is charted properly below with its axis and its
            readout, and this is here so the headline figure sits on the shape
            it came from rather than on nothing. */}
        <svg className="hero-trace" viewBox="0 0 300 60" preserveAspectRatio="none"
             aria-hidden="true">
          <path d={heroTrace} fill="none" stroke="currentColor" strokeWidth="1.5"
                strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        </svg>
        <div className="hero-main">
          <div className="hero-label">Signals · collection of {latest.date}</div>
          <div className="hero-figure">
            <span className="val tnum" ref={heroRef}>{latest.total}</span>
            <Delta pct={change.total} />
          </div>
          {/* The split as a bar as well as two figures. Two numbers side by
              side have to be compared; a bar is already the comparison, and
              this is the one proportion the page is about. */}
          <div className="hero-bar" role="img"
               aria-label={`${latest.au} from Australia, ${latest.png} from Papua New Guinea`}>
            <span style={{
              width: `${(latest.au / Math.max(latest.au + latest.png, 1)) * 100}%`,
              background: 'var(--teal)',
            }} />
            <span style={{
              width: `${(latest.png / Math.max(latest.au + latest.png, 1)) * 100}%`,
              background: 'var(--moss)',
            }} />
          </div>
          <div className="hero-split">
            <span><i className="swatch" style={{ background: 'var(--teal)' }} />
              {REGION_LABEL.AU} <b className="tnum">{latest.au}</b></span>
            <span><i className="swatch" style={{ background: 'var(--moss)' }} />
              {REGION_LABEL.PNG} <b className="tnum">{latest.png}</b></span>
          </div>
        </div>
        <div className="hero-side">
          {/* The run behind the figures. A dashboard that shows a number without
              saying which run produced it cannot tell a quiet week from a
              failed one. */}
          {run ? (
            <>
              <div className={`run-chip ${run.status === 'ok' ? 'ok' : 'warn'}`}>
                {run.status === 'ok' ? 'Run completed' : `Run ${run.status}`}
              </div>
              <div className="hero-meta">
                {run.trigger === 'schedule' ? 'Scheduled run' : `${run.trigger} run`}
                {run.finishedAt && <> · finished {new Date(run.finishedAt).toLocaleString('en-AU', {
                  day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                })}</>}
              </div>
              <div className="hero-meta">{run.collected} records collected before classification</div>
            </>
          ) : (
            <div className="hero-meta">No run log for this collection.</div>
          )}
          <Link to="/monitor/digest" className="btn sm hero-cta">Open the weekly digest →</Link>
        </div>
      </div>

      {/* ---------- KPI strip ---------- */}
      <div className="kpi2-row">
        <Kpi label="Australia" value={latest.au} delta={change.au}
             points={auPoints} colour="var(--teal)" />
        <Kpi label="Papua New Guinea" value={latest.png} delta={change.png}
             points={pngPoints} colour="var(--moss)" />
        <Kpi label="Watchlist seen" value={watchlist.seen} suffix={`of ${watchlist.total}`}
             foot={`${watchlist.seenShare}% of the list appeared`} />
        <Kpi label="New names" value={newNames}
             foot="companies not on the watchlist" />
      </div>

      <Section
        title="Signals per collection"
        tools={
          <div className="seg" role="group" aria-label="Chart type">
            {(['line', 'bar'] as const).map((m) => (
              <button key={m} type="button"
                      className={`seg-btn${mode === m ? ' on' : ''}`}
                      aria-pressed={mode === m}
                      onClick={() => setMode(m)}>
                {m === 'line' ? 'Line' : 'Bars'}
              </button>
            ))}
          </div>
        }
      >
        <div style={{ padding: '18px 22px 14px' }}>
          <TrendChart points={collections} mode={mode} />
        </div>
        <Explainer title="Why collections, not calendar weeks">
          <p>
            Each point is one collection, not one calendar week. The pipeline runs weekly
            so the two usually coincide — but when a run is missed, a calendar chart has
            to draw something for the gap, and every option misleads: a zero says nobody
            was hiring, a joined line invents a measurement, and repeating the last value
            states it twice.
          </p>
          <p>
            Movement is measured against the previous collection. Where there is no
            earlier one, the tile says so instead of showing a direction it cannot
            justify.
          </p>
          <p>
            Only classified signals are counted here. A row that has been collected but
            not yet read has no sector or region, so including it would move the totals
            without being able to say where.
          </p>
        </Explainer>
      </Section>

      {/* ---------- what kind of week ---------- */}
      <div className="split-2">
        <Section
          title="What kind of week"
          tools={acting ? <span>{acting.share}% ACTIONABLE</span> : undefined}
        >
          <div style={{ padding: '16px 22px 18px' }}>
            <Composition items={groups} total={latest.total} />
          </div>
          {/* The one judgement on the page, and what it rests on. Collapsed, but
              first in its own card rather than in a note at the foot of the
              page: somebody questioning the number is looking at this panel. */}
          <Explainer title="What counts as a decision point">
            <p>
              <b>This grouping is a judgement, not a measurement.</b> Treating a project or
              a leadership change as a decision point and a vacancy as routine is an
              editorial call about what usually merits a call. The signal categories
              panel below shows what the classifier actually recorded.
            </p>
            <ul className="group-notes">
              {groups.map((g) => (
                <li key={g.key}>
                  <b>{g.label}.</b> {g.what}
                </li>
              ))}
            </ul>
          </Explainer>
        </Section>

        <Section title={`Sectors · ${latest.date}`}>
          <div style={{ padding: '16px 22px 18px' }}>
            <Composition items={sectors} total={latest.total} />
          </div>
        </Section>
      </div>

      {/* ---------- most active companies ---------- */}
      <Section
        title="Most active companies"
        tools={<span>COLLECTION OF {latest.date}</span>}
      >
        {companies.length === 0 ? (
          <div className="center-empty">No company could be identified in this collection.</div>
        ) : (
          <table className="tbl co-table">
            <thead>
              <tr>
                <th>Company</th><th>Sector</th><th>Region</th><th>Relationship</th>
                <th className="num">Signals</th>
              </tr>
            </thead>
            <tbody>
              {companies.map((c) => (
                <tr key={c.name}>
                  <td className="co-cell">
                    {/* The row's purpose is to be followed: a company worth
                        noticing here is one somebody wants the signals for. */}
                    <Link to="/monitor/feed" search={{ q: c.name }} className="co-link">
                      {c.name}
                    </Link>
                  </td>
                  <td className="muted">{c.sector}</td>
                  <td className="muted">{c.region}</td>
                  <td>
                    {c.tier
                      ? <span className="rel-chip client">Tier {c.tier} client</span>
                      : <span className="rel-chip new">{c.isNew ? 'New name' : 'Not on the list'}</span>}
                  </td>
                  <td className="num">
                    <span className="co-bar" style={{
                      width: `${(c.count / Math.max(companies[0]!.count, 1)) * 100}%`,
                    }} />
                    <b className="tnum">{c.count}</b>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {/* ---------- collectors and detail ---------- */}
      <div className="split-2">
        <Section title="Where it came from">
          <div style={{ padding: '16px 22px 18px' }}>
            <Composition
              items={sources.map((s) => ({
                key: s.name, label: s.name, count: s.count, share: s.share,
              }))}
              total={sources.reduce((n, s) => n + s.count, 0)}
            />
          </div>
          <Explainer title="Why this adds up differently">
            <p>
              These count what each source <i>collected</i>, including rows still awaiting
              classification — a row not yet read was still collected by its source, and
              excluding it would understate a scraper that ran perfectly. Every other
              panel counts classified signals only, so this total can be higher.
            </p>
            <p>
              A source missing from this list contributed nothing to the collection, which
              is the quickest way to see a scraper that has quietly stopped working.
              {' '}<Link to="/sources">Source health →</Link>
            </p>
          </Explainer>
        </Section>

        <Section title="Signal categories">
          <div style={{ padding: '16px 22px 18px' }}>
            <Composition items={categories} total={latest.total} />
          </div>
        </Section>
      </div>

    </div>
  )
}
