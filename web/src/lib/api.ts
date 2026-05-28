import { queryOptions } from '@tanstack/react-query'
import type { DigestPayload } from './types'

// The Python/LLM backend (FastAPI bridge over the MIOS pipeline).
// Override at build/dev time with VITE_API_BASE.
export const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8787'

export async function fetchDigest(): Promise<DigestPayload> {
  const res = await fetch(`${API_BASE}/api/digest`)
  if (!res.ok) {
    throw new Error(`Backend returned ${res.status} for /api/digest`)
  }
  return (await res.json()) as DigestPayload
}

export const digestQueryOptions = queryOptions({
  queryKey: ['digest'],
  queryFn: fetchDigest,
})
