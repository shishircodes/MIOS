import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { Link } from '@tanstack/react-router'
import { useRef } from 'react'
import { Loading, Section } from '~/components/ui'
import { dashboardQueryOptions } from '~/lib/api'
import { useCountUpAll } from '~/lib/motion'
import type { Breakdown, DashboardPayload } from '~/lib/types'

export const Route = createFileRoute('/dashboard')({
  head: () => ({ meta: [{ title: 'Dashboard · MIOS' }] }),
  component: DashboardScreen,
})

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
  return (
    <div className="kpi2">
      <div className="kpi2-label">{label}</div>
      <div className="kpi2-value">
        {/* The number stays alone in .val: the count-up animation rewrites its
            text content, so anything sharing the node would be overwritten. */}
        <span className="val tnum">{value}</span>
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

/** Two series over the same collections, drawn together.
 *
 *  One chart rather than two stacked ones: the question is whether the two
 *  markets move together, and that cannot be read from charts with independent
 *  vertical scales sitting one above the other.
 */
function TrendChart({ data }: { data: DashboardPayload }) {
  const points = data.collections
  if (points.length < 2) {
    return (
      <div className="center-empty">
        One collection so far. A trend needs at least two — the next weekly run
        will give this a shape.
      </div>
    )
  }

  const w = 720
  const h = 190
  const pad = { l: 34, r: 12, t: 12, b: 24 }
  const max = Math.max(...points.map((p) => Math.max(p.au, p.png)), 1)
  const x = (i: number) => pad.l + (i * (w - pad.l - pad.r)) / (points.length - 1)
  const y = (v: number) => pad.t + (1 - v / max) * (h - pad.t - pad.b)
  const line = (get: (p: typeof points[number]) => number) =>
    points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(get(p)).toFixed(1)}`).join(' ')

  // Four gridlines, at round fractions of the maximum rather than round
  // numbers: the scale is signals per collection, which has no natural unit.
  const grid = [0, 0.25, 0.5, 0.75, 1].map((f) => ({ f, v: Math.round(max * f) }))

  return (
    <div className="trend">
      <svg viewBox={`0 0 ${w} ${h}`} className="trend-svg" role="img"
           aria-label={`Signals per collection, Australia and Papua New Guinea, ${points.length} collections`}>
        {grid.map(({ f, v }) => (
          <g key={f}>
            <line x1={pad.l} x2={w - pad.r} y1={y(max * f)} y2={y(max * f)}
                  stroke="var(--line)" strokeWidth="1" />
            <text x={pad.l - 6} y={y(max * f) + 3} textAnchor="end" className="trend-tick">{v}</text>
          </g>
        ))}
        <path d={`${line((p) => p.au)} L${x(points.length - 1)},${y(0)} L${x(0)},${y(0)} Z`}
              fill="var(--teal)" opacity="0.10" />
        <path d={line((p) => p.au)} fill="none" stroke="var(--teal-2)" strokeWidth="2"
              strokeLinejoin="round" strokeLinecap="round" />
        <path d={line((p) => p.png)} fill="none" stroke="var(--moss)" strokeWidth="2"
              strokeLinejoin="round" strokeLinecap="round" strokeDasharray="5 3" />
        {points.map((p, i) => (
          <g key={p.date}>
            <circle cx={x(i)} cy={y(p.au)} r="2.6" fill="var(--teal-2)" />
            <circle cx={x(i)} cy={y(p.png)} r="2.6" fill="var(--moss)" />
            {/* Every point gets a hit area and a native tooltip: a hover
                readout that only works with a mouse would leave the figures
                unreachable on a tablet, which is what the BD team uses. */}
            <rect x={x(i) - 12} y={pad.t} width="24" height={h - pad.t - pad.b}
                  fill="transparent">
              <title>{`${p.date} — ${p.au} Australia, ${p.png} Papua New Guinea`}</title>
            </rect>
          </g>
        ))}
      </svg>
      <div className="trend-axis">
        <span>{points[0]!.date}</span>
        <span>{points[points.length - 1]!.date}</span>
      </div>
      <div className="legend">
        <span className="legend-item"><i className="swatch" style={{ background: 'var(--teal-2)' }} />Australia</span>
        <span className="legend-item"><i className="swatch dashed" style={{ background: 'var(--moss)' }} />Papua New Guinea</span>
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
  if (!items.length) return <div className="muted comp-empty">Nothing classified in this collection.</div>
  return (
    <div className="comp">
      <div className="comp-bar" role="img"
           aria-label={items.map((s) => `${s.label} ${s.count}`).join(', ')}>
        {items.map((s, i) => (
          // `title` as an attribute, not a <title> child. A <title> element is
          // only valid in <head> or inside SVG; nested in a span the browser
          // hoists it and it replaces the document title, which turned the
          // browser tab into "Leadership: 2 of 80".
          <span key={s.key} className="comp-slice"
                title={`${s.label}: ${s.count} of ${total}`}
                style={{ width: `${(s.count / Math.max(total, 1)) * 100}%`, background: sliceColour(i) }} />
        ))}
      </div>
      <ul className="comp-legend">
        {items.map((s, i) => (
          <li key={s.key}>
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

function DashboardScreen() {
  const { data, isPending, error } = useQuery(dashboardQueryOptions)
  const scope = useRef<HTMLDivElement>(null)

  // Before the early returns: hooks cannot be called conditionally.
  useCountUpAll(scope, '.kpi2 .val', data?.latest?.date ?? 'none', { delay: 0.1, stagger: 0.04 })

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

  const auPoints = collections.map((c) => c.au)
  const pngPoints = collections.map((c) => c.png)
  const totalPoints = collections.map((c) => c.total)
  const acting = groups.find((g) => g.key === 'acting')

  return (
    <div className="page" ref={scope}>
      <div className="page-header">
        <div>
          <div className="kicker">Overview</div>
          <h1>What the last collection found</h1>
        </div>
        <div className="meta">
          <div>
            {coverage.collections} collection{coverage.collections === 1 ? '' : 's'}
            {coverage.collections > trendWindow && ` · last ${trendWindow} charted`}
          </div>
          <div style={{ marginTop: 4 }}>{coverage.from} to {coverage.to}</div>
        </div>
      </div>

      {/* ---------- the hero band ---------- */}
      <div className="hero">
        <div className="hero-main">
          <div className="hero-label">Signals · collection of {latest.date}</div>
          <div className="hero-figure">
            <span className="val tnum">{latest.total}</span>
            <Delta pct={change.total} />
          </div>
          <div className="hero-split">
            <span><i className="swatch" style={{ background: 'var(--teal-2)' }} />
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
        tools={<span>{collections.length} POINTS</span>}
      >
        <div style={{ padding: '18px 22px 14px' }}>
          <TrendChart data={data} />
        </div>
      </Section>

      {/* ---------- what kind of week ---------- */}
      <div className="split-2">
        <Section
          title="What kind of week"
          tools={acting ? <span>{acting.share}% ACTIONABLE</span> : undefined}
        >
          <div style={{ padding: '16px 22px 18px' }}>
            <Composition items={groups} total={latest.total} />
            <ul className="group-notes">
              {groups.map((g) => (
                <li key={g.key}>
                  <b>{g.label}.</b> {g.what}
                </li>
              ))}
            </ul>
          </div>
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
                  <td className="co-cell">{c.name}</td>
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
            <p className="drawer-lede" style={{ marginTop: 12, marginBottom: 0 }}>
              A source missing from this list contributed nothing to the collection.
              <Link to="/sources"> Source health →</Link>
            </p>
          </div>
        </Section>

        <Section title="Signal categories">
          <div style={{ padding: '16px 22px 18px' }}>
            <Composition items={categories} total={latest.total} />
          </div>
        </Section>
      </div>

      <Section title="How to read this">
        <div className="explainer-body" style={{ padding: '16px 22px 18px' }}>
          <p>
            Each point on the chart is one collection, not one calendar week. The pipeline
            runs weekly so the two usually coincide — but when a run is missed, a calendar
            chart has to draw something for the gap, and every option misleads: a zero says
            nobody was hiring, a joined line invents a measurement, and repeating the last
            value states it twice.
          </p>
          <p>
            Movement is measured against the previous collection. Where there is no earlier
            one, the tile says so instead of showing a direction.
          </p>
          <p>
            Only classified signals are counted, except under “Where it came from”,
            which counts what each source collected — a row awaiting classification was
            still collected. A classified row has no sector or region until it is read, so
            including it elsewhere would move the totals without being able to say where.
          </p>
          <p>
            <b>“What kind of week” is a judgement, not a measurement.</b> Grouping a
            project or a leadership change as a decision point and a vacancy as routine is
            an editorial call about what usually merits a call. The categories beneath it
            are what the classifier actually recorded.
          </p>
        </div>
      </Section>
    </div>
  )
}
