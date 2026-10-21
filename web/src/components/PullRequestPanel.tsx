import { useState } from 'react'
import { getPullRequest, openPullRequest, type PullRequestState } from '../api/pullRequest'

/** Opens a pull request for the Build branch, only when the person clicks. The agents never push. */
export function PullRequestPanel({ sessionId, hasCommits, opened = null }: { sessionId: string; hasCommits: boolean; opened?: string | null }) {
  const [state, setState] = useState<PullRequestState | null>(null)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [asDraft, setAsDraft] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [url, setUrl] = useState<string | null>(opened)
  const [editing, setEditing] = useState(false)

  async function begin() {
    setError(null)
    try {
      const s = await getPullRequest(sessionId)
      setState(s); setTitle(s.draft.title); setBody(s.draft.body)
      setAsDraft(!s.ready); setUrl(s.opened?.url ?? null); setEditing(!s.opened)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not prepare the pull request.')
    }
  }

  async function submit() {
    if (!state) return
    setBusy(true); setError(null)
    try {
      const rec = await openPullRequest(sessionId, {
        title, body, draft: asDraft || !state.ready, open_unfinished: !state.ready,
      })
      setUrl(rec.url); setEditing(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The pull request could not be opened.')
    } finally {
      setBusy(false)
    }
  }

  if (url) {
    return (
      <section className="cs-pr" aria-label="Pull request">
        <p className="cs-line" role="status">Pull request opened: <a href={url} target="_blank" rel="noreferrer">{url}</a></p>
      </section>
    )
  }
  if (!hasCommits) return null
  return (
    <section className="cs-pr" aria-label="Pull request">
      {!editing ? (
        <>
          <button type="button" className="btn" onClick={begin}>Open pull request…</button>
          {error && <p className="field-error" role="alert">{error}</p>}
        </>
      ) : state && (
        <form onSubmit={(e) => { e.preventDefault(); void submit() }}>
          <p className="cs-sub">
            This pushes <code>{state.branch}</code> to <code>origin</code> with your own git and GitHub login, and opens a
            pull request into <code>{state.base_branch}</code>. Nothing else is pushed or merged.
          </p>
          <label className="field">
            <span>Title</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} required />
          </label>
          <label className="field">
            <span>Description</span>
            <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={10} required />
          </label>
          <label className="check">
            <input type="checkbox" checked={asDraft || !state.ready} disabled={!state.ready}
                   onChange={(e) => setAsDraft(e.target.checked)} />
            <span>Open as a draft{!state.ready ? ' (required: the team did not finish cleanly)' : ''}</span>
          </label>
          {error && <p className="field-error" role="alert">{error}</p>}
          <div className="cs-pr-actions">
            <button type="submit" className="btn primary" disabled={busy || !title.trim() || !body.trim()}>
              {busy ? 'Opening…' : 'Push branch and open pull request'}
            </button>
            <button type="button" className="btn" onClick={() => setEditing(false)} disabled={busy}>Cancel</button>
          </div>
        </form>
      )}
    </section>
  )
}
