import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { AdminOnly } from '~/components/AdminOnly'
import { GoogleDocsPanel } from '~/components/GoogleDocsPanel'
import { HubSpotPanel } from '~/components/HubSpotPanel'
import { SlackPanel } from '~/components/SlackPanel'
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

/** Google's redirect back from the consent screen lands here with the outcome
 *  in the query string. Read once, then removed so a reload does not replay it. */
function useGoogleDocsFlash() {
  const [flash, setFlash] = useState<{ ok: boolean; text: string } | null>(null)
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    const outcome = q.get('googleDocs')
    if (!outcome) return
    setFlash(outcome === 'connected'
      ? { ok: true, text: `Google Docs connected${q.get('account') ? ` as ${q.get('account')}` : ''}.` }
      : { ok: false, text: q.get('reason') || 'Connecting Google Docs failed.' })
    window.history.replaceState(null, '', window.location.pathname)
  }, [])
  return flash
}

/** Systems MIOS connects to besides the collectors, in the order of the
 *  sidebar: Slack receives the weekly digest, HubSpot supplies the watchlist,
 *  Google Docs receives the quarterly reports. */
function IntegrationsScreen() {
  const { isPending } = useQuery(hubspotStatusQueryOptions)
  const flash = useGoogleDocsFlash()
  // Back from Google's consent screen: the outcome is shown in the Google Docs
  // panel, which is last on the page, so bring it into view.
  useEffect(() => {
    if (flash) document.getElementById('google-docs')?.scrollIntoView({ block: 'start' })
  }, [flash])

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Admin · Integrations</div>
          <h1>Connected systems</h1>
        </div>
        <div className="meta">
          <div>The digest is posted when a run finishes — see <Link to="/schedule">Schedule &amp; runs</Link></div>
          <div>The synced companies appear under <Link to="/watchlist">Watchlist</Link></div>
          <div>Reports are sent from <Link to="/publish">Quarterly reports</Link></div>
        </div>
      </div>
      <SlackPanel />
      {isPending ? <Loading lines={['Checking the HubSpot connection']} /> : <HubSpotPanel />}
      <div id="google-docs">
        <GoogleDocsPanel flash={flash} />
      </div>
    </div>
  )
}
