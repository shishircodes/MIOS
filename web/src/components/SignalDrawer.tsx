import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState, type KeyboardEvent } from 'react'
import { Drawer, Icons, TierChip } from '~/components/ui'
import { companyCandidatesQueryOptions } from '~/lib/api'
import type { Signal } from '~/lib/types'

/** Props that make a signal row open the drawer by mouse *and* keyboard. A
 *  bare clickable div cannot be reached with Tab, so the drawer was mouse-only. */
export function openableRow(onOpen: () => void) {
  return {
    role: 'button' as const,
    tabIndex: 0,
    onClick: onOpen,
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        onOpen()
      }
    },
    style: { cursor: 'pointer' },
  }
}

/** The side panel for one signal. Shared by the weekly digest and the signal
 *  feed, so the same signal reads the same wherever it is opened. */
export function SignalDrawer({ signal, onClose }: { signal: Signal | null; onClose: () => void }) {
  return (
    <Drawer open={!!signal} onClose={onClose} title={signal ? `Signal · ${signal.id.slice(0, 12)}` : ''}>
      {/* Keyed so opening another signal starts with its candidates closed. */}
      {signal && <SignalDetail key={signal.id} s={signal} />}
    </Drawer>
  )
}

function Tag({ children }: { children: string }) {
  return <div className="drawer-tag">{children}</div>
}

function SignalDetail({ s }: { s: Signal }) {
  const [showCandidates, setShowCandidates] = useState(false)
  // A signal the classifier could not attribute has nobody to match against.
  const named = !!s.company && s.company.toLowerCase() !== 'unknown'

  return (
    <>
      <div className="drawer-chips">
        <TierChip tier={s.tier} />
        <span className="chip">{s.sector}</span>
        <span className="chip">{s.region}</span>
        <span className="chip">{s.cycle}</span>
      </div>
      <h2 className="drawer-company">{s.company}</h2>
      <p className="drawer-title">{s.title}</p>
      <div className="hr" />
      <div className="row-2 drawer-facts">
        <div>
          <div className="drawer-fact-label">Source</div>
          <div className="drawer-fact">{s.source}</div>
          {s.publication && s.publication.toLowerCase() !== s.source.toLowerCase() && (
            <div className="publication">{s.publication}</div>
          )}
        </div>
        <div>
          <div className="drawer-fact-label">Confidence</div>
          <div className="drawer-fact mono">{s.conf}/100</div>
        </div>
      </div>
      <Tag>Detection</Tag>
      <p className="drawer-desc">{s.desc}</p>
      {s.action && (
        <>
          <Tag>Recommended action / analyst note</Tag>
          <div className="pull">{s.action}</div>
        </>
      )}
      <Tag>Classified by</Tag>
      <p className="drawer-classified">Reviewed automatically · {s.cycle} review cycle</p>

      <div className="drawer-actions">
        {named && (
          <button
            className="btn primary"
            aria-expanded={showCandidates}
            onClick={() => setShowCandidates((v) => !v)}
          >
            {Icons.push} {showCandidates ? 'Hide candidates' : 'Find candidates'}
          </button>
        )}
        {s.sourceUrl && (
          <a className="btn" href={s.sourceUrl} target="_blank" rel="noopener">
            {Icons.ext} Open source
          </a>
        )}
      </div>

      {named && showCandidates && <Candidates company={s.company} />}
    </>
  )
}

/** Saved candidates ranked for this company, each opening their full match. */
function Candidates({ company }: { company: string }) {
  const { data, isPending, error } = useQuery(companyCandidatesQueryOptions(company))

  return (
    <section className="drawer-candidates" aria-live="polite">
      <Tag>{`Saved candidates for ${company}`}</Tag>
      {isPending && <p className="muted drawer-small">Scoring saved candidates…</p>}
      {error && <p className="notice err drawer-small">Could not score candidates. {error.message}</p>}
      {data && data.profilesConsidered === 0 && (
        <p className="muted drawer-small">
          No candidates saved yet. Add one under <Link to="/push">Candidate matching</Link>.
        </p>
      )}
      {data && data.profilesConsidered > 0 && data.companySignals === 0 && (
        <p className="muted drawer-small">
          {company} has no signals in the last {data.windowDays} days, which is the window
          candidate matching scores on — so there is nothing current to match against.
        </p>
      )}
      {data && data.candidates.length > 0 && (
        <>
          <ol className="cand-list">
            {data.candidates.map((c) => (
              <li key={c.id}>
                <Link to="/push" search={{ profile: c.id, company }} className="cand-row">
                  <span className="cand-name">
                    <strong>{c.fullName ?? 'Unnamed candidate'}</strong>
                    <span className="muted">{c.currentTitle ?? 'No title'}{c.region ? ` · ${c.region}` : ''}</span>
                  </span>
                  <span className="cand-score mono tnum">{c.score}<span className="muted">/100</span></span>
                </Link>
              </li>
            ))}
          </ol>
          <p className="muted drawer-small">
            Top {data.candidates.length} of {data.profilesConsidered} saved candidates, scored as in
            Candidate matching over the last {data.windowDays} days. Open one for the full working.
          </p>
        </>
      )}
    </section>
  )
}
