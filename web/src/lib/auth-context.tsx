import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { fetchSession, signOut as apiSignOut } from './auth'
import type { SessionInfo } from './auth'

type Status = 'loading' | 'ready' | 'unreachable'

interface AuthState {
  status: Status
  session: SessionInfo | null
  error: string | null
  refresh: () => Promise<void>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

/**
 * Resolves the session on the client only.
 *
 * The session lives in an HttpOnly cookie on the API's origin, which an SSR
 * render has no access to — asking during SSR would always answer "signed out"
 * and flash a sign-in screen at users who are in fact signed in. So the first
 * paint is a neutral "checking session" state and the real answer arrives on mount.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('loading')
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const s = await fetchSession()
      setSession(s)
      setStatus('ready')
      setError(null)
    } catch (e) {
      // /api/me is public, so a failure here means the API itself is down.
      setSession(null)
      setStatus('unreachable')
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const signOut = useCallback(async () => {
    await apiSignOut()
    await refresh()
  }, [refresh])

  return (
    <AuthContext.Provider value={{ status, session, error, refresh, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
