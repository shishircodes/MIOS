import { Icons, TierChip } from '~/components/ui'
import type { Signal } from '~/lib/types'

export function SignalDetail({ s }: { s: Signal }) {
  const tag = (label: string) => (
    <div style={{ fontFamily: 'var(--mono)', fontSize: 10.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: 6, marginTop: 18 }}>
      {label}
    </div>
  )

  return (
    <>
      <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
        <TierChip tier={s.tier} />
        <span className="chip">{s.sector}</span>
        <span className="chip">{s.region}</span>
        <span className="chip">{s.cycle}</span>
      </div>
      <h2 style={{ fontSize: 20, margin: '0 0 6px', fontWeight: 500 }}>{s.company}</h2>
      <p style={{ fontSize: 14, color: 'var(--ink-2)', marginTop: 0 }}>{s.title}</p>
      <div className="hr" />
      <div className="row-2" style={{ marginBottom: 4, fontSize: 12 }}>
        <div>
          <div className="muted" style={{ fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Source</div>
          <div style={{ fontWeight: 500, marginTop: 4 }}>{s.source}</div>
        </div>
        <div>
          <div className="muted" style={{ fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Confidence</div>
          <div style={{ fontFamily: 'var(--mono)', fontWeight: 500, marginTop: 4 }}>{s.conf}/100</div>
        </div>
      </div>
      {tag('Detection')}
      <p style={{ fontSize: 13, color: 'var(--ink-2)' }}>{s.desc}</p>
      {s.action && (
        <>
          {tag('Recommended action / analyst note')}
          <div className="pull">{s.action}</div>
        </>
      )}
      {tag('Classified by')}
      <p style={{ fontSize: 12.5, color: 'var(--ink-3)', fontFamily: 'var(--mono)' }}>
        Reviewed automatically · {s.cycle} review cycle
      </p>
      <div style={{ display: 'flex', gap: 8, marginTop: 24 }}>
        <button className="btn primary">{Icons.push} Push to match</button>
        {s.sourceUrl && (
          <a className="btn" href={s.sourceUrl} target="_blank" rel="noopener">
            {Icons.ext} Open source
          </a>
        )}
        <button className="btn ghost">{Icons.archive} Archive</button>
      </div>
    </>
  )
}
