import { useId, useState, type FormEvent } from 'react'
import type { TaskCreateRequest } from '../api/tasks'

interface NewTaskFormProps {
  submitting: boolean
  error: string | null
  onSubmit: (req: TaskCreateRequest) => void
  onCancel: () => void
}

export function NewTaskForm({ submitting, error, onSubmit, onCancel }: NewTaskFormProps) {
  const [title, setTitle] = useState('')
  const [brief, setBrief] = useState('')
  const [outputType, setOutputType] = useState('')
  const [successCriteria, setSuccessCriteria] = useState('')
  const titleId = useId()
  const briefId = useId()
  const outputTypeId = useId()
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
    <div className="new-task-overlay" role="dialog" aria-modal="true" aria-label="New task">
      <form className="new-task-form" onSubmit={handleSubmit}>
        <h2>New task</h2>

        <label htmlFor={titleId}>Title</label>
        <input
          id={titleId}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          required
        />

        <label htmlFor={briefId}>Brief</label>
        <textarea
          id={briefId}
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
          required
          rows={5}
        />

        <label htmlFor={outputTypeId}>Output type</label>
        <input
          id={outputTypeId}
          value={outputType}
          onChange={(e) => setOutputType(e.target.value)}
          placeholder="e.g. memo, report, code-review"
          required
        />

        <label htmlFor={successCriteriaId}>Success criteria (optional)</label>
        <textarea
          id={successCriteriaId}
          value={successCriteria}
          onChange={(e) => setSuccessCriteria(e.target.value)}
          rows={3}
        />

        {error && (
          <p role="alert" className="new-task-error">
            {error}
          </p>
        )}

        <div className="new-task-actions">
          <button type="button" onClick={onCancel} disabled={submitting}>
            Cancel
          </button>
          <button type="submit" disabled={!canSubmit || submitting}>
            {submitting ? 'Creating…' : 'Create task'}
          </button>
        </div>
      </form>
    </div>
  )
}
