import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { Icons, Loading, Section } from '~/components/ui'
import {
  approveReport,
  deleteReport,
  editSection,
  exportReportUrl,
  exportToGoogleDocs,
  generateReport,
  quartersQueryOptions,
  reportGoogleDocQueryOptions,
  reportQueryOptions,
  reportsQueryOptions,
  setSectionApproval,
} from '~/lib/api'
import { UnauthenticatedError } from '~/lib/auth'
import { useAuth } from '~/lib/auth-context'
import { useReveal } from '~/lib/motion'
import type { Report, ReportSection } from '~/lib/types'

export const Route = createFileRoute('/publish')({
  head: () => ({ meta: [{ title: 'Quarterly reports · MIOS' }] }),
  component: PublishScreen,
})

function PublishScreen() {
  const qc = useQueryClient()
  const { refresh } = useAuth()
  const [selected, setSelected] = useState<string | null>(null)
  const [quarter, setQuarter] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draftBody, setDraftBody] = useState('')

  const scope = useRef<HTMLDivElement>(null)
  const list = useQuery(reportsQueryOptions)
  const quarters = useQuery(quartersQueryOptions)
  const report = useQuery(reportQueryOptions(selected))

  // Keyed on which report is open, so switching quarters replays the reveal —
  // but editing or approving a section does not restage the whole document
  // underneath the cursor, which would be actively annoying mid-review.
  useReveal(scope, '.doc-section', { key: selected ?? 'none', delay: 0.08, stagger: 0.06 })
  useReveal(scope, '.doc-toc li', { key: selected ?? 'none', delay: 0.05, stagger: 0.03, y: 6 })
  useReveal(scope, '.report-list li', { key: list.data?.reports.length ?? 0, delay: 0.1, y: 6, max: 10 })

  // Open the newest report on arrival, so the page is never an empty shell when
  // there is something to read.
  useEffect(() => {
    const first = list.data?.reports[0]
    if (!selected && first) setSelected(first.id)
  }, [list.data, selected])

  useEffect(() => {
    if (!quarter && quarters.data) setQuarter(quarters.data.currentQuarter)
  }, [quarters.data, quarter])

  const refreshAll = (r: Report) => {
    setError(null)
    qc.setQueryData(['publish', 'report', r.id], r)
    void qc.invalidateQueries({ queryKey: ['publish', 'reports'] })
  }

  const generate = useMutation({
    mutationFn: () => generateReport(quarter),
    onSuccess: (r) => { setSelected(r.id); refreshAll(r) },
    onError: (e: Error) => setError(e.message),
  })

  const save = useMutation({
    mutationFn: ({ id, body }: { id: string; body: string }) => editSection(id, body),
    onSuccess: (r) => { setEditing(null); refreshAll(r) },
    onError: (e: Error) => setError(e.message),
  })

  const approve = useMutation({
    mutationFn: ({ id, value }: { id: string; value: boolean }) => setSectionApproval(id, value),
    onSuccess: refreshAll,
    onError: (e: Error) => setError(e.message),
  })

  const signOff = useMutation({
    mutationFn: (id: string) => approveReport(id),
    onSuccess: refreshAll,
    onError: (e: Error) => setError(e.message),
  })

  const remove = useMutation({
    mutationFn: deleteReport,
    onSuccess: () => {
      setSelected(null)
      void qc.invalidateQueries({ queryKey: ['publish', 'reports'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  if (list.error instanceof UnauthenticatedError) {
    return (
      <div className="page">
        <div className="center-empty">
          Your session has expired.
          <div style={{ marginTop: 12 }}>
            <button className="btn" onClick={() => void refresh()}>Sign in again</button>
          </div>
        </div>
      </div>
    )
  }

  const doc = report.data
  const locked = doc?.status === 'approved'

  return (
    <div className="page page-wide" ref={scope}>
      <div className="page-header">
        <div>
          <div className="kicker">Mode Publish · Quarterly reports</div>
          <h1>{doc ? doc.title : 'Quarterly reports'}</h1>
        </div>
        <div className="meta">
          <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', alignItems: 'center' }}>
            <select
              className="select"
              style={{ width: 'auto' }}
              aria-label="Quarter to generate"
              value={quarter}
              onChange={(e) => setQuarter(e.target.value)}
            >
              {(quarters.data?.quarters ?? []).map((q) => (
                <option key={q} value={q}>{q}</option>
              ))}
            </select>
            <button
              className="btn"
              disabled={generate.isPending || !quarter}
              onClick={() => generate.mutate()}
            >
              {generate.isPending ? 'Assembling…' : 'Generate draft'}
            </button>
          </div>
          {doc && (
            <div style={{ marginTop: 6 }}>
              {doc.signalsAnalysed} signals analysed · {doc.sectionsApproved}/{doc.sectionsTotal} approved
            </div>
          )}
        </div>
      </div>

      <div aria-live="polite">
        {error && (
          <div className="intake-note err" role="alert">
            <span className="ico" aria-hidden="true">{Icons.alert}</span>
            <div><h4>That didn’t work</h4><p>{error}</p></div>
          </div>
        )}
      </div>

      {list.isLoading && <Loading lines={['Looking for reports…']} />}

      {!list.isLoading && !list.data?.reports.length && (
        <Section title="No reports yet">
          <div className="center-empty">
            Choose a quarter above and generate a draft. Every figure is counted
            from the signals MIOS collected in that quarter.
          </div>
        </Section>
      )}

      {doc && (
        <>
          {/* The review gate, stated before the document rather than after it. */}
          <div className={`intake-note ${locked ? 'ok' : 'warn'}`}>
            <span className="ico" aria-hidden="true">{locked ? Icons.check : Icons.alert}</span>
            <div>
              <h4>
                {locked
                  ? `Approved by ${doc.approvedBy} on ${(doc.approvedAt ?? '').slice(0, 10)}`
                  : `Draft — ${doc.outstanding.length} section${doc.outstanding.length === 1 ? '' : 's'} awaiting review`}
              </h4>
              {locked ? (
                <p>
                  This report is final and can no longer be edited. Distribution to
                  clients happens outside MIOS — export it below and send it yourself.
                </p>
              ) : (
                <>
                  <p>
                    Every section must be read and approved before the report can be
                    signed off. MIOS does not distribute anything itself.
                  </p>
                  {doc.outstanding.length > 0 && (
                    <ul>{doc.outstanding.map((h) => <li key={h}>{h}</li>)}</ul>
                  )}
                </>
              )}
            </div>
          </div>

          {/* Where the wording came from. The figures are counted either way,
              but a reader signing this deserves to know a model touched it. */}
          <p className="prose-source">
            {doc.proseSource === 'gemini'
              ? 'Figures counted from signals · wording drafted by Gemini · every number checked against the data'
              : 'Figures and wording both computed directly from the signals'}
            {doc.proseNote ? ` — ${doc.proseNote}` : ''}
          </p>

          <div className="doc-shell">
            {/* ---- Contents ---- */}
            <nav className="doc-toc" aria-label="Report contents">
              <div className="doc-label">Contents</div>
              <ol>
                {doc.sections.map((s, i) => (
                  <li key={s.id} className={s.approved ? 'done' : undefined}>
                    {/* A link, because a quarterly report runs to twenty sections
                        and scrolling to the ninth is not navigation. */}
                    <a href={`#sec-${s.id}`}>
                      <span className="mono num">{String(i + 1).padStart(2, '0')}</span>
                      <span className="toc-heading">{s.heading}</span>
                      <span className="toc-state" aria-hidden="true">
                        {s.approved ? '✓' : s.empty ? '—' : '·'}
                      </span>
                    </a>
                  </li>
                ))}
              </ol>
            </nav>

            {/* ---- The document ---- */}
            <article className="doc-body">
              <div className="doc-label">
                Easy Skill Market Intelligence · {locked ? 'Approved' : 'Draft'}
              </div>
              <h1>{doc.title}</h1>
              <p className="muted">Australia · Papua New Guinea · {doc.quarter}</p>

              {doc.sections.map((s) => (
                <SectionBlock
                  key={s.id}
                  section={s}
                  locked={locked}
                  editing={editing === s.id}
                  draftBody={draftBody}
                  busy={save.isPending || approve.isPending}
                  onStartEdit={() => { setEditing(s.id); setDraftBody(s.body) }}
                  onChange={setDraftBody}
                  onCancel={() => setEditing(null)}
                  onSave={() => save.mutate({ id: s.id, body: draftBody })}
                  onToggleApprove={() => approve.mutate({ id: s.id, value: !s.approved })}
                />
              ))}
            </article>

            {/* ---- Review rail ---- */}
            <aside className="doc-rail" aria-label="Review and export">
              <div className="doc-label">Review</div>
              <p className="muted" style={{ fontSize: 12.5, marginTop: 0 }}>
                {doc.sectionsApproved} of {doc.sectionsTotal} sections approved.
              </p>

              <button
                className="btn rust"
                style={{ width: '100%', justifyContent: 'center' }}
                disabled={!doc.canApprove || signOff.isPending}
                onClick={() => signOff.mutate(doc.id)}
              >
                {locked ? 'Approved' : signOff.isPending ? 'Signing off…' : 'Approve report'}
              </button>
              {!locked && !doc.canApprove && (
                <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                  Approve every section first.
                </p>
              )}

              <div className="hr" />
              <div className="doc-label">Export</div>
              <div style={{ display: 'grid', gap: 6 }}>
                <a className="btn sm" href={exportReportUrl(doc.id, 'html')} target="_blank" rel="noopener">
                  {Icons.ext} Open printable
                </a>
                <a className="btn sm" href={exportReportUrl(doc.id, 'md')}>
                  {Icons.publish} Download Markdown
                </a>
              </div>
              <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
                A draft exports with a “not approved” banner. Use the printable
                view and your browser’s Print → Save as PDF.
              </p>
              <GoogleDocExport reportId={doc.id} approved={locked} />

              <div className="hr" />
              <div className="doc-label">Reports</div>
              <ul className="report-list">
                {(list.data?.reports ?? []).map((r) => (
                  <li key={r.id}>
                    <button
                      className={r.id === selected ? 'active' : undefined}
                      onClick={() => { setSelected(r.id); setEditing(null) }}
                    >
                      <strong>{r.quarter}</strong>
                      <span className="muted">
                        {r.status === 'approved'
                          ? 'approved'
                          : `${r.sectionsApproved}/${r.sectionsTotal}`}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              <button
                className="btn sm ghost"
                style={{ marginTop: 8 }}
                disabled={remove.isPending}
                onClick={() => remove.mutate(doc.id)}
              >
                Delete this draft
              </button>
            </aside>
          </div>
        </>
      )}
    </div>
  )
}

/** A section body: paragraphs, "- " lists, "|" tables and "###" subheadings —
 *  the structures the report builder writes, rendered the same way as the
 *  printable export so the screen and the page show one document. */
function ReportBody({ text }: { text: string }) {
  const blocks = text.split('\n\n').map((b) => b.trim()).filter(Boolean)
  return (
    <>
      {blocks.map((block, i) => {
        const lines = block.split('\n')
        if (lines.every((l) => l.trimStart().startsWith('|'))) {
          const rows = lines
            .map((l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim()))
            .filter((r) => !r.every((c) => /^[-: ]+$/.test(c)))
          const [head, ...body] = rows
          if (!head) return null
          return (
            <div key={i} className="doc-table-wrap">
              <table className="doc-table">
                <thead><tr>{head.map((c, j) => <th key={j}>{c}</th>)}</tr></thead>
                <tbody>
                  {body.map((r, k) => (
                    <tr key={k}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
        if (lines.every((l) => l.trimStart().startsWith('- '))) {
          return (
            <ul key={i}>
              {lines.map((l, j) => <li key={j}>{l.trimStart().slice(2)}</li>)}
            </ul>
          )
        }
        if (block.startsWith('### ')) return <h3 key={i} className="doc-sub">{block.slice(4)}</h3>
        return <p key={i}>{block}</p>
      })}
    </>
  )
}

function SectionBlock({
  section, locked, editing, draftBody, busy,
  onStartEdit, onChange, onCancel, onSave, onToggleApprove,
}: {
  section: ReportSection
  locked: boolean
  editing: boolean
  draftBody: string
  busy: boolean
  onStartEdit: () => void
  onChange: (v: string) => void
  onCancel: () => void
  onSave: () => void
  onToggleApprove: () => void
}) {
  return (
    <section className="doc-section" id={`sec-${section.id}`}>
      <div className="doc-section-h">
        <h2>{section.heading}</h2>
        <div className="doc-section-tools">
          {section.source === 'manual' && (
            <span className="chip" title="The data cannot supply this — a reviewer writes it">
              Written by you
            </span>
          )}
          {section.rewritten && !section.edited && (
            <span className="chip" title="Gemini reworded the computed text; no figure changed">
              Reworded
            </span>
          )}
          {section.edited && <span className="chip teal">Edited</span>}
          {section.approved && (
            <span className="chip teal" title={`Approved by ${section.approvedBy}`}>
              ✓ Approved
            </span>
          )}
        </div>
      </div>

      {editing ? (
        <>
          <textarea
            className="textarea"
            value={draftBody}
            aria-label={`Edit ${section.heading}`}
            onChange={(e) => onChange(e.target.value)}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button className="btn sm rust" disabled={busy} onClick={onSave}>
              {busy ? 'Saving…' : 'Save'}
            </button>
            <button className="btn sm ghost" onClick={onCancel}>Cancel</button>
            <span className="muted" style={{ fontSize: 12, alignSelf: 'center' }}>
              Saving clears this section’s approval.
            </span>
          </div>
        </>
      ) : (
        <>
          {section.body.trim()
            ? <ReportBody text={section.body} />
            : (
              <p className="doc-empty">
                This section has not been written yet. The outlook is a judgement,
                not something the signal data can supply.
              </p>
            )}
          {!locked && (
            <div className="doc-section-actions">
              <button className="btn sm" disabled={busy} onClick={onStartEdit}>Edit</button>
              <button
                className={section.approved ? 'btn sm ghost' : 'btn sm rust'}
                disabled={busy || (!section.approved && section.empty)}
                onClick={onToggleApprove}
                title={section.empty ? 'Write this section before approving it' : undefined}
              >
                {section.approved ? 'Withdraw approval' : 'Approve section'}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  )
}

function sentOn(iso: string): string {
  return new Date(iso).toLocaleString('en-AU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

/** Send the report to its Google Doc. The first send creates the Doc; later
 *  sends replace its contents, so a link shared once keeps working. */
function GoogleDocExport({ reportId, approved }: { reportId: string; approved: boolean }) {
  const qc = useQueryClient()
  const { session } = useAuth()
  const status = useQuery(reportGoogleDocQueryOptions(reportId))
  const [problem, setProblem] = useState<string | null>(null)

  const send = useMutation({
    mutationFn: () => exportToGoogleDocs(reportId),
    onSuccess: (r) => {
      qc.setQueryData(reportGoogleDocQueryOptions(reportId).queryKey, (old) => ({ ...old, ...r }))
      setProblem(null)
    },
    onError: (e: Error) => setProblem(e.message),
  })

  if (status.isPending || status.error || !status.data) return null
  const { connected, export: sent } = status.data

  if (!connected) {
    return (
      <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
        Google Docs is not connected.{' '}
        {session?.isAdmin
          ? <>Connect it under <Link to="/integrations">Integrations</Link>.</>
          : 'An administrator can connect it under Integrations.'}
      </p>
    )
  }

  const onSend = () => {
    if (sent && !window.confirm(
      'This replaces the contents of the Google Doc with the report as it is in MIOS now. '
      + 'Edits made inside Google Docs will be lost; comments may lose their place. Continue?')) return
    send.mutate()
  }

  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ display: 'grid', gap: 6 }}>
        <button className="btn sm" disabled={send.isPending} onClick={onSend}>
          {Icons.publish} {send.isPending ? 'Sending to Google Docs…' : sent ? 'Update Google Doc' : 'Send to Google Docs'}
        </button>
        {sent && (
          <a className="btn sm ghost" href={sent.url} target="_blank" rel="noopener">
            {Icons.ext} Open Google Doc
          </a>
        )}
      </div>
      {problem && <p className="notice err" role="alert" style={{ fontSize: 12, marginTop: 8 }}>{problem}</p>}
      {sent && !problem && (
        <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          {send.data?.export?.replacedLink
            ? 'The earlier Doc could not be reached, so a new one was made — share this new link. '
            : ''}
          Sent {sentOn(sent.exportedAt)}{sent.exportedBy ? ` by ${sent.exportedBy}` : ''}
          {!approved && ' · marked DRAFT until approved'}.
        </p>
      )}
    </div>
  )
}
