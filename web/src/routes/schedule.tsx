import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { AdminOnly } from '~/components/AdminOnly'
import { SchedulePanel } from '~/components/SchedulePanel'
import { Loading } from '~/components/ui'
import { scheduleQueryOptions } from '~/lib/api'

export const Route = createFileRoute('/schedule')({
  head: () => ({ meta: [{ title: 'Schedule & runs · MIOS' }] }),
  component: () => (
    <AdminOnly>
      <ScheduleScreen />
    </AdminOnly>
  ),
})

/** When the pipeline runs, and how each run went. It used to sit at the top of
 *  Data sources, which is about what is collected rather than when. */
function ScheduleScreen() {
  // Waited for here so the panel renders whole on its first pass rather than
  // appearing a moment after the page around it.
  const { isPending } = useQuery(scheduleQueryOptions)

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Admin · Schedule &amp; runs</div>
          <h1>When the pipeline runs</h1>
        </div>
        <div className="meta">
          <div>What each run collects is set under <Link to="/sources">Data sources</Link></div>
        </div>
      </div>
      {isPending ? <Loading lines={['Reading the schedule', 'Checking recent runs']} /> : <SchedulePanel />}
    </div>
  )
}
