import { useEffect, useMemo, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { Section, TierChip } from '~/components/ui'

type WatchlistCompany = {
  company_name: string
  tier: 'A' | 'B' | 'C'
  sector: string | null
  notes: string | null
  aliases: string[]
}

type WatchlistResponse = {
  total: number
  companies: WatchlistCompany[]
}

export const Route = createFileRoute('/watchlist')({
  head: () => ({ meta: [{ title: 'Watchlist · MIOS' }] }),
  component: WatchlistScreen,
})

function WatchlistScreen() {
  const [watchlist, setWatchlist] = useState<WatchlistCompany[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const tiers = ['A', 'B', 'C'] as const

  useEffect(() => {
    async function loadWatchlist() {
      try {
        setLoading(true)

        const response = await fetch('http://127.0.0.1:8788/api/watchlist', {
          credentials: 'include',
        })

        if (!response.ok) {
          throw new Error(`Watchlist request failed: ${response.status}`)
        }

        const data: WatchlistResponse = await response.json()
        setWatchlist(data.companies)
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load watchlist')
      } finally {
        setLoading(false)
      }
    }

    loadWatchlist()
  }, [])

  const tierCounts = useMemo(
    () => ({
      A: watchlist.filter((w) => w.tier === 'A').length,
      B: watchlist.filter((w) => w.tier === 'B').length,
      C: watchlist.filter((w) => w.tier === 'C').length,
    }),
    [watchlist],
  )

  if (loading) {
    return (
      <div className="page">
        <div className="page-header">
          <div>
            <div className="kicker">Reference · Client Watchlist</div>
            <h1>Loading watchlist...</h1>
          </div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="page">
        <div className="page-header">
          <div>
            <div className="kicker">Reference · Client Watchlist</div>
            <h1>Unable to load watchlist</h1>
            <div className="muted">{error}</div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Reference · Client Watchlist</div>
          <h1>Watchlist — {watchlist.length} companies</h1>
        </div>

        <div className="meta">
          <div>
            {tierCounts.A} Tier A · {tierCounts.B} Tier B · {tierCounts.C} Tier C
          </div>
          <div style={{ marginTop: 4 }}>Synced from watchlist database</div>
        </div>
      </div>

      {tiers.map((t) => {
        const rows = watchlist.filter((w) => w.tier === t)

        const label =
          t === 'A'
            ? 'Active clients'
            : t === 'B'
              ? 'Target prospects'
              : 'Market indicators'

        return (
          <Section
            key={t}
            title={`Tier ${t} · ${label}`}
            tools={<span>{rows.length} COMPANIES</span>}
          >
            <table className="tbl">
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Sector</th>
                  <th>Aliases</th>
                  <th>Note</th>
                </tr>
              </thead>

              <tbody>
                {rows.map((w) => (
                  <tr key={w.company_name}>
                    <td>
                      <strong>{w.company_name}</strong>{' '}
                      <TierChip tier={w.tier} />
                    </td>

                    <td className="muted">
                      {w.sector ?? '—'}
                    </td>

                    <td className="muted">
                      {w.aliases.length > 0
                        ? w.aliases.join(', ')
                        : '—'}
                    </td>

                    <td className="muted">
                      {w.notes ?? '—'}
                    </td>
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