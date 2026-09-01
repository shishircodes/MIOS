import { useQuery } from '@tanstack/react-query'
import { Drawer } from '~/components/ui'
import { scoringModelQueryOptions } from '~/lib/api'

/**
 * How a match score is arrived at.
 *
 * Every number here comes from the API, which reads the scorer's own constants.
 * Writing the weights out in this file would be quicker and would go stale the
 * first time somebody tuned one — silently, because a screen explaining a
 * calculation that no longer happens still renders perfectly.
 */
export function ScoringExplainer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data, isPending, error } = useQuery({ ...scoringModelQueryOptions, enabled: open })

  return (
    <Drawer open={open} onClose={onClose} title="How the match score works">
      {isPending && <p className="muted">Reading the scoring model…</p>}
      {error && (
        <div className="notice err">
          Could not load the scoring model. {(error as Error).message}
        </div>
      )}

      {data && (
        <div className="scoring-doc">
          <p>
            Each company is scored out of {data.total} against this candidate. Every point
            comes from one of the contributors below, and each contributor produces the
            evidence line you see on the row — so any score can be taken apart and argued
            with.
          </p>

          <h4>What earns points</h4>
          <div className="score-bars">
            {data.contributors.map((c) => (
              <div key={c.key} className="score-bar">
                <div className="score-bar-head">
                  <span className="score-bar-label">{c.label}</span>
                  <span className="score-bar-weight tnum">{c.weight}</span>
                </div>
                <div className="score-bar-rail">
                  <div
                    className="score-bar-fill"
                    style={{ width: `${(c.weight / data.total) * 100}%` }}
                  />
                </div>
                <p className="score-bar-what">{c.what}</p>
              </div>
            ))}
          </div>

          <h4>Confidence is not the score</h4>
          <p>
            A thin case and a strong one can reach the same number. Confidence is shown
            beside the score rather than folded into it, because the difference changes what
            you should do next.
          </p>
          <ul className="score-list">
            {data.confidence.map((c) => (
              <li key={c.level}>
                <strong>{c.level}</strong> — {c.what}
              </li>
            ))}
          </ul>

          <h4>What the AI does, and does not</h4>
          <p>{data.llm.what}</p>
          <p className="muted" style={{ fontSize: 12.5 }}>
            Currently {data.llm.provider} · {data.llm.model}, in a single call covering the
            top {data.llm.annotatesTop} companies. An administrator can change which model
            that is under Admin → Models &amp; cost.
          </p>

          <div className="score-caveat">
            <strong>Worth knowing.</strong> {data.caveat}
          </div>
        </div>
      )}
    </Drawer>
  )
}
