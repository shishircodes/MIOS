import { Link, createFileRoute } from '@tanstack/react-router'
import { prefetch } from '~/lib/query-client'
import { pipelineSettingsQueryOptions, sourceConfigQueryOptions } from '~/lib/api'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useRef, useState } from 'react'
import { AdminOnly } from '~/components/AdminOnly'
import { RunLimitsPanel } from '~/components/RunLimitsPanel'
import { SourceOptionsPanel } from '~/components/SourceOptionsPanel'
import { Section, PageSkeleton } from '~/components/ui'
import { setSourceEnabled, sourceHealthQueryOptions } from '~/lib/api'
import { useFigure, useReveal } from '~/lib/motion'
import type { SourceHealth, SourceStatus } from '~/lib/types'

export const Route = createFileRoute('/sources')({
  head: () => ({ meta: [{ title: 'Data sources · MIOS' }] }),
  loader: prefetch(sourceHealthQueryOptions, pipelineSettingsQueryOptions, sourceConfigQueryOptions),
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
  not_configured: { label: 'Not configured', cls: 'off', help: 'Missing a key or setting, so it is skipped.' },
  off: {
    label: 'Switched off',
    cls: 'off',
    help: 'Excluded from the next scrape. The figures beside this are what it '
      + 'collected while it was on.',
  },
  retired: { label: 'Retired', cls: 'off', help: 'No longer collected; past records are kept.' },
  // A source the guide lists that MIOS does not collect from. The row says why.
  subscription: { label: 'Subscription', cls: 'off', help: 'A paid service; nothing can be read without a licence.' },
  needs_key: { label: 'Needs a key', cls: 'off', help: 'Needs an account or API key that has not been supplied.' },
  blocked: { label: 'Blocked', cls: 'off', help: 'The site forbids or refuses automated readers.' },
  unreachable: { label: 'Unreachable', cls: 'warn', help: 'Did not answer when it was checked.' },
  manual: { label: 'Documents only', cls: 'off', help: 'Published as reports or downloads, not as a feed.' },
  connected: { label: 'Connected', cls: 'ok', help: 'Set up under Integrations.' },
  planned: { label: 'Planned', cls: 'off', help: 'In the guide for a later phase.' },
}

const VIEWS = [
  { key: 'all', label: 'All' },
  { key: 'collected', label: 'Collected' },
  { key: 'other', label: 'Not collected' },
] as const
type View = (typeof VIEWS)[number]['key']

function ago(iso: string | null): string {
  if (!iso) return 'never'
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000)
  if (days <= 0) return 'today'
  if (days === 1) return 'yesterday'
  return `${days} days ago`
}

/** Name, and beneath it the sectors the guide gives for the source. */
function NameCell({ s, cls }: { s: SourceHealth; cls: string }) {
  return (
    <div className="name">
      <span className={`dot-${cls}`} aria-hidden="true" />
      <span className="src-name">
        {s.label}
        <span className="src-sub">{s.sectors}</span>
      </span>
    </div>
  )
}

function TypeCell({ s }: { s: SourceHealth }) {
  return (
    <div className="muted" title={s.provides}>
      {s.kind} · {s.market}
      {s.cost !== 'Free' && <div className="src-note">{s.cost}</div>}
    </div>
  )
}

/** A source MIOS collects from: its health, and its switch. */
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
  // A source that ships off looks identical to one somebody switched off by
  // accident unless the row says which it is.
  const shipsOff = s.defaultEnabled === false && s.offReason
  // A run that came back exactly at the cap was almost certainly truncated —
  // worth flagging, because the number is a limit, not a measurement.
  const capped = s.lastRunRecords >= limit

  return (
    <div className="src-row">
      <NameCell s={s} cls={st.cls} />
      <div className="muted" title={s.provides}>
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
        <label className="switch" title={
          s.enabled
            ? `Included in the next scrape${s.status === 'not_configured'
                ? ' — but it is not configured, so it will collect nothing' : ''}`
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
      </div>

      {/* Spans the whole row rather than sitting in the toggle column, which is
          80px wide and rendered this one letter per line. The reason itself is
          in the page guide, and repeated as a warning if the switch is turned
          on — which is the moment it has to be read. */}
      {/* Not for a source that is simply waiting on a key: its row already says
          what it needs, and a second line would say it again. */}
      {shipsOff && !s.enabled && s.status !== 'not_configured' && (
        <p className="src-why">Off by default for a known reason — the page guide explains it.</p>
      )}
    </div>
  )
}

/** A source in the guide that MIOS does not collect from: what stands in the
 *  way, in place of figures it will never have. */
function InfoRow({ s }: { s: SourceHealth }) {
  const st = STATUS[s.status] ?? STATUS.planned
  return (
    <div className="src-row src-info">
      <NameCell s={s} cls={st.cls} />
      <TypeCell s={s} />
      <div>
        <span className={`status-chip ${st.cls}`} title={st.help}>{st.label}</span>
      </div>
      <div className="src-reason">{s.note}</div>
    </div>
  )
}

function SourcesScreen() {
  const qc = useQueryClient()
  const { data, isPending, error } = useQuery(sourceHealthQueryOptions)
  const [problem, setProblem] = useState<string | null>(null)
  const [caution, setCaution] = useState<string | null>(null)
  const [view, setView] = useState<View>('all')

  const toggle = useMutation({
    mutationFn: (v: { name: string; enabled: boolean }) => setSourceEnabled(v.name, v.enabled),
    onSuccess: (payload) => {
      qc.setQueryData(sourceHealthQueryOptions.queryKey, payload)
      setProblem(null)
      // Returned when a source that ships off has just been switched on. The
      // request succeeded, so this is not an error — but turning SEEK on and
      // collecting nothing for a week with no explanation would be worse than
      // an error, because nobody would know to look.
      setCaution(payload.warning ?? null)
    },
    onError: (e: Error) => setProblem(e.message),
  })

  // Before the early returns below: hooks cannot be called conditionally, so
  // these tolerate `data` being undefined while the query is in flight.
  const scope = useRef<HTMLDivElement>(null)
  const healthyRef = useFigure(data?.sources.filter((s) => s.status === 'ok').length ?? 0)
  const totalRef = useFigure(data?.totalRecords ?? 0, { delay: 0.1 })
  useReveal(scope, '.src-row', { key: `${data?.sources.length ?? 0}-${view}`, delay: 0.12, max: 10 })

  // The guide's sections, each holding its sources under the guide's own
  // sub-headings, in the order the server lists them.
  const sections = useMemo(() => {
    if (!data) return []
    return data.categories.map((c) => {
      const all = data.sources.filter((s) => s.category === c.key)
      const shown = all.filter((s) =>
        view === 'all' ? true : view === 'collected' ? s.collectable : !s.collectable)
      const groups: { name: string; rows: SourceHealth[] }[] = []
      for (const s of shown) {
        const g = groups.find((x) => x.name === s.group)
        if (g) g.rows.push(s)
        else groups.push({ name: s.group, rows: [s] })
      }
      return {
        ...c,
        groups,
        shown: shown.length,
        total: all.length,
        on: all.filter((s) => s.collectable && s.enabled).length,
        anyCollected: shown.some((s) => s.collectable),
      }
    }).filter((c) => c.shown > 0)
  }, [data, view])

  if (isPending) return <PageSkeleton kind="list" />
  if (error) return <div className="page"><div className="notice err">Could not load source health. {error.message}</div></div>

  const healthy = data.sources.filter((s) => s.status === 'ok').length
  const listed = data.sources.filter((s) => s.status !== 'retired').length
  // The sources whose limits are worth showing: the ones the next run will use.
  const active = data.sources.filter((s) => s.collectable && s.enabled).map((s) => s.name)

  return (
    <div className="page" ref={scope}>
      <div className="page-header">
        <div>
          <div className="kicker">Admin · Data sources</div>
          <h1>Data sources</h1>
        </div>
        <div className="meta">
          <div>
            <strong ref={healthyRef}>{healthy}</strong> of {data.collectableCount} collecting
            {' · '}{listed} in the guide
          </div>
          <div style={{ marginTop: 4 }}>
            <span ref={totalRef}>{data.totalRecords.toLocaleString()}</span> records all time
          </div>
          <div style={{ marginTop: 4 }}>
            When they run: <Link to="/schedule">Schedule &amp; runs</Link>
          </div>
        </div>
      </div>

      {problem && <div className="notice err" role="alert">{problem}</div>}

      {caution && (
        <div className="notice warn" role="status">
          <strong>Switched on, but read this.</strong>
          <p style={{ margin: '6px 0 0' }}>{caution}</p>
          <button className="btn sm ghost" style={{ marginTop: 8 }}
                  onClick={() => setCaution(null)}>Dismiss</button>
        </div>
      )}

      {/* Zero enabled is a legitimate choice — it is how collection is paused —
          but an empty week would otherwise look like a broken pipeline. */}
      {data.enabledCount === 0 && (
        <div className="notice err" role="status">
          <strong>Collection is paused.</strong> Every source is switched off, so
          the next scrape will fetch nothing. Turn at least one back on below.
        </div>
      )}

      <div className="src-filter" role="group" aria-label="Which sources to show">
        <span className="mono muted">Show</span>
        <div className="seg">
          {VIEWS.map((v) => (
            <button
              key={v.key}
              className={`seg-btn${view === v.key ? ' on' : ''}`}
              aria-pressed={view === v.key}
              onClick={() => setView(v.key)}
            >
              {v.label}
            </button>
          ))}
        </div>
        <span className="muted">
          {data.enabledCount} of {data.collectableCount} on for the next scrape
        </span>
      </div>

      {sections.map((c) => (
        <Section
          key={c.key}
          title={c.label}
          tools={<span>{c.on > 0 ? `${c.on} ON · ` : ''}{c.total} LISTED</span>}
        >
          {/* Eight columns do not fit a tablet or phone; they scroll sideways
              inside the card rather than the card clipping the last ones. */}
          <div className={`src-scroll${c.anyCollected ? '' : ' src-plain'}`}>
            {c.anyCollected ? (
              <div className="src-row src-head">
                <div>Source</div>
                <div>Access / market</div>
                <div>Status</div>
                <div className="num">Last run</div>
                <div className="num">7 days</div>
                <div className="num">All time</div>
                <div>Last seen</div>
                <div>Next scrape</div>
              </div>
            ) : (
              <div className="src-row src-info src-head">
                <div>Source</div>
                <div>Access / market</div>
                <div>Status</div>
                <div className="src-reason" style={{ font: 'inherit', color: 'inherit' }}>What stands in the way</div>
              </div>
            )}
            {c.groups.map((g) => (
              <div key={g.name}>
                {/* The guide's own sub-heading. Left out when it would only
                    repeat the section's name. */}
                {(c.groups.length > 1 || g.name !== c.label) && (
                  <div className="src-group">{g.name}</div>
                )}
                {g.rows.map((s) => s.collectable ? (
                  <SourceRow
                    key={s.name}
                    s={s}
                    limit={s.limit ?? data.perSourceLimit}
                    busy={toggle.isPending}
                    onToggle={(name, enabled) => toggle.mutate({ name, enabled })}
                  />
                ) : (
                  <InfoRow key={s.name} s={s} />
                ))}
              </div>
            ))}
          </div>
        </Section>
      ))}

      <SourceOptionsPanel />
      <RunLimitsPanel active={active} />
    </div>
  )
}
