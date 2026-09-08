// Types mirror the JSON shape produced by api/digest_service.py (the Python backend).

export type Tier = 'A' | 'B' | 'C' | null

/** What the week's collection consisted of. Nested rather than independent:
 *  `collected` is the whole, `shown` is the ranked subset listed below it, and
 *  `newNames` are the unfamiliar companies found among them. */
export interface Collection {
  collected: number
  jobs: number
  news: number
  shown: number
  newNames: number
  sources: number
  regions: { AU: number; PNG: number }
}

/** A Market Pulse bullet. `kind` is the model's own declaration of whether it
 *  is restating the figures or reasoning past them — the UI must show which,
 *  so a reader can tell a measurement from a reading of one. */
export interface PulseBullet {
  text: string
  kind: 'fact' | 'interpretation'
}

export interface MarketPulse {
  bullets: PulseBullet[]
  signalsAnalysed: number
  generatedAt: string
  note: string | null
}

export interface Signal {
  id: string
  n: string
  region: string
  tier: Tier
  company: string
  title: string
  desc: string
  action: string | null
  sector: string
  source: string
  /** Original posting URL when available; null when missing or not http(s). */
  sourceUrl: string | null
  cycle: string
  conf: number
  /** When the scraper collected this. Lets a reader tell a posting found in
   *  this run from one carried over from an earlier run in the same window. */
  capturedAt: string | null
}

export interface VelocityRow {
  co: string
  wk: number
  /** Measured average over the previous `basis` windows. Null when there is no
   *  history to compare against — the table must then show no comparison. */
  avg: number | null
  /** Percentage change against `avg`. Null when there is no baseline, or when
   *  the company had no prior signals (nothing to be a percentage of). */
  change: number | null
  /** Number of earlier windows `avg` covers. 0 means no history exists yet. */
  basis: number
  /** Counts per window, oldest first, ending with this window. */
  trend: number[]
  sector: string
  tier: Tier
}

export interface NewName {
  co: string
  signal: string
  sector: string
  region: string
  reco: string
  status: string
}

// ---------- Watchlist ----------

export type WatchlistCompany = {
  company_name: string
  tier: 'A' | 'B' | 'C'
  sector: string | null
  notes: string | null
  aliases: string[]
}

export type WatchlistResponse = {
  total: number
  companies: WatchlistCompany[]
}

// ---------- Mode Publish (api/publish_api.py) ----------

export interface ReportSection {
  id: string
  position: number
  heading: string
  body: string
  /** 'generated' is computed from signals; 'manual' must be written by a human. */
  source: 'generated' | 'manual'
  approved: boolean
  approvedAt: string | null
  approvedBy: string | null
  editedAt: string | null
  /** True once a human has changed what the generator wrote. */
  edited: boolean
  empty: boolean
  /** The deterministic prose this section was computed from, before any rewrite. */
  computedBody: string
  /** True when Gemini reworded this section rather than shipping the computed text. */
  rewritten: boolean
}

export interface ReportSummary {
  id: string
  quarter: string
  title: string
  status: 'draft' | 'approved'
  generatedAt: string
  signalsAnalysed: number
  approvedAt: string | null
  approvedBy: string | null
  sectionsApproved: number
  sectionsTotal: number
  /** Whether a language model wrote the wording. Figures are computed either way. */
  proseSource: 'computed' | 'gemini'
}

export interface Report extends ReportSummary {
  windowFrom: string | null
  windowTo: string | null
  sections: ReportSection[]
  /** Why the computed wording was kept, when it was — quota, key, or a rewrite
   *  that introduced figures the data does not support. */
  proseNote: string | null
  /** Headings still blocking sign-off, named so the reviewer need not hunt. */
  outstanding: string[]
  canApprove: boolean
}

// ---------- Mode Push (api/push_api.py) ----------

export type Confidence = 'high' | 'medium' | 'low'

/** A profile as the BD team edits it. Every field is optional except the name,
 *  because a CV parse can miss any of them and a half-known consultant is still
 *  worth matching. */
export interface ProfileDraft {
  fullName: string | null
  email: string | null
  phone: string | null
  currentTitle: string | null
  sector: string | null
  yearsExperience: number | null
  region: string | null
  skills: string[]
  availability?: string | null
  notes?: string | null
  /** Per-field parser confidence, keyed by snake_case field name. Absent on a
   *  form the user typed themselves — nothing was guessed. */
  confidence?: Record<string, Confidence>
}

/** A stored profile: a draft plus its identity and provenance. */
export interface StoredProfile extends ProfileDraft {
  id: string
  intakeSource: 'cv_upload' | 'manual_form'
  sourceFilename: string | null
  createdAt: string
}

export interface ParsedCV {
  draft: ProfileDraft
  sourceFilename: string
  charactersRead: number
  /** Always false — the draft is for review, not a saved record. */
  saved: boolean
}

export interface Match {
  rank: number
  co: string
  score: number
  rel: string
  region: string
  sector: string
  evidence: string[]
  action: string
  signalCount: number
  breakdown: Record<string, number>
  /** How much evidence stands behind the score — reported beside it, never
   *  folded into it: a thin case and a strong one can reach the same number. */
  confidence?: string
  confidenceNote?: string
  /** Points earned, and the weight of the contributors that could be judged.
   *  `score` is these scaled to 100. Shown so a reader can see the working:
   *  a contributor with nothing to judge leaves the denominator rather than
   *  scoring zero, so the company is not charged for gaps in our own data. */
  earned?: number
  assessable?: number
  /** Contributors that had no input to judge — what the score does not cover. */
  notAssessed?: string[]
  /** Written by a model after the ranking was fixed. It cannot reorder. */
  rationale?: string
  fit?: string
  caveat?: string | null
  /** True where the model's read and the score point different ways. A flag for
   *  a human to look at, not a correction applied to the ranking. */
  disagrees?: boolean
  /** The full working, one entry per contributor including those that could not
   *  be judged. What the detail drawer renders. */
  contributions?: Contribution[]
  /** Every skill the candidate claims, matched or not, with how rare each is in
   *  this market. Shown in full so a thin match cannot read as a strong one. */
  skillDetail?: SkillDetail[]
  /** What the team already decided about this company for this candidate. */
  outcome?: { outcome: string; by?: string | null; at?: string | null; note?: string | null }
}

/** One contributor's share of a score, with what it asks and what it found. */
export interface Contribution {
  key: string
  label: string
  /** What this contributor asks, in plain language. */
  asks: string
  /** Points available — this contributor's share of the model. */
  weight: number
  /** Points earned, or null when there was nothing to judge. */
  earned: number | null
  evidence?: string | null
  /** Why it could not be judged. Written to be acted on: "no skills recorded on
   *  the profile" tells a reader what to do; "not assessed" does not. */
  unassessedBecause?: string | null
  /** Fraction of the available points earned, for the bar. Null when unassessed,
   *  so the bar is absent rather than drawn at zero — a contributor that could
   *  not be judged did not score badly. */
  share: number | null
}

export interface SkillDetail {
  name: string
  kind: string
  kindLabel: string
  matched: boolean
  /** Plain words: "rare in this market", "common", "not weighted". */
  rarity: string
}

/** Whether rarity weighting was in play for this run.
 *
 *  A score computed with it and one computed without are different numbers, so
 *  a reader comparing across weeks is told which they are looking at. */
export interface RarityState {
  applies: boolean
  corpusSize: number
  minimum: number
}

export interface OutcomeSummary {
  verbs: Record<string, string>
  total: number
  counts: Record<string, number>
  byBand: { band: string; total: number; [verb: string]: string | number }[]
  minimumForRates: number
  /** False until there are enough decisions to say anything. Until then the UI
   *  shows the count and how far off it is, never a derived rate. */
  readyToCalibrate: boolean
  note: string
}

export interface MatchResponse {
  profile: ProfileDraft
  matches: Match[]
  /** Why there are no AI notes, when there are none. Absent when they worked. */
  rationaleNote?: string | null
  windowDays: number
  /** How many signals the ranking stood on — context for a short result list. */
  signalsConsidered: number
  rarity?: RarityState
}

export interface FeedQuery {
  limit: number
  offset: number
  region?: string
  cycle?: string
  source?: string
  q?: string
}

export interface FeedPayload {
  signals: Signal[]
  /** Rows matching the current filter — what pagination walks through. */
  total: number
  /** Every classified row, ignoring filters. */
  totalClassified: number
  /** Every row ever collected, including any not yet classified. */
  scrapedAllTime: number
  /** Every source the feed can be filtered to, read from the data. Drives the
   *  filter buttons so they cannot drift from what is actually collectable. */
  sources: string[]
  limit: number
  offset: number
}

/** One entry in the digest archive — enough to choose a week, no payload. */
export interface DigestArchiveEntry {
  runId: string
  windowFrom: string
  windowTo: string
  generatedAt: string
  signalCount: number
}

export interface DigestPayload {
  /** Set when this digest was read from the archive: which run produced it and
   *  what it covered. Null when the payload was computed live because nothing
   *  has been archived yet. */
  archived?: DigestArchiveEntry | null
  /** True only for that live fallback. A stored digest is a snapshot of what
   *  was published; a live one is being computed right now. */
  live?: boolean
  /** The Slack message as posted for this run, when one was stored. */
  digestText?: string | null
  sourceMode: 'live' | 'synthetic'
  /** Which engine served this — e.g. "Neon PostgreSQL". Absent on older payloads. */
  backend?: string
  /** Length in days of the capture window these figures cover. */
  windowDays: number
  /** True when nothing was captured in the window and these are older signals. */
  windowEmpty: boolean
  /** ISO timestamps bounding when the rendered signals were scraped. */
  collectedFrom: string | null
  collectedTo: string | null
  week: string
  weekLabel: string
  generatedAt: string
  collection: Collection
  /** Null when the week produced none. The section is then omitted —
   *  there is deliberately no computed substitute. */
  marketPulse: MarketPulse | null
  signals: Signal[]
  velocity: VelocityRow[]
  newNames: NewName[]
}

// ---------- Admin ----------

/** A person who can sign in. `source` says whether this screen can change it:
 *  'database' rows are editable, 'environment' grants come from ALLOWED_EMAILS
 *  and need a config change plus a restart. */
export interface AccessUser {
  email: string
  role: 'admin' | 'member'
  addedBy: string | null
  addedAt: string | null
  note: string | null
  lastSeen: string | null
  source: 'database' | 'environment'
}

export interface AccessPayload {
  users: AccessUser[]
  envGrants: AccessUser[]
  /** Workspace domain admitted wholesale as members, if configured. */
  domain: string | null
  roles: string[]
  you: string
  /** Set after a revoke that did not actually close every door. */
  warning?: string | null
}

export type SourceStatus =
  | 'ok'
  | 'stale'
  | 'never_run'
  | 'not_configured'
  /** Switched off, so it collects nothing regardless of how recent its last
   *  records are. Distinct from `not_configured`, which persists past the
   *  toggle and has to be fixed before turning it on would achieve anything. */
  | 'off'
  | 'retired'

export interface SourceHealth {
  /** Whether this source ships switched on. A source that is off for a
   *  documented reason must not look like one switched off by accident. */
  defaultEnabled: boolean
  /** Why it ships off, when it does. Shown beside the toggle and repeated as a
   *  warning if somebody switches it on. */
  offReason: string | null
  name: string
  label: string
  market: string
  kind: string
  status: SourceStatus
  note: string | null
  lastSeen: string | null
  totalRecords: number
  last7Days: number
  lastRunRecords: number
  pending: number
  runDays: number
  /** Whether the next scrape will use this source. Distinct from `status`,
   *  which describes what it has been doing — a source can be collecting
   *  healthily and still be switched off for the next run. */
  enabled: boolean
  changedBy: string | null
  changedAt: string | null
}

export interface SourcesPayload {
  sources: SourceHealth[]
  staleAfterDays: number
  perSourceLimit: number
  totalRecords: number
  /** How many sources the next scrape will use. Zero is a legitimate choice
   *  — it is how collection is paused — but the UI must say so, or an empty
   *  week reads as a broken pipeline. */
  enabledCount: number
  /** Set after switching on a source that ships off, explaining what was just
   *  turned on. Not an error — the request succeeded. */
  warning?: string | null
}

/** One pipeline run, however it was started. */
export interface PipelineRun {
  id: string
  trigger: 'schedule' | 'manual' | 'cli'
  dueAt: string | null
  startedAt: string
  finishedAt: string | null
  status: 'running' | 'ok' | 'failed'
  startedBy: string | null
  collected: number | null
  note: string | null
}

export interface SchedulePayload {
  enabled: boolean
  dayOfWeek: number
  hour: number
  minute: number
  timezone: string
  changedBy: string | null
  changedAt: string | null
  describe: string
  /** Computed on the server: "Monday 05:00 in Sydney" depends on that zone's
   *  daylight-saving rules, not on the rules of the browser's own zone. */
  nextRunAt: string | null
  dayNames: string[]
  graceHours: number
  /** Whether any process is actually watching this schedule. A time set on a
   *  server with SCHEDULER_ENABLED unset looks right and never fires. */
  schedulerRunning: boolean
  activeRun: PipelineRun | null
  history: PipelineRun[]
}

/** One job the pipeline asks a model to do, and what currently answers it. */
export interface LlmRoute {
  purpose: string
  label: string
  /** What this job needs from a model, in plain terms — so somebody choosing
   *  does not have to read the pipeline to know what they are choosing for. */
  needs: string
  callsPerRun: number
  provider: string
  model: string
  /** False when the chosen provider has no API key. */
  configured: boolean
  /** Where this choice came from: 'admin', 'environment' or 'default'. */
  source: string
  changedBy: string | null
  changedAt: string | null
  /** Set when an admin choice is overriding a value pinned on the server. */
  overriddenEnv: string | null
}

/** Where a provider's API key comes from, and never the key itself.
 *
 *  The API returns a hint — the last four characters — because an administrator
 *  has to be able to tell which key is loaded without the app being able to
 *  show it to them. */
export interface LlmKeyStatus {
  provider: string
  /** 'panel' (entered here), 'environment' (set on the server), or 'none'. */
  source: string
  /** Last four characters of whichever key is in play. Null when there is none. */
  hint: string | null
  /** A key entered here is overriding one set on the server. Shown so an
   *  environment variable that appears to do nothing is explainable. */
  shadowsEnvironment: boolean
  /** A stored key that will not decrypt — almost always because the server's
   *  MIOS_CREDENTIAL_KEY changed. Looks identical to "no key" and needs a
   *  completely different fix, so it is reported separately. */
  unreadable: boolean
  /** False when the server has no MIOS_CREDENTIAL_KEY, so keys cannot be
   *  encrypted and the form is not offered. */
  canStore: boolean
  changedBy: string | null
  changedAt: string | null
}

export interface LlmProvider {
  name: string
  label: string
  configured: boolean
  defaultModel: string
  /** Suggestions, not a whitelist — any model name can be typed. */
  models: string[]
  key: LlmKeyStatus
  /** False when the client library is not installed. A different problem from
   *  a missing key, and a different fix: this one needs a deploy. */
  sdkInstalled: boolean
}

export interface LlmUsage {
  provider: string
  label: string
  configured: boolean
  /** Attempts today, successful or not — a provider charges for a rejected
   *  request the same as a served one. */
  usedToday: number
  dailyLimit: number | null
  remaining: number | null
}

export interface LlmSettingsPayload {
  routing: LlmRoute[]
  providers: LlmProvider[]
  usage: LlmUsage[]
  history: { provider: string; date: string; calls: number }[]
  you: string
  /** Set after choosing a provider that has no key. Not an error. */
  warning?: string | null
  /** Confirmation of a key saved or cleared. Not an error. */
  note?: string | null
  /** The result of pressing Test on a provider. */
  test?: { provider: string; ok: boolean; message: string } | null
}

/** How a Mode Push score is arrived at. Served from the scorer's own constants,
 *  so the explanation cannot drift from the calculation. */
export interface ScoringModel {
  total: number
  contributors: { key: string; weight: number; label: string; what: string }[]
  /** Why a score can be out of fewer than the full set of points. */
  normalisation: string
  confidence: { level: string; what: string }[]
  llm: {
    provider: string
    model: string
    annotatesTop: number
    what: string
  }
  caveat: string
}

/** One collection run's counts. A point on the trend chart is one of these —
 *  not one calendar week, because a missed run leaves a gap no honest chart can
 *  fill. */
export interface CollectionPoint {
  date: string
  total: number
  au: number
  png: number
}

export interface DashboardPayload {
  collections: CollectionPoint[]
  latest: CollectionPoint | null
  /** Movement against the previous collection. Null where there was none, so
   *  the UI shows nothing rather than a direction it cannot justify. */
  change: { total?: number | null; au?: number | null; png?: number | null }
  sectors: Breakdown[]
  /** What kind of signals the latest collection was made of. */
  categories: (Breakdown & { group: string })[]
  /** The coarse three-way split over `categories`: a reason to act, routine
   *  hiring, or market background. Returned rather than derived in the browser
   *  so the mapping lives in one place. A group with nothing in it is still
   *  present at zero — dropping it would make a week with no decision points
   *  look like a week where the question was not asked. */
  groups: (Breakdown & { what: string })[]
  /** Which collectors produced this week's signals. A source absent from this
   *  list contributed nothing, which is the quickest way to see a scraper that
   *  has quietly stopped working. */
  sources: { name: string; kind: string; count: number; share: number }[]
  /** The most active companies in the latest collection. */
  companies: {
    name: string
    count: number
    sector: string
    region: string
    /** Watchlist tier, or null for a company not on it. */
    tier: string | null
    isNew: boolean
  }[]
  /** Companies seen this collection that are not on the watchlist. */
  newNames: number
  /** The run behind the latest collection, read from the run log rather than
   *  inferred from signals — so a run that collected nothing still reports. */
  run: {
    id: string
    trigger: string
    status: string
    finishedAt: string | null
    collected: number
  } | null
  watchlist: {
    total: number
    byTier: Record<string, number>
    /** How many of them appeared in the latest collection. A watchlist is only
     *  worth keeping if the pipeline is seeing the companies on it. */
    seen: number
    seenShare: number
  }
  /** What the charts actually stand on, rather than what they promise. */
  coverage: { collections: number; from: string | null; to: string | null }
  trendWindow: number
}

/** One slice of a composition, with its share of the whole already computed. */
export interface Breakdown {
  key: string
  label: string
  count: number
  share: number
}

