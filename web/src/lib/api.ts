import { queryOptions } from '@tanstack/react-query'
import { fetchJson } from './auth'
import type { DigestPayload } from './types'

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
