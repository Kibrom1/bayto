import { useId, useState, type FormEvent } from 'react'
import type { TaskCreateRequest } from '../api/tasks'
import { Modal } from '../components/Dialog'

interface NewTaskFormProps {
  submitting: boolean
  error: string | null
  onSubmit: (req: TaskCreateRequest) => void
  onCancel: () => void
}

const OUTPUT_TYPES = ['decision', 'plan', 'memo', 'review', 'ideas', 'report']

export function NewTaskForm({ submitting, error, onSubmit, onCancel }: NewTaskFormProps) {
  const [title, setTitle] = useState('')
  const [brief, setBrief] = useState('')
  const [outputType, setOutputType] = useState('')
  const [successCriteria, setSuccessCriteria] = useState('')
  const titleId = useId()
  const briefId = useId()
  const outputTypeId = useId()
  const listId = useId()
  const successCriteriaId = useId()

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    onSubmit({
      title: title.trim(),
      brief: brief.trim(),
      output_type: outputType.trim(),
      success_criteria: successCriteria.trim() || null,
    })
  }

  const canSubmit = title.trim() !== '' && brief.trim() !== '' && outputType.trim() !== ''

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
            <input id={outputTypeId} list={listId} value={outputType} onChange={(e) => setOutputType(e.target.value)}
              placeholder="Pick one or type your own" required />
            <datalist id={listId}>{OUTPUT_TYPES.map((t) => <option key={t} value={t} />)}</datalist>
            <span className="hint">What the final synthesis should be, for example a decision or a plan.</span>
            <span className="error-text">Choose or type an output type.</span>
          </div>

          <div className="field">
            <label htmlFor={successCriteriaId}>Success criteria (optional)</label>
            <textarea id={successCriteriaId} value={successCriteria} onChange={(e) => setSuccessCriteria(e.target.value)} rows={2}
              placeholder="How will you judge a good answer?" />
          </div>

          {error && <p role="alert" className="alert bad" style={{ margin: 0 }}>{error}</p>}
        </div>
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onCancel} disabled={submitting}>Cancel</button>
          <button type="submit" className="btn primary" disabled={!canSubmit || submitting}>
            {submitting ? 'Creating…' : 'Create task'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
