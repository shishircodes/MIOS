import { createFileRoute } from '@tanstack/react-router'
import { BarBlock, Section, SparkBar } from '~/components/ui'
import { hist } from '~/data/mock'

export const Route = createFileRoute('/dashboard')({
  component: DashboardScreen,
})

function DashboardScreen() {
  const sectors = Object.entries(hist.SECTORS)
  const maxSector = Math.max(...sectors.map(([, v]) => v))

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Reference · Trends Dashboard</div>
          <h1>12-week hiring trends</h1>
        </div>
        <div className="meta"><div>Rolling 12 weeks · AU + PNG</div></div>
      </div>

      <div className="kpi-row">
        <div className="kpi">
          <div className="label">AU roles · this week</div>
          <div className="val tnum">{hist.AU[hist.AU.length - 1]}</div>
          <div className="delta up">↑ trending</div>
        </div>
        <div className="kpi">
          <div className="label">PNG roles · this week</div>
          <div className="val tnum">{hist.PNG[hist.PNG.length - 1]}</div>
          <div className="delta up">↑ trending</div>
        </div>
        <div className="kpi">
          <div className="label">Sectors tracked</div>
          <div className="val tnum">{sectors.length}</div>
          <div className="delta flat">Mining-led</div>
        </div>
        <div className="kpi">
          <div className="label">Watchlist companies</div>
          <div className="val tnum">20</div>
          <div className="delta flat">10 / 7 / 3</div>
        </div>
      </div>

      <Section title="Australia — 12-week hiring velocity">
        <div style={{ padding: '20px 22px' }}>
          <SparkBar data={[...hist.AU]} color="var(--ink)" height={80} />
        </div>
      </Section>

      <Section title="Papua New Guinea — 12-week hiring velocity">
        <div style={{ padding: '20px 22px' }}>
          <SparkBar data={[...hist.PNG]} color="var(--moss)" height={80} />
        </div>
      </Section>

      <Section title="Roles by sector · this week">
        <div style={{ padding: '16px 22px' }}>
          {sectors.map(([name, v]) => (
            <BarBlock key={name} label={name} value={v} max={maxSector} />
          ))}
        </div>
      </Section>
    </div>
  )
}
