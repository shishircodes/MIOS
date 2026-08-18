import { createFileRoute } from '@tanstack/react-router'
import { Icons } from '~/components/ui'

export const Route = createFileRoute('/publish')({
  head: () => ({ meta: [{ title: 'Publish · MIOS' }] }),
  component: PublishScreen,
})

const TOC = [
  'Executive Summary',
  'Australia — Mining & Resources',
  'Australia — Construction',
  'Papua New Guinea',
  'Skills Demand Heatmap',
  'Looking Ahead — Q2 2026',
  'Methodology',
]

function PublishScreen() {
  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Mode Publish · Quarterly Market Report</div>
          <h1>Q1 2026 Report</h1>
        </div>
        <div className="meta">
          <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
            <button className="btn sm">{Icons.ext} Export PDF</button>
            <button className="btn sm rust">{Icons.check} Publish to HubSpot</button>
          </div>
        </div>
      </div>

      <div className="doc-shell" style={{ display: 'grid', gridTemplateColumns: '220px 1fr 280px', border: '1px solid var(--line)', overflow: 'hidden', background: 'var(--surface)' }}>
        <div className="doc-toc" style={{ borderRight: '1px solid var(--line)', padding: '16px 14px', background: 'var(--paper)' }}>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.10em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: 10 }}>Contents</div>
          <ol style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {TOC.map((t, i) => (
              <li key={i} style={{ padding: '6px 6px', fontSize: 12.5, color: i === 0 ? 'var(--ink)' : 'var(--ink-2)', borderLeft: i === 0 ? '2px solid var(--moss)' : '2px solid transparent', background: i === 0 ? 'var(--surface-2)' : 'transparent', fontWeight: i === 0 ? 600 : 400 }}>
                <span className="mono muted" style={{ fontSize: 10, marginRight: 8 }}>{String(i + 1).padStart(2, '0')}</span>{t}
              </li>
            ))}
          </ol>
        </div>

        <div style={{ padding: '44px 60px', background: 'var(--surface-2)', lineHeight: 1.65, overflowY: 'auto', maxHeight: 720 }}>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.10em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: 24 }}>Easy Skill Market Intelligence · Confidential</div>
          <h1 style={{ fontFamily: 'var(--display)', fontWeight: 700, fontSize: 28, letterSpacing: '-0.02em', margin: '0 0 4px' }}>Industrial Workforce Trends — Q1 2026</h1>
          <p style={{ color: 'var(--muted)' }}>Australia · Papua New Guinea · Pacific</p>
          <h2 style={{ fontFamily: 'var(--display)', fontWeight: 600, fontSize: 18, marginTop: 26, paddingTop: 14, borderTop: '1px solid var(--line)' }}>Executive Summary</h2>
          <p>The first quarter of 2026 showed moderate hiring growth across Australia's industrial sectors, with mining maintenance roles in Western Australia driving the strongest demand. Papua New Guinea's resource sector remains in a preparatory phase, with several large-scale projects approaching final investment decisions.</p>
          <div className="pull">Mining accounted for 54% of detected postings, followed by Construction (18%), Oil &amp; Gas (12%), Defence (9%), and Energy Transition (7%).</div>
          <p style={{ color: 'var(--muted)', fontFamily: 'var(--mono)', fontSize: 11 }}>[ Draft assembled by the Report Generator agent — pending human review ]</p>
        </div>

        <div style={{ borderLeft: '1px solid var(--line)', padding: 14, background: 'var(--paper)', fontSize: 12 }}>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.10em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: 10 }}>Review status</div>
          <p className="muted">3 sections approved · 4 pending. Quarterly report distributes to client list via HubSpot Marketing once approved.</p>
        </div>
      </div>
    </div>
  )
}
