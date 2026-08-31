import { API_BASE } from './config'

export interface MiosUser {
  sub: string
  email: string
  name: string
  picture: string | null
  domain: string | null
  authDisabled?: boolean
}

/** 'admin' reaches the Admin section; 'member' does not. Null when signed out. */
export type Role = 'admin' | 'member'

export interface SessionInfo {
  authenticated: boolean
  user: MiosUser | null
  authDisabled: boolean
  /** False when the server has no Google client credentials — sign-in cannot complete. */
  oauthConfigured: boolean
  /** Workspace domain the server restricts sign-in to, if configured. */
  domain: string | null
  /** True when accounts outside the domain are also permitted — whether named
   *  in ALLOWED_EMAILS or granted a row on the People & access screen. */
  hasExceptions: boolean
  loginUrl: string
  role: Role | null
  /** Mirrors role === 'admin'. The server decides this; the UI only reflects it,
   *  and every admin endpoint re-checks server-side. */
  isAdmin: boolean
}

/** Thrown by fetchJson when the API says we're not signed in. */
export class UnauthenticatedError extends Error {
  constructor(message = 'Not signed in') {
    super(message)
    this.name = 'UnauthenticatedError'
  }
}

/**
 * fetch() against the MIOS API with the session cookie attached.
 * Turns a 401 into UnauthenticatedError so callers can bounce to sign-in
 * instead of rendering a generic "backend unreachable" error.
 */
export async function fetchJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { ...init, credentials: 'include' })
  if (res.status === 401) {
    throw new UnauthenticatedError()
  }
  if (!res.ok) {
    throw new Error(`Backend returned ${res.status} for ${path}`)
  }
  return (await res.json()) as T
}

export async function fetchSession(): Promise<SessionInfo> {
  return fetchJson<SessionInfo>('/api/me')
}

export async function signOut(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, { method: 'POST', credentials: 'include' })
}

/**
 * Send the browser to Google. This is a full page navigation, not a fetch —
 * the OAuth flow is a series of top-level redirects and cannot run over XHR.
 */
export function startSignIn(loginUrl: string, next?: string): void {
  const target = new URL(loginUrl)
  target.searchParams.set('next', next ?? window.location.pathname)
  window.location.href = target.toString()
}
