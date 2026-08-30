import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { CapturedAt, Icons, Loading, Section, TierChip } from '~/components/ui'
import { signalsQueryOptions } from '~/lib/api'
import { useFigure, useReveal } from '~/lib/motion'

export const Route = createFileRoute('/monitor/feed')({
  head: () => ({ meta: [{ title: 'Signal Feed · MIOS' }] }),
  component: SignalFeed,
})

const REGIONS = ['ALL', 'AU', 'PNG'] as const
const CYCLES = ['ALL', 'WEEKLY', 'MONTHLY', 'QUARTERLY'] as const
const SOURCES = ['ALL', 'PNGWORKFORCE', 'SEEK', 'ADZUNA', 'NEWSFEED'] as const
const PAGE_SIZE = 50

/** Debounce, so typing a search term is one request rather than one per key. */
function useDebounced<T>(value: T, ms = 300): T {
  const [held, setHeld] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setHeld(value), ms)
    return () => clearTimeout(id)
  }, [value, ms])
  return held
}

function SignalFeed() {
  const [region, setRegion] = useState<string>('ALL')
  const [cycle, setCycle] = useState<string>('ALL')
  const [source, setSource] = useState<string>('ALL')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)

  const debouncedQuery = useDebounced(query)

  // Any filter change invalidates the current position: page 4 of an unfiltered
  // list is not page 4 of a filtered one, and can be past the end entirely.
  useEffect(() => {
    setPage(0)
  }, [region, cycle, source, debouncedQuery])

  // Filtering and paging both happen on the server. Doing either in the browser
  // would only ever see the loaded page, so a search would report "3 results"
  // for a term with forty matches.
  const { data, isLoading, isPlaceholderData } = useQuery(
    signalsQueryOptions({
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      region: region === 'ALL' ? undefined : region,
      cycle: cycle === 'ALL' ? undefined : cycle,
      source: source === 'ALL' ? undefined : source,
      q: debouncedQuery.trim() || undefined,
    }),
  )

  // Before any early return. Re-keyed on the page and filters, so paging
  // replays the reveal; the hook caps how many rows actually animate.
  const scope = useRef<HTMLDivElement>(null)
  const scrapedRef = useFigure(data?.scrapedAllTime ?? 0)
  useReveal(scope, '.signal', {
    key: `${page}-${region}-${cycle}-${source}-${debouncedQuery}`,
    delay: 0.08,
  })

  const signals = data?.signals ?? []
  const total = data?.total ?? 0
  const anyFilterActive = region !== 'ALL' || cycle !== 'ALL' || source !== 'ALL' || query.trim() !== ''
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const firstShown = total === 0 ? 0 : page * PAGE_SIZE + 1
  const lastShown = page * PAGE_SIZE + signals.length

  const reset = () => { setRegion('ALL'); setCycle('ALL'); setSource('ALL'); setQuery('') }

  return (
    <div className="page" ref={scope}>
      <div className="page-header">
        <div>
          <div className="kicker">Mode Monitor · Signal Feed</div>
          <h1>All signals</h1>
        </div>
        <div className="meta">
          {/* The all-time figure, not the filtered count — "how much has MIOS
              ever collected?" is a different question from "what am I looking
              at?", and the latter is answered above the list. */}
          <div>
            <strong className="tnum" style={{ fontSize: 18, color: 'var(--ink)' }} ref={scrapedRef}>
              {(data?.scrapedAllTime ?? 0).toLocaleString()}
            </strong>{' '}
            signals collected all time
          </div>
          <div style={{ marginTop: 4 }}>
            <span className="chip"><span className="dot-ok" style={{ marginRight: 4 }} /> COLLECTING</span>
          </div>
        </div>
      </div>

      <div className="section" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 16, padding: '10px 16px', alignItems: 'center', flexWrap: 'wrap', fontSize: 12 }}>
          <span className="mono muted" style={{ fontSize: 10.5, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Filter</span>
          <FilterGroup label="Region" value={region} options={[...REGIONS]} onChange={setRegion} />
          <FilterGroup label="Cycle" value={cycle} options={[...CYCLES]} onChange={setCycle} />
          <FilterGroup label="Source" value={source} options={[...SOURCES]} onChange={setSource} />

          <div className="feed-search">
            <span className="feed-search-icon" aria-hidden="true">{Icons.search}</span>
            <input
              type="search"
              value={query}
              aria-label="Search signals"
              placeholder="Search company, role, sector…"
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          {anyFilterActive && (
            <button className="btn sm ghost" onClick={reset}>Reset</button>
          )}
        </div>
      </div>

      <Section
        title={
          isLoading
            ? 'Signals'
            : `${total.toLocaleString()} ${total === 1 ? 'signal' : 'signals'}${anyFilterActive ? ' matched' : ''}`
        }
        tools={
          total > 0
            ? <span className="mono">{firstShown}–{lastShown} of {total.toLocaleString()}</span>
            : undefined
        }
      >
        {isLoading && <Loading lines={['Gathering signals…']} />}

        {!isLoading && total === 0 && (
          <div className="center-empty">
            {anyFilterActive ? 'No signals match this filter.' : 'No signals collected yet.'}
            {anyFilterActive && (
              <div style={{ marginTop: 12 }}>
                <button className="btn sm" onClick={reset}>Reset filters</button>
              </div>
            )}
          </div>
        )}

        {/* Dimmed while the next page is in flight: the old rows stay in place
            so the list does not collapse and jump, but they are visibly stale. */}
        <div style={{ opacity: isPlaceholderData ? 0.55 : 1, transition: 'opacity 120ms' }}>
          {signals.map((s) => (
            <div className="signal" key={s.id}>
              <div className="num mono">{s.n}</div>
              <div className="body">
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
                  <TierChip tier={s.tier} />
                  <span className="chip">{s.sector.toUpperCase()}</span>
                  <CapturedAt at={s.capturedAt} />
                  <span className="chip">{s.cycle}</span>
                  <span className="chip">{s.region}</span>
                </div>
                <p className="title">{s.company} — {s.title}</p>
                <p className="desc">{s.desc}</p>
                {s.action && <div className="action">→ {s.action}</div>}
              </div>
              <div className="meta-col">
                <div>conf {s.conf}</div>
                <div style={{ color: 'var(--ink-3)' }}>{s.source}</div>
              </div>
            </div>
          ))}
        </div>

        {pageCount > 1 && (
          <nav className="pager" aria-label="Signal pages">
            <button
              className="btn sm"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
            >
              ← Previous
            </button>
            <span className="pager-status mono">
              Page {page + 1} of {pageCount}
            </span>
            <button
              className="btn sm"
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              disabled={page >= pageCount - 1}
            >
              Next →
            </button>
          </nav>
        )}
      </Section>
    </div>
  )
}

function FilterGroup({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
      <span className="muted" style={{ fontSize: 11 }}>{label}:</span>
      <div style={{ display: 'flex', border: '1px solid var(--line-2)', background: 'var(--surface-2)', overflow: 'hidden' }}>
        {options.map((o) => (
          <button
            key={o}
            onClick={() => onChange(o)}
            aria-pressed={value === o}
            style={{
              padding: '3px 9px', fontSize: 11, fontFamily: 'var(--mono)', letterSpacing: '0.04em',
              border: 'none', borderRight: '1px solid var(--line)',
              background: value === o ? 'var(--teal-2)' : 'transparent',
              color: value === o ? 'var(--on-teal)' : 'var(--ink-3)', cursor: 'pointer',
            }}
          >{o}</button>
        ))}
      </div>
    </div>
  )
}
