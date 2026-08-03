import { createFileRoute } from '@tanstack/react-router'
import { SignIn } from '~/components/SignIn'

/**
 * The sign-in screen, at its own URL.
 *
 * `AuthGate` in __root.tsx redirects here when there's no session (carrying the
 * attempted path in `?next=`), and redirects away again once signed in. The API
 * also lands here after a failed OAuth round trip, via `?error=` / `?detail=`.
 *
 * Rendered without the dashboard shell — see AuthGate.
 */
export const Route = createFileRoute('/signin')({
  validateSearch: (search: Record<string, unknown>) => ({
    next: typeof search.next === 'string' ? search.next : undefined,
    error: typeof search.error === 'string' ? search.error : undefined,
    detail: typeof search.detail === 'string' ? search.detail : undefined,
  }),
  component: SignInRoute,
})

function SignInRoute() {
  const { next, error, detail } = Route.useSearch()
  return <SignIn next={next} error={error} detail={detail} />
}
