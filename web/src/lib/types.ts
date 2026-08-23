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