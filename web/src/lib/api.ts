import { queryOptions } from '@tanstack/react-query'
import { API_BASE } from './config'
import { fetchJson } from './auth'
import type {
  DigestPayload,
  FeedPayload,
  FeedQuery,
  MatchResponse,
  ParsedCV,
  ProfileDraft,
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

// ---------- Mode Push ----------

/**
 * The API answers a rejected CV with a written explanation ("that PDF is a
 * scan", "re-save as .docx"). fetchJson collapses every non-401 into a status
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
