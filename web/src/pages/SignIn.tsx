import { useState } from 'react'
import type { FormEvent } from 'react'
import { BrandMark } from '../components/AppShell'

/** Shown instead of the app when the server has BAYTO_API_TOKEN set and this browser has no session. */
export function SignIn({ onSubmit }: { onSubmit: (token: string) => Promise<void> }) {
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!token || busy) return
    setBusy(true)
    setError(null)
    try {
      await onSubmit(token)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not sign in')
      setBusy(false)
    }
  }
  return (
    <main className="signin" id="main">
      <form onSubmit={submit} className="signin-card">
        <h1><BrandMark size={26} /> Bayto</h1>
        <p>This server asks for an access token.</p>
        <label htmlFor="access-token">Access token</label>
        <input id="access-token" type="password" autoComplete="current-password" value={token}
          onChange={(e) => setToken(e.target.value)} autoFocus />
        {error && <p role="alert" className="signin-error">{error}</p>}
        <button type="submit" disabled={!token || busy}>Sign in</button>
      </form>
    </main>
  )
}
