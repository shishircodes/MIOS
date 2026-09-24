import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Explainer, Section } from '~/components/ui'
import {
  clearGoogleDocsClient,
  disconnectGoogleDocs,
  googleDocsStatusQueryOptions,
  setGoogleDocsClient,
  startGoogleDocsConnect,
} from '~/lib/api'
import type { GoogleDocsStatus } from '~/lib/types'

const CLIENT_SOURCE: Record<string, string> = {
  panel: 'Entered here',
  environment: 'Set on the server',
  'sign-in': 'Reusing the Google sign-in client',
  none: 'No client',
}

function when(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-AU', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

/** Where quarterly reports go when someone presses "Send to Google Docs".
 *  `flash` is the outcome Google's redirect brought back, if any. */
export function GoogleDocsPanel({ flash }: { flash?: { ok: boolean; text: string } | null }) {
  const qc = useQueryClient()
  const { data, isPending, error } = useQuery(googleDocsStatusQueryOptions)
  const [open, setOpen] = useState(false)
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const settle = (p: GoogleDocsStatus) => {
    qc.setQueryData(googleDocsStatusQueryOptions.queryKey, p)
    setProblem(null)
    setNote(p.note ?? null)
  }

  const saveClient = useMutation({
    mutationFn: () => setGoogleDocsClient(clientId.trim(), secret.trim()),
    onSuccess: (p) => { settle(p); setOpen(false); setClientId(''); setSecret('') },
    onError: (e: Error) => setProblem(e.message),
  })
  const removeClient = useMutation({
    mutationFn: clearGoogleDocsClient,
    onSuccess: settle,
    onError: (e: Error) => setProblem(e.message),
  })
  const connect = useMutation({
    mutationFn: startGoogleDocsConnect,
    // A full navigation: Google's consent screen cannot be shown in a frame.
    onSuccess: ({ url }) => { window.location.href = url },
    onError: (e: Error) => setProblem(e.message),
  })
  const disconnect = useMutation({
    mutationFn: disconnectGoogleDocs,
    onSuccess: settle,
    onError: (e: Error) => setProblem(e.message),
  })

  if (isPending) return null
  if (error || !data) {
    return (
      <Section title="Google Docs">
        <div className="notice err">Could not load the Google Docs settings. {error?.message}</div>
      </Section>
    )
  }

  const busy = saveClient.isPending || removeClient.isPending || connect.isPending || disconnect.isPending
  const hasClient = data.client.source !== 'none'

  return (
    <Section
      title="Google Docs"
      tools={<span>{data.connected ? 'CONNECTED' : 'NOT CONNECTED'}</span>}
    >
      {problem && <div className="notice err" role="alert">{problem}</div>}
      {!problem && flash && (
        <div className={`notice ${flash.ok ? 'ok' : 'err'}`} role="status">{flash.text}</div>
      )}
      {!problem && !flash && note && <div className="notice ok" role="status">{note}</div>}

      <p className="muted key-row" style={{ margin: 0 }}>
        Quarterly reports can be sent to Google Docs from Mode Publish. Each report gets one Doc
        in a <b>MIOS Quarterly Reports</b> folder of the connected account&rsquo;s Drive; sending
        it again updates that same Doc. MIOS never shares a Doc — whoever owns the Drive decides
        who sees it.
      </p>

      {/* Account */}
      <div className="key-row">
        <div className="key-head">
          <div>
            <div className="llm-purpose">Google account</div>
            <div className="llm-meta">
              {data.connected
                ? <>Connected as <b>{data.account?.email ?? 'an unnamed account'}</b> · by {data.account?.connectedBy} on {when(data.account?.connectedAt)}</>
                : data.account
                  ? <span className="llm-warn">The stored Google access can no longer be read. Connect again.</span>
                  : 'Not connected'}
              {data.folder?.url && (
                <> · <a href={data.folder.url} target="_blank" rel="noopener">Open folder</a></>
              )}
            </div>
            <div className="llm-meta">
              Access: only files MIOS creates (<span className="mono">drive.file</span>) — not the
              rest of the Drive.
            </div>
          </div>
          <div className="key-actions">
            {data.connected && (
              <button className="btn sm ghost" disabled={busy} onClick={() => disconnect.mutate()}>
                {disconnect.isPending ? 'Disconnecting…' : 'Disconnect'}
              </button>
            )}
            <button className="btn sm" disabled={busy || !hasClient} onClick={() => connect.mutate()}>
              {connect.isPending ? 'Opening Google…' : data.connected ? 'Reconnect' : 'Connect Google account'}
            </button>
          </div>
        </div>
      </div>

      {/* OAuth client */}
      <div className="key-row">
        <div className="key-head">
          <div>
            <div className="llm-purpose">OAuth client</div>
            <div className="llm-meta">
              {CLIENT_SOURCE[data.client.source] ?? data.client.source}
              {data.client.id && <> · <span className="mono">{data.client.id.slice(0, 18)}…</span></>}
            </div>
            {!data.canStoreKey && data.client.source !== 'panel' && (
              <div className="llm-meta llm-warn">
                A client cannot be stored here until the server has MIOS_CREDENTIAL_KEY. Setting{' '}
                {data.clientEnv.join(' and ')} on the server works too.
              </div>
            )}
          </div>
          <div className="key-actions">
            {data.client.source === 'panel' && (
              <button className="btn sm ghost" disabled={busy} onClick={() => removeClient.mutate()}>Remove</button>
            )}
            {data.canStoreKey && (
              <button className="btn sm" disabled={busy} onClick={() => setOpen((o) => !o)}>
                {open ? 'Cancel' : data.client.source === 'panel' ? 'Replace' : 'Add client'}
              </button>
            )}
          </div>
        </div>
        {open && data.canStoreKey && (
          <form
            className="key-form gd-form"
            onSubmit={(e) => {
              e.preventDefault()
              if (clientId.trim() && secret.trim()) saveClient.mutate()
            }}
          >
            <label className="key-label" htmlFor="gd-client-id">Client ID</label>
            <input
              id="gd-client-id" className="key-input" value={clientId} autoComplete="off" spellCheck={false}
              placeholder="1234567890-abc….apps.googleusercontent.com"
              onChange={(e) => setClientId(e.target.value)}
            />
            <label className="key-label" htmlFor="gd-client-secret">Client secret</label>
            <input
              id="gd-client-secret" type="password" className="key-input" value={secret}
              autoComplete="off" spellCheck={false} placeholder="GOCSPX-…"
              onChange={(e) => setSecret(e.target.value)}
            />
            <button className="btn sm" type="submit" disabled={busy || !clientId.trim() || !secret.trim()}>
              {saveClient.isPending ? 'Saving…' : 'Save'}
            </button>
          </form>
        )}
        <div className="llm-meta" style={{ marginTop: 8 }}>
          Authorised redirect URI to add on the client:{' '}
          <span className="mono">{data.redirectUri}</span>{' '}
          <button
            className="btn sm ghost"
            type="button"
            onClick={() => {
              void navigator.clipboard?.writeText(data.redirectUri).then(() => {
                setCopied(true)
                setTimeout(() => setCopied(false), 1500)
              })
            }}
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>

      <Explainer title="Setting it up in Google Cloud">
        <ol>
          <li>
            Open the Google Cloud project that holds MIOS&rsquo;s sign-in client (or any project
            you prefer) and enable the <b>Google Drive API</b> under APIs &amp; Services › Library.
          </li>
          <li>
            Under Credentials, open the OAuth client — the sign-in one is reused when no other is
            entered — and add the redirect URI above to its authorised redirect URIs.
          </li>
          <li>
            On the OAuth consent screen, add the <span className="mono">…/auth/drive.file</span>{' '}
            scope. With an <b>Internal</b> (Workspace) app nothing else is needed. An{' '}
            <b>External</b> app left in Testing loses access every 7 days — publish it; this
            scope does not need Google&rsquo;s review.
          </li>
          <li>Press <b>Connect Google account</b> and sign in with the account whose Drive should hold the reports.</li>
        </ol>
      </Explainer>
    </Section>
  )
}
