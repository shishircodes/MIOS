import { useEffect, useRef } from 'react'
import type { Contribution, Match, RarityState } from '~/lib/types'

/** Everything behind one company's score.
 *
 *  The list view can only show a number and a few lines. This is where a
 *  consultant finds out whether to trust it before putting the company in front
 *  of a client — which contributors earned what, which could not be judged and
 *  why, and which of the candidate's skills the adverts actually asked for.
 *
 *  The unassessed contributors are shown rather than hidden. A score of 81 out
 *  of an assessable 60 is a different thing from 81 out of 100, and the reader
 *  cannot tell without seeing what was left out.
 */

/** One contributor as a labelled bar.
 *
 *  A contributor that could not be judged gets no bar at all — not a bar of
 *  zero length. Drawing zero says it scored badly, when in fact it was never
 *  marked, and that is the distinction the whole normalisation rests on.
 */
function ContributionRow({ c, total }: { c: Contribution; total: number }) {
  const width = (c.weight / total) * 100

  return (
    <div className="contrib">
      <div className="contrib-head">
        <span className="contrib-label">{c.label}</span>
        <span className="contrib-num tnum">
          {c.earned === null ? (
            <span className="muted">not assessed</span>
          ) : (
            <>
              {c.earned} <span className="muted">/ {c.weight}</span>
            </>
          )}
        </span>
      </div>

      {/* The rail is sized by the contributor's share of the whole model, so
          the bars are comparable to each other rather than each filling its own
          row — 4 points of recency should look like 4 points beside 28 of role
          demand. */}
      <div className="contrib-rail" style={{ width: `${Math.max(width, 6)}%` }}>
        {c.share !== null && (
          <div className="contrib-fill" style={{ width: `${Math.round(c.share * 100)}%` }} />
        )}
      </div>

      {c.evidence && <div className="contrib-ev">{c.evidence}</div>}
      {c.earned === null && c.unassessedBecause && (
        <div className="contrib-ev muted">Not judged — {c.unassessedBecause}.</div>
      )}
      <div className="contrib-asks">{c.asks}</div>
    </div>
  )
}

export function MatchDetail({
  match,
  rarity,
  onClose,
  onOutcome,
  busy,
}: {
  match: Match | null
  rarity?: RarityState
  onClose: () => void
  onOutcome?: (verb: string) => void
  busy?: boolean
}) {
  const panel = useRef<HTMLDivElement>(null)

  // Escape closes, and focus moves into the panel when it opens so a keyboard
  // user is not left behind on the list underneath.
  useEffect(() => {
    if (!match) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    panel.current?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [match, onClose])

  if (!match) return null

  const contributions = match.contributions ?? []
  const assessed = contributions.filter((c) => c.earned !== null)
  const unassessed = contributions.filter((c) => c.earned === null)
  const modelTotal = contributions.reduce((n, c) => n + c.weight, 0) || 100
  const skills = match.skillDetail ?? []
  const matchedSkills = skills.filter((s) => s.matched)

  return (
    <>
      {/* The app's existing drawer classes, not a second set: `.drawer-mask`
          and `.drawer` already carry the slide animation, the full-width
          fallback under 620px and the reduced-motion handling. */}
      <div className="drawer-mask" onClick={onClose} />
      <aside
        className="drawer"
        ref={panel}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={`Why ${match.co} scored ${match.score}`}
      >
        <header className="drawer-h match-drawer-h">
          <div>
            <div className="kicker">Rank {match.rank} · {match.rel}</div>
            <h2>{match.co}</h2>
            <div className="co-meta">
              <span>{match.region}</span><span>·</span><span>{match.sector}</span>
              <span>·</span><span>{match.signalCount} signals</span>
            </div>
          </div>
          <button className="btn sm ghost" onClick={onClose} aria-label="Close">Close</button>
        </header>

        <div className="drawer-score">
          <div className="drawer-score-num tnum">{match.score}<span className="out-of">/100</span></div>
          <div>
            {match.assessable !== undefined && match.assessable < 100 ? (
              <div className="drawer-score-note">
                <strong>{match.earned} of {match.assessable} points</strong> that could be
                judged, scaled to 100. {unassessed.length} contributor
                {unassessed.length === 1 ? '' : 's'} had nothing to assess, so
                {unassessed.length === 1 ? ' it was' : ' they were'} left out of the total
                rather than scored zero.
              </div>
            ) : (
              <div className="drawer-score-note">Every contributor could be judged.</div>
            )}
            {match.confidence && (
              <div className={`conf-chip ${match.confidence}`}>
                {match.confidence} confidence — {match.confidenceNote}
              </div>
            )}
          </div>
        </div>

        {/* Whether rarity was in play. A score computed with it and one without
            are different numbers, and somebody comparing two weeks deserves to
            know which they have. */}
        {rarity && !rarity.applies && (
          <div className="notice warn drawer-notice" role="status">
            Skills were <strong>not</strong> weighted by rarity this run: {rarity.corpusSize}{' '}
            advert{rarity.corpusSize === 1 ? '' : 's'} is below the {rarity.minimum} needed to
            measure how common a term is. Every skill counted equally.
          </div>
        )}

        <section className="drawer-section">
          <h3>What earned the score</h3>
          {assessed.map((c) => <ContributionRow key={c.key} c={c} total={modelTotal} />)}
        </section>

        {unassessed.length > 0 && (
          <section className="drawer-section">
            <h3>What could not be judged</h3>
            <p className="drawer-lede">
              These carry {unassessed.reduce((n, c) => n + c.weight, 0)} points between them.
              They were removed from the total rather than scored zero, so the company is not
              charged for gaps in our own data.
            </p>
            {unassessed.map((c) => <ContributionRow key={c.key} c={c} total={modelTotal} />)}
          </section>
        )}

        {skills.length > 0 && (
          <section className="drawer-section">
            <h3>Skills</h3>
            <p className="drawer-lede">
              {matchedSkills.length} of {skills.length} claimed skills appear in their adverts.
              Rarer skills count for more: matching something the whole market asks for
              separates nobody.
            </p>
            <ul className="skill-list">
              {skills.map((s) => (
                <li key={s.name} className={s.matched ? 'hit' : 'miss'}>
                  {/* A word, not only a colour — WCAG 1.4.1. */}
                  <span className="skill-mark">{s.matched ? 'found' : 'not found'}</span>
                  <span className="skill-name">{s.name}</span>
                  <span className="skill-meta">{s.kindLabel} · {s.rarity}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {match.rationale && (
          <section className="drawer-section">
            <h3>Written note</h3>
            <div className={`match-note${match.disagrees ? ' flagged' : ''}`}>
              <div className="match-note-head">
                <span className="match-note-tag">AI note</span>
                {match.fit && <span className={`fit-chip ${match.fit}`}>{match.fit} fit</span>}
                {match.disagrees && (
                  <span className="fit-chip disagrees">disagrees with the score</span>
                )}
              </div>
              <p>{match.rationale}</p>
              {match.caveat && <p className="match-caveat">Check first: {match.caveat}</p>}
            </div>
          </section>
        )}

        {onOutcome && (
          <section className="drawer-section">
            <h3>What did you do?</h3>
            <p className="drawer-lede">
              Recorded against the score as it stands now, so the weights can eventually be
              checked against what actually led somewhere. Nothing here changes this ranking.
            </p>
            {match.outcome ? (
              <div className="outcome-current">
                Marked <strong>{match.outcome.outcome.replace('_', ' ')}</strong>
                {match.outcome.by && <> by {match.outcome.by}</>}. Choosing again replaces it.
              </div>
            ) : null}
            <div className="outcome-actions">
              <button className="btn sm" disabled={busy}
                      onClick={() => onOutcome('contacted')}>Contacted them</button>
              <button className="btn sm ghost" disabled={busy}
                      onClick={() => onOutcome('placed')}>Placed the candidate</button>
              <button className="btn sm ghost" disabled={busy}
                      onClick={() => onOutcome('not_relevant')}>Not relevant</button>
            </div>
          </section>
        )}
      </aside>
    </>
  )
}
