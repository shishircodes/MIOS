import { QueryClient } from '@tanstack/react-query'
import type { FetchQueryOptions } from '@tanstack/react-query'

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false } },
  })
}

let browserClient: QueryClient | undefined

/** One QueryClient in the browser, shared by the router's loaders and the
 *  components — so data a loader starts fetching is the data a component reads.
 *  The server gets a fresh one per request, so one visitor's data never lands
 *  in another's page. */
export function getQueryClient(): QueryClient {
  if (typeof window === 'undefined') return makeQueryClient()
  return (browserClient ??= makeQueryClient())
}

// Any query's options: each keeps its own data type where it is defined.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type PrefetchOptions = FetchQueryOptions<any, any, any, any>

/** A route loader that starts a page's requests as soon as navigation begins —
 *  including on hover, since the router preloads on intent — instead of after
 *  the page has rendered. The component reads the same queries with useQuery
 *  and shows its skeleton until they land.
 *
 *  Browser only: the API authenticates with a cookie on its own domain, which
 *  a server-side render cannot send. A prefetch never throws, so a signed-out
 *  visitor is still sent to sign in by the auth gate as before. */
export function prefetch(...options: PrefetchOptions[]) {
  return ({ context }: { context: { queryClient: QueryClient } }) => {
    if (typeof window === 'undefined') return
    for (const o of options) void context.queryClient.prefetchQuery(o)
  }
}
