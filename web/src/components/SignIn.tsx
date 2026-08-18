import { Icons } from '~/components/ui'
import { useAuth } from '~/lib/auth-context'
import { startSignIn } from '~/lib/auth'

/** Google's brand mark. Required by Google's branding guidelines on the sign-in button. */
function GoogleMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  )
}

const ERRORS: Record<string, string> = {
  not_configured: 'Sign-in is not set up yet. Please contact whoever administers MIOS.',
  oauth_failed: 'Google sign-in did not complete. Please try again.',
  not_authorised: 'That account is not permitted to access MIOS.',
}

export interface SignInProps {
  /** Where to land after a successful sign-in. */
  next?: string
  /** Failure code from the API's post-OAuth redirect. */
  error?: string
  /** Human-readable detail accompanying `error`. */
  detail?: string
}

/**
 * What MIOS is, for whoever lands here.
 *
 * Everything on this panel is a fixed description of the product. Nothing is
 * read from the API: this screen renders *before* anyone has authenticated, so
 * a signal count or a client name here would be intelligence leaked to an
 * unauthenticated visitor — the exact thing the sign-in gate exists to prevent.
 */
function ContextPanel() {
  return (
    <aside className="signin-aside" aria-label="About MIOS">
      <p className="signin-aside-kicker">Easy Skill Australia</p>
      <p className="signin-aside-lead">
        Market intelligence for industrial recruitment across Australia and
        Papua New Guinea.
      </p>

      <ul className="signin-modes">
        <li>
          <span className="ico" aria-hidden="true">{Icons.monitor}</span>
          <div>
            <h2>Monitor</h2>
            <p>Watches the market each week and reports what changed.</p>
          </div>
        </li>
        <li>
          <span className="ico" aria-hidden="true">{Icons.push}</span>
          <div>
            <h2>Push</h2>
            <p>Takes one consultant and ranks the companies who need them.</p>
          </div>
        </li>
      </ul>

      <p className="signin-aside-foot">
        <span className="tag">AU</span>
        <span className="tag">PNG</span>
        <span>Mining · Oil &amp; Gas · Construction · Defence · Energy</span>
      </p>
    </aside>
  )
}

/**
 * The /signin screen. The card itself stays deliberately minimal — a brand
 * mark, one line of context and the Google button. Everything else in the card
 * is a failure state that only appears when something is actually wrong; the
 * panel beside it is static product context.
 */
export function SignIn({ next, error: errCode, detail }: SignInProps) {
  const { session, status, error: apiError } = useAuth()

  const message = errCode ? ERRORS[errCode] ?? 'Sign-in failed.' : null
  const apiDown = status === 'unreachable'
  const canSignIn = !apiDown && !!session?.oauthConfigured

  return (
    <main className="signin">
      {/* Decorative only — the pattern and glow carry no meaning, so they are
          hidden from assistive tech rather than announced as empty regions. */}
      <div className="signin-bg" aria-hidden="true">
        <span className="signin-glow" />
        <span className="signin-rule" />
      </div>

      <div className="signin-split">
      <div className="signin-inner">
        <div className="signin-brand">
          <div className="brand-mark" />
          <span>MIOS</span>
        </div>

        <h1>Sign in</h1>
        <p className="signin-sub">Market Intelligence Operating System</p>

        {message && (
          <div className="signin-alert">
            <strong>{message}</strong>
            {detail && <div className="signin-alert-detail">{detail}</div>}
          </div>
        )}

        {apiDown && (
          <div className="signin-alert">
            <strong>MIOS is not responding.</strong>
            <div className="signin-alert-detail">
              Sign-in will work again once the service is back. Try again shortly.
              <details className="tech-detail">
                <summary>Technical details</summary>
                <p>
                  Start the service with <code>python -m uvicorn api.server:app --port 8787</code>
                </p>
                {apiError && <p className="tech-detail-err">{apiError}</p>}
              </details>
            </div>
          </div>
        )}

        {!apiDown && session && !session.oauthConfigured && (
          <div className="signin-alert">
            <strong>Sign-in is not set up yet.</strong>
            <div className="signin-alert-detail">
              Ask whoever administers MIOS to finish connecting Google Sign-In.
              <details className="tech-detail">
                <summary>Technical details</summary>
                <p>
                  Add <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> to{' '}
                  <code>.env</code>, then restart the service.
                </p>
              </details>
            </div>
          </div>
        )}

        <button
          className="btn signin-btn"
          disabled={!canSignIn}
          onClick={() => session && startSignIn(session.loginUrl, next)}
        >
          <GoogleMark />
          <span>Continue with Google</span>
        </button>

        {session?.domain && (
          <p className="signin-note">
            {/* Don't claim "only" when an allowlist admits other accounts too. */}
            {session.domain} accounts{session.hasAllowlist ? ' and approved exceptions' : ' only'}
          </p>
        )}
      </div>

        <ContextPanel />
      </div>
    </main>
  )
}
