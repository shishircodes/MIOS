import type { QueryClient } from '@tanstack/react-query'
import { QueryClientProvider, QueryClient as QC, useQuery } from '@tanstack/react-query'
import {
  HeadContent,
  Link,
  Outlet,
  Scripts,
  createRootRouteWithContext,
  useNavigate,
  useRouterState,
} from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'
import { NotFound } from '~/components/NotFound'
import { Icons, Loading } from '~/components/ui'
import { watchlistQueryOptions } from '~/lib/api'
import { AuthProvider, useAuth } from '~/lib/auth-context'
import appCss from '~/styles/app.css?url'

interface RouterContext {
  queryClient: QueryClient
}

export const Route = createRootRouteWithContext<RouterContext>()({
  notFoundComponent: NotFound,
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
      { to: '/watchlist', label: 'Watchlist', icon: 'watch', badge: '' },
      { to: '/dashboard', label: 'Dashboard', icon: 'dash', badge: '' },
    ],
  },
  {
    group: 'ADMIN',
    // Hidden from members. This is presentation only — every /api/admin/*
    // endpoint re-checks the role, so a member who types the URL still gets
    // nothing back.
    adminOnly: true,
    items: [
      { to: '/sources', label: 'Sources health', icon: 'src', badge: '' },
      { to: '/access', label: 'People & access', icon: 'people', badge: '' },
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
  '/access': ['Admin', 'People & access'],
  '/tokens': ['Admin', 'Tokens & cost'],
}

const SIGNIN_PATH = '/signin'
const DEFAULT_LANDING = '/monitor/digest'

/**
 * Routes between the sign-in screen and the dashboard, and supplies the shell.
 *
 * Signed out, the browser is *redirected* to /signin rather than having the
 * screen swapped in place — so the URL always reflects what's on screen, and
 * the sign-in page is linkable and reloadable. The attempted path rides along
 * in `?next=` so we can return there afterwards.
 *
 * This is the UX half of the gate: the enforcing half is `require_user` on the
 * API, which 401s every data endpoint regardless of what the browser does. If
 * this component were bypassed the shell would render empty, not leak anything.
 */
function AuthGate({ children }: { children: ReactNode }) {
  const { status, session } = useAuth()
  const navigate = useNavigate()
  const { pathname, search } = useRouterState({
    select: (s) => ({ pathname: s.location.pathname, search: s.location.search }),
  })

  const onSignIn = pathname === SIGNIN_PATH
  // `unreachable` means the API is down; the sign-in screen explains that, so
  // treat it like signed-out rather than showing a bare error over the shell.
  const signedIn = status === 'ready' && !!session?.authenticated
  const resolved = status !== 'loading'

  useEffect(() => {
    if (!resolved) return
    if (!signedIn && !onSignIn) {
      navigate({
        to: SIGNIN_PATH,
        search: { next: pathname, error: undefined, detail: undefined },
        replace: true,
      })
    } else if (signedIn && onSignIn) {
      const next = (search as { next?: string })?.next
      navigate({ to: next ?? DEFAULT_LANDING, replace: true })
    }
  }, [resolved, signedIn, onSignIn, pathname, search, navigate])

  if (!resolved) {
    return (
      <div className="signin-checking">
        <Loading lines={['Checking you in…']} />
      </div>
    )
  }
  // /signin renders bare — no sidebar, no topbar.
  if (onSignIn) {
    return <>{children}</>
  }
  if (!signedIn) {
    // Redirecting; render nothing rather than a flash of dashboard chrome.
    return (
      <div className="signin-checking">
        <Loading lines={['Taking you to sign in…']} />
      </div>
    )
  }
  return <Shell>{children}</Shell>
}

function UserMenu() {
  const { session, signOut } = useAuth()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const user = session?.user

  // Click-outside and Escape both close it — a menu that can only be dismissed
  // by clicking the trigger again feels broken.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (!user) return null

  // Letters only — a name like "Dev (auth disabled)" would otherwise render "D(".
  const initials =
    user.name
      .split(/\s+/)
      .map((p) => p.replace(/[^\p{L}]/gu, ''))
      .filter(Boolean)
      .slice(0, 2)
      .map((p) => p.charAt(0).toUpperCase())
      .join('') || user.email.charAt(0).toUpperCase()

  return (
    <div className="who-menu" ref={ref}>
      <button
        className="who-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {user.picture ? (
          <img className="avatar" src={user.picture} alt="" width={26} height={26} referrerPolicy="no-referrer" />
        ) : (
          <div className="avatar">{initials || '?'}</div>
        )}
        <span className="who-name">{user.name.split(' ')[0]}</span>
        <span className="who-caret" aria-hidden="true">▾</span>
      </button>

      {open && (
        <div className="who-dropdown" role="menu">
          <div className="who-identity">
            <div className="who-fullname">{user.name}</div>
            {/* Shown in full: on a shared dashboard, knowing which account you
                are signed in as matters more than a tidy truncation. */}
            <div className="who-email">{user.email}</div>
            {user.domain && <span className="chip">{user.domain}</span>}
          </div>
          <button className="who-action" role="menuitem" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}

const NAV_KEY = 'mios.nav.collapsed'

function Shell({ children }: { children: ReactNode }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const crumbs = CRUMBS[pathname] ?? ['Mode Monitor', 'Weekly Digest']
  const { session } = useAuth()
  const isAdmin = Boolean(session?.isAdmin)
  const { data: watchlistData } = useQuery(watchlistQueryOptions)

  // Expanded is the default, and the server always renders that. The stored
  // preference is applied after mount rather than read during render: this app
  // is server-rendered, so touching localStorage in the render path would make
  // the client's first paint disagree with the server's HTML.
  const [collapsed, setCollapsed] = useState(false)
  useEffect(() => {
    try {
      if (localStorage.getItem(NAV_KEY) === '1') setCollapsed(true)
    } catch {
      // Private mode or blocked storage — the default is fine.
    }
  }, [])

  const toggleNav = () =>
    setCollapsed((c) => {
      try {
        localStorage.setItem(NAV_KEY, c ? '0' : '1')
      } catch { /* not worth failing the toggle over */ }
      return !c
    })

  const shellClass = ['app', collapsed && 'nav-collapsed', session?.authDisabled && 'has-auth-bar']
    .filter(Boolean)
    .join(' ')

  return (
    <>
      {session?.authDisabled && (
        <div className="auth-off-bar">
          AUTH_DISABLED — sign-in is bypassed. Development only.
        </div>
      )}
      <div className={shellClass}>
      <div className="topbar">
        <button
          className="nav-toggle"
          onClick={toggleNav}
          aria-expanded={!collapsed}
          aria-controls="sidebar-nav"
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          {Icons.panel}
        </button>
        <div className="brand">
          <div className="brand-mark" />
          <span>MIOS</span>
        </div>
        {/* flex:1 so the badge and user menu sit hard right now that the
            search has been removed from between them. */}
        <nav className="crumbs" aria-label="Breadcrumb">
          {crumbs.map((c, i) => (
            <span key={i} className={i === crumbs.length - 1 ? 'current' : ''}>
              {c}
              {i < crumbs.length - 1 && <span className="sep" aria-hidden="true">/</span>}
            </span>
          ))}
        </nav>
        <div className="week-badge" title="Easy Skill Australia · Australia and Papua New Guinea">
          EASY SKILL · AU · PNG
        </div>
        <UserMenu />
      </div>

      <nav className="sidebar" id="sidebar-nav" aria-label="Main">
        {NAV.filter((g) => !('adminOnly' in g && g.adminOnly) || isAdmin).map((g) => (
          <div className="nav-group" key={g.group}>
            {/* Collapsed, the text is hidden by CSS and the element becomes a
                rule between groups — so the sections stay visually separated
                without a label that no longer fits. */}
            <div className="nav-group-label"><span>{g.group}</span></div>
            {g.items.map((it) => (
              <Link
                key={it.to}
                to={it.to}
                className="nav-item"
                activeProps={{ className: 'nav-item active' }}
                // Collapsed there is no visible text, so the icon needs an
                // accessible name of its own; expanded, the label supplies it
                // and a duplicate would be read twice.
                aria-label={collapsed ? it.label : undefined}
                title={collapsed ? it.label : undefined}
              >
                <span className="ico" aria-hidden="true">{Icons[it.icon]}</span>
                <span className="label">{it.label}</span>
                {it.to === '/watchlist'
                  ? watchlistData && <span className="count mono">{watchlistData.total}</span>
                  : it.badge && <span className="count mono">{it.badge}</span>}
              </Link>
            ))}
          </div>
        ))}
        <div className="footer">
          <span
            className="rail-dot dot-ok"
            title="Connected"
            aria-label="Connected"
            role="img"
          />
          <div>MIOS v0.2.0</div>
          <div style={{ marginTop: 4 }}>
            <span className="dot-ok" />
            <span style={{ marginLeft: 6 }}>Connected</span>
          </div>
          <div style={{ marginTop: 8, color: 'var(--ink-3)' }}>Updates Sunday, 22:00 AEST</div>
        </div>
      </nav>

      <div className="main">{children}</div>
      </div>
    </>
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
        <AuthProvider>
          {/* AuthGate decides whether to wrap the route in the dashboard shell;
              /signin renders bare. */}
          <AuthGate>
            <Outlet />
          </AuthGate>
        </AuthProvider>
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
