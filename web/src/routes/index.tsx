import { createFileRoute } from '@tanstack/react-router'
import { prefetch } from '~/lib/query-client'
import { dashboardQueryOptions } from '~/lib/api'
import { DashboardScreen } from '~/components/DashboardScreen'

/** The dashboard is the site root.
 *
 *  It used to redirect to the weekly digest, which is why moving the dashboard
 *  to the top of the navigation and pointing the post-sign-in landing at it
 *  changed nothing: everything arriving at `/` was sent straight past it.
 *
 *  The overview belongs at the root. It says what the week looked like in one
 *  screen, and every detail view is one click from it.
 */
export const Route = createFileRoute('/')({
  head: () => ({ meta: [{ title: 'Dashboard · MIOS' }] }),
  loader: prefetch(dashboardQueryOptions({})),
  component: DashboardScreen,
})
