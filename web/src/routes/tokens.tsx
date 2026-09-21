import { createFileRoute, redirect } from '@tanstack/react-router'

// The page that lived here was split into AI models and Usage & cost. Kept so
// bookmarks and old links land on the model settings instead of a 404.
export const Route = createFileRoute('/tokens')({
  beforeLoad: () => {
    throw redirect({ to: '/models' })
  },
})
