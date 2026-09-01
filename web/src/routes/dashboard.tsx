import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useRef } from 'react'
import { BarBlock, Loading, Section, SparkBar } from '~/components/ui'
import { dashboardQueryOptions } from '~/lib/api'
import { useCountUpAll } from '~/lib/motion'

export const Route = createFileRoute('/dashboard')({
  head: () => ({ meta: [{ title: 'Dashboard · MIOS' }] }),
  component: DashboardScreen,
})

/** A movement, or nothing.
 *
 *  `null` means there was no earlier collection to compare against, and the
 *  page shows nothing rather than a delta it cannot justify. The version this
 *  replaces printed "↑ trending" on every tile regardless of what had happened.
 */
function Delta({ pct }: { pct: number | null | undefined }) {
  if (pct === null || pct === undefined) {
    return <div className="delta flat">no earlier collection to compare</div>
  }
  if (pct === 0) return <div className="delta flat">level on the previous collection</div>
  const up = pct > 0
  return (
    <div className={`delta ${up ? 'up' : 'down'}`}>
      {up ? '↑' : '↓'} {Math.abs(pct)}% on the previous collection
    </div>
  )
}

function DashboardScreen() {
  const { data, isPending, error } = useQuery(dashboardQueryOptions)
  const scope = useRef<HTMLDivElement>(null)

  // Before the early returns: hooks cannot be called conditionally.
  useCountUpAll(scope, '.kpi .val', data?.coverage.collections ?? 0, { delay: 0.1, stagger: 0.05 })

  if (isPending) {
    return (
      <div className="page">
        <Loading lines={['Counting what each collection found', 'Lining up the weeks']} />
      </div>
    )
  }
  if (error) {
    return (
      <div className="page">
        <div className="notice err">Could not load trends. {(error as Error).message}</div>
      </div>
    )
  }

  const { collections, latest, change, sectors, watchlist, coverage, trendWindow } = data

  if (!latest || collections.length === 0) {
    return (
      <div className="page">
        <div className="page-header">
          <div>
            <div className="kicker">Reference · Trends</div>
            <h1>Hiring trends</h1>
          </div>
        </div>
        <div className="center-empty">
          Nothing has been collected and classified yet, so there is no trend to show.
        </div>
      </div>
    )
  }

  const maxSector = Math.max(...sectors.map((s) => s.count), 1)
  // The empty case returned above, so there is a first point; TypeScript cannot
  // see that through the array index.
  const first = collections[0]!
  const tiers = Object.entries(watchlist.byTier)

  return (
    <div className="page" ref={scope}>
      <div className="page-header">
        <div>
          <div className="kicker">Reference · Trends</div>
          <h1>Hiring trends across collections</h1>
        </div>
        <div className="meta">
          {/* What the charts actually stand on. The page this replaces promised
              twelve weeks and drew twelve whatever it had. */}
          <div>
            {coverage.collections} collection{coverage.collections === 1 ? '' : 's'}
            {coverage.collections > trendWindow && ` · last ${trendWindow} shown`}
          </div>
          <div style={{ marginTop: 4 }}>{coverage.from} to {coverage.to}</div>
        </div>
      </div>

      <div className="kpi-row">
        <div className="kpi">
          <div className="label">Australia · latest collection</div>
          <div className="val tnum">{latest.au}</div>
          <Delta pct={change.au} />
        </div>
        <div className="kpi">
          <div className="label">Papua New Guinea · latest</div>
          <div className="val tnum">{latest.png}</div>
          <Delta pct={change.png} />
        </div>
        <div className="kpi">
          <div className="label">Signals · latest collection</div>
          <div className="val tnum">{latest.total}</div>
          <Delta pct={change.total} />
        </div>
        <div className="kpi">
          <div className="label">Watchlist companies</div>
          <div className="val tnum">{watchlist.total}</div>
          <div className="delta flat">
            {tiers.length ? tiers.map(([t, n]) => `${n} tier ${t}`).join(' · ') : 'none recorded'}
          </div>
        </div>
      </div>

      <Section
        title="Australia — signals per collection"
        tools={<span>{collections.length} POINTS</span>}
      >
        <div style={{ padding: '20px 22px' }}>
          <SparkBar data={collections.map((c) => c.au)} color="var(--ink)" height={80} fill />
          <div className="spark-axis">
            <span>{first.date}</span>
            <span>{latest.date}</span>
          </div>
        </div>
      </Section>

      <Section
        title="Papua New Guinea — signals per collection"
        tools={<span>{collections.length} POINTS</span>}
      >
        <div style={{ padding: '20px 22px' }}>
          <SparkBar data={collections.map((c) => c.png)} color="var(--moss)" height={80} fill />
          <div className="spark-axis">
            <span>{first.date}</span>
            <span>{latest.date}</span>
          </div>
        </div>
      </Section>

      <Section title={`Sectors — collection of ${latest.date}`}>
        <div style={{ padding: '16px 22px' }}>
          {sectors.length === 0 ? (
            <div className="muted">Nothing classified in this collection.</div>
          ) : (
            sectors.map((s) => (
              <BarBlock key={s.key} label={s.label} value={s.count} max={maxSector} />
            ))
          )}
        </div>
      </Section>

      <Section title="How to read this">
        <div className="prose-note">
          <p>
            Each point is one collection, not one calendar week. The pipeline runs weekly so
            the two usually coincide — but when a run is missed, a calendar chart has to draw
            something for the gap, and every option misleads: a zero says nobody was hiring,
            a joined line invents a measurement, and repeating the last value states it twice.
            A series of collections says what it is — this is what we found each time we
            looked.
          </p>
          <p>
            Movement is measured against the previous collection. Where there is no earlier
            one, the tile says so instead of showing a direction.
          </p>
          <p>
            Only classified signals are counted. A row that has been collected but not yet
            read has no sector or region, so including it would move the totals without being
            able to say where.
          </p>
        </div>
      </Section>
    </div>
  )
}
