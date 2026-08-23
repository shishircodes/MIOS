import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { Loading, Section, TierChip } from '~/components/ui'
import { watchlistQueryOptions } from '~/lib/api'

export const Route = createFileRoute('/watchlist')({
  head: () => ({ meta: [{ title: 'Watchlist · MIOS' }] }),
  component: WatchlistScreen,
})

function WatchlistScreen() {
  const { data, isLoading } = useQuery(watchlistQueryOptions)

  const watchlist = data?.companies ?? []
  const tiers = ['A', 'B', 'C'] as const

  const tierCounts = {
    A: watchlist.filter((w) => w.tier === 'A').length,
    B: watchlist.filter((w) => w.tier === 'B').length,
    C: watchlist.filter((w) => w.tier === 'C').length,
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Reference · Client Watchlist</div>
          <h1>
            Watchlist — {isLoading ? '…' : `${data?.total ?? watchlist.length} companies`}
          </h1>
        </div>

        <div className="meta">
          <div>
            {isLoading
              ? 'Loading tiers…'
              : `${tierCounts.A} Tier A · ${tierCounts.B} Tier B · ${tierCounts.C} Tier C`}
          </div>

          <div style={{ marginTop: 4 }}>
            Synced from watchlist database
          </div>
        </div>
      </div>

      {isLoading && (
        <Section title="Watchlist">
          <Loading lines={['Loading companies…']} />
        </Section>
      )}

      {!isLoading &&
        tiers.map((t) => {
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