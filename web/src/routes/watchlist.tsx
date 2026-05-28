import { createFileRoute } from '@tanstack/react-router'
import { Section, TierChip, Trend } from '~/components/ui'
import { watchlist } from '~/data/mock'

export const Route = createFileRoute('/watchlist')({
  component: WatchlistScreen,
})

function WatchlistScreen() {
  const tiers = ['A', 'B', 'C'] as const
  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Reference · Client Watchlist</div>
          <h1>Watchlist — 20 companies</h1>
        </div>
        <div className="meta">
          <div>10 Tier A · 7 Tier B · 3 Tier C</div>
          <div style={{ marginTop: 4 }}>Synced from HubSpot pipeline stages</div>
        </div>
      </div>

      {tiers.map((t) => {
        const rows = watchlist.filter((w) => w.tier === t)
        const label = t === 'A' ? 'Active clients' : t === 'B' ? 'Target prospects' : 'Market indicators'
        return (
          <Section key={t} title={`Tier ${t} · ${label}`} tools={<span>{rows.length} COMPANIES</span>}>
            <table className="tbl">
              <thead>
                <tr><th>Company</th><th>Sector</th><th>Region</th><th className="num">Roles · 30d</th><th>Trend</th><th>Note</th></tr>
              </thead>
              <tbody>
                {rows.map((w) => (
                  <tr key={w.co}>
                    <td><strong>{w.co}</strong> <TierChip tier={w.tier} /></td>
                    <td className="muted">{w.sector}</td>
                    <td><span className="chip">{w.region}</span></td>
                    <td className="num"><strong>{w.monthRoles}</strong></td>
                    <td><Trend dir={w.trend} value={w.trend === 'flat' ? '' : ''} /></td>
                    <td className="muted">{w.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
        )
      })}
    </div>
  )
}
