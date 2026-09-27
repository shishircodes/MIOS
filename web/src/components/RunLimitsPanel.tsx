import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Explainer, Section } from '~/components/ui'
import { pipelineSettingsQueryOptions, savePipelineSettings, sourceHealthQueryOptions } from '~/lib/api'
import type { PipelineSetting, PipelineSettings } from '~/lib/types'

function when(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('en-AU', { day: 'numeric', month: 'short' })
}

/** How many records each run takes from each source, and how those records
 *  are sent to the AI. Every number used to be a constant in the code. */
export function RunLimitsPanel({ labels }: { labels: Record<string, string> }) {
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

  if (isPending) return null
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
  const scrapeTotal = data.sources.reduce((n, s) => n + num(s.key), 0)

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
            Up to {scrapeTotal.toLocaleString()} records a run across all sources. A run that comes
            back exactly at a source’s limit was probably cut short — the collectors table flags it.
          </p>
          <div className="limit-grid">
            {data.sources.map((s) => field(s, labels[s.source] ?? s.source))}
          </div>
        </div>

        <div className="limits-block">
          <div className="llm-purpose">Sending records to the AI for classification</div>
          <p className="llm-needs">
            At these settings up to <strong>{perDay.toLocaleString()} records a day</strong> can be
            classified. Anything beyond that waits for the next run and is not lost.
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

      <Explainer title="What these numbers change">
        <p>
          A run fetches up to each source’s limit, stores what is new, then sends the new records
          to the AI in batches to be classified. Nothing here changes records already collected.
        </p>
        <p>
          Raising a source’s limit collects more per run, but repeats are not stored twice, so
          the number of <em>new</em> records rarely rises as much. More records also means more
          AI calls: if the day’s calls run out, the rest wait for the next run.
        </p>
        <p>
          The records-per-call limit stops at 50 on purpose: at 100 the model’s answer ran past
          its length limit and a whole batch was lost.
        </p>
      </Explainer>
    </Section>
  )
}
