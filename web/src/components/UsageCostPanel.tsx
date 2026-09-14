import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Explainer, Loading, Section } from '~/components/ui'
import { llmUsageQueryOptions } from '~/lib/api'
import type { UsageBucket, UsageReport } from '~/lib/types'

/** Dollars with enough precision to be useful at free-tier scale, where a
 *  week can cost a few cents. "$0.00" would read as "nothing was used". */
function usd(v: number): string {
  if (v === 0) return '$0.00'
  if (v < 0.01) return '<$0.01'
  if (v < 100) return `$${v.toFixed(2)}`
  return `$${Math.round(v).toLocaleString()}`
}

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}K`
  return n.toLocaleString()
}

function CostCell({ b, priced = true }: { b: UsageBucket; priced?: boolean }) {
  if (!priced) return <span className="muted" title="No published rate for this model">no rate</span>
  return (
    <>
      {usd(b.costUsd)}
      {b.unpricedCalls > 0 && <span className="muted"> + {b.unpricedCalls} unpriced</span>}
    </>
  )
}

/** One bar per day. A single series, so no legend: the section title names it. */
function DailyCost({ days }: { days: UsageReport['daily'] }) {
  const [at, setAt] = useState<number | null>(null)
  const plot = useRef<HTMLDivElement>(null)

  const w = 720
  const h = 150
  const pad = { l: 44, r: 8, t: 10, b: 20 }
  const max = Math.max(...days.map((d) => d.costUsd), 0)
  // Round the axis up to a clean step so the ticks read as money, not noise.
  const step = [0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 25, 50, 100, 250, 500, 1000]
    .find((s) => s * 4 >= max) ?? Math.ceil(max / 4)
  const top = Math.max(step * 4, 0.01)
  const band = (w - pad.l - pad.r) / days.length
  const barW = Math.min(24, Math.max(2, band - 2))
  const x = (i: number) => pad.l + i * band + (band - barW) / 2
  const y = (v: number) => pad.t + (1 - v / top) * (h - pad.t - pad.b)
  const active = at === null ? null : days[at]

  function pick(clientX: number) {
    const el = plot.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const rel = ((clientX - rect.left) / rect.width) * w - pad.l
    setAt(Math.min(days.length - 1, Math.max(0, Math.floor(rel / band))))
  }

  return (
    <div
      ref={plot}
      className="trend-plot cost-plot"
      tabIndex={0}
      role="group"
      aria-label={`Estimated cost per day from ${days[0]?.date} to ${days[days.length - 1]?.date}. Use the arrow keys to read each day.`}
      onMouseMove={(e) => pick(e.clientX)}
      onMouseLeave={() => setAt(null)}
      onFocus={() => setAt((c) => c ?? days.length - 1)}
      onBlur={() => setAt(null)}
      onKeyDown={(e) => {
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
          e.preventDefault()
          setAt((c) => Math.min(days.length - 1, Math.max(0, (c ?? days.length - 1) + (e.key === 'ArrowLeft' ? -1 : 1))))
        }
      }}
    >
      <svg viewBox={`0 0 ${w} ${h}`} className="trend-svg" aria-hidden="true">
        {[0, 1, 2, 3, 4].map((k) => (
          <g key={k}>
            <line x1={pad.l} x2={w - pad.r} y1={y(step * k)} y2={y(step * k)} stroke="var(--line)" strokeWidth="1" />
            <text x={pad.l - 6} y={y(step * k) + 3} textAnchor="end" className="trend-tick">
              {usd(step * k)}
            </text>
          </g>
        ))}
        {days.map((d, i) => {
          const hgt = Math.max(0, y(0) - y(d.costUsd))
          const r = Math.min(4, hgt / 2, barW / 2)
          const x0 = x(i)
          const y0 = y(d.costUsd)
          // Rounded at the data end only, square on the baseline.
          const path = hgt === 0 ? '' :
            `M${x0},${y(0)} V${y0 + r} Q${x0},${y0} ${x0 + r},${y0} H${x0 + barW - r} ` +
            `Q${x0 + barW},${y0} ${x0 + barW},${y0 + r} V${y(0)} Z`
          return (
            <g key={d.date}>
              <rect x={pad.l + i * band} y={pad.t} width={band} height={h - pad.t - pad.b}
                    fill={at === i ? 'var(--teal-bg)' : 'transparent'} />
              {path && <path d={path} fill={at === i ? 'var(--teal-2)' : 'var(--teal)'} />}
            </g>
          )
        })}
        <text x={pad.l} y={h - 4} className="trend-tick">{days[0]?.date}</text>
        <text x={w - pad.r} y={h - 4} textAnchor="end" className="trend-tick">{days[days.length - 1]?.date}</text>
      </svg>
      {active && at !== null && (
        <div
          className="trend-read"
          role="status"
          style={{
            left: `${((x(at) + barW / 2) / w) * 100}%`,
            transform: at > days.length / 2 ? 'translateX(-100%)' : 'none',
          }}
        >
          <div className="trend-read-date">{active.date}</div>
          <div className="trend-read-row"><span>Estimated</span><b>{usd(active.costUsd)}</b></div>
          <div className="trend-read-row"><span>Calls</span><b>{active.calls}</b></div>
          <div className="trend-read-row"><span>Tokens in / out</span><b>{compact(active.inputTokens)} / {compact(active.outputTokens)}</b></div>
        </div>
      )}
    </div>
  )
}

export function UsageCostPanel() {
  const [range, setRange] = useState('30d')
  const { data, isPending, error, isPlaceholderData } = useQuery({
    ...llmUsageQueryOptions(range),
    // Switching the time frame keeps the previous figures on screen until the
    // new ones arrive, rather than collapsing the section to a loader.
    placeholderData: keepPreviousData,
  })

  const tools = (
    <div className="cost-range" role="group" aria-label="Time frame">
      {(data?.ranges ?? [{ key: range, label: range }]).map((r) => (
        <button
          key={r.key}
          type="button"
          className={r.key === range ? 'on' : ''}
          aria-pressed={r.key === range}
          onClick={() => setRange(r.key)}
        >
          {r.key === 'today' ? 'Today' : r.key === 'all' ? 'All' : r.key.toUpperCase()}
        </button>
      ))}
    </div>
  )

  if (isPending) {
    return (
      <Section title="Tokens & estimated cost">
        <Loading lines={['Adding up tokens', 'Pricing each model']} />
      </Section>
    )
  }
  if (error) {
    return (
      <Section title="Tokens & estimated cost">
        <div className="notice err">Could not load usage. {error.message}</div>
      </Section>
    )
  }

  const t = data.totals
  const claude = data.byModel.filter((m) => m.provider === 'anthropic')
  const claudeCost = claude.reduce((s, m) => s + m.costUsd, 0)
  const nothing = t.calls === 0

  return (
    <Section title="Tokens & estimated cost" tools={tools}>
      <div className={`cost-body${isPlaceholderData ? ' is-stale' : ''}`}>
        <div className="cost-tiles">
          <div className="cost-tile">
            <div className="cost-label">Estimated cost · {data.label.toLowerCase()}</div>
            <div className="cost-value">{usd(t.costUsd)}</div>
            <div className="cost-sub">at list prices, in US dollars</div>
          </div>
          <div className="cost-tile">
            <div className="cost-label">Claude</div>
            <div className="cost-value">{usd(claudeCost)}</div>
            <div className="cost-sub">
              {claude.length === 0
                ? 'no Claude calls in this period'
                : `${claude.reduce((s, m) => s + m.calls, 0)} calls · ${compact(
                    claude.reduce((s, m) => s + m.inputTokens + m.outputTokens, 0),
                  )} tokens`}
            </div>
          </div>
          <div className="cost-tile">
            <div className="cost-label">Tokens in / out</div>
            <div className="cost-value">{compact(t.inputTokens + t.cacheReadTokens + t.cacheWriteTokens)} / {compact(t.outputTokens)}</div>
            <div className="cost-sub">
              {t.cacheReadTokens > 0 ? `${compact(t.cacheReadTokens)} read from cache` : 'no cached input'}
            </div>
          </div>
          <div className="cost-tile">
            <div className="cost-label">Calls</div>
            <div className="cost-value">{t.calls.toLocaleString()}</div>
            <div className="cost-sub">
              {t.failed > 0 ? `${t.failed} failed (no tokens billed)` : 'none failed'}
            </div>
          </div>
        </div>

        {nothing ? (
          <div className="center-empty">
            No model calls recorded {data.range === 'today' ? 'today' : 'in this period'}.
            {data.trackedSince === null && ' Token tracking starts with the next call.'}
          </div>
        ) : (
          <>
            {data.daily.length > 1 && <DailyCost days={data.daily} />}

            <div className="cost-tables">
              <table className="cost-table">
                <caption>By model</caption>
                <thead>
                  <tr><th>Model</th><th className="num">Calls</th><th className="num">In</th><th className="num">Out</th><th className="num">Cost</th></tr>
                </thead>
                <tbody>
                  {data.byModel.map((m) => (
                    <tr key={`${m.provider}:${m.model}`}>
                      <td>
                        <div>{m.model || '—'}</div>
                        <div className="muted cost-sub">{m.providerLabel}</div>
                      </td>
                      <td className="num">{m.calls}{m.failed > 0 && <span className="muted"> ({m.failed} failed)</span>}</td>
                      <td className="num">{compact(m.inputTokens + m.cacheReadTokens + m.cacheWriteTokens)}</td>
                      <td className="num">{compact(m.outputTokens)}</td>
                      <td className="num"><CostCell b={m} priced={m.priced} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <table className="cost-table">
                <caption>By job</caption>
                <thead>
                  <tr><th>Job</th><th className="num">Calls</th><th className="num">Tokens</th><th className="num">Cost</th></tr>
                </thead>
                <tbody>
                  {data.byPurpose.map((p) => (
                    <tr key={p.purpose}>
                      <td>{p.label}</td>
                      <td className="num">{p.calls}</td>
                      <td className="num">{compact(p.inputTokens + p.cacheReadTokens + p.cacheWriteTokens + p.outputTokens)}</td>
                      <td className="num"><CostCell b={p} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      <Explainer title="How these costs are estimated">
        <p>
          Every model call records the tokens the provider reported, and each call is priced
          at that model&rsquo;s list rate as of {data.pricesAsOf}. For Claude, cached input
          read back costs a tenth of the input rate and a cache write costs 1.25&times;.
        </p>
        <p>
          <strong>These are estimates, not a bill.</strong> The free Gemini tier is not charged
          at all, so a deployment inside it pays nothing; the figure is what the same usage
          would cost on a paid plan. A model with no published rate shows its tokens with no
          cost rather than a guess, and a failed call is counted but carries no tokens.
        </p>
        {data.trackedSince && (
          <p>
            Token tracking began {data.trackedSince.slice(0, 10)}. Calls before that were
            counted but not measured, so a range reaching further back understates the total.
          </p>
        )}
        <table className="cost-table cost-rates">
          <caption>Rates used, US$ per million tokens</caption>
          <thead>
            <tr><th>Model</th><th className="num">Input</th><th className="num">Output</th><th className="num">Cache read</th></tr>
          </thead>
          <tbody>
            {data.rates.map((r) => (
              <tr key={`${r.provider}:${r.model}`}>
                <td>{r.model}{r.longPromptThreshold && <span className="muted"> (higher above {compact(r.longPromptThreshold)} prompt tokens)</span>}</td>
                <td className="num">{r.input.toFixed(2)}</td>
                <td className="num">{r.output.toFixed(2)}</td>
                <td className="num">{r.cacheRead.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Explainer>
    </Section>
  )
}
