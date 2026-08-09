import { defineConfig } from 'vite'
import tsConfigPaths from 'vite-tsconfig-paths'
import { tanstackStart } from '@tanstack/react-start/plugin/vite'
import viteReact from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  server: { port: 3000 },
  plugins: [
    tsConfigPaths(),
    tailwindcss(),
    // `customViteReactPlugin: true` used to tell tanstackStart not to inject its
    // own React plugin. The option was removed upstream (it is no longer in any
    // @tanstack type or bundle), so passing it was a no-op that failed tsc.
    // The plugin no longer injects React, so listing viteReact() here is correct.
    tanstackStart(),
    viteReact(),
  ],
})
