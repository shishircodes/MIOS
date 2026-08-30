import { createFileRoute } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { AdminOnly } from '~/components/AdminOnly'
import { Loading, Section } from '~/components/ui'
import { setSourceEnabled, sourceHealthQueryOptions } from '~/lib/api'
import { useFigure, useReveal } from '~/lib/motion'
import type { SourceHealth, SourceStatus } from '~/lib/types'

export const Route = createFileRoute('/sources')({
  head: () => ({ meta: [{ title: 'Sources · MIOS' }] }),
  component: () => (
    <AdminOnly>
      <SourcesScreen />
    </AdminOnly>
  ),
})

/** Status is carried by a word as well as a colour — colour alone would fail
 *  WCAG 1.4.1 for anyone who cannot distinguish green from amber. */
const STATUS: Record<SourceStatus, { label: string; cls: string; help: string }> = {
  ok: { label: 'Collecting', cls: 'ok', help: 'Ran recently and returned records.' },
  stale: { label: 'Stale', cls: 'warn', help: 'Has not collected anything lately.' },
  never_run: { label: 'No data yet', cls: 'warn', help: 'Configured, but has never returned a record.' },
  not_configured: { label: 'Not configured', cls: 'off', help: 'Missing credentials, so it is skipped.' },
  retired: { label: 'Retired', cls: 'off', help: 'No longer collected; past records are kept.' },
}

function ago(iso: string | null): string {
  if (!iso) return 'never'
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000)
  if (days <= 0) return 'today'
  if (days === 1) return 'yesterday'
  return `${days} days ago`
}

function SourceRow({
  s,
  limit,
  onToggle,
  busy,
}: {
  s: SourceHealth
  limit: number
  onToggle: (name: string, enabled: boolean) => void
  busy: boolean
}) {
  const st = STATUS[s.status] ?? STATUS.retired
  // A run that came back exactly at the cap was almost certainly truncated —
  // worth flagging, because the number is a limit, not a measurement.
  const capped = s.lastRunRecords >= limit

  return (
    <div className="src-row">
      <div className="name">
        <span className={`dot-${st.cls}`} aria-hidden="true" />
        <span style={{ marginLeft: 8 }}>{s.label}</span>
      </div>
      <div className="muted">
        {s.kind} · {s.market}
        {s.note && <div className="src-note">{s.note}</div>}
      </div>
      <div>
        <span className={`status-chip ${st.cls}`} title={st.help}>{st.label}</span>
      </div>
      <div className="num">
        <strong>{s.lastRunRecords.toLocaleString()}</strong>
        {capped && <div className="src-note">at the {limit} cap</div>}
      </div>
      <div className="num">{s.last7Days.toLocaleString()}</div>
      <div className="num">{s.totalRecords.toLocaleString()}</div>
      <div className="muted mono" style={{ fontSize: 11 }}>{ago(s.lastSeen)}</div>

      <div className="src-toggle">
        {s.status === 'retired' ? (
          <span className="muted" style={{ fontSize: 11 }}>—</span>
        ) : (
          <label className="switch" title={
            s.enabled
              ? `Included in the next scrape${s.status === 'not_configured'
                  ? ' — but it has no credentials, so it will collect nothing' : ''}`
              : `Skipped${s.changedBy ? ` — switched off by ${s.changedBy}` : ''}`
          }>
            <input
              type="checkbox"
              checked={s.enabled}
              disabled={busy}
              onChange={(e) => onToggle(s.name, e.target.checked)}
            />
            <span className="switch-track" aria-hidden="true" />
            {/* The word carries the state as well as the position, so it does
                not depend on reading a small visual difference. */}
            <span className="switch-label">{s.enabled ? 'On' : 'Off'}</span>
          </label>
        )}
      </div>
    </div>
  )
}

function SourcesScreen() {
  const qc = useQueryClient()
  const { data, isPending, error } = useQuery(sourceHealthQueryOptions)
  const [problem, setProblem] = useState<string | null>(null)

  const toggle = useMutation({
    mutationFn: (v: { name: string; enabled: boolean }) => setSourceEnabled(v.name, v.enabled),
    onSuccess: (payload) => {
      qc.setQueryData(sourceHealthQueryOptions.queryKey, payload)
      setProblem(null)
    },
    onError: (e: Error) => setProblem(e.message),
  })

  // Before the early returns below: hooks cannot be called conditionally, so
  // these tolerate `data` being undefined while the query is in flight.
  const scope = useRef<HTMLDivElement>(null)
  const healthyRef = useFigure(data?.sources.filter((s) => s.status === 'ok').length ?? 0)
  const totalRef = useFigure(data?.totalRecords ?? 0, { delay: 0.1 })
  useReveal(scope, '.src-row', { key: data?.sources.length ?? 0, delay: 0.12, max: 10 })

  if (isPending) return <div className="page"><Loading lines={['Checking each source', 'Counting what came back']} /></div>
  if (error) return <div className="page"><div className="notice err">Could not load source health. {error.message}</div></div>

  const healthy = data.sources.filter((s) => s.status === 'ok').length
  const live = data.sources.filter((s) => s.status !== 'retired')
  const pending = data.sources.reduce((n, s) => n + s.pending, 0)


  return (
    <div className="page" ref={scope}>
      <div className="page-header">
        <div>
          <div className="kicker">Admin · Source health</div>
          <h1>Data sources</h1>
        </div>
        <div className="meta">
          <div>
            <strong ref={healthyRef}>{healthy}</strong> of {live.length} collecting
          </div>
          <div style={{ marginTop: 4 }}>
            <span ref={totalRef}>{data.totalRecords.toLocaleString()}</span> records all time
          </div>
        </div>
      </div>

      {problem && <div className="notice err" role="alert">{problem}</div>}

      {/* Zero enabled is a legitimate choice — it is how collection is paused —
          but an empty week would otherwise look like a broken pipeline. */}
      {data.enabledCount === 0 && (
        <div className="notice err" role="status">
          <strong>Collection is paused.</strong> Every source is switched off, so
          the next scrape will fetch nothing. Turn at least one back on below.
        </div>
      )}

      <Section
        title="Collectors"
        tools={<span>{data.enabledCount} OF {live.length} ON FOR NEXT SCRAPE</span>}
      >
        <div className="src-row src-head">
          <div>Source</div>
          <div>Type / market</div>
          <div>Status</div>
          <div className="num">Last run</div>
          <div className="num">7 days</div>
          <div className="num">All time</div>
          <div>Last seen</div>
          <div>Next scrape</div>
        </div>
        {data.sources.map((s) => (
          <SourceRow
            key={s.name}
            s={s}
            limit={data.perSourceLimit}
            busy={toggle.isPending}
            onToggle={(name, enabled) => toggle.mutate({ name, enabled })}
          />
        ))}
      </Section>

      <Section title="How to read this">
        <div className="prose-note">
          <p>
            Every figure here is counted from the collected records themselves, not
            from a separate log — so it cannot drift out of step with what is
            actually in the database.
          </p>
          <p>
            <strong>Last run</strong> is how many records a source returned the last
            day it collected. Each source stops at {data.perSourceLimit} records per
            run, so a run sitting exactly on that number was probably cut short
            rather than finished.
          </p>
          <p>
            A source is marked <strong>stale</strong> once {data.staleAfterDays} days
            pass with nothing collected — longer than the weekly cycle, so a normal
            week never trips it.
          </p>
          {pending > 0 && (
            <p>
              {pending.toLocaleString()} records are waiting to be classified. They
              are collected and stored; they just have not been read yet.
            </p>
          )}
        </div>
      </Section>
    </div>
  )
}
