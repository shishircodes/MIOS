// The Python/LLM backend (FastAPI bridge over the MIOS pipeline).
// Override at build/dev time with VITE_API_BASE.
//
// Lives in its own module so `api.ts` and `auth.ts` can both use it without
// importing each other — auth.ts needs the base URL, api.ts needs auth's
// credentialed fetch, and a cycle between them would force a dynamic import.
export const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8787'
