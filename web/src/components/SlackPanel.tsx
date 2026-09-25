import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Explainer, Section } from '~/components/ui'
import {
  clearSlackWebhook,
  sendSlackTest,
  setSlackEnabled,
  setSlackWebhook,
  slackStatusQueryOptions,
} from '~/lib/api'
import type { SlackStatus } from '~/lib/types'

const SOURCE: Record<string, string> = {
  panel: 'Entered here',
  environment: 'Set on the server',
  none: 'No webhook',
}

function when(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-AU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

/** Where the weekly digest is posted after each pipeline run. */
export function SlackPanel() {
  const qc = useQueryClient()
  const { data, isPending, error } = useQuery(slackStatusQueryOptions)
  const [open, setOpen] = useState(false)
  const [url, setUrl] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)

  const settle = (p: SlackStatus) => {
    qc.setQueryData(slackStatusQueryOptions.queryKey, p)
    setProblem(null)
    setNote(p.note ? { ok: p.testOk !== false, text: p.note } : null)
  }
  const onError = (e: Error) => { setProblem(e.message); setNote(null) }

  const save = useMutation({
    mutationFn: () => setSlackWebhook(url.trim()),
    onSuccess: (p) => { settle(p); setOpen(false); setUrl('') },
    onError,
  })
  const remove = useMutation({ mutationFn: clearSlackWebhook, onSuccess: settle, onError })
  const toggle = useMutation({ mutationFn: setSlackEnabled, onSuccess: settle, onError })
  const test = useMutation({ mutationFn: sendSlackTest, onSuccess: settle, onError })

  if (isPending) return null
  if (error || !data) {
    return (
      <Section title="Slack digest">
        <div className="notice err">Could not load the Slack settings. {error?.message}</div>
      </Section>
    )
  }

  const busy = save.isPending || remove.isPending || toggle.isPending || test.isPending
  const hasWebhook = data.webhook.source !== 'none'
  const last = data.lastDelivery
  const state = !hasWebhook ? 'NOT SET UP' : data.enabled ? 'POSTING' : 'SWITCHED OFF'

  return (
    <Section title="Slack digest" tools={<span>{state}</span>}>
      {problem && <div className="notice err" role="alert">{problem}</div>}
      {!problem && note && (
        <div className={`notice ${note.ok ? 'ok' : 'err'}`} role="status">{note.text}</div>
      )}

      <p className="muted key-row" style={{ margin: 0 }}>
        After each pipeline run the weekly digest is posted to a Slack channel through an
        incoming webhook. Switching it off here stops the post only — the digest is still built
        and kept in the Weekly digest archive.
      </p>

      {/* On / off */}
      <div className="key-row">
        <div className="key-head">
          <div>
            <div className="llm-purpose">Post the digest after each run</div>
            <div className="llm-meta">
              {data.changedBy
                ? <>Last changed by {data.changedBy} on {when(data.changedAt)}</>
                : 'On unless switched off here'}
              {!hasWebhook && data.enabled && (
                <span className="llm-warn"> · nothing is posted until a webhook is added</span>
              )}
            </div>
          </div>
          <div className="key-actions">
            <label className="switch" title={data.enabled ? 'Posting after each run' : 'Not posting'}>
              <input
                type="checkbox"
                checked={data.enabled}
                disabled={busy}
                aria-label="Post the digest to Slack after each run"
                onChange={(e) => toggle.mutate(e.target.checked)}
              />
              <span className="switch-track" aria-hidden="true" />
              <span className="switch-label">{data.enabled ? 'On' : 'Off'}</span>
            </label>
          </div>
        </div>
      </div>

      {/* Webhook */}
      <div className="key-row">
        <div className="key-head">
          <div>
            <div className="llm-purpose">Incoming webhook</div>
            <div className="llm-meta">
              {SOURCE[data.webhook.source] ?? data.webhook.source}
              {data.webhook.hint && <> · ends <span className="mono">…{data.webhook.hint}</span></>}
              {data.webhook.shadowsEnvironment && <> · replaces {data.keyEnv} while set</>}
            </div>
            {data.webhook.unreadable && (
              <div className="llm-meta llm-warn">
                A webhook is stored but will not decrypt with this server’s MIOS_CREDENTIAL_KEY.
                Enter it again.
              </div>
            )}
            {!data.canStoreKey && data.webhook.source !== 'panel' && (
              <div className="llm-meta llm-warn">
                A webhook cannot be stored here until the server has MIOS_CREDENTIAL_KEY. Setting{' '}
                {data.keyEnv} on the server works too.
              </div>
            )}
            <div className="llm-meta">
              Last post:{' '}
              {last
                ? <>
                    {last.kind === 'test' ? 'test message' : 'weekly digest'} · {when(last.at)} ·{' '}
                    <span className={last.ok ? undefined : 'llm-warn'}>{last.detail}</span>
                  </>
                : 'none recorded yet'}
            </div>
          </div>
          <div className="key-actions">
            {hasWebhook && (
              <button className="btn sm ghost" disabled={busy} onClick={() => test.mutate()}>
                {test.isPending ? 'Sending…' : 'Send test message'}
              </button>
            )}
            {data.webhook.source === 'panel' && (
              <button className="btn sm ghost" disabled={busy} onClick={() => remove.mutate()}>Remove</button>
            )}
            {data.canStoreKey && (
              <button className="btn sm" disabled={busy} onClick={() => setOpen((o) => !o)}>
                {open ? 'Cancel' : data.webhook.source === 'panel' ? 'Replace' : 'Add webhook'}
              </button>
            )}
          </div>
        </div>
        {open && data.canStoreKey && (
          <form
            className="key-form"
            onSubmit={(e) => {
              e.preventDefault()
              if (url.trim()) save.mutate()
            }}
          >
            <label className="key-label" htmlFor="slack-webhook">Webhook URL</label>
            <input
              id="slack-webhook" type="password" className="key-input" value={url}
              autoComplete="off" spellCheck={false}
              placeholder="https://hooks.slack.com/services/…"
              onChange={(e) => setUrl(e.target.value)}
            />
            <button className="btn sm" type="submit" disabled={busy || !url.trim()}>
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
          </form>
        )}
      </div>

      <Explainer title="Getting a webhook URL from Slack">
        <ol>
          <li>In Slack, open <b>Tools › Apps</b> (or api.slack.com/apps) and create an app for your workspace, or open an existing one.</li>
          <li>Under <b>Incoming Webhooks</b>, switch them on and choose <b>Add New Webhook to Workspace</b>.</li>
          <li>Pick the channel the digest should go to and allow it.</li>
          <li>Copy the URL — it starts with <span className="mono">https://hooks.slack.com/services/</span> — add it here, and send a test message.</li>
        </ol>
        <p>
          Anyone holding the URL can post to that channel, so it is stored encrypted and never
          shown again in full. To move the digest to another channel, add a webhook for that
          channel and replace this one.
        </p>
      </Explainer>
    </Section>
  )
}
