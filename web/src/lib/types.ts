// Types mirror the JSON shape produced by api/digest_service.py (the Python backend).

export type Dir = 'up' | 'down' | 'flat'
export type Tier = 'A' | 'B' | 'C' | null

export interface Kpi {
  val: number
  delta: string
  dir: Dir
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
  cycle: string
  conf: number
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
}

export interface MatchResponse {
  profile: ProfileDraft
  matches: Match[]
  windowDays: number
  /** How many signals the ranking stood on — context for a short result list. */
  signalsConsidered: number
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
  limit: number
  offset: number
}

export interface DigestPayload {
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
  kpis: {
    rolesThisWeek: Kpi
    newSignals: Kpi
    newNames: Kpi
    pushQueries: Kpi
  }
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

export type SourceStatus = 'ok' | 'stale' | 'never_run' | 'not_configured' | 'retired'

export interface SourceHealth {
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
}

export interface SourcesPayload {
  sources: SourceHealth[]
  staleAfterDays: number
  perSourceLimit: number
  totalRecords: number
}
