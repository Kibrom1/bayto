import { Link } from 'react-router-dom'

export function NotFound() {
  return (
    <div className="page">
      <h1>Page not found</h1>
      <p className="muted">There is nothing at this address.</p>
      <Link className="btn" to="/">← Tasks</Link>
    </div>
  )
}
