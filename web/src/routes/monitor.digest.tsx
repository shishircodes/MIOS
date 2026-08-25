import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import { CapturedAt, Drawer, Icons, Loading, Section, SparkBar, TierChip, Trend } from '~/components/ui'
import { digestQueryOptions } from '~/lib/api'
import { UnauthenticatedError } from '~/lib/auth'
import { useAuth } from '~/lib/auth-context'
import { useFigure, useGrowBar, useGrowBars, useReveal } from '~/lib/motion'
import type { Collection, MarketPulse, Signal, VelocityRow } from '~/lib/types'

/** Says what the "Change" column is measured against, so the reader is never
 *  left to assume a comparison that the data cannot support. */
function baselineLabel(velocity: VelocityRow[]): string {
  const basis = velocity[0]?.basis ?? 0
  if (basis === 0) return 'WEEK / NO BASELINE YET'
  return `WEEK / AVG OF PRIOR ${basis} / Δ`
}

/** Signals rendered before the reader has to ask for more. Twelve fills a
 *  screen without burying the rail beside it. */
const PREVIEW_COUNT = 12

export const Route = createFileRoute('/monitor/digest')({
  // `?q=` lets the topbar search deep-link into this page's filter, so the
  // global box does something real instead of decorating the header.
  //
  // The key is omitted rather than set to undefined: returning `{ q: undefined }`
  // types the param as required, and every existing `<Link to="/monitor/digest">`
  // then fails to compile for want of a `search` prop.
  validateSearch: (search: Record<string, unknown>): { q?: string } =>
    typeof search.q === 'string' && search.q ? { q: search.q } : {},
  head: () => ({ meta: [{ title: 'Weekly Digest · MIOS' }] }),
  component: WeeklyDigest,
})

function WeeklyDigest() {
  const { data, isLoading, isError, error } = useQuery(digestQueryOptions)
  const { refresh } = useAuth()
  const [drawer, setDrawer] = useState<Signal | null>(null)
  const { q: urlQuery } = Route.useSearch()
  const [query, setQuery] = useState(urlQuery ?? '')
  const [showAll, setShowAll] = useState(false)

  // Follow the URL when the topbar search navigates here while already on the page.
  useEffect(() => {
    setQuery(urlQuery ?? '')
  }, [urlQuery])

  // Same rule as `matched` below: these run before any early return, so they
  // tolerate `data` being undefined while the query is in flight. Keyed on what
  // is being shown, so filtering replays the reveal but an unrelated re-render
  // does not. Both are capped inside the hooks — forty rows on the compositor
  // at once costs more than the effect is worth.
  const mainRef = useRef<HTMLDivElement>(null)
  const railRef = useRef<HTMLElement>(null)
  useReveal(mainRef, '.signal', {
    key: `${query}-${showAll}-${data?.signals.length ?? 0}`,
    delay: 0.1,
  })
  useReveal(railRef, '.vel-item', { key: data?.velocity.length ?? 0, delay: 0.2, stagger: 0.04 })
  useGrowBars(railRef, '.vel-rail > span', data?.velocity.length ?? 0, { delay: 0.3 })
  // New Names sits below the velocity chart in the same rail, so it starts a
  // little later — the eye should reach it after, not alongside.
  useReveal(railRef, '.nn-item', { key: data?.newNames.length ?? 0, delay: 0.4, stagger: 0.05 })

  // Hooks must run before any early return, so this tolerates `data` being
  // undefined while the query is still in flight.
  const matched = useMemo(() => {
    const all = data?.signals ?? []
    const q = query.trim().toLowerCase()
    if (!q) return all
    // Every term must appear somewhere in the row, so "bhp mining" narrows
    // rather than widening the way a plain OR would.
    const terms = q.split(/\s+/)
    return all.filter((s) => {
      const haystack = [s.company, s.title, s.desc, s.sector, s.region, s.source, s.tier ?? '']
        .join(' ')
        .toLowerCase()
      return terms.every((t) => haystack.includes(t))
    })
  }, [data?.signals, query])

  if (isLoading) {
    return (
      <div className="page">
        <Loading
          lines={[
            'Reading this week’s market…',
            'Sorting signal from noise…',
            'Lining up the numbers…',
          ]}
        />
      </div>
    )
  }

  if (isError || !data) {
    // A 401 here means the session expired while the tab was open. Re-checking
    // the session flips AuthGate back to the sign-in screen.
    if (error instanceof UnauthenticatedError) {
      return (
        <div className="page">
          <div className="center-empty">
            Your session has expired.
            <div style={{ marginTop: 12 }}>
              <button className="btn" onClick={() => void refresh()}>Sign in again</button>
            </div>
          </div>
        </div>
      )
    }
    return (
      <div className="page">
        <div className="center-empty">
          MIOS can’t reach its data service right now.<br />
          <span style={{ color: 'var(--ink-3)' }}>Try again in a moment.</span>
          {/* The recovery command is the only thing that helps when this happens
              locally, but it means nothing to the BD team. Folded away rather
              than removed, so both readers are served. */}
          <details className="tech-detail">
            <summary>Technical details</summary>
            <p>
              Start the service with <code>python -m uvicorn api.server:app --port 8787</code>
            </p>
            <p className="tech-detail-err">{String(error)}</p>
          </details>
        </div>
      </div>
    )
  }

  // Split the *filtered* set, so the region headings and counts agree with
  // what the search actually left on screen.
  const visible = showAll ? matched : matched.slice(0, PREVIEW_COUNT)
  const hidden = matched.length - visible.length
  const au = visible.filter((s) => s.region === 'AU')
  const png = visible.filter((s) => s.region === 'PNG')

  // Bars are scaled against the busiest company, so the rail reads as a
  // comparison rather than ten unrelated numbers.
  const velocityMax = data.velocity.reduce((m, r) => Math.max(m, r.wk), 0)


  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Mode Monitor · Weekly Intelligence Digest</div>
          <h1>{data.weekLabel}</h1>
        </div>
        <div className="meta">
          <div style={{ marginTop: 6, display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
            <span className={`mode-banner ${data.sourceMode}`}>
              <span className={data.sourceMode === 'live' ? 'dot-ok' : 'dot-warn'} />
              {data.sourceMode === 'live' ? 'Live data' : 'Sample data'}
            </span>
          </div>
        </div>
      </div>

      {/* Nothing captured in the window — say so rather than passing older
          signals off as this week's. */}
      {data.windowEmpty && (
        <div className="window-notice">
          <strong>Nothing new in the last {data.windowDays} days.</strong>{' '}
          Showing the most recent collection instead — {data.weekLabel}.
        </div>
      )}

      <CollectionBand
        c={data.collection}
        windowDays={data.windowDays}
        windowEmpty={data.windowEmpty}
      />

      {/* Omitted entirely when the week produced none — see delivery/pulse.py.
          Nothing computed is substituted in its place. */}
      {data.marketPulse && <MarketPulseSection pulse={data.marketPulse} />}

      {/* Signals on the left, the week's measurements alongside them on the
          right. Previously everything was one column, so Hiring Velocity and
          New Names sat below forty signal rows — the numbers that answer "what
          changed?" were the last thing anyone saw, if they scrolled at all. */}
      <div className="digest-grid">
        <div className="digest-main" ref={mainRef}>
          <Section
            title="Key Signals This Week"
            tools={
              <div className="section-search">
                <input
                  type="search"
                  value={query}
                  placeholder="Filter by company, role, sector…"
                  aria-label="Filter key signals"
                  onChange={(e) => setQuery(e.target.value)}
                />
                <span className="count mono">
                  {query ? `${matched.length} / ${data.signals.length}` : `${data.signals.length} ITEMS`}
                </span>
              </div>
            }
          >
            {au.length > 0 && (
              <>
                <div className="region-h">
                  <span className="flag" style={{ background: 'var(--ink)' }} />
                  <h3>Australia</h3>
                  <span className="count">{au.length} shown</span>
                </div>
                {au.map((s) => <SignalRow key={s.id} s={s} onOpen={() => setDrawer(s)} />)}
              </>
            )}
            {png.length > 0 && (
              <>
                <div className="region-h">
                  <span className="flag" style={{ background: 'var(--moss)' }} />
                  <h3>Papua New Guinea</h3>
                  <span className="count">{png.length} shown</span>
                </div>
                {png.map((s) => <SignalRow key={s.id} s={s} onOpen={() => setDrawer(s)} />)}
              </>
            )}
            {matched.length === 0 && (
              <div className="center-empty">
                {query
                  ? <>No signals match “{query}”.{' '}
                      <button className="btn sm" onClick={() => setQuery('')}>Clear filter</button>
                    </>
                  : 'No signals collected yet.'}
              </div>
            )}
            {/* Forty rows is a wall. Showing a readable slice first, with the
                rest one click away, keeps the page scannable without hiding
                anything. */}
            {hidden > 0 && (
              <div className="feed-more">
                <span className="muted">
                  Showing {visible.length} of {matched.length}
                  {query ? ' matching signals' : ' signals'}
                </span>
                <button className="btn sm" onClick={() => setShowAll(true)}>
                  Show all {matched.length}
                </button>
              </div>
            )}
            {showAll && matched.length > PREVIEW_COUNT && (
              <div className="feed-more">
                <span className="muted">Showing all {matched.length}</span>
                <button className="btn sm" onClick={() => setShowAll(false)}>Show fewer</button>
              </div>
            )}
          </Section>
        </div>

        <aside className="digest-rail" aria-label="Measurements for this week" ref={railRef}>
          <Section
            title="Hiring Velocity"
            tools={<span>TOP {data.velocity.length}</span>}
          >
            {data.velocity.length === 0 ? (
              <div className="center-empty" style={{ padding: 28 }}>No watchlist activity.</div>
            ) : (
              <>
                <div className="vel-list">
                  {data.velocity.map((row, i) => (
                    <VelocityItem key={i} row={row} max={velocityMax} />
                  ))}
                </div>
                <p className="rail-foot">{baselineLabel(data.velocity)}</p>
              </>
            )}
          </Section>

          <Section title="New Names" tools={<span>{data.newNames.length} FOUND</span>}>
            {data.newNames.length === 0 ? (
              <div className="center-empty" style={{ padding: 28 }}>No new prospects.</div>
            ) : (
              <div className="nn-list">
                {data.newNames.map((n, i) => (
                  <div className="nn-item" key={i}>
                    <div className="nn-head">
                      <strong>{n.co}</strong>
                      <span className="chip">{n.region}</span>
                    </div>
                    <p className="nn-sig">{n.signal}</p>
                    <div className="nn-foot">
                      <span className="muted">{n.sector}</span>
                      <span className="chip teal">{n.reco}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <p className="rail-foot">Not on the watchlist — review before adding.</p>
          </Section>
        </aside>
      </div>

      <div style={{ textAlign: 'center', padding: '24px 0', fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)', letterSpacing: '0.08em' }}>
        END OF DIGEST · {data.sourceMode === 'live' ? 'LIVE DATA' : 'SAMPLE DATA'}
        {data.sourceMode === 'live' && ` · ${data.windowEmpty ? 'MOST RECENT COLLECTION' : `LAST ${data.windowDays} DAYS`}`}
      </div>

      <Drawer open={!!drawer} onClose={() => setDrawer(null)} title={drawer ? `Signal · ${drawer.id.slice(0, 12)}` : ''}>
        {drawer && <SignalDetail s={drawer} />}
      </Drawer>
    </div>
  )
}

/**
 * What was collected this week, as one connected statement rather than four
 * separate tiles.
 *
 * The tiles it replaced invited fabrication: each needed a headline number and
 * a delta, so two ended up showing the same variable, one carried an upward
 * arrow with nothing measured behind it, and Mode Push reported a hardcoded
 * zero. These figures are nested — collected, of which shown — which is what
 * they always were.
 */
/**
 * The week's written read, the one section a model writes rather than counts.
 *
 * Interpretation bullets are labelled. The model is allowed to reason past the
 * figures — "suggests shutdown preparation" — but a consultant deciding who to
 * call is entitled to see which bullets are measured and which are a reading of
 * them, so the distinction is carried into the UI rather than flattened here.
 */
function MarketPulseSection({ pulse }: { pulse: MarketPulse }) {
  const scope = useRef<HTMLDivElement>(null)
  useReveal(scope, '.pulse-item', { key: pulse.generatedAt, delay: 0.15, stagger: 0.06 })

  return (
    <div className="pulse" ref={scope}>
      <div className="pulse-head">
        <span className="kicker">Market Pulse</span>
        <span className="muted">
          Written from {pulse.signalsAnalysed.toLocaleString()} signals
        </span>
      </div>
      <ul className="pulse-list">
        {pulse.bullets.map((b, i) => (
          <li key={i} className={`pulse-item ${b.kind}`}>
            <span className="pulse-text">{b.text}</span>
            {b.kind === 'interpretation' && (
              <span
                className="pulse-tag"
                title="A reading of the data, not a measurement from it"
              >
                interpretation
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function CollectionBand({
  c,
  windowDays,
  windowEmpty,
}: {
  c: Collection
  windowDays: number
  windowEmpty: boolean
}) {
  const scope = useRef<HTMLDivElement>(null)
  const auFill = useRef<HTMLSpanElement>(null)
  const pngFill = useRef<HTMLSpanElement>(null)

  const total = useFigure(c.collected)
  const jobs = useFigure(c.jobs, { delay: 0.15 })
  const news = useFigure(c.news, { delay: 0.2 })
  const shown = useFigure(c.shown, { delay: 0.25 })
  const names = useFigure(c.newNames, { delay: 0.3 })

  // Both segments grow from their own leading edge, PNG a beat later, so the
  // bar reads as filling left to right rather than splitting apart.
  useGrowBar(auFill, c.regions.AU)
  useGrowBar(pngFill, c.regions.PNG, { delay: 0.28 })
  useReveal(scope, '.collection-facts li', { key: c.collected, delay: 0.35 })

  const pct = (n: number) => (c.collected ? (n / c.collected) * 100 : 0)
  const auShare = pct(c.regions.AU)
  const pngShare = 100 - auShare

  return (
    <div className="collection" ref={scope}>
      <div className="collection-lead">
        <div className="kicker">
          {windowEmpty ? 'Most recent collection' : `Collected over ${windowDays} days`}
        </div>
        <div className="collection-figure">
          {/* Starts at the real value so it is correct before hydration and if
              JavaScript never runs; the count-up overwrites it either way. */}
          <strong className="tnum" ref={total}>
            {c.collected}
          </strong>
          <span>
            signals from {c.sources} source{c.sources === 1 ? '' : 's'}
          </span>
        </div>
        <div className="collection-kinds">
          <span className="tnum" ref={jobs}>
            {c.jobs}
          </span>{' '}
          job postings ·{' '}
          <span className="tnum" ref={news}>
            {c.news}
          </span>{' '}
          news articles
        </div>
      </div>

      <div className="collection-split">
        {/* Labels sit above their own segment and share its width, so nothing
            has to fit inside a fill that may be narrow. */}
        <div className="split-labels" aria-hidden="true">
          <span style={{ width: `${auShare}%` }}>
            <em>Australia</em>
            <b className="tnum">{c.regions.AU}</b>
            <i className="tnum">{Math.round(auShare)}%</i>
          </span>
          <span style={{ width: `${pngShare}%` }}>
            <em>Papua New Guinea</em>
            <b className="tnum">{c.regions.PNG}</b>
            <i className="tnum">{Math.round(pngShare)}%</i>
          </span>
        </div>

        <div
          className="split-bar"
          role="img"
          aria-label={`Australia ${c.regions.AU} signals, ${Math.round(auShare)} percent. Papua New Guinea ${c.regions.PNG} signals, ${Math.round(pngShare)} percent.`}
        >
          {/* The outer span holds the layout width; the inner fill is what
              scales, so the growth never triggers a reflow. */}
          <span className="seg au" style={{ width: `${auShare}%` }}>
            <span className="fill" ref={auFill} />
          </span>
          <span className="seg png" style={{ width: `${pngShare}%` }}>
            <span className="fill" ref={pngFill} />
          </span>
        </div>
      </div>

      <ul className="collection-facts">
        <li>
          <b className="tnum" ref={shown}>
            {c.shown}
          </b>
          <span>ranked as key signals below</span>
        </li>
        <li>
          <b className="tnum" ref={names}>
            {c.newNames}
          </b>
          <span>companies not yet on the watchlist</span>
        </li>
      </ul>
    </div>
  )
}


function SignalRow({ s, onOpen }: { s: Signal; onOpen: () => void }) {
  return (
    <div className="signal" onClick={onOpen} style={{ cursor: 'pointer' }}>
      <div className="num mono">{s.n}</div>
      <div className="body">
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
          <TierChip tier={s.tier} />
          <span className="chip">{s.sector.toUpperCase()}</span>
          <span className="chip">{s.cycle}</span>
        </div>
        <p className="title">{s.company} — {s.title}</p>
        <p className="desc">{s.desc}</p>
        {s.action && <div className="action">→ {s.action}</div>}
      </div>
      <div className="meta-col">
        <div>conf {s.conf}</div>
        <div style={{ color: 'var(--ink-3)' }}>{s.source}</div>
        <CapturedAt at={s.capturedAt} />
      </div>
    </div>
  )
}

function SignalDetail({ s }: { s: Signal }) {
  const tag = (label: string) => (
    <div style={{ fontFamily: 'var(--mono)', fontSize: 10.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: 6, marginTop: 18 }}>
      {label}
    </div>
  )
  return (
    <>
      <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
        <TierChip tier={s.tier} />
        <span className="chip">{s.sector}</span>
        <span className="chip">{s.region}</span>
        <span className="chip">{s.cycle}</span>
      </div>
      <h2 style={{ fontSize: 20, margin: '0 0 6px', fontWeight: 500 }}>{s.company}</h2>
      <p style={{ fontSize: 14, color: 'var(--ink-2)', marginTop: 0 }}>{s.title}</p>
      <div className="hr" />
      <div className="row-2" style={{ marginBottom: 4, fontSize: 12 }}>
        <div>
          <div className="muted" style={{ fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Source</div>
          <div style={{ fontWeight: 500, marginTop: 4 }}>{s.source}</div>
        </div>
        <div>
          <div className="muted" style={{ fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Confidence</div>
          <div style={{ fontFamily: 'var(--mono)', fontWeight: 500, marginTop: 4 }}>{s.conf}/100</div>
        </div>
      </div>
      {tag('Detection')}
      <p style={{ fontSize: 13, color: 'var(--ink-2)' }}>{s.desc}</p>
      {s.action && (
        <>
          {tag('Recommended action / analyst note')}
          <div className="pull">{s.action}</div>
        </>
      )}
      {tag('Classified by')}
      <p style={{ fontSize: 12.5, color: 'var(--ink-3)', fontFamily: 'var(--mono)' }}>
        Reviewed automatically · {s.cycle} review cycle
      </p>
      <div style={{ display: 'flex', gap: 8, marginTop: 24 }}>
        <button className="btn primary">{Icons.push} Push to match</button>
        <button className="btn">{Icons.ext} Open source</button>
        <button className="btn ghost">{Icons.archive} Archive</button>
      </div>
    </>
  )
}

/** One company in the rail: how much they are hiring, how that compares with
 *  their own recent norm, and how they rank against the others on screen. */
function VelocityItem({ row, max }: { row: VelocityRow; max: number }) {
  // A floor of 3% keeps a company with one signal visible as a mark rather
  // than an empty rail that reads as "no data".
  const pct = max > 0 ? Math.max(3, Math.round((row.wk / max) * 100)) : 0
  return (
    <div className="vel-item">
      <div className="vel-head">
        <span className="co">{row.co}</span>
        <span className="wk mono tnum">{row.wk}</span>
      </div>
      <div className="vel-rail"><span style={{ width: `${pct}%` }} /></div>
      <div className="vel-meta">
        <TierChip tier={row.tier} />
        <span className="sector">{row.sector}</span>
        <span className="vel-chg">
          {row.change === null
            ? <span className="muted">{row.basis === 0 ? 'no baseline' : 'new'}</span>
            : <Trend
                dir={row.change > 5 ? 'up' : row.change < -5 ? 'down' : 'flat'}
                value={Math.abs(row.change)}
                unit="%"
              />}
          {row.trend.length > 1 && (
            <SparkBar data={row.trend} color="var(--teal)" height={14} />
          )}
        </span>
      </div>
    </div>
  )
}
