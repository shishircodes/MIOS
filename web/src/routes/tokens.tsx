import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { AdminOnly } from '~/components/AdminOnly'
import { Loading, Section } from '~/components/ui'
import {
  clearLlmRoute,
  clearProviderKey,
  llmSettingsQueryOptions,
  setLlmRoute,
  setProviderKey,
  testProviderKey,
} from '~/lib/api'
import type { LlmProvider, LlmRoute, LlmUsage } from '~/lib/types'

export const Route = createFileRoute('/tokens')({
  head: () => ({ meta: [{ title: 'Models & cost · MIOS' }] }),
  component: () => (
    <AdminOnly>
      <ModelsScreen />
    </AdminOnly>
  ),
})

/** Where a model choice came from. Worth showing: somebody wondering why Market
 *  Pulse uses a model they did not pick should not have to read the deployment
 *  to find out. */
const SOURCE_LABEL: Record<string, string> = {
  admin: 'Set here',
  environment: 'Set on the server',
  default: 'Default',
}

function UsageBar({ u }: { u: LlmUsage }) {
  const pct = u.dailyLimit ? Math.min(100, Math.round((u.usedToday / u.dailyLimit) * 100)) : 0
  // Amber past two thirds: a weekly run needs a couple of calls, so "nearly
  // out" matters before "out".
  const state = !u.dailyLimit ? 'none' : pct >= 100 ? 'err' : pct >= 66 ? 'warn' : 'ok'

  return (
    <div className="usage-row">
      <div className="usage-name">
        {u.label}
        {!u.configured && <span className="usage-tag">no API key</span>}
      </div>
      {u.dailyLimit === null ? (
        <div className="muted" style={{ fontSize: 12.5 }}>
          {u.usedToday} call{u.usedToday === 1 ? '' : 's'} today · no published daily cap
        </div>
      ) : (
        <>
          <div className="usage-rail">
            <div className={`usage-fill ${state}`} style={{ width: `${pct}%` }} />
          </div>
          <div className="usage-num tnum">
            {u.usedToday} / {u.dailyLimit}
            <span className="muted"> · {u.remaining} left today</span>
          </div>
        </>
      )}
    </div>
  )
}

/** Where a key came from. Text, not a colour: the distinction between "yours"
 *  and "the server's" decides what somebody does next, and a colour alone
 *  cannot be read by everyone (WCAG 1.4.1). */
const KEY_SOURCE_LABEL: Record<string, string> = {
  panel: 'Entered here',
  environment: 'Set on the server',
  none: 'No key',
}

function KeyRow({
  p,
  onSave,
  onClear,
  onTest,
  busy,
  testResult,
}: {
  p: LlmProvider
  onSave: (provider: string, key: string) => void
  onClear: (provider: string) => void
  onTest: (provider: string) => void
  busy: boolean
  testResult: { provider: string; ok: boolean; message: string } | null
}) {
  const [value, setValue] = useState('')
  const [open, setOpen] = useState(false)
  const k = p.key
  const mine = testResult?.provider === p.name ? testResult : null

  return (
    <div className="key-row">
      <div className="key-head">
        <div>
          <div className="llm-purpose">{p.label}</div>
          <div className="llm-meta">
            {KEY_SOURCE_LABEL[k.source] ?? k.source}
            {/* The hint identifies a key to somebody already holding it and is
                useless to anybody else, which is why it is safe to print. */}
            {k.hint && <> · ends <span className="mono">…{k.hint}</span></>}
            {k.changedBy && k.source === 'panel' && <> · by {k.changedBy}</>}
          </div>
          {k.shadowsEnvironment && (
            <div className="llm-meta llm-warn">
              This key is overriding the one set on the server.
            </div>
          )}
          {/* Two different reasons a stored key cannot be read, and naming the
              wrong one sends somebody to the wrong fix. */}
          {k.unreadable && (
            <div className="llm-meta llm-warn">
              {k.canStore
                ? 'A key is stored but will not decrypt — the server’s ' +
                  'MIOS_CREDENTIAL_KEY has changed since it was saved. Enter the ' +
                  'key again, or restore the previous value.'
                : 'A key is stored but cannot be read while the server has no ' +
                  'MIOS_CREDENTIAL_KEY. It is not lost — set that variable back ' +
                  'and it becomes readable again.'}
            </div>
          )}
          {!p.sdkInstalled && (
            <div className="llm-meta llm-warn">
              The client library for {p.label} is not installed, so a key alone
              will not make it work. This one needs a deploy, not a key.
            </div>
          )}
        </div>
        <div className="key-actions">
          {k.source !== 'none' && (
            <button className="btn sm ghost" disabled={busy} onClick={() => onTest(p.name)}>
              Test
            </button>
          )}
          {k.source === 'panel' && (
            <button className="btn sm ghost" disabled={busy} onClick={() => onClear(p.name)}>
              Remove
            </button>
          )}
          {k.canStore && (
            <button className="btn sm" disabled={busy} onClick={() => setOpen((o) => !o)}>
              {open ? 'Cancel' : k.source === 'panel' ? 'Replace' : 'Add key'}
            </button>
          )}
        </div>
      </div>

      {open && k.canStore && (
        <form
          className="key-form"
          onSubmit={(e) => {
            e.preventDefault()
            if (!value.trim()) return
            onSave(p.name, value.trim())
            // Cleared immediately: the key has no reason to sit in a form field
            // after it has been sent, and it cannot be read back to refill it.
            setValue('')
            setOpen(false)
          }}
        >
          <label className="key-label" htmlFor={`key-${p.name}`}>
            {p.label} API key
          </label>
          <input
            id={`key-${p.name}`}
            type="password"
            className="key-input"
            value={value}
            autoComplete="off"
            spellCheck={false}
            placeholder={`Paste the ${p.label} key`}
            onChange={(e) => setValue(e.target.value)}
          />
          <button className="btn sm" type="submit" disabled={busy || !value.trim()}>
            Save
          </button>
        </form>
      )}

      {mine && (
        <div className={`notice ${mine.ok ? 'ok' : 'err'} key-result`} role="status">
          <strong>{mine.ok ? 'Works.' : 'Did not work.'}</strong> {mine.message}
        </div>
      )}
    </div>
  )
}

function RouteRow({
  r,
  providers,
  onSet,
  onClear,
  busy,
}: {
  r: LlmRoute
  providers: { name: string; label: string; models: string[]; configured: boolean }[]
  onSet: (purpose: string, provider: string, model: string) => void
  onClear: (purpose: string) => void
  busy: boolean
}) {
  const current = `${r.provider}:${r.model}`

  // Every provider's models in one list, labelled by provider. A flat list is
  // shorter to scan than two dependent dropdowns, and the choice is one thing:
  // which model answers this.
  const options = providers.flatMap((p) =>
    p.models.map((m) => ({
      value: `${p.name}:${m}`,
      label: `${p.label} · ${m}${p.configured ? '' : ' (no key)'}`,
    })),
  )

  return (
    <div className="llm-row">
      <div>
        <div className="llm-purpose">{r.label}</div>
        <div className="llm-needs">{r.needs}</div>
        <div className="llm-meta">
          {SOURCE_LABEL[r.source] ?? r.source}
          {r.changedBy && ` by ${r.changedBy}`}
          {' · '}
          {r.callsPerRun} call{r.callsPerRun === 1 ? '' : 's'} per run
          {!r.configured && (
            <span className="llm-warn"> · this provider has no API key</span>
          )}
        </div>
        {r.overriddenEnv && (
          <div className="llm-meta llm-warn">
            Overriding the server setting ({r.overriddenEnv})
          </div>
        )}
      </div>
      <div className="llm-pick">
        <select
          value={current}
          disabled={busy}
          onChange={(e) => {
            // "provider:model", and a model name may itself contain a colon.
            const [provider, ...rest] = e.target.value.split(':')
            if (provider && rest.length) onSet(r.purpose, provider, rest.join(':'))
          }}
        >
          {/* A stored model the list does not carry must still be selectable,
              or opening this page would silently change it. */}
          {!options.some((o) => o.value === current) && (
            <option value={current}>{r.provider} · {r.model}</option>
          )}
          {options.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        {r.source === 'admin' && (
          <button className="btn sm ghost" disabled={busy} onClick={() => onClear(r.purpose)}>
            Reset
          </button>
        )}
      </div>
    </div>
  )
}

function ModelsScreen() {
  const qc = useQueryClient()
  const { data, isPending, error } = useQuery(llmSettingsQueryOptions)
  const [problem, setProblem] = useState<string | null>(null)
  const [caution, setCaution] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [tested, setTested] = useState<
    { provider: string; ok: boolean; message: string } | null
  >(null)

  const save = useMutation({
    mutationFn: (v: { purpose: string; provider: string; model: string }) =>
      setLlmRoute(v.purpose, v.provider, v.model),
    onSuccess: (payload) => {
      qc.setQueryData(llmSettingsQueryOptions.queryKey, payload)
      setProblem(null)
      setCaution(payload.warning ?? null)
    },
    onError: (e: Error) => setProblem(e.message),
  })

  const reset = useMutation({
    mutationFn: (purpose: string) => clearLlmRoute(purpose),
    onSuccess: (payload) => {
      qc.setQueryData(llmSettingsQueryOptions.queryKey, payload)
      setProblem(null)
      setCaution(null)
    },
    onError: (e: Error) => setProblem(e.message),
  })

  const saveKey = useMutation({
    mutationFn: (v: { provider: string; key: string }) => setProviderKey(v.provider, v.key),
    onSuccess: (payload) => {
      qc.setQueryData(llmSettingsQueryOptions.queryKey, payload)
      setProblem(null)
      setNote(payload.note ?? null)
      setTested(null)
    },
    onError: (e: Error) => setProblem(e.message),
  })

  const removeKey = useMutation({
    mutationFn: (provider: string) => clearProviderKey(provider),
    onSuccess: (payload) => {
      qc.setQueryData(llmSettingsQueryOptions.queryKey, payload)
      setProblem(null)
      setNote(payload.note ?? null)
      setTested(null)
    },
    onError: (e: Error) => setProblem(e.message),
  })

  const testKey = useMutation({
    mutationFn: (provider: string) => testProviderKey(provider),
    onSuccess: (payload) => {
      qc.setQueryData(llmSettingsQueryOptions.queryKey, payload)
      setProblem(null)
      setNote(null)
      setTested(payload.test ?? null)
    },
    onError: (e: Error) => setProblem(e.message),
  })

  if (isPending) {
    return (
      <div className="page">
        <Loading lines={['Checking which models are configured', 'Counting today’s calls']} />
      </div>
    )
  }
  if (error) {
    return (
      <div className="page">
        <div className="notice err">Could not load model settings. {error.message}</div>
      </div>
    )
  }

  const busy =
    save.isPending || reset.isPending ||
    saveKey.isPending || removeKey.isPending || testKey.isPending
  const spent = data.usage.find((u) => u.dailyLimit !== null && u.remaining === 0)
  // A property of the deployment, not of any one provider: every row reports
  // the same answer, so ask the first.
  const locked = data.providers.length > 0 && !data.providers[0]!.key.canStore

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Admin · Models &amp; cost</div>
          <h1>Which model answers what</h1>
        </div>
      </div>

      {/* The failure that actually happened: a run that collected fine and
          classified nothing, because the day's allowance was gone. */}
      {spent && (
        <div className="notice err" role="status">
          <strong>{spent.label}’s daily allowance is spent.</strong> A pipeline run started
          now would collect signals and fail to classify them, which looks like a working
          run with an empty digest. The allowance resets at midnight Pacific time.
        </div>
      )}

      {problem && <div className="notice err" role="alert">{problem}</div>}
      {note && !problem && (
        <div className="notice ok" role="status">
          {note}
          <button className="btn sm ghost" style={{ marginLeft: 10 }}
                  onClick={() => setNote(null)}>Dismiss</button>
        </div>
      )}
      {caution && !problem && (
        <div className="notice warn" role="status">
          <strong>Saved, but read this.</strong>
          <p style={{ margin: '6px 0 0' }}>{caution}</p>
          <button className="btn sm ghost" style={{ marginTop: 8 }}
                  onClick={() => setCaution(null)}>Dismiss</button>
        </div>
      )}

      <Section title="Today’s usage" tools={<span>RESETS MIDNIGHT PACIFIC</span>}>
        <div className="usage-list">
          {data.usage.map((u) => <UsageBar key={u.provider} u={u} />)}
        </div>
        <div className="prose-note">
          <p>
            Every attempt is counted, not just the ones that worked — a provider charges the
            allowance for a rejected request the same as a served one. A counter that only
            recorded successes read zero on the day this pipeline ran out.
          </p>
        </div>
      </Section>

      <Section
        title="Provider API keys"
        tools={<span>{locked ? 'ENTRY DISABLED' : 'STORED ENCRYPTED'}</span>}
      >
        {locked && (
          <div className="notice warn" role="status" style={{ margin: '14px 22px 0' }}>
            <strong>Keys cannot be entered here yet.</strong>
            <p style={{ margin: '6px 0 0' }}>
              The server has no <span className="mono">MIOS_CREDENTIAL_KEY</span>, so there
              is nothing to encrypt a key with — and storing one in plain text would leave
              every provider key readable to anyone who can reach the database. Set that
              variable in the deployment and restart, then keys can be added here. Keys set
              as server environment variables keep working either way.
            </p>
          </div>
        )}
        <div className="key-list">
          {data.providers.map((p) => (
            <KeyRow
              key={p.name}
              p={p}
              busy={busy}
              testResult={tested}
              onSave={(provider, key) => saveKey.mutate({ provider, key })}
              onClear={(provider) => removeKey.mutate(provider)}
              onTest={(provider) => testKey.mutate(provider)}
            />
          ))}
        </div>
        <div className="prose-note">
          <p>
            A key entered here takes effect on the next call — there is no redeploy to wait
            for, which is the point: a key gets replaced because it leaked or because the
            free allowance ran out, and both are moments where waiting for a build is the
            opposite of what is wanted.
          </p>
          <p>
            Keys are encrypted before they are written, with a secret held in the server’s
            environment and never in the database. A database dump therefore yields
            ciphertext rather than working credentials. The key is never sent back to this
            page — the last four characters are shown so you can tell which one is loaded.
          </p>
          <p>
            A key entered here takes precedence over the same provider’s server environment
            variable, and the row says so when it is doing that. Remove it to go back.
          </p>
          <p>
            <strong>Test</strong> spends one real call. That is deliberate: a key that is
            stored is not necessarily a key that works — it can be truncated by a paste,
            revoked, or belong to a project with the API switched off, and all three look
            identical here until a run fails at five on a Monday morning.
          </p>
        </div>
      </Section>

      <Section title="Model for each job">
        {data.routing.map((r) => (
          <RouteRow
            key={r.purpose}
            r={r}
            providers={data.providers}
            busy={busy}
            onSet={(purpose, provider, model) => save.mutate({ purpose, provider, model })}
            onClear={(purpose) => reset.mutate(purpose)}
          />
        ))}
        <div className="prose-note">
          <p>
            A change applies to the next call — nothing is cached between requests, so there
            is no restart to do.
          </p>
          <p>
            A provider with no API key can still be selected. The choice is saved and takes
            effect once the key is set on the server, which is more useful than refusing to
            record a decision that has already been made.
          </p>
        </div>
      </Section>

      {data.history.length > 0 && (
        <Section title="Recent days">
          <div className="usage-history">
            {data.history.slice(0, 14).map((h) => (
              <div key={`${h.provider}-${h.date}`} className="usage-hist-row">
                <span className="mono">{h.date}</span>
                <span className="muted">{h.provider}</span>
                <span className="tnum">{h.calls} call{h.calls === 1 ? '' : 's'}</span>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  )
}
