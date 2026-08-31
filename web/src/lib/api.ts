import { queryOptions } from '@tanstack/react-query'
import { API_BASE } from './config'
import { fetchJson } from './auth'
import type {
  AccessPayload,
  DigestArchiveEntry,
  DigestPayload,
  FeedPayload,
  FeedQuery,
  MatchResponse,
  ParsedCV,
  ProfileDraft,
  Report,
  ReportSummary,
  SchedulePayload,
  SourcesPayload,
  StoredProfile,
  WatchlistResponse,
} from './types'

export { API_BASE } from './config'

export async function fetchDigest(): Promise<DigestPayload> {
  return fetchJson<DigestPayload>('/api/digest')
}

export const digestQueryOptions = queryOptions({
  queryKey: ['digest'],
  queryFn: fetchDigest,
  // A 401 means "sign in", not "retry" — the auth gate handles it.
  retry: false,
})

/** The archive: every past digest, newest first, without payloads. */
export const digestArchiveQueryOptions = queryOptions({
  queryKey: ['digest', 'archive'],
  queryFn: () => fetchJson<{ digests: DigestArchiveEntry[] }>('/api/digests'),
  retry: false,
})

/** One past digest, exactly as it was published. `runId` empty means "latest",
 *  which is what the page shows until somebody picks an earlier week. */
export function digestByRunQueryOptions(runId: string) {
  return queryOptions({
    queryKey: ['digest', 'run', runId],
    queryFn: () => fetchJson<DigestPayload>(`/api/digest/${encodeURIComponent(runId)}`),
    enabled: runId !== '',
    retry: false,
  })
}

// ---------- Signal Feed ----------

export async function fetchSignals(query: FeedQuery): Promise<FeedPayload> {
  const params = new URLSearchParams({
    limit: String(query.limit),
    offset: String(query.offset),
  })
  // Only send filters that are actually set, so the URL stays readable in the
  // network tab and an empty search does not become `q=`.
  if (query.region) params.set('region', query.region)
  if (query.cycle) params.set('cycle', query.cycle)
  if (query.source) params.set('source', query.source)
  if (query.q) params.set('q', query.q)
  return fetchJson<FeedPayload>(`/api/signals?${params}`)
}

export function signalsQueryOptions(query: FeedQuery) {
  return queryOptions({
    queryKey: ['signals', query],
    queryFn: () => fetchSignals(query),
    retry: false,
    // Paging back and forth should not blank the list each time.
    placeholderData: (prev: FeedPayload | undefined) => prev,
  })
}

// ---------- Watchlist ----------

export async function fetchWatchlist(): Promise<WatchlistResponse> {
  return fetchJson<WatchlistResponse>('/api/watchlist')
}

export const watchlistQueryOptions = queryOptions({
  queryKey: ['watchlist'],
  queryFn: fetchWatchlist,
  // A 401 means "sign in", not "retry" — the auth gate handles it.
  retry: false,
})

/**
 * Several endpoints answer a rejected request with a written explanation — an
 * unreadable CV, a report that cannot be approved yet. fetchJson collapses every non-401 into a status
 * code, which would throw that away — so this reads `detail` and surfaces it.
 */
async function postOrExplain<T>(path: string, init: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { ...init, credentials: 'include' })
  if (res.status === 401) {
    const { UnauthenticatedError } = await import('./auth')
    throw new UnauthenticatedError()
  }
  if (!res.ok) {
    const detail = await res
      .json()
      .then((b) => (b as { detail?: string }).detail)
      .catch(() => null)
    throw new Error(detail || `Backend returned ${res.status}`)
  }
  return (await res.json()) as T
}

// ---------- Mode Publish ----------

export const reportsQueryOptions = queryOptions({
  queryKey: ['publish', 'reports'],
  queryFn: () =>
    fetchJson<{ reports: ReportSummary[]; currentQuarter: string }>('/api/publish/reports'),
  retry: false,
})

export const quartersQueryOptions = queryOptions({
  queryKey: ['publish', 'quarters'],
  queryFn: () =>
    fetchJson<{ quarters: string[]; currentQuarter: string }>('/api/publish/quarters'),
  retry: false,
})

export function reportQueryOptions(reportId: string | null) {
  return queryOptions({
    queryKey: ['publish', 'report', reportId],
    queryFn: () => fetchJson<Report>(`/api/publish/reports/${reportId}`),
    enabled: Boolean(reportId),
    retry: false,
  })
}

export async function generateReport(quarter: string): Promise<Report> {
  return postOrExplain<Report>('/api/publish/reports', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ quarter }),
  })
}

export async function editSection(sectionId: string, body: string): Promise<Report> {
  return postOrExplain<Report>(`/api/publish/sections/${sectionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body }),
  })
}

export async function setSectionApproval(sectionId: string, approved: boolean): Promise<Report> {
  return postOrExplain<Report>(`/api/publish/sections/${sectionId}/approve`, {
    method: approved ? 'POST' : 'DELETE',
  })
}

export async function approveReport(reportId: string): Promise<Report> {
  return postOrExplain<Report>(`/api/publish/reports/${reportId}/approve`, { method: 'POST' })
}

export async function deleteReport(reportId: string): Promise<void> {
  await postOrExplain(`/api/publish/reports/${reportId}`, { method: 'DELETE' })
}

/** Export opens in a new tab rather than fetching: the response carries a
 *  Content-Disposition the browser should honour, and the session cookie rides
 *  along on a normal navigation. */
export function exportReportUrl(reportId: string, format: 'md' | 'html'): string {
  return `${API_BASE}/api/publish/reports/${reportId}/export?format=${format}`
}

// ---------- Mode Push ----------

/** Upload a CV and get a draft back. Nothing is stored until saveProfile. */
export async function parseCV(file: File): Promise<ParsedCV> {
  const body = new FormData()
  body.append('file', file)
  // No Content-Type header: the browser must set the multipart boundary itself.
  return postOrExplain<ParsedCV>('/api/push/parse-cv', { method: 'POST', body })
}

export async function saveProfile(
  draft: ProfileDraft,
  intake: { intakeSource: 'cv_upload' | 'manual_form'; sourceFilename?: string | null },
): Promise<StoredProfile> {
  return postOrExplain<StoredProfile>('/api/push/profiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...draft, ...intake }),
  })
}

export async function fetchProfiles(): Promise<StoredProfile[]> {
  const body = await fetchJson<{ profiles: StoredProfile[] }>('/api/push/profiles')
  return body.profiles
}

export const profilesQueryOptions = queryOptions({
  queryKey: ['push', 'profiles'],
  queryFn: fetchProfiles,
  retry: false,
})

export async function fetchMatches(profileId: string): Promise<MatchResponse> {
  return fetchJson<MatchResponse>(`/api/push/profiles/${profileId}/matches`)
}

/** Rank companies for a draft without storing the person. */
export async function matchDraft(draft: ProfileDraft): Promise<MatchResponse> {
  return postOrExplain<MatchResponse>('/api/push/match', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(draft),
  })
}

export async function deleteProfile(profileId: string): Promise<void> {
  await postOrExplain(`/api/push/profiles/${profileId}`, { method: 'DELETE' })
}

// ---------- Admin ----------
//
// These 403 for a member. The UI hides the section, but that is presentation —
// the server is what actually refuses.

export const accessQueryOptions = queryOptions({
  queryKey: ['admin', 'access'],
  queryFn: () => fetchJson<AccessPayload>('/api/admin/access'),
  retry: false,
})

export const scheduleQueryOptions = queryOptions({
  queryKey: ['admin', 'schedule'],
  queryFn: () => fetchJson<SchedulePayload>('/api/admin/schedule'),
  retry: false,
  // A run in flight changes this payload without the browser doing anything,
  // so the panel polls rather than showing a stale "running" for minutes.
  refetchInterval: 15_000,
})

export const sourceHealthQueryOptions = queryOptions({
  queryKey: ['admin', 'sources'],
  queryFn: () => fetchJson<SourcesPayload>('/api/admin/sources'),
  retry: false,
})

export async function grantAccess(
  email: string,
  role: 'admin' | 'member',
): Promise<AccessPayload> {
  return postOrExplain<AccessPayload>('/api/admin/access', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, role }),
  })
}

export async function revokeAccess(email: string): Promise<AccessPayload> {
  return postOrExplain<AccessPayload>(`/api/admin/access/${encodeURIComponent(email)}`, {
    method: 'DELETE',
  })
}

/** Turn a source on or off for the next scrape. Returns the refreshed listing. */
export async function setSourceEnabled(
  name: string,
  enabled: boolean,
): Promise<SourcesPayload> {
  return postOrExplain<SourcesPayload>(`/api/admin/sources/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
}

/** Change when the pipeline runs by itself. */
export async function saveSchedule(next: {
  enabled: boolean
  dayOfWeek: number
  hour: number
  minute: number
  timezone: string
}): Promise<SchedulePayload> {
  return postOrExplain<SchedulePayload>('/api/admin/schedule', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(next),
  })
}

/** Start a run now. Returns as soon as it has started, not when it finishes —
 *  a full cycle takes minutes, far longer than any sensible request timeout. */
export async function runPipelineNow(): Promise<{ started: boolean; runId: string; note: string }> {
  return postOrExplain('/api/admin/schedule/run', { method: 'POST' })
}

