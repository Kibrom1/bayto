import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { createTask, listTasks, type Task, type TaskCreateRequest } from '../api/tasks'
import { NewTaskForm } from './NewTaskForm'
import './TaskBoard.css'

const STATUS_LABEL: Record<string, string> = {
  created: 'Ready',
  starting: 'Starting',
  active: 'In progress',
  needs_human: 'Paused',
  stopping: 'Finishing',
  finished: 'Done',
  completed: 'Done',
  failed: 'Failed',
}

function statusChip(status: string | null) {
  if (!status) return <span className="chip">Not started</span>
  const tone = status === 'active' ? 'ok' : status === 'failed' ? 'bad' : status === 'needs_human' || status === 'stopping' ? 'warn' : status === 'finished' || status === 'completed' ? 'accent' : ''
  return <span className={`chip ${tone}`}>{STATUS_LABEL[status] ?? status}</span>
}

function actionsFor(task: Task) {
  const live = ['active', 'needs_human', 'starting', 'stopping'].includes(task.status ?? '')
  const done = ['finished', 'completed'].includes(task.status ?? '')
  return (
    <>
      {task.last_session_id && (
        <Link className={`btn ${live || done ? 'primary' : ''}`} to={`/room/${task.last_session_id}`}>
          {live ? 'Open room' : done ? 'View result' : 'View last session'}
        </Link>
      )}
      <Link className={`btn ${task.last_session_id ? 'ghost' : 'primary'}`} to={`/setup/${task.id}`}>
        {task.last_session_id ? 'New session' : 'Start session'}
      </Link>
    </>
  )
}

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
    <section className="page">
      <header className="page-head">
        <div>
          <h1>Tasks</h1>
          <p className="page-sub">Each task is a question for a team of agents to work through, ending in a written synthesis you can use.</p>
        </div>
        <button type="button" className="btn primary" onClick={() => setFormOpen(true)}>New task</button>
      </header>

      {error && (
        <p role="alert" className="alert bad">
          Couldn't load tasks: {error}
          <button type="button" className="btn sm" style={{ marginLeft: 'var(--space-3)' }} onClick={() => { setError(null); refresh() }}>Retry</button>
        </p>
      )}

      {tasks === null && !error && (
        <div aria-busy="true" aria-label="Loading tasks">
          {[0, 1, 2].map((i) => <div key={i} className="skeleton" style={{ height: '5.5rem', marginBottom: 'var(--space-3)' }} />)}
        </div>
      )}

      {tasks !== null && tasks.length === 0 && (
        <div className="empty-state card">
          <h2>No tasks yet</h2>
          <p>Create your first task, pick a few agents, and watch them debate it to a conclusion.</p>
          <button type="button" className="btn primary" onClick={() => setFormOpen(true)}>Create your first task</button>
        </div>
      )}

      {tasks !== null && tasks.length > 0 && (
        <ul className="task-list">
          {tasks.map((task) => (
            <li key={task.id} className="task-card card">
              <div className="task-main">
                <h2 className="task-title">{task.title}</h2>
                {task.brief && <p className="task-brief">{task.brief}</p>}
                <div className="task-meta">
                  {statusChip(task.status)}
                  <span className="chip">{task.output_type}</span>
                  {task.output_artifact_id && <span className="chip accent">Synthesis ready</span>}
                </div>
              </div>
              <div className="task-actions">{actionsFor(task)}</div>
            </li>
          ))}
        </ul>
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
