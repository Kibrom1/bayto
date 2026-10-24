import { useId, useState } from 'react'
import type { FormEvent } from 'react'
import { BrandMark } from '../components/AppShell'
import { ApiError } from '../api/errors'
import { useDocumentTitle } from '../hooks/useDocumentTitle'

/** Shown instead of the app when the server asks for an access token and this browser has no session. */
export function SignIn({ onSubmit }: { onSubmit: (token: string) => Promise<void> }) {
  useDocumentTitle('Sign in')
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [show, setShow] = useState(false)
  const helpId = useId()
  async function submit(e: FormEvent) {
    e.preventDefault()
    if (busy) return
    if (!token) { setError('Enter your access token to continue.'); return }
    setBusy(true)
    setError(null)
    try {
      await onSubmit(token)
    } catch (err) {
      const rejected = err instanceof ApiError && (err.status === 401 || err.status === 403)
      setError(rejected ? 'That token was not accepted. Check it and try again.' : err instanceof Error ? err.message : 'Could not sign in')
      setBusy(false)
    }
  }
  return (
    <main className="signin" id="main">
      <form onSubmit={submit} className="signin-card" noValidate>
        <div className="signin-brand"><BrandMark size={26} /> Bayto</div>
        <h1>Sign in</h1>
        <p id={helpId} className="hint">This server asks for an access token. Ask whoever runs it, or check the token it was set up with.</p>
        <label htmlFor="access-token">Access token</label>
        <div className="signin-field">
          <input id="access-token" className="input" type={show ? 'text' : 'password'} autoComplete="current-password" value={token}
            aria-describedby={helpId} aria-invalid={error ? true : undefined}
            onChange={(e) => { setToken(e.target.value); if (error) setError(null) }} autoFocus />
          <button type="button" className="btn" aria-pressed={show} onClick={() => setShow((v) => !v)}>{show ? 'Hide token' : 'Show token'}</button>
        </div>
        {error && <p role="alert" className="signin-error">{error}</p>}
        <button type="submit" className="btn primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </main>
  )
}
