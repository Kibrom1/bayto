import { useEffect, useState } from 'react'
import { createTask, listTasks, type Task, type TaskCreateRequest } from '../api/tasks'
import { NewTaskForm } from './NewTaskForm'
import './TaskBoard.css'

export function TaskBoard() {
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  function refresh() {
    listTasks()
      .then(setTasks)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
  }

  useEffect(refresh, [])

  async function handleCreate(req: TaskCreateRequest) {
    setSubmitting(true)
    setSubmitError(null)
    try {
      const task = await createTask(req)
      setTasks((prev) => (prev ? [task, ...prev] : [task]))
      setFormOpen(false)
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="task-board">
      <header className="task-board-header">
        <h1>Tasks</h1>
        <button type="button" onClick={() => setFormOpen(true)}>
          New task
        </button>
      </header>

      {error && (
        <p role="alert" className="task-board-error">
          Couldn't load tasks: {error}
        </p>
      )}

      {tasks === null && !error && <p>Loading tasks…</p>}

      {tasks !== null && tasks.length === 0 && <p>No tasks yet. Create one to get started.</p>}

      {tasks !== null && tasks.length > 0 && (
        <table className="task-table">
          <thead>
            <tr>
              <th>Title</th>
              <th>Status</th>
              <th>Last session</th>
              <th>Output artifact</th>
              <th>Session</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr key={task.id}>
                <td>{task.title}</td>
                <td>{task.status ?? 'not started'}</td>
                <td>{task.last_session_id ?? '—'}</td>
                <td>{task.output_artifact_id ?? '—'}</td>
                <td>
                  <button
                    type="button"
                    className="task-row-action"
                    onClick={() => window.location.assign(`/tasks/${task.id}/session-setup`)}
                  >
                    Start session
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {formOpen && (
        <NewTaskForm
          submitting={submitting}
          error={submitError}
          onSubmit={handleCreate}
          onCancel={() => {
            setFormOpen(false)
            setSubmitError(null)
          }}
        />
      )}
    </section>
  )
}
