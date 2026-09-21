import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { AdminOnly } from '~/components/AdminOnly'
import { HubSpotPanel } from '~/components/HubSpotPanel'
import { Loading } from '~/components/ui'
import { hubspotStatusQueryOptions } from '~/lib/api'

export const Route = createFileRoute('/integrations')({
  head: () => ({ meta: [{ title: 'Integrations · MIOS' }] }),
  component: () => (
    <AdminOnly>
      <IntegrationsScreen />
    </AdminOnly>
  ),
})

/** Systems MIOS reads from besides the collectors. The HubSpot sync used to sit
 *  under Data sources, but it supplies the watchlist, not signals. */
function IntegrationsScreen() {
  const { isPending } = useQuery(hubspotStatusQueryOptions)

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Admin · Integrations</div>
          <h1>Connected systems</h1>
        </div>
        <div className="meta">
          <div>The synced companies appear under <Link to="/watchlist">Watchlist</Link></div>
        </div>
      </div>
      {isPending ? <Loading lines={['Checking the HubSpot connection']} /> : <HubSpotPanel />}
    </div>
  )
}
