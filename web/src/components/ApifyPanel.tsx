import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { Section, SkeletonCard } from '~/components/ui'
import {
  clearApifyToken,
  setApifyBoard,
  setApifyRunCharge,
  setApifyToken,
  sourceConfigQueryOptions,
  sourceHealthQueryOptions,
  testApifyToken,
} from '~/lib/api'
import type { ApifyBoard, SourceConfigStatus } from '~/lib/types'

type Preset = SourceConfigStatus['apify']['presets'][number]

/** Apify reads names case-insensitively and writes them with "~" in addresses. */
const actorKey = (name: string) => name.trim().toLowerCase().replace('~', '/')

/** One board: the actor that reads it, and what that actor searches for. */
function BoardRow({
  b,
  presets,
  hasToken,
  busy,
  onSave,
}: {
  b: ApifyBoard
  presets: Preset[]
  hasToken: boolean
  busy: boolean
  onSave: (v: { id: string; actor: string; input: string }, done: () => void) => void
}) {
  const [open, setOpen] = useState(false)
  const [actor, setActor] = useState(b.actor)
  const [input, setInput] = useState(b.input || b.defaultInput)

  const presetFor = (name: string) => presets.find((p) => p.actor === actorKey(name))
  // The actors MIOS has a default search for, on this board.
  const known = presets.filter((p) => p.board === b.id)
  const preset = presetFor(actor)
  const onDefault = !!preset && input.trim() === preset.input.trim()

  const state = b.ready
    ? { cls: 'ok', label: 'Ready' }
    : b.actor
      ? { cls: 'warn', label: 'Needs the token' }
      : { cls: 'off', label: 'No actor' }

  // What the board searches for, said in a word: it is the difference between
  // a week of mining jobs and a week of every job in the country.
  const search = !b.actor
    ? null
    : b.usingDefault
      ? { text: 'default search', warn: false }
      : b.input
        ? { text: 'your search', warn: false }
        : { text: 'no search set, so every kind of job', warn: true }

  return (
    <div className="apify-board">
      <div className="apify-board-head">
        <div>
          <div className="apify-board-name">
            {b.label} <span className="muted">· {b.market}</span>
          </div>
          <div className="llm-meta">
            {b.actor ? <span className="mono">{b.actor}</span> : 'Not read'}
            {b.actor && <> · up to {b.limit} results a run</>}
            {search && <> · <span className={search.warn ? 'llm-warn' : undefined}>{search.text}</span></>}
          </div>
        </div>
        <span className={`status-chip ${state.cls}`}>{state.label}</span>
        <button
          className="btn sm ghost"
          disabled={busy}
          aria-expanded={open}
          onClick={() => {
            // Reopen on what is stored, not on an edit that was abandoned.
            setActor(b.actor)
            setInput(b.input || b.defaultInput)
            setOpen((o) => !o)
          }}
        >
          {open ? 'Cancel' : b.actor ? 'Change' : 'Set actor'}
        </button>
      </div>

      {open && (
        <form
          className="key-form gd-form"
          onSubmit={(e) => {
            e.preventDefault()
            onSave({ id: b.id, actor: actor.trim(), input }, () => setOpen(false))
          }}
        >
          <label className="key-label" htmlFor={`actor-${b.id}`}>Actor</label>
          <div className="apify-field">
            <input
              id={`actor-${b.id}`} className="key-input" value={actor}
              autoComplete="off" spellCheck={false} placeholder="username/actor-name"
              onChange={(e) => {
                const next = e.target.value
                // Naming an actor MIOS knows fills in its default search, unless
                // something has been typed there that is not the last default.
                const before = presetFor(actor)?.input ?? ''
                if (input.trim() === '' || input.trim() === before.trim()) {
                  setInput(presetFor(next)?.input ?? '')
                }
                setActor(next)
              }}
            />
            {known.length > 0 && !preset && (
              <div className="llm-meta">
                A default search is built in for{' '}
                {known.map((p, i) => (
                  <span key={p.actor}>
                    {i > 0 && ', '}
                    <button
                      type="button" className="link-btn mono"
                      onClick={() => { setActor(p.actor); setInput(p.input) }}
                    >
                      {p.actor}
                    </button>
                  </span>
                ))}
                .
              </div>
            )}
          </div>

          <label className="key-label" htmlFor={`input-${b.id}`}>Search settings</label>
          <div className="apify-field">
            <textarea
              id={`input-${b.id}`} className="key-input apify-json" value={input}
              rows={preset ? 8 : 4} spellCheck={false}
              placeholder={'{\n  "keyword": "mining"\n}'}
              onChange={(e) => setInput(e.target.value)}
            />
            <div className="llm-meta">
              {preset
                ? onDefault
                  ? 'The default search for this actor: Easy Skill’s sectors, the last week. Edit it to search for something else.'
                  : <>
                      Your own search.{' '}
                      <button type="button" className="link-btn" onClick={() => setInput(preset.input)}>
                        Put the default back
                      </button>
                    </>
                : (
                  <span className={input.trim() ? undefined : 'llm-warn'}>
                    MIOS has no default search for this actor. Left empty, it uses its own
                    defaults, which is every kind of job. Its field names are on the actor’s
                    Input tab in the Apify Store.
                  </span>
                )}
            </div>
          </div>

          <div className="apify-form-actions">
            <button className="btn sm" type="submit" disabled={busy || (!actor.trim() && !b.actor)}>
              {!actor.trim() && b.actor ? 'Remove actor' : 'Save'}
            </button>
            {!hasToken && actor.trim() && (
              <span className="llm-meta llm-warn">Saved now, read once a token is added.</span>
            )}
          </div>
        </form>
      )}
    </div>
  )
}

/** Job boards with no feed of their own, read through Apify actors. Optional:
 *  with nothing entered these boards are simply not read. */
export function ApifyPanel() {
  const qc = useQueryClient()
  const { data, isPending, error } = useQuery(sourceConfigQueryOptions)
  const [open, setOpen] = useState(false)
  const [token, setToken] = useState('')
  const [capOpen, setCapOpen] = useState(false)
  const [cap, setCap] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)

  const settle = (p: SourceConfigStatus) => {
    qc.setQueryData(sourceConfigQueryOptions.queryKey, p)
    // A board that just became readable changes its row on Data sources.
    void qc.invalidateQueries({ queryKey: sourceHealthQueryOptions.queryKey })
    setProblem(null)
    setNote(p.note ? { ok: p.testOk !== false, text: p.note } : null)
  }
  const onError = (e: Error) => { setProblem(e.message); setNote(null) }

  const save = useMutation({
    mutationFn: () => setApifyToken(token.trim()),
    onSuccess: (p) => { settle(p); setOpen(false); setToken('') },
    onError,
  })
  const remove = useMutation({ mutationFn: clearApifyToken, onSuccess: settle, onError })
  const test = useMutation({ mutationFn: testApifyToken, onSuccess: settle, onError })
  const board = useMutation({ mutationFn: setApifyBoard, onSuccess: settle, onError })
  const charge = useMutation({
    mutationFn: setApifyRunCharge,
    onSuccess: (p) => { settle(p); setCapOpen(false) },
    onError,
  })

  if (isPending) return <SkeletonCard rows={4} />
  if (error || !data) {
    return (
      <Section title="Apify job boards">
        <div className="notice err">Could not load the Apify settings. {error?.message}</div>
      </Section>
    )
  }

  const a = data.apify
  const busy = save.isPending || remove.isPending || test.isPending || board.isPending
    || charge.isPending
  const usd = (n: number) => `$${n.toFixed(2)}`
  const hasToken = a.token.source === 'panel'
  const named = a.boards.filter((b) => b.actor).length
  const state = a.readyCount > 0
    ? `${a.readyCount} OF ${a.boards.length} READY`
    : hasToken || named > 0 ? 'PART SET UP' : 'NOT SET UP'

  return (
    <Section title="Apify job boards" tools={<span>{state}</span>}>
      {problem && <div className="notice err" role="alert">{problem}</div>}
      {!problem && note && (
        <div className={`notice ${note.ok ? 'ok' : 'err'}`} role="status">{note.text}</div>
      )}

      <p className="muted key-row" style={{ margin: 0 }}>
        Reads job boards that publish no feed, through actors on Apify. Optional — leave it
        empty and these boards are simply not read.
      </p>

      {/* Step 1: the token */}
      <div className="key-row">
        <div className="key-head">
          <div>
            <div className="llm-purpose"><span className="step-no">1</span>API token</div>
            <div className="llm-meta">
              {hasToken ? 'Entered here' : 'No token'}
              {a.token.hint && <> · ends <span className="mono">…{a.token.hint}</span></>}
            </div>
            {a.token.unreadable && (
              <div className="llm-meta llm-warn">
                A token is stored but will not decrypt with this server’s MIOS_CREDENTIAL_KEY.
                Enter it again.
              </div>
            )}
            {!a.canStoreKey && !hasToken && (
              <div className="llm-meta llm-warn">
                A token cannot be stored until the server has MIOS_CREDENTIAL_KEY.
              </div>
            )}
          </div>
          <div className="key-actions">
            {hasToken && (
              <button className="btn sm ghost" disabled={busy} onClick={() => test.mutate()}>
                {test.isPending ? 'Testing…' : 'Test token'}
              </button>
            )}
            {hasToken && (
              <button className="btn sm ghost" disabled={busy} onClick={() => remove.mutate()}>Remove</button>
            )}
            {a.canStoreKey && (
              <button className="btn sm" disabled={busy} onClick={() => setOpen((o) => !o)}>
                {open ? 'Cancel' : hasToken ? 'Replace' : 'Add token'}
              </button>
            )}
          </div>
        </div>
        {open && a.canStoreKey && (
          <form
            className="key-form"
            onSubmit={(e) => {
              e.preventDefault()
              if (token.trim()) save.mutate()
            }}
          >
            <label className="key-label" htmlFor="apify-token">API token</label>
            <input
              id="apify-token" type="password" className="key-input" value={token}
              autoComplete="off" spellCheck={false} placeholder="apify_api_…"
              onChange={(e) => setToken(e.target.value)}
            />
            <button className="btn sm" type="submit" disabled={busy || !token.trim()}>
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
          </form>
        )}
      </div>

      {/* Step 2: an actor per board */}
      <div className="key-row">
        <div className="llm-purpose"><span className="step-no">2</span>Actor for each board</div>
        <div className="llm-meta">
          {named} of {a.boards.length} named. A board with no actor is not read.
        </div>
        <div className="apify-boards">
          {a.boards.map((b) => (
            <BoardRow
              key={b.id}
              b={b}
              presets={a.presets}
              hasToken={hasToken}
              busy={busy}
              onSave={(v, done) => board.mutate(v, { onSuccess: done })}
            />
          ))}
        </div>
      </div>

      {/* Step 3: what a run may cost */}
      <div className="key-row">
        <div className="key-head">
          <div>
            <div className="llm-purpose"><span className="step-no">3</span>Spending limit</div>
            <div className="llm-meta">
              A run of an actor is charged at most <b>{usd(a.run.maxChargeUsd)}</b>
              {a.run.custom
                ? <> · set by {a.run.changedBy}</>
                : ' · the default'}
            </div>
            <div className="llm-meta">
              Apify is also told not to charge for more results than a board&rsquo;s limit, which
              is set under <Link to="/sources">Data sources</Link> › Options &amp; limits.
            </div>
          </div>
          <div className="key-actions">
            {a.run.custom && (
              <button className="btn sm ghost" disabled={busy} onClick={() => charge.mutate('')}>
                Use default
              </button>
            )}
            <button
              className="btn sm"
              disabled={busy}
              onClick={() => { setCap(a.run.maxChargeUsd.toFixed(2)); setCapOpen((o) => !o) }}
            >
              {capOpen ? 'Cancel' : 'Change'}
            </button>
          </div>
        </div>
        {capOpen && (
          <form
            className="key-form"
            onSubmit={(e) => {
              e.preventDefault()
              if (cap.trim()) charge.mutate(cap.trim())
            }}
          >
            <label className="key-label" htmlFor="apify-cap">US dollars per run</label>
            <input
              id="apify-cap" type="number" inputMode="decimal" className="key-input cap-input"
              value={cap} min={a.run.min} max={a.run.max} step="0.05"
              onChange={(e) => setCap(e.target.value)}
            />
            <button className="btn sm" type="submit" disabled={busy || !cap.trim()}>
              {charge.isPending ? 'Saving…' : 'Save'}
            </button>
            <span className="llm-meta">Between {usd(a.run.min)} and {usd(a.run.max)}.</span>
          </form>
        )}
      </div>

      {/* Step 4: where the result shows up */}
      <div className="key-row">
        <div className="llm-purpose"><span className="step-no">4</span>Check the boards</div>
        <div className="llm-meta">
          A ready board is read from the next run and appears under{' '}
          <Link to="/sources">Data sources</Link>, where it can be switched off and given a limit.
        </div>
      </div>
    </Section>
  )
}
