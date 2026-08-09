/**
 * Production server for the MIOS dashboard.
 *
 * `vite build` emits `dist/server/server.js`, which exports a Web-Fetch handler
 * (`{ fetch(request) }`) and deliberately does NOT listen on a port — TanStack
 * Start leaves the choice of runtime to the deployment. This file is that
 * choice: it binds the handler to a Node HTTP server and serves the client
 * bundle in front of it.
 *
 *   node server.js          # PORT / HOST from the environment
 *
 * srvx is TanStack's own transitive dependency, so this adds no new package.
 * Its Node adapter handles the Request/Response <-> node:http bridge, including
 * streaming SSR responses, which is fiddly to get right by hand.
 */
import { existsSync, statSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { extname, join, normalize, resolve, sep } from 'node:path'

import { serve } from 'srvx/node'

import handler from './dist/server/server.js'

const CLIENT_DIR = resolve(import.meta.dirname, 'dist/client')
const PORT = Number(process.env.PORT ?? 3000)
const HOST = process.env.HOST ?? '0.0.0.0'

const MIME = {
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.map': 'application/json; charset=utf-8',
}

/**
 * Resolve a URL path to a file inside dist/client, or null.
 *
 * Rejects anything that escapes the directory: a request for
 * `/../../etc/passwd` must not be served just because the path joins cleanly.
 */
function resolveStatic(pathname) {
  if (pathname === '/' || pathname.endsWith('/')) return null
  const decoded = decodeURIComponent(pathname)
  const candidate = normalize(join(CLIENT_DIR, decoded))
  if (candidate !== CLIENT_DIR && !candidate.startsWith(CLIENT_DIR + sep)) return null
  if (!existsSync(candidate) || !statSync(candidate).isFile()) return null
  return candidate
}

serve({
  port: PORT,
  hostname: HOST,
  async fetch(request) {
    const { pathname } = new URL(request.url)

    const file = resolveStatic(pathname)
    if (file) {
      const body = await readFile(file)
      // Vite fingerprints filenames under /assets/, so those are immutable.
      const immutable = pathname.startsWith('/assets/')
      return new Response(body, {
        headers: {
          'content-type': MIME[extname(file).toLowerCase()] ?? 'application/octet-stream',
          'cache-control': immutable
            ? 'public, max-age=31536000, immutable'
            : 'public, max-age=3600',
        },
      })
    }

    // Everything else is an SSR route.
    return handler.fetch(request)
  },
})

console.log(`MIOS dashboard listening on http://${HOST}:${PORT}`)
