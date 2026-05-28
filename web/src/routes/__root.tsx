import type { QueryClient } from '@tanstack/react-query'
import { QueryClientProvider, QueryClient as QC } from '@tanstack/react-query'
import {
  HeadContent,
  Link,
  Outlet,
  Scripts,
  createRootRouteWithContext,
  useRouterState,
} from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { Icons } from '~/components/ui'
import appCss from '~/styles/app.css?url'

interface RouterContext {
  queryClient: QueryClient
}

export const Route = createRootRouteWithContext<RouterContext>()({
  head: () => ({
    meta: [
      { charSet: 'utf-8' },
      { name: 'viewport', content: 'width=device-width, initial-scale=1' },
      { title: 'MIOS — Market Intelligence Operating System' },
    ],
    links: [{ rel: 'stylesheet', href: appCss }],
  }),
  component: RootComponent,
})

const NAV = [
  {
    group: 'INTELLIGENCE',
    items: [
      { to: '/monitor/digest', label: 'Weekly Digest', icon: 'monitor', badge: 'LIVE' },
      { to: '/monitor/feed', label: 'Signal Feed', icon: 'spark', badge: '' },
    ],
  },
  { group: 'OUTBOUND', items: [{ to: '/push', label: 'Mode Push', icon: 'push', badge: '3' }] },
  { group: 'PUBLISH', items: [{ to: '/publish', label: 'Mode Publish', icon: 'publish', badge: 'Q1' }] },
  {
    group: 'REFERENCE',
    items: [
      { to: '/watchlist', label: 'Watchlist', icon: 'watch', badge: '21' },
      { to: '/dashboard', label: 'Dashboard', icon: 'dash', badge: '' },
    ],
  },
  {
    group: 'ADMIN',
    items: [
      { to: '/sources', label: 'Sources health', icon: 'src', badge: '12' },
      { to: '/tokens', label: 'Tokens & cost', icon: 'tokens', badge: '' },
    ],
  },
] as const

const CRUMBS: Record<string, string[]> = {
  '/monitor/digest': ['Mode Monitor', 'Weekly Digest'],
  '/monitor/feed': ['Mode Monitor', 'Signal Feed'],
  '/push': ['Mode Push', 'Submit profile'],
  '/publish': ['Mode Publish', 'Q1 2026 Report'],
  '/watchlist': ['Reference', 'Watchlist'],
  '/dashboard': ['Reference', 'Dashboard'],
  '/sources': ['Admin', 'Source health'],
  '/tokens': ['Admin', 'Tokens & cost'],
}

function Shell({ children }: { children: ReactNode }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const crumbs = CRUMBS[pathname] ?? ['Mode Monitor', 'Weekly Digest']

  return (
    <div className="app">
      <div className="topbar">
        <div className="brand">
          <div className="brand-mark" />
          <span>MIOS</span>
        </div>
        <div className="crumbs">
          {crumbs.map((c, i) => (
            <span key={i} className={i === crumbs.length - 1 ? 'current' : ''}>
              {c}
              {i < crumbs.length - 1 && <span className="sep" style={{ marginLeft: 10 }}>/</span>}
            </span>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--muted)' }}>
          {Icons.search}
          <span>Search signals, companies, profiles…</span>
          <span className="kbd">⌘K</span>
        </div>
        <div className="week-badge">EASY SKILL · AU · PNG</div>
        <div className="who">
          <div className="avatar">JM</div>
          <span>Jonathan</span>
        </div>
      </div>

      <div className="sidebar">
        {NAV.map((g) => (
          <div key={g.group}>
            <div className="nav-group-label">{g.group}</div>
            {g.items.map((it) => (
              <Link
                key={it.to}
                to={it.to}
                className="nav-item"
                activeProps={{ className: 'nav-item active' }}
              >
                <span className="ico">{Icons[it.icon]}</span>
                <span>{it.label}</span>
                {it.badge && <span className="count mono">{it.badge}</span>}
              </Link>
            ))}
          </div>
        ))}
        <div className="footer">
          <div>MIOS v0.2.0 · build 14</div>
          <div style={{ marginTop: 4 }}>
            <span className="dot-ok" />
            <span style={{ marginLeft: 6 }}>Python backend online</span>
          </div>
          <div style={{ marginTop: 8, color: 'var(--ink-3)' }}>Sun batch: 22:00 AEST</div>
        </div>
      </div>

      <div className="main">{children}</div>
    </div>
  )
}

// One QueryClient for the browser; router context provides its own per request on the server.
const browserQueryClient = new QC({
  defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false } },
})

function RootComponent() {
  return (
    <RootDocument>
      <QueryClientProvider client={browserQueryClient}>
        <Shell>
          <Outlet />
        </Shell>
      </QueryClientProvider>
    </RootDocument>
  )
}

function RootDocument({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  )
}
