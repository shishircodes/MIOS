import { createRouter as createTanStackRouter } from '@tanstack/react-router'
import { routeTree } from './routeTree.gen'
import { getQueryClient } from './lib/query-client'

export function getRouter() {
  const queryClient = getQueryClient()

  return createTanStackRouter({
    routeTree,
    context: { queryClient },
    defaultPreload: 'intent',
    // React Query decides whether cached data is fresh, so the router always
    // runs a loader rather than keeping its own copy (TanStack's advice when
    // an external cache is in use).
    defaultPreloadStaleTime: 0,
    scrollRestoration: true,
  })
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof getRouter>
  }
}
