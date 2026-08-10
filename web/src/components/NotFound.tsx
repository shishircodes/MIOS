import { Link } from '@tanstack/react-router'

export function NotFound() {
  return (
    <div className="page">
      <div className="center-empty">
        <h1>Page not found</h1>
        <p>The page you requested does not exist.</p>
        <Link className="btn" to="/monitor/digest">
          Back to the dashboard
        </Link>
      </div>
    </div>
  )
}
