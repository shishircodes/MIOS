import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Section, SkeletonCard } from '~/components/ui'
import { pipelineSettingsQueryOptions, savePipelineSettings, sourceHealthQueryOptions } from '~/lib/api'
import type { PipelineSetting, PipelineSettings } from '~/lib/types'

function when(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('en-AU', { day: 'numeric', month: 'short' })
}

/** How many records each run takes from each source, and how those records
 *  are sent to the AI. Every number used to be a constant in the code. */
export function RunLimitsPanel({ active }: { active: string[] }) {
  const qc = useQueryClient()
  const { data, isPending, error } = useQuery(pipelineSettingsQueryOptions)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [problem, setProblem] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const all = useMemo(() => (data ? [...data.sources, ...data.classifier] : []), [data])
  const reset = (d: PipelineSettings) =>
    setDraft(Object.fromEntries([...d.sources, ...d.classifier].map((s) => [s.key, String(s.value)])))
  useEffect(() => { if (data) reset(data) }, [data])

  const save = useMutation({
    mutationFn: (values: Record<string, number>) => savePipelineSettings(values),
    onSuccess: (p) => {
      qc.setQueryData(pipelineSettingsQueryOptions.queryKey, p)
      // The collectors table shows each source's limit too.
      void qc.invalidateQueries({ queryKey: sourceHealthQueryOptions.queryKey })
      setProblem(null)
      setNote(p.changed && p.changed.length
        ? `Saved ${p.changed.length} change${p.changed.length === 1 ? '' : 's'}. They apply from the next run.`
        : 'Nothing had changed.')
    },
    onError: (e: Error) => { setProblem(e.message); setNote(null) },
  })

  if (isPending) return <SkeletonCard rows={4} />
  if (error || !data) {
    return (
      <Section title="Collection limits">
        <div className="notice err">Could not load the run limits. {error?.message}</div>
      </Section>
    )
  }

  const invalid = (s: PipelineSetting) => {
    const raw = draft[s.key] ?? ''
    const n = Number(raw)
    return raw.trim() === '' || !Number.isInteger(n) || n < s.min || n > s.max
  }
  const changed = all.filter((s) => draft[s.key] !== undefined && Number(draft[s.key]) !== s.value)
  const bad = all.filter(invalid)
  const atDefaults = all.every((s) => Number(draft[s.key]) === s.default)

  // Recomputed from the form, so the effect of a change shows before saving.
  const num = (key: string) => Number(draft[key] ?? 0) || 0
  const perDay = num('classify.batch_size') * num('classify.daily_calls')
  // Only the sources the next run will use. A board waiting on an Apify actor
  // keeps its limit, but a field for it here would be a number that does
  // nothing, among thirty.
  const shown = data.sources.filter((s) => active.includes(s.source))
  const hidden = data.sources.length - shown.length
  const scrapeTotal = shown.reduce((n, s) => n + num(s.key), 0)

  const field = (s: PipelineSetting, label: string) => (
    <div className="limit-field" key={s.key}>
      <label htmlFor={`lim-${s.key}`}>{label}</label>
      <div className="limit-input">
        <input
          id={`lim-${s.key}`}
          type="number"
          inputMode="numeric"
          min={s.min}
          max={s.max}
          step={1}
          className={`input${invalid(s) ? ' flagged' : ''}`}
          value={draft[s.key] ?? ''}
          aria-describedby={`lim-${s.key}-hint`}
          aria-invalid={invalid(s) || undefined}
          onChange={(e) => setDraft((d) => ({ ...d, [s.key]: e.target.value }))}
        />
        <span className="muted">{s.unit}</span>
      </div>
      <div id={`lim-${s.key}-hint`} className="limit-hint">
        {s.min}–{s.max} · default {s.default}
        {s.changedBy && <> · set by {s.changedBy} {when(s.changedAt)}</>}
      </div>
    </div>
  )

  return (
    <Section
      title="Collection limits"
      tools={<span>{changed.length ? `${changed.length} UNSAVED` : 'APPLIES FROM THE NEXT RUN'}</span>}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (bad.length || !changed.length) return
          save.mutate(Object.fromEntries(changed.map((s) => [s.key, Number(draft[s.key])])))
        }}
      >
        {problem && <div className="notice err" role="alert">{problem}</div>}
        {note && !problem && <div className="notice ok" role="status">{note}</div>}

        <div className="limits-block">
          <div className="llm-purpose">Records taken from each source per run</div>
          <p className="llm-needs">
            Up to {scrapeTotal.toLocaleString()} records a run across the {shown.length} sources
            that are switched on{hidden > 0 && <>; the {hidden} that are off keep their limits</>}.
          </p>
          <div className="limit-grid">
            {shown.map((s) => field(s, s.label))}
          </div>
        </div>

        <div className="limits-block">
          <div className="llm-purpose">Sending records to the AI for classification</div>
          <p className="llm-needs">
            At these settings up to <strong>{perDay.toLocaleString()} records a day</strong> can be
            classified.
          </p>
          <div className="limit-grid">
            {data.classifier.map((s) => (
              <div key={s.key}>
                {field(s, s.label)}
                <p className="limit-help">{s.help}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="limits-actions">
          <button className="btn sm rust" type="submit" disabled={save.isPending || !changed.length || bad.length > 0}>
            {save.isPending ? 'Saving…' : 'Save changes'}
          </button>
          <button className="btn sm ghost" type="button" disabled={!changed.length} onClick={() => reset(data)}>
            Undo edits
          </button>
          <button
            className="btn sm ghost"
            type="button"
            disabled={atDefaults}
            onClick={() => setDraft(Object.fromEntries(all.map((s) => [s.key, String(s.default)])))}
          >
            Fill in the defaults
          </button>
          {bad.length > 0 && (
            <span className="llm-warn limit-hint">
              {bad.length} value{bad.length === 1 ? ' is' : 's are'} outside the allowed range.
            </span>
          )}
        </div>
      </form>

    </Section>
  )
}
