import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Section } from '~/components/ui'
import { runPipelineNow, saveSchedule, scheduleQueryOptions } from '~/lib/api'
import type { PipelineRun, SchedulePayload } from '~/lib/types'

/** Zones the operators actually work in, plus UTC as an escape hatch. A full
 *  IANA list is 600 entries and would be a worse way to pick one of three; the
 *  server accepts any valid zone, so nothing is lost by keeping this short. */
const ZONES = ['Australia/Sydney', 'Australia/Perth', 'Pacific/Port_Moresby', 'UTC']

/** A moment, spelled out in the schedule's own timezone.
 *
 *  Deliberately not the viewer's zone. The schedule means "05:00 in Sydney",
 *  and an admin reading this from Port Moresby or on a laptop still set to
 *  another country would otherwise see a different number than the one they
 *  set, with nothing on screen saying why. The zone is always shown, so the
 *  reading is never ambiguous. */
function when(iso: string | null, tz: string): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-AU', {
    weekday: 'short', day: 'numeric', month: 'short',
    hour: '2-digit', minute: '2-digit',
    timeZone: tz, timeZoneName: 'short',
  })
}

function RunRow({ r, tz }: { r: PipelineRun; tz: string }) {
  const label =
    r.status === 'ok' ? 'Completed' : r.status === 'failed' ? 'Failed' : 'Running'
  const cls = r.status === 'ok' ? 'ok' : r.status === 'failed' ? 'err' : 'warn'

  return (
    <div className="run-row">
      <div>
        {/* A word as well as a colour: colour alone fails WCAG 1.4.1. */}
        <span className={`dot-${cls === 'err' ? 'off' : cls}`} aria-hidden="true" />
        <span style={{ marginLeft: 8 }}>{label}</span>
      </div>
      <div className="muted">{r.trigger === 'schedule' ? 'Automatic' : 'Started by hand'}</div>
      <div className="muted">{when(r.startedAt, tz)}</div>
      <div className="num">{r.collected === null ? '—' : r.collected.toLocaleString()}</div>
      <div className="muted" title={r.note ?? undefined}>
        {r.startedBy ?? (r.trigger === 'schedule' ? 'On schedule' : '—')}
      </div>
    </div>
  )
}

export function SchedulePanel() {
  const qc = useQueryClient()
  const { data, isPending, error } = useQuery(scheduleQueryOptions)
  const [draft, setDraft] = useState<SchedulePayload | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [started, setStarted] = useState<string | null>(null)

  // The form is a draft of the stored schedule, seeded once it arrives. Keyed
  // on `changedAt` so a save (or another admin's change) reseeds it, while
  // typing is never overwritten by the 15-second poll.
  useEffect(() => {
    if (data) setDraft((d) => (d && d.changedAt === data.changedAt ? d : data))
  }, [data])

  const save = useMutation({
    mutationFn: (next: SchedulePayload) =>
      saveSchedule({
        enabled: next.enabled,
        dayOfWeek: next.dayOfWeek,
        hour: next.hour,
        minute: next.minute,
        timezone: next.timezone,
      }),
    onSuccess: (payload) => {
      qc.setQueryData(scheduleQueryOptions.queryKey, payload)
      setDraft(payload)
      setProblem(null)
    },
    onError: (e: Error) => setProblem(e.message),
  })

  const runNow = useMutation({
    mutationFn: runPipelineNow,
    onSuccess: (r) => {
      setStarted(r.note)
      setProblem(null)
      void qc.invalidateQueries({ queryKey: scheduleQueryOptions.queryKey })
    },
    onError: (e: Error) => { setProblem(e.message); setStarted(null) },
  })

  if (isPending || !draft) return null
  if (error) {
    return (
      <Section title="Automatic run">
        <div className="notice err">Could not load the schedule. {error.message}</div>
      </Section>
    )
  }

  const dirty =
    draft.enabled !== data.enabled ||
    draft.dayOfWeek !== data.dayOfWeek ||
    draft.hour !== data.hour ||
    draft.minute !== data.minute ||
    draft.timezone !== data.timezone

  const busy = data.activeRun !== null || runNow.isPending

  return (
    <Section
      title="Automatic run"
      tools={
        <span>
          {data.enabled ? `NEXT ${when(data.nextRunAt, data.timezone).toUpperCase()}` : 'PAUSED'}
        </span>
      }
    >
      {/* The failure this feature can produce that looks like success: a time
          set on a server where no process is watching it. Worth saying loudly,
          because everything else on this panel would still look correct. */}
      {!data.schedulerRunning && (
        <div className="notice err" role="status">
          <strong>Nothing is watching this schedule.</strong> This server was started
          without <code>SCHEDULER_ENABLED=true</code>, so the pipeline will not run by
          itself whatever is set below. The settings are saved and will take effect
          once the deployed server has it set.
        </div>
      )}

      {problem && <div className="notice err" role="alert">{problem}</div>}
      {started && !problem && <div className="notice" role="status">{started}</div>}

      {data.activeRun && (
        <div className="notice" role="status">
          A run started {when(data.activeRun.startedAt, data.timezone)} is in progress. It
          takes a few minutes.
        </div>
      )}

      <div className="sched-form">
        <label className="sched-toggle">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
          />
          <span>Run the pipeline automatically</span>
        </label>

        <div className="sched-fields" aria-disabled={!draft.enabled}>
          <label>
            <span className="fld">Day</span>
            <select
              value={draft.dayOfWeek}
              disabled={!draft.enabled}
              onChange={(e) => setDraft({ ...draft, dayOfWeek: Number(e.target.value) })}
            >
              {data.dayNames.map((d, i) => (
                <option key={d} value={i}>{d}</option>
              ))}
            </select>
          </label>

          <label>
            <span className="fld">Time</span>
            <input
              type="time"
              disabled={!draft.enabled}
              value={`${String(draft.hour).padStart(2, '0')}:${String(draft.minute).padStart(2, '0')}`}
              onChange={(e) => {
                const [h, m] = e.target.value.split(':')
                setDraft({ ...draft, hour: Number(h) || 0, minute: Number(m) || 0 })
              }}
            />
          </label>

          <label>
            <span className="fld">Timezone</span>
            <select
              value={draft.timezone}
              disabled={!draft.enabled}
              onChange={(e) => setDraft({ ...draft, timezone: e.target.value })}
            >
              {/* A stored zone the list does not carry still has to be
                  selectable, or opening this panel would silently change it. */}
              {(ZONES.includes(draft.timezone) ? ZONES : [draft.timezone, ...ZONES]).map((z) => (
                <option key={z} value={z}>{z}</option>
              ))}
            </select>
          </label>

          <button
            className="btn sm"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate(draft)}
          >
            {save.isPending ? 'Saving…' : 'Save'}
          </button>
          {dirty && (
            <button className="btn sm ghost" onClick={() => setDraft(data)}>Cancel</button>
          )}
        </div>
      </div>

      <div className="prose-note">
        <p>
          {data.enabled ? (
            <>
              The next automatic run is <strong>{when(data.nextRunAt, data.timezone)}</strong>.
              Every time on this panel is shown in {data.timezone}, the schedule's own
              timezone — not your browser's and not the server's, so it reads the same
              wherever it is opened from and wherever MIOS is deployed. Changes take
              effect within a minute; no redeploy is needed.
            </>
          ) : (
            <>
              Automatic runs are paused. Nothing will be collected until this is turned
              back on or a run is started by hand.
            </>
          )}
        </p>
        <p>
          A run missed because the server was down is picked up when it comes back, but
          only within {data.graceHours} hours. Later than that the week is skipped
          rather than collected late, because the digest is labelled with the week it
          covers.
        </p>
      </div>

      <div className="sched-actions">
        <button className="btn sm ghost" disabled={busy} onClick={() => runNow.mutate()}>
          {data.activeRun ? 'A run is in progress' : 'Run now'}
        </button>
        <span className="muted">
          Collects, classifies and posts the digest immediately. Takes a few minutes.
        </span>
      </div>

      {data.history.length > 0 && (
        <>
          <div className="run-row run-head">
            <div>Result</div>
            <div>Trigger</div>
            <div>Started</div>
            <div className="num">Collected</div>
            <div>By</div>
          </div>
          {data.history.map((r) => <RunRow key={r.id} r={r} tz={data.timezone} />)}
        </>
      )}
    </Section>
  )
}
