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
  never_run: { label: 'No data yet', cls: 'warn', help: 'Set up, but has not returned a record yet.' },
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

/** The guide's section names are sentences; a column needs a word or two. */
const CATEGORY: Record<string, string> = {
  jobs: 'Job boards',
  linkedin: 'LinkedIn',
  projects: 'Project intel',
  news: 'News',
  financial: 'Financial',
  tenders: 'Tenders',
  internal: 'Internal',
  ai: 'AI tools',
  retired: 'Retired',
}

const TABS = [
  { key: 'sources', label: 'Sources' },
  { key: 'options', label: 'Options & limits' },
] as const
type Tab = (typeof TABS)[number]['key']

function ago(iso: string | null): string {
  if (!iso) return 'never'
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000)
  if (days <= 0) return 'today'
  if (days === 1) return 'yesterday'
  return `${days} days ago`
}

/** Name, with the sectors the guide gives for the source beneath it. How it is
 *  read and what it provides are on the tooltip, not in a column of their own. */
function NameCell({ s, cls }: { s: SourceHealth; cls: string }) {
  return (
    <div className="name" title={`${s.provides} · read by ${s.kind}`}>
      <span className={`dot-${cls}`} aria-hidden="true" />
      <span className="src-name">
        {s.label}
        <span className="src-sub">{s.sectors}</span>
      </span>
    </div>
  )
}

/** A source that is set up: its health, and its switch. */
function ActiveRow({
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
  // accident unless the row says which it is. The reason is in the page guide,
  // and comes back as a warning if the switch is turned on.
  const shipsOff = s.defaultEnabled === false && !!s.offReason && !s.enabled
  // A run that came back exactly at the cap was almost certainly truncated —
  // worth flagging, because the number is a limit, not a measurement.
  const capped = s.lastRunRecords > 0 && s.lastRunRecords >= limit

  return (
    <div className="src-row src-active">
      <NameCell s={s} cls={st.cls} />
      <div className="muted">{CATEGORY[s.category] ?? s.category}</div>
      <div className="muted">{s.market}</div>
      <div>
        <span
          className={`status-chip ${st.cls}`}
          title={shipsOff ? 'Ships switched off for a known reason. The page guide explains it.' : st.help}
        >
          {shipsOff ? 'Off by default' : st.label}
        </span>
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
            ? 'Included in the next scrape'
            : `Skipped${s.changedBy ? ` — switched off by ${s.changedBy}` : ''}`
        }>
          <input
            type="checkbox"
            checked={s.enabled}
            disabled={busy}
            aria-label={`Collect from ${s.label}`}
            onChange={(e) => onToggle(s.name, e.target.checked)}
          />
          <span className="switch-track" aria-hidden="true" />
          {/* The word carries the state as well as the position, so it does
              not depend on reading a small visual difference. */}
          <span className="switch-label">{s.enabled ? 'On' : 'Off'}</span>
        </label>
      </div>
    </div>
  )
}

/** A source that could be collected but is waiting on a setting. It has no
 *  switch: once the setting is entered it joins the table above, switched on. */
function SetupRow({ s, onOpenOptions }: { s: SourceHealth; onOpenOptions: () => void }) {
  return (
    <div className="src-row src-setup">
      <NameCell s={s} cls="off" />
      <div className="muted">{CATEGORY[s.category] ?? s.category}</div>
      <div className="muted">{s.market}</div>
      <div className="src-reason">{s.note}</div>
      <div className="src-action">
        {s.setup === 'apify' && (
          <Link className="btn sm" to="/integrations" hash="apify">Set up</Link>
        )}
        {s.setup === 'feeds' && (
          <button className="btn sm" onClick={onOpenOptions}>Add a feed</button>
        )}
      </div>
    </div>
  )
}

/** A source in the guide that MIOS does not collect from: what stands in the
 *  way, in place of figures it will never have. */
function OtherRow({ s }: { s: SourceHealth }) {
  const st = STATUS[s.status] ?? STATUS.planned
  return (
    <div className="src-row src-other">
      <NameCell s={s} cls={st.cls} />
      <div className="muted">{CATEGORY[s.category] ?? s.category}</div>
      <div className="muted">{s.market}</div>
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
  const [tab, setTab] = useState<Tab>('sources')
  const [category, setCategory] = useState('all')
  const [showOther, setShowOther] = useState(false)

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

  // The three lists the page is made of. A source is in exactly one:
  // set up (with a switch), waiting on a setting, or not collected at all.
  const lists = useMemo(() => {
    const all = data?.sources ?? []
    const active = all.filter((s) => s.collectable && s.status !== 'not_configured')
    const setup = all.filter((s) => s.collectable && s.status === 'not_configured')
    const other = all.filter((s) => !s.collectable)
    // Only the categories that have a row to show, in the guide's order.
    const categories = (data?.categories ?? [])
      .map((c) => ({ key: c.key, n: active.filter((s) => s.category === c.key).length }))
      .filter((c) => c.n > 0)
    return { active, setup, other, categories }
  }, [data])

  // Before the early returns below: hooks cannot be called conditionally, so
  // these tolerate `data` being undefined while the query is in flight.
  const scope = useRef<HTMLDivElement>(null)
  const healthyRef = useFigure(data?.sources.filter((s) => s.status === 'ok').length ?? 0)
  const totalRef = useFigure(data?.totalRecords ?? 0, { delay: 0.1 })
  useReveal(scope, '.src-row', {
    key: `${data?.sources.length ?? 0}-${tab}-${category}-${showOther}`, delay: 0.12, max: 10,
  })

  if (isPending) return <PageSkeleton kind="list" />
  if (error) return <div className="page"><div className="notice err">Could not load source health. {error.message}</div></div>

  const { active, setup, other, categories } = lists
  const healthy = data.sources.filter((s) => s.status === 'ok').length
  const on = active.filter((s) => s.enabled).length
  // A filter left on a category that has since emptied falls back to everything.
  const filter = categories.some((c) => c.key === category) ? category : 'all'
  const shown = filter === 'all' ? active : active.filter((s) => s.category === filter)
  // The sources whose limits are worth showing: the ones the next run will use.
  const inUse = active.filter((s) => s.enabled).map((s) => s.name)
  // What stands in the way of the rest, counted, for the one-line summary.
  const reasons = Object.entries(
    other.reduce<Record<string, number>>((acc, s) => {
      const label = (STATUS[s.status] ?? STATUS.planned).label
      acc[label] = (acc[label] ?? 0) + 1
      return acc
    }, {}),
  ).sort((a, b) => b[1] - a[1])

  return (
    <div className="page" ref={scope}>
      <div className="page-header has-tabs">
        <div>
          <div className="kicker">Admin · Data sources</div>
          <h1>Data sources</h1>
        </div>
        <div className="meta">
          <div>
            <strong ref={healthyRef}>{healthy}</strong> collecting · {on} on for the next run
          </div>
          <div style={{ marginTop: 4 }}>
            <span ref={totalRef}>{data.totalRecords.toLocaleString()}</span> records all time
          </div>
          <div style={{ marginTop: 4 }}>
            When they run: <Link to="/schedule">Schedule &amp; runs</Link>
          </div>
        </div>
      </div>

      {/* Tabs, not a filter: each one is a different page of settings, so they
          are drawn as tabs on the header's rule rather than as the small
          buttons that narrow the table below. */}
      <div
        className="page-tabs"
        role="tablist"
        aria-label="Data sources"
        onKeyDown={(e) => {
          // Left and right move between tabs, as a tab list is expected to.
          if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
          const i = TABS.findIndex((t) => t.key === tab)
          const next = TABS[(i + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length]!
          setTab(next.key)
          document.getElementById(`src-tab-${next.key}`)?.focus()
        }}
      >
        {TABS.map((t) => (
          <button
            key={t.key}
            id={`src-tab-${t.key}`}
            role="tab"
            aria-selected={tab === t.key}
            tabIndex={tab === t.key ? 0 : -1}
            className="page-tab"
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'options' ? (
        <>
          <SourceOptionsPanel />
          <RunLimitsPanel active={inUse} />
        </>
      ) : (
        <>
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
          {on === 0 && (
            <div className="notice err" role="status">
              <strong>Collection is paused.</strong> Every source is switched off, so
              the next scrape will fetch nothing. Turn at least one back on below.
            </div>
          )}

          {/* 1. Everything that is set up, in one table. */}
          <Section title="Sources" tools={<span>{on} ON · {active.length} SET UP</span>}>
            {categories.length > 1 && (
              <div className="src-filter" role="group" aria-label="Show one kind of source">
                <div className="seg">
                  <button
                    className={`seg-btn${filter === 'all' ? ' on' : ''}`}
                    aria-pressed={filter === 'all'}
                    onClick={() => setCategory('all')}
                  >
                    All {active.length}
                  </button>
                  {categories.map((c) => (
                    <button
                      key={c.key}
                      className={`seg-btn${filter === c.key ? ' on' : ''}`}
                      aria-pressed={filter === c.key}
                      onClick={() => setCategory(c.key)}
                    >
                      {CATEGORY[c.key] ?? c.key} {c.n}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {/* Nine columns do not fit a tablet or phone; they scroll sideways
                inside the card rather than the card clipping the last ones. */}
            <div className="src-scroll">
              <div className="src-row src-active src-head">
                <div>Source</div>
                <div>Category</div>
                <div>Market</div>
                <div>Status</div>
                <div className="num">Last run</div>
                <div className="num">7 days</div>
                <div className="num">All time</div>
                <div>Last seen</div>
                <div>Collect</div>
              </div>
              {shown.map((s) => (
                <ActiveRow
                  key={s.name}
                  s={s}
                  limit={s.limit ?? data.perSourceLimit}
                  busy={toggle.isPending}
                  onToggle={(name, enabled) => toggle.mutate({ name, enabled })}
                />
              ))}
            </div>
          </Section>

          {/* 2. Waiting on a setting. Each moves up into the table once it has it. */}
          {setup.length > 0 && (
            <Section title="Needs setup" tools={<span>{setup.length} WAITING</span>}>
              <p className="muted src-lead">
                These can be collected once what they need is entered. Each one then moves into
                the table above, switched on.
              </p>
              <div className="src-scroll">
                <div className="src-row src-setup src-head">
                  <div>Source</div>
                  <div>Category</div>
                  <div>Market</div>
                  <div>What it needs</div>
                  <div />
                </div>
                {setup.map((s) => (
                  <SetupRow key={s.name} s={s} onOpenOptions={() => setTab('options')} />
                ))}
              </div>
            </Section>
          )}

          {/* 3. The rest of the guide, closed until asked for. */}
          <Section
            title="Not collected"
            tools={
              <button
                className="btn sm ghost"
                aria-expanded={showOther}
                onClick={() => setShowOther((o) => !o)}
              >
                {showOther ? 'Hide' : `Show all ${other.length}`}
              </button>
            }
          >
            <p className="muted src-lead">
              {other.length} other sources in the guide are not collected:{' '}
              {reasons.map(([label, n]) => `${n} ${label.toLowerCase()}`).join(', ')}.
            </p>
            {showOther && (
              <div className="src-scroll">
                <div className="src-row src-other src-head">
                  <div>Source</div>
                  <div>Category</div>
                  <div>Market</div>
                  <div>Status</div>
                  <div>What stands in the way</div>
                </div>
                {other.map((s) => <OtherRow key={s.name} s={s} />)}
              </div>
            )}
          </Section>
        </>
      )}
    </div>
  )
}
