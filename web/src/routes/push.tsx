import { createFileRoute } from '@tanstack/react-router'
import { Icons, Section } from '~/components/ui'
import { matches } from '~/data/mock'

export const Route = createFileRoute('/push')({
  component: PushScreen,
})

function PushScreen() {
  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Mode Push · Profile-to-Client matching</div>
          <h1>MPC matches</h1>
        </div>
        <div className="meta">
          <div>Active profile: Senior Maintenance Planner</div>
          <div style={{ marginTop: 4 }}>12 yrs · Mining · available Jun 2026</div>
        </div>
      </div>

      <Section title="Ranked matches" tools={<span>{matches.length} RESULTS</span>}>
        {matches.map((m) => (
          <div className="match-row" key={m.rank}>
            <div className="rank">{m.rank}</div>
            <div>
              <p className="co-name">{m.co}</p>
              <div className="co-meta">
                <span>{m.rel}</span><span>·</span><span>{m.region}</span><span>·</span><span>{m.sector}</span>
              </div>
              <ul className="ev-list">
                {m.evidence.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </div>
            <div className="score">
              <span className="big">{m.score}</span>
              match score
              <div style={{ marginTop: 10 }}>
                <button className="btn rust sm">{Icons.push} {m.action}</button>
              </div>
            </div>
          </div>
        ))}
      </Section>
    </div>
  )
}
