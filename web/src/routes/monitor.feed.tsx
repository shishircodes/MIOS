import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { Icons, Section, TierChip } from '~/components/ui'
import { digestQueryOptions } from '~/lib/api'

export const Route = createFileRoute('/monitor/feed')({
  component: SignalFeed,
})

const REGIONS = ['ALL', 'AU', 'PNG'] as const
const CYCLES = ['ALL', 'WEEKLY', 'MONTHLY', 'QUARTERLY'] as const

function SignalFeed() {
  const { data, isLoading } = useQuery(digestQueryOptions)
  const [region, setRegion] = useState<string>('ALL')
  const [cycle, setCycle] = useState<string>('ALL')

  const signals = data?.signals ?? []
  const filtered = signals.filter(
    (s) => (region === 'ALL' || s.region === region) && (cycle === 'ALL' || s.cycle === cycle),
  )

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="kicker">Mode Monitor · Signal Feed</div>
          <h1>All signals — live</h1>
        </div>
        <div className="meta">
          <div>{isLoading ? 'Loading…' : `${signals.length} classified signals`}</div>
          <div style={{ marginTop: 4 }}>
            <span className="chip"><span className="dot-ok" style={{ marginRight: 4 }} /> PIPELINE OK</span>
          </div>
        </div>
      </div>

      <div className="section" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 16, padding: '10px 16px', alignItems: 'center', flexWrap: 'wrap', fontSize: 12 }}>
          <span className="mono muted" style={{ fontSize: 10.5, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Filter</span>
          <FilterGroup label="Region" value={region} options={[...REGIONS]} onChange={setRegion} />
          <FilterGroup label="Cycle" value={cycle} options={[...CYCLES]} onChange={setCycle} />
        </div>
      </div>

      <Section title={`${filtered.length} signals`}>
        {filtered.length === 0 && <div className="center-empty">No signals match this filter.</div>}
        {filtered.map((s) => (
          <div className="signal" key={s.id}>
            <div className="num mono">{s.n}</div>
            <div className="body">
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
                <TierChip tier={s.tier} />
                <span className="chip">{s.sector.toUpperCase()}</span>
                <span className="chip">{s.cycle}</span>
                <span className="chip">{s.region}</span>
              </div>
              <p className="title">{s.company} — {s.title}</p>
              <p className="desc">{s.desc}</p>
              {s.action && <div className="action">→ {s.action}</div>}
            </div>
            <div className="meta-col">
              <div>conf {s.conf}</div>
              <div style={{ color: 'var(--ink-3)' }}>{s.source}</div>
            </div>
          </div>
        ))}
      </Section>
    </div>
  )
}

function FilterGroup({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
      <span className="muted" style={{ fontSize: 11 }}>{label}:</span>
      <div style={{ display: 'flex', border: '1px solid var(--line-2)', background: 'var(--surface-2)', borderRadius: 6, overflow: 'hidden' }}>
        {options.map((o) => (
          <button
            key={o}
            onClick={() => onChange(o)}
            style={{
              padding: '3px 9px', fontSize: 11, fontFamily: 'var(--mono)', letterSpacing: '0.04em',
              border: 'none', borderRight: '1px solid var(--line)',
              background: value === o ? 'var(--ink)' : 'transparent',
              color: value === o ? 'var(--surface-2)' : 'var(--ink-3)', cursor: 'pointer',
            }}
          >{o}</button>
        ))}
      </div>
    </div>
  )
}
