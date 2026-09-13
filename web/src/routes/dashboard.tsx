import { createFileRoute, redirect } from '@tanstack/react-router'

/** `/dashboard` moved to `/`.
 *
 *  Kept as a redirect rather than deleted: the path was live, so it is in
 *  browser histories and anything anyone bookmarked or pasted into Slack. A
 *  404 on a page that existed last week is a worse answer than a redirect that
 *  costs nothing.
 */
export const Route = createFileRoute('/dashboard')({
  beforeLoad: () => {
    throw redirect({ to: '/', replace: true })
  },
})
