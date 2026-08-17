import { createFileRoute } from '@tanstack/react-router'
import { BarBlock, Section } from '~/components/ui'
import { tokens } from '~/data/mock'

export const Route = createFileRoute('/tokens')({
  head: () => ({ meta: [{ title: 'Tokens · MIOS' }] }),
  component: TokensScreen,
})

function TokensScreen() {
  const maxCost = Math.max(...tokens.perAgent.map((a) => a.cost))
  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Admin · Token usage &amp; cost</div>
          <h1>Model spend</h1>
        </div>
        <div className="meta"><div>Week to date</div></div>
      </div>

      <div className="kpi-row">
        <div className="kpi">
          <div className="label">Tokens in · 7d</div>
          <div className="val tnum">{(tokens.weekIn / 1000).toFixed(0)}k</div>
          <div className="delta flat">prompt</div>
        </div>
        <div className="kpi">
          <div className="label">Tokens out · 7d</div>
          <div className="val tnum">{(tokens.weekOut / 1000).toFixed(0)}k</div>
          <div className="delta flat">completion</div>
        </div>
        <div className="kpi">
          <div className="label">Cost · 7d</div>
          <div className="val tnum">A${tokens.weekCost.toFixed(2)}</div>
          <div className="delta up">within budget</div>
        </div>
        <div className="kpi">
          <div className="label">Cost · month</div>
          <div className="val tnum">A${tokens.monthCost.toFixed(0)}</div>
          <div className="delta flat">projected</div>
        </div>
      </div>

      <Section title="Cost by agent">
        <div style={{ padding: '16px 22px' }}>
          {tokens.perAgent.map((a) => (
            <BarBlock key={a.agent} label={`${a.agent} · ${a.model}`} value={a.cost} max={maxCost} suffix=" A$" />
          ))}
        </div>
        <table className="tbl">
          <thead>
            <tr><th>Agent</th><th>Model</th><th className="num">Calls</th><th className="num">Cost</th><th className="num">Share</th></tr>
          </thead>
          <tbody>
            {tokens.perAgent.map((a) => (
              <tr key={a.agent}>
                <td><strong>{a.agent}</strong></td>
                <td className="muted">{a.model}</td>
                <td className="num">{a.calls}</td>
                <td className="num">A${a.cost.toFixed(2)}</td>
                <td className="num">{a.share}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </div>
  )
}
