import { useState } from 'react'
import type { ArtifactOut } from '../api/sessions'
import { PullRequestPanel } from '../components/PullRequestPanel'
import './ChangeSet.css'

interface ChangeSetContent {
  branch: string
  base_branch: string
  switch_command: string
  status: 'ready_to_merge' | 'needs_you'
  commits: { sha: string; subject: string }[]
  files: { status: string; path: string }[]
  files_changed: number
  insertions: number
  deletions: number
  diff_stat?: string
  tests: { command: string | null; exit_code: number | null; tail: string } | null
  test_command?: string | null
  review: { verdict: string | null; notes: string } | null
  fix_cycles: number
  open_issues: string[]
  git_guard?: { changed: string[]; restored: boolean }
  summary?: string | null
  pull_request?: { url: string } | null
}

const FILE_STATUS: Record<string, string> = { A: 'added', M: 'changed', D: 'deleted', R: 'renamed' }
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

/** What a Build session left on its branch: read from git and from the tester's and reviewer's own reports. */
export function ChangeSet({ artifacts, sessionId }: { artifacts: ArtifactOut[]; sessionId?: string }) {
  const [copied, setCopied] = useState(false)
  const art = artifacts.find((a) => a.type === 'change_set')
  if (!art) return null
  const cs = art.content_json as unknown as ChangeSetContent
  const ready = cs.status === 'ready_to_merge'

  async function copy() {
    try {
      await navigator.clipboard.writeText(cs.switch_command)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard unavailable: the command is shown on screen to copy by hand */
    }
  }

  const testsPassed = cs.tests?.exit_code === 0

  return (
    <article className={`changeset ${ready ? 'ready' : 'needs-you'}`} aria-label="Change set">
      <header className="cs-head">
        <div>
          <p className="room-kicker">Change set</p>
          <h2 className="cs-title">{ready ? 'Ready to review and merge' : 'Needs your attention'}</h2>
        </div>
        <span className={`chip ${ready ? 'ok' : 'warn'}`}>{ready ? 'Tests passed · reviewed' : 'Not finished'}</span>
      </header>

      <div className="cs-branch">
        <code>{cs.switch_command}</code>
        <button type="button" className="btn sm" onClick={copy}>{copied ? 'Copied' : 'Copy'}</button>
      </div>
      <p className="cs-sub">
        On branch <code>{cs.branch}</code>, based on <code>{cs.base_branch}</code>. Nothing was pushed until you choose to open a pull request; you merge it.
      </p>
      <p className="cs-sub" data-testid="repo-left-on">
        Your repository is still checked out on <code>{cs.branch}</code>. When you have finished reviewing, go
        back with <code>git switch {cs.base_branch}</code>.
      </p>

      {cs.open_issues.length > 0 && (
        <ul className="cs-issues" aria-label="Open issues">
          {cs.open_issues.map((i) => <li key={i}>{i}</li>)}
        </ul>
      )}

      <dl className="cs-stats">
        <div><dt>Commits</dt><dd>{cs.commits.length}</dd></div>
        <div><dt>Files</dt><dd>{cs.files_changed}</dd></div>
        <div><dt>Added</dt><dd className="plus">+{cs.insertions}</dd></div>
        <div><dt>Removed</dt><dd className="minus">{cs.deletions ? `−${cs.deletions}` : '0'}</dd></div>
        <div><dt>Fix cycles</dt><dd>{cs.fix_cycles}</dd></div>
      </dl>

      <section aria-labelledby="cs-tests">
        <h3 id="cs-tests" className="syn-h">Tests</h3>
        {cs.tests ? (
          <>
            <p className="cs-line">
              <span className={`chip ${testsPassed ? 'ok' : 'bad'}`}>exit {cs.tests.exit_code ?? '?'}</span>{' '}
              {cs.tests.command ? <code>{cs.tests.command}</code> : <em>command not shown</em>}
            </p>
            <pre className="cs-pre" tabIndex={0}>{cs.tests.tail}</pre>
          </>
        ) : (
          <p className="cs-sub">The tester did not report a test run.</p>
        )}
      </section>

      <section aria-labelledby="cs-review">
        <h3 id="cs-review" className="syn-h">Review</h3>
        {cs.review ? (
          <>
            <p className="cs-line">
              <span className={`chip ${cs.review.verdict === 'approve' ? 'ok' : 'warn'}`}>{cs.review.verdict ?? 'no verdict'}</span>
            </p>
            <p className="syn-text">{cs.review.notes}</p>
          </>
        ) : (
          <p className="cs-sub">The reviewer did not report.</p>
        )}
      </section>

      <section aria-labelledby="cs-commits">
        <h3 id="cs-commits" className="syn-h">{plural(cs.commits.length, 'commit')}</h3>
        {cs.commits.length === 0 ? <p className="cs-sub">No commits landed on the branch.</p> : (
          <ul className="cs-list">
            {cs.commits.map((c) => <li key={c.sha}><code>{c.sha}</code> {c.subject}</li>)}
          </ul>
        )}
      </section>

      {cs.files.length > 0 && (
        <details className="cs-files">
          <summary>{plural(cs.files.length, 'file')} changed</summary>
          <ul className="cs-list">
            {cs.files.map((f) => <li key={f.path}><span className="cs-file-status">{FILE_STATUS[f.status] ?? f.status}</span> <code>{f.path}</code></li>)}
          </ul>
        </details>
      )}

      {sessionId && <PullRequestPanel sessionId={sessionId} hasCommits={cs.commits.length > 0} opened={cs.pull_request?.url ?? null} />}
    </article>
  )
}
