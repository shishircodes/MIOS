import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { AdminOnly } from '~/components/AdminOnly'
import { Loading, Section } from '~/components/ui'
import { clearLlmRoute, llmSettingsQueryOptions, setLlmRoute } from '~/lib/api'
import type { LlmRoute, LlmUsage } from '~/lib/types'

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

  const busy = save.isPending || reset.isPending
  const spent = data.usage.find((u) => u.dailyLimit !== null && u.remaining === 0)

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
