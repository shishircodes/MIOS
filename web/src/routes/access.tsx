import { useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AdminOnly } from '~/components/AdminOnly'
import { Icons, Loading, Section } from '~/components/ui'
import { accessQueryOptions, grantAccess, revokeAccess } from '~/lib/api'
import type { AccessPayload, AccessUser } from '~/lib/types'

export const Route = createFileRoute('/access')({
  head: () => ({ meta: [{ title: 'People & access · MIOS' }] }),
  component: () => (
    <AdminOnly>
      <AccessScreen />
    </AdminOnly>
  ),
})

function when(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/** One person, however they got in. */
interface Row extends AccessUser {
  /** True when this address is *also* named in ALLOWED_EMAILS. Removing the
   *  database grant would not lock them out, so the row has to say so. */
  alsoInConfig: boolean
}

/**
 * Everyone who can sign in, as one list.
 *
 * The two sources are merged rather than shown separately: an administrator
 * asking "who has access?" wants one answer, not two lists to reconcile. Where
 * an address appears in both, the database row wins — it is the one carrying a
 * role and the one this screen can change — and the row notes the second route
 * rather than hiding it.
 */
function mergeRows(data: AccessPayload): Row[] {
  const inConfig = new Set(data.envGrants.map((g) => g.email))
  const rows: Row[] = data.users.map((u) => ({ ...u, alsoInConfig: inConfig.has(u.email) }))

  const named = new Set(data.users.map((u) => u.email))
  for (const g of data.envGrants) {
    if (!named.has(g.email)) rows.push({ ...g, alsoInConfig: true })
  }

  // Admins first, then alphabetical — the same order the API uses, reapplied
  // because the merge reintroduces unsorted entries.
  return rows.sort((a, b) =>
    a.role === b.role ? a.email.localeCompare(b.email) : a.role === 'admin' ? -1 : 1,
  )
}

/** How this person got in, in one line. */
function how(r: Row): string {
  if (r.source === 'environment') return 'Configuration'
  const by = r.addedBy === 'system' ? 'Seeded' : `Added by ${r.addedBy ?? 'unknown'}`
  const also = r.alsoInConfig ? ' · also in configuration' : ''
  return `${by} · ${when(r.addedAt)}${also}`
}

function PersonRow({
  r,
  you,
  onChange,
  onRemove,
  busy,
}: {
  r: Row
  you: string
  onChange: (email: string, role: 'admin' | 'member') => void
  onRemove: (email: string) => void
  busy: boolean
}) {
  const locked = r.source === 'environment'

  return (
    <div className="person-row">
      <div className="name">
        <span>{r.email}</span>
        {r.email === you && <span className="you-chip">you</span>}
        <div className="row-sub">
          {r.lastSeen ? `Last signed in ${when(r.lastSeen)}` : 'Never signed in'}
        </div>
      </div>

      <div>
        {locked ? (
          <span className="status-chip off">Member</span>
        ) : (
          <select
            className="select role-select"
            aria-label={`Role for ${r.email}`}
            value={r.role}
            disabled={busy}
            onChange={(e) => onChange(r.email, e.target.value as 'admin' | 'member')}
          >
            <option value="member">Member</option>
            <option value="admin">Administrator</option>
          </select>
        )}
      </div>

      <div className="muted row-how">{how(r)}</div>

      <div className="row-action">
        {locked ? (
          // No Remove button rather than a disabled one: the action is not
          // merely unavailable right now, it lives somewhere else entirely.
          <span
            className="lock"
            title="Set in ALLOWED_EMAILS on the server. Changing this takes a configuration edit and a restart."
          >
            <span className="ico" aria-hidden="true">{Icons.lock}</span>
            <span className="sr-only">Managed in server configuration</span>
          </span>
        ) : (
          <button
            type="button"
            className="btn sm ghost"
            disabled={busy}
            onClick={() => onRemove(r.email)}
          >
            Remove
          </button>
        )}
      </div>
    </div>
  )
}

function AddPerson({
  onAdd,
  onCancel,
  busy,
}: {
  onAdd: (email: string, role: 'admin' | 'member') => void
  onCancel: () => void
  busy: boolean
}) {
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<'admin' | 'member'>('member')

  return (
    <form
      className="person-row add-row"
      onSubmit={(e) => {
        e.preventDefault()
        if (email.trim()) onAdd(email.trim(), role)
      }}
    >
      <div>
        <input
          className="input"
          type="email"
          required
          autoFocus
          value={email}
          disabled={busy}
          aria-label="Email address"
          placeholder="someone@example.com — any domain"
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>
      <div>
        <select
          className="select role-select"
          aria-label="Role"
          value={role}
          disabled={busy}
          onChange={(e) => setRole(e.target.value as 'admin' | 'member')}
        >
          <option value="member">Member</option>
          <option value="admin">Administrator</option>
        </select>
      </div>
      <div className="muted row-how">
        Members get the intelligence pages. Administrators get everything.
      </div>
      <div className="row-action add-actions">
        <button type="submit" className="btn sm primary" disabled={busy || !email.trim()}>
          {busy ? 'Adding…' : 'Add'}
        </button>
        <button type="button" className="btn sm ghost" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

function AccessScreen() {
  const qc = useQueryClient()
  const { data, isPending, error } = useQuery(accessQueryOptions)

  const [adding, setAdding] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  function applied(payload: AccessPayload, text: string) {
    qc.setQueryData(accessQueryOptions.queryKey, payload)
    setProblem(null)
    // A revoke that left another door open says so; swallowing that would let
    // an admin believe someone is locked out when they are not.
    setMessage(payload.warning ? `${text} ${payload.warning}` : text)
  }

  const grant = useMutation({
    mutationFn: (v: { email: string; role: 'admin' | 'member' }) => grantAccess(v.email, v.role),
    onSuccess: (payload, v) => {
      applied(
        payload,
        `${v.email} can now sign in as ${v.role === 'admin' ? 'an administrator' : 'a member'}.`,
      )
      setAdding(false)
    },
    onError: (e: Error) => {
      setMessage(null)
      setProblem(e.message)
    },
  })

  const revoke = useMutation({
    mutationFn: revokeAccess,
    onSuccess: (payload, removed) => applied(payload, `Removed ${removed}.`),
    onError: (e: Error) => {
      setMessage(null)
      setProblem(e.message)
    },
  })

  if (isPending) {
    return (
      <div className="page">
        <Loading lines={['Reading the access list']} />
      </div>
    )
  }
  if (error) {
    return (
      <div className="page">
        <div className="notice err">Could not load the access list. {error.message}</div>
      </div>
    )
  }

  const busy = grant.isPending || revoke.isPending
  const rows = mergeRows(data)
  const admins = rows.filter((r) => r.role === 'admin').length

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Admin · People</div>
          <h1>People &amp; access</h1>
        </div>
        <div className="meta">
          <div>
            <strong>{rows.length}</strong> {rows.length === 1 ? 'person' : 'people'}
          </div>
          <div style={{ marginTop: 4 }}>
            {admins} administrator{admins === 1 ? '' : 's'}
          </div>
        </div>
      </div>

      {message && (
        <div className="notice ok" role="status">
          {message}
        </div>
      )}
      {problem && (
        <div className="notice err" role="alert">
          {problem}
        </div>
      )}

      <Section
        title="Who can sign in"
        tools={
          !adding && (
            <button type="button" className="btn sm" onClick={() => setAdding(true)}>
              Add person
            </button>
          )
        }
      >
        <div className="person-row person-head">
          <div>Person</div>
          <div>Role</div>
          <div>How they got in</div>
          <div />
        </div>

        {adding && (
          <AddPerson
            busy={grant.isPending}
            onCancel={() => setAdding(false)}
            onAdd={(email, role) => grant.mutate({ email, role })}
          />
        )}

        {rows.map((r) => (
          <PersonRow
            key={r.email}
            r={r}
            you={data.you}
            busy={busy}
            onChange={(e, role) => grant.mutate({ email: e, role })}
            onRemove={(e) => revoke.mutate(e)}
          />
        ))}

        {rows.length === 0 && !adding && (
          <div className="muted" style={{ padding: 16 }}>
            Nobody is listed. Sign-in currently depends entirely on the rule below.
          </div>
        )}
      </Section>

      {/* The domain rule admits people who never appear in the list above, so it
          is stated rather than left implicit — but as one line, because it is
          read far less often than the list itself. */}
      <div className="rule-note">
        {data.domain ? (
          <>
            <p>
              Anyone with an <strong>@{data.domain}</strong> Google account can also sign in
              as a member, without appearing above.
            </p>
            <details className="tech-detail">
              <summary>What that means</summary>
              <p>
                The domain is checked against the verified <code>hd</code> claim from Google,
                not the text of the address. It only ever grants <strong>member</strong> —
                administrators must be named in the list above. Changing it means changing{' '}
                <code>ALLOWED_GOOGLE_DOMAIN</code> on the server.
              </p>
              <p>
                Rows marked with a lock come from <code>ALLOWED_EMAILS</code> instead. They
                sign in as members and cannot be edited here — that takes a configuration
                change and a restart.
              </p>
            </details>
          </>
        ) : (
          <p>
            No Workspace domain is configured, so only the people listed above can sign in.
          </p>
        )}
      </div>
    </div>
  )
}
