import { useId, useState, type FormEvent } from 'react'
import { SEMANTIC_LABEL, type TaskCreateRequest } from '../api/tasks'
import { EMPTY_WORKSPACE, WorkspaceFields, workspaceBody, type WorkspaceDraft } from '../components/WorkspaceFields'
import { Modal } from '../components/Dialog'

interface NewTaskFormProps {
  submitting: boolean
  error: string | null
  onSubmit: (req: TaskCreateRequest) => void
  onCancel: () => void
}

// Common finishes, shown as chips; the field below still takes any other type.
const OUTPUT_CHOICES: { label: string; value: string }[] = [
  { label: 'Decision', value: 'decision' },
  { label: 'Plan', value: 'plan' },
  { label: 'List', value: 'list' },
  { label: 'Document', value: 'document' },
  { label: 'Code', value: 'code change' },
]

export function NewTaskForm({ submitting, error, onSubmit, onCancel }: NewTaskFormProps) {
  const [title, setTitle] = useState('')
  const [brief, setBrief] = useState('')
  const [outputType, setOutputType] = useState('')
  const [successCriteria, setSuccessCriteria] = useState('')
  const [semantic, setSemantic] = useState(false)
  const titleId = useId()
  const briefId = useId()
  const outputTypeId = useId()
  const successCriteriaId = useId()

  // Build mode: a task that changes code names the repository the team works in.
  const [build, setBuild] = useState(false)
  const [ws, setWs] = useState<WorkspaceDraft>(EMPTY_WORKSPACE)

  function toggleBuild(on: boolean) {
    setBuild(on)
    if (on && !outputType.trim()) setOutputType('code change')
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    onSubmit({
      title: title.trim(),
      brief: brief.trim(),
      output_type: outputType.trim(),
      success_criteria: successCriteria.trim() || null,
      ...(semantic ? { semantic_retrieval: true } : {}),
      ...(build ? { workspace: workspaceBody(ws) } : {}),
    })
  }

  const canSubmit = title.trim() !== '' && brief.trim() !== '' && outputType.trim() !== '' && (!build || ws.repo_path.trim() !== '')

  return (
    <Modal label="New task" onClose={() => { if (!submitting) onCancel() }}>
      <form onSubmit={handleSubmit}>
        <div className="modal-body">
          <h2>New task</h2>
          <p className="hint" style={{ margin: '0 0 var(--space-4)' }}>
            Describe what you want the team to work out. A clear question and a clear finish line get the best discussion.
          </p>

          <div className="field">
            <label htmlFor={titleId}>Title</label>
            <input id={titleId} value={title} onChange={(e) => setTitle(e.target.value)} required autoFocus placeholder="Weekly or biweekly releases?" />
            <span className="error-text">Give the task a title.</span>
          </div>

          <div className="field">
            <label htmlFor={briefId}>Brief</label>
            <textarea id={briefId} value={brief} onChange={(e) => setBrief(e.target.value)} required rows={5}
              placeholder="Context, constraints and the question the team should answer." />
            <span className="error-text">Add a short brief so the agents know what to discuss.</span>
          </div>

          <div className="field">
            <label htmlFor={outputTypeId}>Output type</label>
            <div className="chip-choices" role="group" aria-label="Quick picks">
              {OUTPUT_CHOICES.map((c) => (
                <button key={c.value} type="button" className="chip-choice" aria-pressed={outputType.trim().toLowerCase() === c.value}
                  onClick={() => setOutputType(c.value)}>{c.label}</button>
              ))}
            </div>
            <input id={outputTypeId} value={outputType} onChange={(e) => setOutputType(e.target.value)}
              placeholder="Or type your own, for example memo" required />
            <span className="hint">What the final synthesis should be.</span>
            <span className="error-text">Choose or type an output type.</span>
          </div>

          <div className="field">
            <label htmlFor={successCriteriaId}>Success criteria (optional)</label>
            <textarea id={successCriteriaId} value={successCriteria} onChange={(e) => setSuccessCriteria(e.target.value)} rows={2}
              placeholder="How will you judge a good answer?" />
          </div>

          <div className="field build-toggle">
            <label>
              <input type="checkbox" checked={semantic} onChange={(e) => setSemantic(e.target.checked)} />{' '}
              {SEMANTIC_LABEL}
            </label>
            <span className="hint">Off by default. Only used when attached documents are too long to send whole. If this server has no search provider set up, keyword search is used instead.</span>
          </div>

          <div className="field build-toggle card-toggle">
            <label>
              <input type="checkbox" checked={build} onChange={(e) => toggleBuild(e.target.checked)} />{' '}
              This task changes code (Build mode)
            </label>
            <span className="hint">The team plans, codes, tests and reviews on a new git branch in your repository. You review and merge it.</span>
          </div>

          {build && <WorkspaceFields value={ws} onChange={setWs} />}

          {error && <p role="alert" className="alert bad" style={{ margin: 0 }}>{error}</p>}
        </div>
        <div className="modal-actions">
          {!canSubmit && !submitting && <span className="hint" id="create-hint">Fill in the title, brief and output type (and the repository path for code changes).</span>}
          <button type="button" className="btn" onClick={onCancel} disabled={submitting}>Cancel</button>
          <button type="submit" className="btn primary" disabled={!canSubmit || submitting} aria-describedby={canSubmit ? undefined : 'create-hint'}>
            {submitting ? 'Creating…' : 'Create task'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
