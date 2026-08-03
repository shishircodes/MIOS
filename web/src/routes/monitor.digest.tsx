import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { Drawer, Icons, Section, SparkBar, TierChip, Trend } from '~/components/ui'
import { digestQueryOptions } from '~/lib/api'
import { UnauthenticatedError } from '~/lib/auth'
import { useAuth } from '~/lib/auth-context'
import type { Signal } from '~/lib/types'

export const Route = createFileRoute('/monitor/digest')({
  component: WeeklyDigest,
})

function WeeklyDigest() {
  const { data, isLoading, isError, error } = useQuery(digestQueryOptions)
  const { refresh } = useAuth()
  const [drawer, setDrawer] = useState<Signal | null>(null)

  if (isLoading) {
    return (
      <div className="page">
        <div className="loading-shimmer">Loading digest from the Python backend…</div>
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
          Could not reach the backend.<br />
          <span style={{ color: 'var(--ink-3)' }}>
            Start it with <code>python -m uvicorn api.server:app --port 8787</code>
          </span>
          <div style={{ marginTop: 10, color: 'var(--crimson)' }}>{String(error)}</div>
        </div>
      </div>
    )
  }

  const au = data.signals.filter((s) => s.region === 'AU')
  const png = data.signals.filter((s) => s.region === 'PNG')

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Mode Monitor · Weekly Intelligence Digest</div>
          <h1>{data.weekLabel}</h1>
        </div>
        <div className="meta">
          <div>Generated {data.generatedAt}</div>
          <div style={{ marginTop: 6, display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
            <span className={`mode-banner ${data.sourceMode}`}>
              <span className={data.sourceMode === 'live' ? 'dot-ok' : 'dot-warn'} />
              {data.sourceMode === 'live' ? 'Live · SQLite pipeline' : 'Synthetic dataset'}
            </span>
          </div>
        </div>
      </div>

      {/* KPI strip */}
      <div className="kpi-row">
        <Kpi label="Roles detected · 7d" kpi={data.kpis.rolesThisWeek} />
        <Kpi label="Key signals" kpi={data.kpis.newSignals} />
        <Kpi label="New Names" kpi={data.kpis.newNames} />
        <Kpi label="Mode Push queries" kpi={data.kpis.pushQueries} />
      </div>

      {/* Key Signals */}
      <Section
        title="Key Signals This Week"
        tools={<span>{data.signals.length} ITEMS</span>}
      >
        {au.length > 0 && (
          <>
            <div className="region-h">
              <span className="flag" style={{ background: '#1A2837' }} />
              <h3>Australia</h3>
              <span className="count">{au.length} signals</span>
            </div>
            {au.map((s) => <SignalRow key={s.id} s={s} onOpen={() => setDrawer(s)} />)}
          </>
        )}
        {png.length > 0 && (
          <>
            <div className="region-h">
              <span className="flag" style={{ background: '#0F6E3D' }} />
              <h3>Papua New Guinea</h3>
              <span className="count">{png.length} signals</span>
            </div>
            {png.map((s) => <SignalRow key={s.id} s={s} onOpen={() => setDrawer(s)} />)}
          </>
        )}
        {data.signals.length === 0 && <div className="center-empty">No classified signals yet.</div>}
      </Section>

      {/* Hiring Velocity */}
      <Section title="Hiring Velocity · Top 10 Watchlist" tools={<span>WEEK / EST AVG / Δ</span>}>
        <table className="tbl">
          <thead>
            <tr>
              <th>Company</th>
              <th>Sector</th>
              <th>Tier</th>
              <th className="num">This wk</th>
              <th className="num">Est avg</th>
              <th className="num">Change</th>
              <th style={{ width: 120 }}>Pattern</th>
            </tr>
          </thead>
          <tbody>
            {data.velocity.map((row, i) => (
              <tr key={i}>
                <td><strong>{row.co}</strong></td>
                <td className="muted">{row.sector}</td>
                <td><TierChip tier={row.tier} /></td>
                <td className="num"><strong>{row.wk}</strong></td>
                <td className="num muted">{row.avg}</td>
                <td className="num">
                  <Trend dir={row.change > 5 ? 'up' : row.change < -5 ? 'down' : 'flat'} value={Math.abs(row.change)} unit="%" />
                </td>
                <td>
                  <SparkBar
                    data={[row.avg, row.avg * 1.1, row.avg * 0.95, row.avg * 1.05, row.wk]}
                    color={row.change > 25 ? 'var(--rust)' : 'var(--ink-3)'}
                    height={20}
                  />
                </td>
              </tr>
            ))}
            {data.velocity.length === 0 && (
              <tr><td colSpan={7} className="muted" style={{ textAlign: 'center', padding: 24 }}>No watchlist activity.</td></tr>
            )}
          </tbody>
        </table>
      </Section>

      {/* New Names */}
      <Section title="New Names · Not in Watchlist" tools={<span>{data.newNames.length} CANDIDATES</span>}>
        <table className="tbl">
          <thead>
            <tr><th>Company</th><th>Signal</th><th>Sector</th><th>Region</th><th>Recommendation</th></tr>
          </thead>
          <tbody>
            {data.newNames.map((n, i) => (
              <tr key={i}>
                <td><strong>{n.co}</strong></td>
                <td className="muted">{n.signal}</td>
                <td className="muted">{n.sector}</td>
                <td><span className="chip">{n.region}</span></td>
                <td><span className="chip new">{n.reco}</span></td>
              </tr>
            ))}
            {data.newNames.length === 0 && (
              <tr><td colSpan={5} className="muted" style={{ textAlign: 'center', padding: 24 }}>No new prospects.</td></tr>
            )}
          </tbody>
        </table>
      </Section>

      <div style={{ textAlign: 'center', padding: '24px 0', fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)', letterSpacing: '0.08em' }}>
        END OF DIGEST · DATA FROM MIOS PYTHON PIPELINE ({data.sourceMode.toUpperCase()})
      </div>

      <Drawer open={!!drawer} onClose={() => setDrawer(null)} title={drawer ? `Signal · ${drawer.id.slice(0, 12)}` : ''}>
        {drawer && <SignalDetail s={drawer} />}
      </Drawer>
    </div>
  )
}

function Kpi({ label, kpi }: { label: string; kpi: { val: number; delta: string; dir: string } }) {
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="val tnum">{kpi.val}</div>
      <div className={`delta ${kpi.dir}`}>
        {kpi.dir === 'up' ? '↑ ' : kpi.dir === 'down' ? '↓ ' : ''}{kpi.delta}
      </div>
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
        Signal Analyst (Gemini) · {s.cycle} · watchlist match via rapidfuzz wRatio
      </p>
      <div style={{ display: 'flex', gap: 8, marginTop: 24 }}>
        <button className="btn primary">{Icons.push} Push to match</button>
        <button className="btn">{Icons.ext} Open source</button>
        <button className="btn ghost">{Icons.archive} Archive</button>
      </div>
    </>
  )
}
