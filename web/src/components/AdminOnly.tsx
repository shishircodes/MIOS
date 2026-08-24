import type { ReactNode } from 'react'
import { Icons } from '~/components/ui'
import { useAuth } from '~/lib/auth-context'

/**
 * Wraps an Admin page so a member who reaches the URL directly sees an
 * explanation rather than a failed request.
 *
 * This is courtesy, not security. The data behind these pages comes from
 * /api/admin/*, which checks the role server-side and returns 403 regardless of
 * what the browser renders.
 */
export function AdminOnly({ children }: { children: ReactNode }) {
  const { status, session } = useAuth()

  if (status !== 'ready') return null
  if (session?.isAdmin) return <>{children}</>

  return (
    <div className="page">
      <div className="empty-state">
        <span className="ico" aria-hidden="true">{Icons.lock}</span>
        <h2>This section is for administrators</h2>
        <p>
          Your account can use the intelligence pages, but not the Admin section.
          Ask an administrator if you need access to source health or the people list.
        </p>
      </div>
    </div>
  )
}
