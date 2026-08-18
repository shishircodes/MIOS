import { createFileRoute } from '@tanstack/react-router'
import { Section } from '~/components/ui'
import { sources } from '~/data/mock'

export const Route = createFileRoute('/sources')({
  head: () => ({ meta: [{ title: 'Sources · MIOS' }] }),
  component: SourcesScreen,
})

function dot(sla: string) {
  if (sla === 'OK') return <span className="dot-ok" />
  if (sla === 'WARN') return <span className="dot-warn" />
  if (sla === 'OFF') return <span className="dot-err" />
  return <span className="dot-err" />
}

function SourcesScreen() {
  const ok = sources.filter((s) => s.sla === 'OK').length
  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Admin · Source Health</div>
          <h1>Data sources</h1>
        </div>
        <div className="meta">
          <div>{ok}/{sources.length} healthy</div>
          <div style={{ marginTop: 4 }}>PNGworkforce · SEEK · Adzuna</div>
        </div>
      </div>

      <Section title="Connectors" tools={<span>{sources.length} SOURCES</span>}>
        <div className="src-row" style={{ fontFamily: 'var(--mono)', fontSize: 10.5, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--muted)' }}>
          <div>Source</div><div>Type / region</div><div className="num">Records</div><div className="num">Uptime</div><div>Last run</div>
        </div>
        {sources.map((s) => (
          <div className="src-row" key={s.name}>
            <div className="name">{dot(s.sla)} <span style={{ marginLeft: 8 }}>{s.name}</span></div>
            <div className="muted">{s.type} · {s.region}</div>
            <div className="num"><strong>{s.records}</strong></div>
            <div className="num">{s.uptime}</div>
            <div className="muted mono" style={{ fontSize: 11 }}>{s.last}</div>
          </div>
        ))}
      </Section>
    </div>
  )
}
