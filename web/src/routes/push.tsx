import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useRef, useState } from 'react'
import { ScoringExplainer } from '~/components/ScoringExplainer'
import { Icons, RegionChip, Section } from '~/components/ui'
import { useCountUpAll, useReveal } from '~/lib/motion'
import { deleteProfile, fetchMatches, matchDraft, parseCV, profilesQueryOptions, recordOutcome, saveProfile, scoringModelQueryOptions } from '~/lib/api'
import { UnauthenticatedError } from '~/lib/auth'
import { useAuth } from '~/lib/auth-context'
import type {
  Confidence, Match, ParsedCV, ProfileDraft, RarityState, StoredProfile,
} from '~/lib/types'
import { MatchDetail } from '~/components/MatchDetail'

export const Route = createFileRoute('/push')({
  head: () => ({ meta: [{ title: 'Push · MIOS' }] }),
  component: PushScreen,
})

const EMPTY: ProfileDraft = {
  fullName: null, email: null, phone: null, currentTitle: null,
  sector: null, yearsExperience: null, region: null, skills: [],
  availability: null, notes: null,
}

const SECTORS = ['mining', 'oil_gas', 'construction', 'defence', 'energy_transition', 'other']
/** Why the AI notes are missing, in a sentence rather than a provider's raw
 *  error. The full text is a JSON blob with a support URL in it; a consultant
 *  needs to know whether to wait, ask an admin, or ignore it. */
function explainNote(note: string): string {
  const n = note.toLowerCase()
  if (n.includes('429') || n.includes('quota') || n.includes('resource_exhausted')) {
    return 'the daily model allowance is spent. The ranking below is unaffected.'
  }
  if (n.includes('api_key') || n.includes('not set') || n.includes('not configured')) {
    return 'no model is configured. An administrator can set one under Models & cost.'
  }
  return 'the model could not be reached. The ranking below is unaffected.'
}

const SECTOR_LABEL: Record<string, string> = {
  mining: 'Mining', oil_gas: 'Oil & Gas', construction: 'Construction',
  defence: 'Defence', energy_transition: 'Energy Transition', other: 'Other',
}

/** Human labels for every field, so feedback can name what needs attention
 *  rather than echoing a property name at the reviewer. */
const FIELD_LABEL: Record<string, string> = {
  full_name: 'Full name', current_title: 'Current title', sector: 'Sector',
  region: 'Region', years_experience: 'Years experience', email: 'Email',
  phone: 'Phone', skills: 'Skills',
}

/** camelCase draft key -> the snake_case key the parser reports confidence under. */
const CONF_KEY: Record<string, string> = {
  fullName: 'full_name', currentTitle: 'current_title',
  yearsExperience: 'years_experience',
}

type FieldState = 'ok' | 'check' | 'missing'

function fieldState(value: unknown, level: Confidence | undefined, parsed: boolean): FieldState {
  const empty = value === null || value === undefined || value === '' ||
    (Array.isArray(value) && value.length === 0)
  // "Missing" only means something after a parse. On a blank manual form every
  // field is empty, and flagging all of them would be noise, not feedback.
  if (empty) return parsed ? 'missing' : 'ok'
  return !level || level === 'high' ? 'ok' : 'check'
}

/**
 * The reason a field is flagged, tied to the input with aria-describedby.
 *
 * Status is carried by the wording, not the colour: "Check this" and "Not found"
 * read differently in greyscale and to a screen reader, which colour alone would
 * not (WCAG 1.4.1).
 */
function FieldNote({ id, state }: { id: string; state: FieldState }) {
  if (state === 'ok') return null
  const check = state === 'check'
  return (
    <p id={id} className={`field-note ${check ? 'check' : 'missing'}`}>
      <span aria-hidden="true" style={{ display: 'flex' }}>
        {check ? Icons.alert : Icons.info}
      </span>
      {check
        ? 'Check this — the CV was ambiguous here'
        : 'Not found in the CV — add it if you have it'}
    </p>
  )
}

/**
 * What the CV upload actually produced.
 *
 * A filled-in form is not feedback: the reviewer cannot tell which values came
 * from the document, which were guesses, and which the parser never found. This
 * says so explicitly, and is the first thing screen readers hear after the
 * upload because the container is a live region.
 */
function IntakeSummary({ result, draft }: { result: ParsedCV; draft: ProfileDraft }) {
  const conf = draft.confidence ?? {}
  const keys = Object.keys(FIELD_LABEL)

  const states = keys.map((k) => {
    const draftKey = Object.entries(CONF_KEY).find(([, v]) => v === k)?.[0] ?? k
    return { key: k, state: fieldState((draft as never)[draftKey], conf[k], true) }
  })

  const filled = states.filter((s) => s.state !== 'missing')
  const check = states.filter((s) => s.state === 'check')
  const missing = states.filter((s) => s.state === 'missing')
  const flagged = check.length + missing.length
  const clean = flagged === 0

  return (
    <div className={`intake-note ${clean ? 'ok' : 'warn'}`}>
      <span className="ico" aria-hidden="true">
        {clean ? Icons.check : Icons.alert}
      </span>
      <div>
        <h4>
          {clean
            ? 'CV read — every field filled'
            : `CV read — ${flagged} ${flagged === 1 ? 'field needs' : 'fields need'} your attention`}
        </h4>
        <p>
          <span className="fname">{result.sourceFilename}</span>
          {' · '}
          {result.charactersRead.toLocaleString()} characters read
          {' · '}
          {filled.length} of {keys.length} fields filled
        </p>
        {(check.length > 0 || missing.length > 0) && (
          <ul>
            {check.map((s) => (
              <li key={s.key}>
                <strong>{FIELD_LABEL[s.key]}</strong> — confirm this is right
              </li>
            ))}
            {missing.map((s) => (
              <li key={s.key}>
                <strong>{FIELD_LABEL[s.key]}</strong> — not found in the document
              </li>
            ))}
          </ul>
        )}
        <p style={{ marginTop: 8 }}>
          The document itself was not saved. Nothing is stored until you choose
          “Save profile and match”.
        </p>
      </div>
    </div>
  )
}

function PushScreen() {
  const qc = useQueryClient()
  const { refresh } = useAuth()
  const fileInput = useRef<HTMLInputElement>(null)

  const [parsed, setParsed] = useState<ParsedCV | null>(null)
  const [draft, setDraft] = useState<ProfileDraft>(EMPTY)
  const [origin, setOrigin] = useState<{ source: 'cv_upload' | 'manual_form'; filename: string | null }>(
    { source: 'manual_form', filename: null },
  )
  const [explainOpen, setExplainOpen] = useState(false)
  // The denominator, read from the scorer rather than written as 100 here. The
  // weights sum to 100 and a test keeps them there, but a number on screen
  // should follow the calculation instead of restating a fact about it.
  const scoring = useQuery(scoringModelQueryOptions)
  const scoreTotal = scoring.data?.total ?? 100
  const [matches, setMatches] = useState<Match[] | null>(null)
  const [matchMeta, setMatchMeta] = useState<{ windowDays: number; considered: number } | null>(null)
  // Why the AI notes are missing, when they are. A silent absence would leave
  // nobody able to tell a spent quota from a model that had nothing to say.
  const [rationaleNote, setRationaleNote] = useState<string | null>(null)
  const [subject, setSubject] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  //: Which half of the page is showing. Results are a view, not a section
  //: appended below the form.
  const [view, setView] = useState<'form' | 'results'>('form')
  //: The company whose full working is open in the drawer.
  const [openMatch, setOpenMatch] = useState<Match | null>(null)
  const [rarity, setRarity] = useState<RarityState | undefined>(undefined)
  //: Outcomes attach to a stored profile. A draft match has nobody to attach
  //: to, so the buttons are not offered rather than failing on use.
  const [matchedProfileId, setMatchedProfileId] = useState<string | null>(null)

  const profiles = useQuery(profilesQueryOptions)

  // Matches arrive after a request, not on mount, so these key off the result
  // itself — a new ranking replays; re-rendering the form beside it does not.
  const scope = useRef<HTMLDivElement>(null)
  const matchKey = matches ? `${matches.length}-${subject}` : 'none'
  useReveal(scope, '.match-row', { key: matchKey, delay: 0.05, stagger: 0.06, y: 12 })
  useCountUpAll(scope, '.match-row .score .big', matchKey, { delay: 0.15, stagger: 0.06 })
  useReveal(scope, '.tbl tbody tr', { key: profiles.data?.length ?? 0, delay: 0.1, max: 10, y: 6 })

  function set<K extends keyof ProfileDraft>(key: K, value: ProfileDraft[K]) {
    setDraft((d) => ({ ...d, [key]: value }))
  }

  function applyMatches(
    res: { matches: Match[]; windowDays: number; signalsConsidered: number
           rationaleNote?: string | null; rarity?: RarityState },
    who: string,
    forProfileId?: string,
  ) {
    setMatches(res.matches)
    setMatchMeta({ windowDays: res.windowDays, considered: res.signalsConsidered })
    setRationaleNote(res.rationaleNote ?? null)
    setRarity(res.rarity)
    setSubject(who)
    setMatchedProfileId(forProfileId ?? null)
    // Results own the screen from here. The form is one click away and keeps
    // its contents — the previous version left a ranking buried under a long
    // form, so reading it meant scrolling past the fields that produced it.
    setView('results')
    setOpenMatch(null)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const upload = useMutation({
    mutationFn: parseCV,
    onSuccess: (res) => {
      setError(null)
      setParsed(res)
      setDraft({ ...EMPTY, ...res.draft })
      setOrigin({ source: 'cv_upload', filename: res.sourceFilename })
      // The draft replaces whatever was on screen, so any ranking shown for the
      // previous person is now stale and misleading.
      setMatches(null)
    },
    onError: (e: Error) => {
      setParsed(null)
      setError(e.message)
    },
    // Reset only once the read has finished. Clearing the input while the
    // request is still in flight can detach the File the mutation is holding,
    // which fails as an unreadable-file error that has nothing to do with the
    // file. Doing it here still lets the same document be re-selected.
    onSettled: () => {
      if (fileInput.current) fileInput.current.value = ''
    },
  })

  const search = useMutation({
    mutationFn: () => matchDraft(draft),
    onSuccess: (res) => {
      setError(null)
      applyMatches(res, draft.fullName || 'this profile')
    },
    onError: (e: Error) => setError(e.message),
  })

  const save = useMutation({
    mutationFn: async () => {
      const stored = await saveProfile(draft, {
        intakeSource: origin.source,
        sourceFilename: origin.filename,
      })
      return { stored, res: await fetchMatches(stored.id) }
    },
    onSuccess: ({ stored, res }) => {
      setError(null)
      void qc.invalidateQueries({ queryKey: ['push', 'profiles'] })
      applyMatches(res, stored.fullName || stored.id, stored.id)
    },
    onError: (e: Error) => setError(e.message),
  })

  const openSaved = useMutation({
    mutationFn: async (p: StoredProfile) => ({ p, res: await fetchMatches(p.id) }),
    onSuccess: ({ p, res }) => {
      setError(null)
      // A stored profile has already been reviewed, so the parse summary from
      // whatever was on screen before no longer describes what is shown.
      setParsed(null)
      setDraft({ ...EMPTY, ...p })
      setOrigin({ source: p.intakeSource, filename: p.sourceFilename })
      applyMatches(res, p.fullName || p.id, p.id)
    },
    onError: (e: Error) => setError(e.message),
  })

  const remove = useMutation({
    mutationFn: deleteProfile,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['push', 'profiles'] }),
    onError: (e: Error) => setError(e.message),
  })

  const mark = useMutation({
    mutationFn: (v: { company: string; verb: string; m: Match }) =>
      recordOutcome(matchedProfileId!, {
        company: v.company,
        outcome: v.verb,
        // The score as displayed, not as recomputed. It has to be the number
        // the consultant was looking at when they decided.
        score: v.m.score,
        confidence: v.m.confidence,
        assessable: v.m.assessable,
        rank: v.m.rank,
      }),
    onSuccess: (_res, v) => {
      setError(null)
      const stamped = { outcome: v.verb }
      setMatches((list) => list?.map(
        (m) => (m.co === v.company ? { ...m, outcome: stamped } : m)) ?? null)
      setOpenMatch((m) => (m && m.co === v.company ? { ...m, outcome: stamped } : m))
    },
    onError: (e: Error) => setError(e.message),
  })

  if (profiles.error instanceof UnauthenticatedError) {
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

  const busy = upload.isPending || search.isPending || save.isPending || openSaved.isPending
  const named = Boolean(draft.fullName?.trim())
  const conf = draft.confidence ?? {}
  const fromCV = parsed !== null

  /** State + the props that tie an input to its explanatory note. */
  function flag(draftKey: keyof ProfileDraft, inputId: string, base: 'input' | 'select') {
    const confKey = CONF_KEY[draftKey as string] ?? (draftKey as string)
    const state = fieldState(draft[draftKey], conf[confKey], fromCV)
    const noteId = `${inputId}-note`
    return {
      state,
      noteId,
      // Only "check" tints the border: a field the CV simply didn't mention is
      // not an error, and colouring it would make a blank look like a fault.
      className: state === 'check' ? `${base} flagged` : base,
      describedBy: state === 'ok' ? undefined : noteId,
    }
  }

  // ---------- Results view ----------
  //
  // A view rather than a section under the form. The ranking is the answer to
  // the question the page asks, and it used to sit below every field that
  // produced it — so reading it meant scrolling past the CV, and comparing two
  // companies meant scrolling between them.
  if (view === 'results' && matches !== null) {
    return (
      <div className="page" ref={scope}>
        <div className="results-bar">
          <button className="btn sm ghost" onClick={() => setView('form')}>
            ← Back to the profile
          </button>
          <div className="results-subject">
            <strong>{subject || 'this profile'}</strong>
            <span className="muted">
              {[draft.currentTitle, draft.sector && (SECTOR_LABEL[draft.sector] ?? draft.sector),
                draft.region].filter(Boolean).join(' · ') || 'no details recorded'}
            </span>
          </div>
          <button className="btn sm ghost" onClick={() => setExplainOpen(true)}>
            How does the scoring work?
          </button>
        </div>

        <div className="page-header">
          <div>
            <div className="kicker">Mode Push · Ranked matches</div>
            <h1>Who to approach about {subject || 'this candidate'}</h1>
          </div>
          <div className="meta">
            <div>{matches.length} result{matches.length === 1 ? '' : 's'}</div>
            <div style={{ marginTop: 4 }}>
              from {matchMeta?.considered ?? 0} signals over {matchMeta?.windowDays ?? 30} days
            </div>
          </div>
        </div>

        {error && <div className="notice err" role="alert">{error}</div>}

        {/* Whether rarity weighting was in play. Two scores computed under
            different models are not comparable, and the reader is told which
            one they are looking at rather than left to assume. */}
        {rarity && !rarity.applies && (
          <div className="notice warn" role="status">
            <strong>Skills were not weighted by rarity this run.</strong> {rarity.corpusSize}{' '}
            advert{rarity.corpusSize === 1 ? '' : 's'} is below the {rarity.minimum} needed to
            tell a rare skill from a common one, so every skill counted equally. Scores are
            still comparable with each other, but not with a run that had more to measure.
          </div>
        )}

        {matches.length > 0 && rationaleNote && (
          <div className="scoring-hint">
            <span className="muted">No AI notes this time — {explainNote(rationaleNote)}</span>
          </div>
        )}

        {matches.length === 0 && (
          <div className="center-empty">
            No companies matched.
            {matchMeta?.considered === 0
              ? ' No market activity has been collected yet for this period.'
              : ' Try widening the sector or region, or clearing the current title.'}
          </div>
        )}

        {matches.map((m) => (
          <div
            className="match-row clickable"
            key={m.rank}
            role="button"
            tabIndex={0}
            aria-label={`${m.co}, scored ${m.score}. Open the full breakdown.`}
            onClick={() => setOpenMatch(m)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                setOpenMatch(m)
              }
            }}
          >
            <div className="rank">{m.rank}</div>
            <div>
              <p className="co-name">{m.co}</p>
              <div className="co-meta">
                <span>{m.rel}</span><span>·</span><span>{m.region}</span>
                <span>·</span><span>{SECTOR_LABEL[m.sector] ?? m.sector}</span>
                {m.outcome && (
                  <>
                    <span>·</span>
                    <span className="outcome-tag">{m.outcome.outcome.replace('_', ' ')}</span>
                  </>
                )}
              </div>
              <ul className="ev-list">
                {m.evidence.slice(0, 3).map((e, i) => <li key={i}>{e}</li>)}
              </ul>
              {m.evidence.length > 3 && (
                <div className="ev-more">
                  +{m.evidence.length - 3} more · open for the full breakdown
                </div>
              )}

              {/* The written half. Marked as written by a model, because a
                  sentence a consultant may repeat to a client should say where
                  it came from. */}
              {m.rationale && (
                <div className={`match-note${m.disagrees ? ' flagged' : ''}`}>
                  <div className="match-note-head">
                    <span className="match-note-tag">AI note</span>
                    {m.fit && <span className={`fit-chip ${m.fit}`}>{m.fit} fit</span>}
                    {m.disagrees && (
                      <span className="fit-chip disagrees">disagrees with the score</span>
                    )}
                  </div>
                  <p>{m.rationale}</p>
                  {m.caveat && <p className="match-caveat">Check first: {m.caveat}</p>}
                </div>
              )}
            </div>
            <div className="score">
              <div className="score-line">
                {/* The number stays alone in .big: the count-up animation
                    rewrites its text content. */}
                <span className="big">{m.score}</span>
                <span className="out-of">/ {scoreTotal}</span>
              </div>
              match score
              {/* Only when it is not the whole model. Saying "scored on 100 of
                  100" on every row would be noise; saying nothing when it was
                  86 would hide that the number is a scaling. */}
              {m.assessable !== undefined && m.assessable < scoreTotal && (
                <div className="assessed-on">
                  {m.earned}/{m.assessable} assessed
                </div>
              )}
              {/* Beside the score, never folded into it: a thin case and a
                  strong one can reach the same number. */}
              {m.confidence && (
                <div className={`conf-chip ${m.confidence}`}>{m.confidence} confidence</div>
              )}
              <div style={{ marginTop: 10 }}>
                <span className="btn rust sm">{Icons.push} Why this score</span>
              </div>
            </div>
          </div>
        ))}

        <MatchDetail
          match={openMatch}
          rarity={rarity}
          busy={mark.isPending}
          onClose={() => setOpenMatch(null)}
          // Outcomes attach to a stored profile. A draft has no id to attach
          // to, so the buttons are absent rather than failing when pressed.
          onOutcome={matchedProfileId && openMatch
            ? (verb) => mark.mutate({ company: openMatch.co, verb, m: openMatch })
            : undefined}
        />
        <ScoringExplainer open={explainOpen} onClose={() => setExplainOpen(false)} />
      </div>
    )
  }

  return (
    <div className="page" ref={scope}>
      <div className="page-header">
        <div>
          <div className="kicker">Mode Push · Profile-to-client matching</div>
          <h1>Who in the market needs this person?</h1>
        </div>
        <div className="meta">
          <div>{profiles.data?.length ?? 0} saved profile(s)</div>
          <div style={{ marginTop: 4 }}>Matched against the last 30 days of signals</div>
          {/* In the header, not beside the results: "how does this decide who to
              contact?" is a question somebody asks before trusting it with a
              candidate, and answering it only after they already have a ranking
              is answering it too late. */}
          <div style={{ marginTop: 8 }}>
            <button className="btn sm ghost" onClick={() => setExplainOpen(true)}>
              How does the scoring work?
            </button>
          </div>
        </div>
      </div>

      {/* Announced the moment it appears, and kept in the DOM as a live region
          so the parse summary that replaces it is announced too. */}
      <div aria-live="polite">
        {error && (
          <div className="intake-note err" role="alert">
            <span className="ico" aria-hidden="true">{Icons.alert}</span>
            <div>
              <h4>That file could not be read</h4>
              <p>{error}</p>
            </div>
          </div>
        )}
        {!error && parsed && <IntakeSummary result={parsed} draft={draft} />}
      </div>

      {/* ---------- Intake ---------- */}
      <Section
        title="Candidate profile"
        tools={
          <span>
            {origin.source === 'cv_upload' && origin.filename
              ? `FROM ${origin.filename.toUpperCase()}`
              : 'MANUAL ENTRY'}
          </span>
        }
      >
        <div style={{ padding: '16px 18px' }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 18 }}>
            <input
              ref={fileInput}
              type="file"
              accept=".docx,.pdf"
              style={{ display: 'none' }}
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) upload.mutate(f)   // input is cleared in onSettled
              }}
            />
            <button
              className="btn"
              disabled={upload.isPending}
              onClick={() => fileInput.current?.click()}
            >
              {Icons.push} {upload.isPending ? 'Reading CV…' : 'Upload CV (.docx or .pdf)'}
            </button>
            <span className="muted" style={{ fontSize: 13 }}>
              Read in memory and discarded — the document is never stored. Check the
              fields below before saving.
            </span>
          </div>

          {(() => {
            const name = flag('fullName', 'p-name', 'input')
            const title = flag('currentTitle', 'p-title', 'input')
            const years = flag('yearsExperience', 'p-years', 'input')
            return (
              <div className="row-3">
                <div className="field">
                  <label htmlFor="p-name">Full name</label>
                  <input
                    id="p-name" className={name.className} value={draft.fullName ?? ''}
                    aria-describedby={name.describedBy}
                    onChange={(e) => set('fullName', e.target.value || null)}
                  />
                  <FieldNote id={name.noteId} state={name.state} />
                </div>
                <div className="field">
                  <label htmlFor="p-title">Current title</label>
                  <input
                    id="p-title" className={title.className} value={draft.currentTitle ?? ''}
                    aria-describedby={title.describedBy}
                    onChange={(e) => set('currentTitle', e.target.value || null)}
                  />
                  <FieldNote id={title.noteId} state={title.state} />
                </div>
                <div className="field">
                  <label htmlFor="p-years">Years experience</label>
                  <input
                    id="p-years" className={years.className} type="number" min={0} max={60}
                    value={draft.yearsExperience ?? ''}
                    aria-describedby={years.describedBy}
                    onChange={(e) =>
                      set('yearsExperience', e.target.value === '' ? null : Number(e.target.value))
                    }
                  />
                  <FieldNote id={years.noteId} state={years.state} />
                </div>
              </div>
            )
          })()}

          {(() => {
            const sector = flag('sector', 'p-sector', 'select')
            const region = flag('region', 'p-region', 'select')
            return (
              <div className="row-3">
                <div className="field">
                  <label htmlFor="p-sector">Sector</label>
                  <select
                    id="p-sector" className={sector.className} value={draft.sector ?? ''}
                    aria-describedby={sector.describedBy}
                    onChange={(e) => set('sector', e.target.value || null)}
                  >
                    <option value="">—</option>
                    {SECTORS.map((s) => (
                      <option key={s} value={s}>{SECTOR_LABEL[s]}</option>
                    ))}
                  </select>
                  <FieldNote id={sector.noteId} state={sector.state} />
                </div>
                <div className="field">
                  <label htmlFor="p-region">Region</label>
                  <select
                    id="p-region" className={region.className} value={draft.region ?? ''}
                    aria-describedby={region.describedBy}
                    onChange={(e) => set('region', e.target.value || null)}
                  >
                    <option value="">—</option>
                    <option value="AU">Australia</option>
                    <option value="PNG">Papua New Guinea</option>
                  </select>
                  <FieldNote id={region.noteId} state={region.state} />
                </div>
                <div className="field">
                  <label htmlFor="p-avail">Availability</label>
                  <input
                    id="p-avail" className="input" placeholder="e.g. from June 2026"
                    value={draft.availability ?? ''}
                    onChange={(e) => set('availability', e.target.value || null)}
                  />
                </div>
              </div>
            )
          })()}

          {(() => {
            const email = flag('email', 'p-email', 'input')
            const phone = flag('phone', 'p-phone', 'input')
            return (
              <div className="row-2">
                <div className="field">
                  <label htmlFor="p-email">Email</label>
                  <input
                    id="p-email" className={email.className} value={draft.email ?? ''}
                    aria-describedby={email.describedBy}
                    onChange={(e) => set('email', e.target.value || null)}
                  />
                  <FieldNote id={email.noteId} state={email.state} />
                </div>
                <div className="field">
                  <label htmlFor="p-phone">Phone</label>
                  <input
                    id="p-phone" className={phone.className} value={draft.phone ?? ''}
                    aria-describedby={phone.describedBy}
                    onChange={(e) => set('phone', e.target.value || null)}
                  />
                  <FieldNote id={phone.noteId} state={phone.state} />
                </div>
              </div>
            )
          })()}

          <div className="field">
            <label htmlFor="p-skills">Skills (comma separated)</label>
            <input
              id="p-skills" className={flag('skills', 'p-skills', 'input').className}
              aria-describedby={flag('skills', 'p-skills', 'input').describedBy}
              value={draft.skills.join(', ')}
              onChange={(e) =>
                set('skills', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))
              }
            />
          </div>

          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 4 }}>
            <button
              className="btn rust"
              disabled={busy || !named}
              onClick={() => search.mutate()}
            >
              {Icons.push} {search.isPending ? 'Searching…' : 'Find matches'}
            </button>
            <button className="btn" disabled={busy || !named} onClick={() => save.mutate()}>
              {save.isPending ? 'Saving…' : 'Save profile and match'}
            </button>
            <button
              className="btn sm"
              disabled={busy}
              onClick={() => {
                setDraft(EMPTY)
                setMatches(null)
                setParsed(null)
                setError(null)
                setOrigin({ source: 'manual_form', filename: null })
              }}
            >
              Clear
            </button>
            {!named && (
              <span className="muted" style={{ fontSize: 13 }}>A name is required.</span>
            )}
          </div>
          <p className="muted" style={{ fontSize: 13, marginTop: 10, marginBottom: 0 }}>
            “Find matches” searches without storing anyone — use it to test a CV
            against the market before deciding to keep the profile.
          </p>
        </div>
      </Section>

      {/* Results live in their own view — see the results branch above. */}

      {/* ---------- Saved profiles ---------- */}
      <ScoringExplainer open={explainOpen} onClose={() => setExplainOpen(false)} />

      <Section title="Saved profiles" tools={<span>{profiles.data?.length ?? 0} STORED</span>}>
        <table className="tbl">
          <thead>
            <tr>
              <th>Name</th><th>Title</th><th>Sector</th><th>Region</th>
              <th>Intake</th><th>Added</th><th />
            </tr>
          </thead>
          <tbody>
            {profiles.data?.map((p) => (
              <tr key={p.id}>
                <td><strong>{p.fullName}</strong></td>
                <td className="muted">{p.currentTitle ?? '—'}</td>
                <td className="muted">{p.sector ? SECTOR_LABEL[p.sector] ?? p.sector : '—'}</td>
                <td>{p.region ? <RegionChip region={p.region} /> : '—'}</td>
                <td className="muted">{p.intakeSource === 'cv_upload' ? 'CV' : 'Form'}</td>
                <td className="muted">{p.createdAt.slice(0, 10)}</td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                  <button className="btn sm" disabled={busy} onClick={() => openSaved.mutate(p)}>
                    Match
                  </button>{' '}
                  <button
                    className="btn sm"
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(p.id)}
                    title="Delete this profile"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {!profiles.isLoading && (profiles.data?.length ?? 0) === 0 && (
              <tr>
                <td colSpan={7} className="muted" style={{ textAlign: 'center', padding: 24 }}>
                  No profiles saved yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Section>
    </div>
  )
}
