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
  avg: number
  change: number
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
