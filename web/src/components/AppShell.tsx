import { useEffect, useState } from 'react'
import { NavLink, Outlet, useMatch } from 'react-router-dom'
import { feedbackHref, getAppConfig } from '../api/appConfig'

export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <circle cx="16" cy="16" r="12" fill="none" stroke="currentColor" strokeWidth="2" />
      <circle cx="16" cy="4" r="3.2" fill="currentColor" />
      <circle cx="26.4" cy="22" r="3.2" fill="currentColor" />
      <circle cx="5.6" cy="22" r="3.2" fill="#f5b301" />
      <circle cx="16" cy="16" r="2" fill="currentColor" />
    </svg>
  )
}

export function AppShell() {
  const [feedbackUrl, setFeedbackUrl] = useState<string | null>(null)
  const roomId = useMatch('/room/:sessionId')?.params.sessionId
  useEffect(() => {
    let live = true
    getAppConfig().then((c) => { if (live) setFeedbackUrl(c.feedback_url) })
    return () => { live = false }
  }, [])
  const feedback = feedbackHref(feedbackUrl, roomId)
  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="shell-header">
        <NavLink to="/" className="brand">
          <BrandMark />
          Bayto
        </NavLink>
        <nav className="shell-nav" aria-label="Main">
          <NavLink to="/" end>Tasks</NavLink>
          <NavLink to="/services">Services</NavLink>
        </nav>
        {feedback && (
          <a className="feedback-link" href={feedback} target="_blank" rel="noopener noreferrer">Send feedback</a>
        )}
      </header>
      <main id="main" tabIndex={-1}>
        <Outlet />
      </main>
    </>
  )
}
