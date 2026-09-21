import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { AdminOnly } from '~/components/AdminOnly'
import { UsageCostPanel } from '~/components/UsageCostPanel'
import { Explainer, Loading, Section } from '~/components/ui'
import { llmSettingsQueryOptions } from '~/lib/api'
import type { LlmUsage } from '~/lib/types'

export const Route = createFileRoute('/usage')({
  head: () => ({ meta: [{ title: 'Usage & cost · MIOS' }] }),
  component: () => (
    <AdminOnly>
      <UsageScreen />
    </AdminOnly>
  ),
})

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
        // Three cells, like every other row, rather than one spanning two
        // columns. A spanning item leaves nothing to size the `1fr` track from,
        // and both tracks collapsed to zero — the text still painted, out of
        // its own box, so it looked fine while being one line-length away from
        // wrapping into a column 12px wide.
        <>
          <div className="usage-note">
            {u.usedToday} call{u.usedToday === 1 ? '' : 's'} today
          </div>
          <div className="usage-num muted">no published daily cap</div>
        </>
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


/** What the models have been used for and what that would cost. Separate from
 *  AI models, which is where the choices are made: this page only reports. */
function UsageScreen() {
  const { data, isPending, error } = useQuery(llmSettingsQueryOptions)

  if (isPending) {
    return (
      <div className="page">
        <Loading lines={['Counting today’s calls', 'Adding up tokens']} />
      </div>
    )
  }
  if (error) {
    return (
      <div className="page">
        <div className="notice err">Could not load usage. {error.message}</div>
      </div>
    )
  }

  const spent = data.usage.find((u) => u.dailyLimit !== null && u.remaining === 0)

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Admin · Usage &amp; cost</div>
          <h1>What the models have cost</h1>
        </div>
      </div>

      {spent && (
        <div className="notice err" role="status">
          <strong>{spent.label}’s daily allowance is spent.</strong> A pipeline run started
          now would collect signals and fail to classify them. The allowance resets at
          midnight Pacific time. Models are chosen under <Link to="/models">AI models</Link>.
        </div>
      )}

      <UsageCostPanel />

      <Section title="Today’s allowance" tools={<span>RESETS MIDNIGHT PACIFIC</span>}>
        <div className="usage-list">
          {data.usage.map((u) => <UsageBar key={u.provider} u={u} />)}
        </div>
        <Explainer title="Why a failed call still counts">
          <p>
            Every attempt is counted, not just the ones that worked — a provider charges the
            allowance for a rejected request the same as a served one. A counter that only
            recorded successes read zero on the day this pipeline ran out.
          </p>
        </Explainer>
      </Section>

      {data.history.length > 0 && (
        <Section title="Calls per day">
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
