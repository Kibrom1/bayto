import { Link } from 'react-router-dom'
import { useDocumentTitle } from '../hooks/useDocumentTitle'

interface NotFoundProps {
  title?: string
  message?: string
}

/** The one "nothing here" page: also used for a missing session or task, so they all look the same. */
export function NotFound({ title = 'Page not found', message = 'There is nothing at this address.' }: NotFoundProps) {
  useDocumentTitle(title)
  return (
    <div className="page">
      <h1>{title}</h1>
      <p className="muted">{message}</p>
      <Link className="btn" to="/">← Tasks</Link>
    </div>
  )
}
