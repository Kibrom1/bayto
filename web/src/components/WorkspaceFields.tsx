import { useId, useState } from 'react'
import { preflightWorkspace, type WorkspaceReport } from '../api/tasks'

export interface WorkspaceDraft {
  repo_path: string
  base_branch: string
  test_command: string
  allowed_commands: string // one command per line
}

export const EMPTY_WORKSPACE: WorkspaceDraft = { repo_path: '', base_branch: 'main', test_command: '', allowed_commands: '' }

/** The request body for a draft: trimmed, with an empty test command sent as null. */
export const workspaceBody = (d: WorkspaceDraft) => ({
  repo_path: d.repo_path.trim(),
  base_branch: d.base_branch.trim() || 'main',
  test_command: d.test_command.trim() || null,
  allowed_commands: d.allowed_commands.split('\n').map((l) => l.trim()).filter(Boolean),
})

/** Repository, base branch and test command for Build mode, with a "Check repository" button. */
export function WorkspaceFields({ value, onChange, legend = 'Repository' }: { value: WorkspaceDraft; onChange: (v: WorkspaceDraft) => void; legend?: string }) {
  const repoId = useId()
  const branchId = useId()
  const testId = useId()
  const extraId = useId()
  const [checking, setChecking] = useState(false)
  const [report, setReport] = useState<WorkspaceReport | null>(null)
  const [checkError, setCheckError] = useState<string | null>(null)

  function set(patch: Partial<WorkspaceDraft>) {
    setReport(null)
    onChange({ ...value, ...patch })
  }

  async function check() {
    setChecking(true)
    setCheckError(null)
    setReport(null)
    try {
      setReport(await preflightWorkspace(workspaceBody(value)))
    } catch (err) {
      setCheckError(err instanceof Error ? err.message : String(err))
    } finally {
      setChecking(false)
    }
  }

  return (
    <fieldset className="build-fields">
      {legend && <legend>{legend}</legend>}
      <div className="field">
        <label htmlFor={repoId}>Repository path</label>
        <input id={repoId} value={value.repo_path} onChange={(e) => set({ repo_path: e.target.value })}
          placeholder="/Users/you/code/my-project" spellCheck={false} />
        <span className="hint">The full path to the repository root, on this computer.</span>
      </div>
      <div className="field">
        <label htmlFor={branchId}>Base branch</label>
        <input id={branchId} value={value.base_branch} onChange={(e) => set({ base_branch: e.target.value })} spellCheck={false} />
      </div>
      <div className="field">
        <label htmlFor={testId}>Test command</label>
        <input id={testId} value={value.test_command} onChange={(e) => onChange({ ...value, test_command: e.target.value })}
          placeholder="npm test   or   cd web && npm test" spellCheck={false} />
        <span className="hint">Plain commands joined with &amp;&amp;. No pipes, redirects or semicolons. The tester runs exactly this.</span>
      </div>
      <div className="field">
        <label htmlFor={extraId}>Other allowed commands (optional)</label>
        <textarea id={extraId} rows={2} value={value.allowed_commands} onChange={(e) => set({ allowed_commands: e.target.value })}
          placeholder={'uv sync\nnpm run lint'} spellCheck={false} />
        <span className="hint">One per line, same rules as the test command. The developer and tester may run these too. Commands like git push, curl and sudo are never allowed.</span>
      </div>
      <button type="button" className="btn sm check-btn" disabled={checking || value.repo_path.trim() === ''} onClick={check}>
        {checking ? 'Checking…' : 'Check repository'}
      </button>
      {checkError && <p role="alert" className="alert bad">{checkError}</p>}
      {report && report.ok && (
        <p role="status" className={`alert ${report.dirty ? 'warn' : 'ok'}`}>
          {report.dirty
            ? `Found the repository on ${report.current_branch}, but it has uncommitted changes (${report.dirty_files.length} file${report.dirty_files.length === 1 ? '' : 's'}). Commit or stash them before you start a Build session.`
            : `Found the repository on ${report.current_branch}. Working tree is clean.`}
        </p>
      )}
      {report && !report.ok && <p role="alert" className="alert bad">{report.problems[0]?.detail}</p>}
    </fieldset>
  )
}
