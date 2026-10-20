import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { archiveSession, deleteSession } from '../api/sessions'
import { ConfirmDialog } from '../components/Dialog'
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
  if (!status) return <span className="chip"><span className="dot" />Not started</span>
  const tone = status === 'active' ? 'ok' : status === 'failed' ? 'bad' : status === 'needs_human' || status === 'stopping' ? 'warn' : ''
  return <span className={`chip ${tone}`}><span className="dot" />{STATUS_LABEL[status] ?? status}</span>
}

type Filter = 'all' | 'live' | 'finished' | 'ready'
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'live', label: 'Live' },
  { id: 'finished', label: 'Finished' },
  { id: 'ready', label: 'Not started' },
]
const LIVE = ['active', 'needs_human', 'starting', 'stopping']
const DONE = ['finished', 'completed']

function matches(task: Task, f: Filter): boolean {
  if (f === 'all') return true
  if (f === 'live') return LIVE.includes(task.status ?? '')
  if (f === 'finished') return DONE.includes(task.status ?? '') || !!task.output_artifact_id
  return !task.status
}

function actionsFor(task: Task, onArchive: (t: Task) => void, onDelete: (t: Task) => void) {
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
      {task.last_session_id && !['active', 'starting', 'stopping'].includes(task.status ?? '') && (
        <details
          className="row-menu"
          onKeyDown={(e) => {
            const d = e.currentTarget
            const items = [...d.querySelectorAll<HTMLButtonElement>('.row-menu-list button')]
            const i = items.indexOf(document.activeElement as HTMLButtonElement)
            if (e.key === 'Escape' && d.open) { e.preventDefault(); d.open = false; d.querySelector('summary')?.focus() }
            else if (e.key === 'ArrowDown' && d.open) { e.preventDefault(); items[(i + 1) % items.length]?.focus() }
            else if (e.key === 'ArrowUp' && d.open) { e.preventDefault(); items[(i <= 0 ? items.length : i) - 1]?.focus() }
          }}
          onToggle={(e) => { if (e.currentTarget.open) e.currentTarget.querySelector<HTMLButtonElement>('.row-menu-list button')?.focus() }}
        >
          <summary className="btn ghost sm" aria-label={`More actions for ${task.title}`}>More</summary>
          <div className="row-menu-list" role="group" aria-label="Task actions">
            <button type="button" onClick={() => onArchive(task)}>Archive</button>
            <button type="button" className="danger" onClick={() => onDelete(task)}>Delete</button>
          </div>
        </details>
      )}
    </>
  )
}

export function TaskBoard() {
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const [toDelete, setToDelete] = useState<Task | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  function refresh() {
    listTasks()
      .then(setTasks)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
  }

  useEffect(refresh, [])

  async function archive(task: Task) {
    setActionError(null)
    try {
      await archiveSession(task.last_session_id!)
      refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    }
  }

  async function remove(task: Task) {
    setToDelete(null)
    setActionError(null)
    try {
      await deleteSession(task.last_session_id!)
      refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    }
  }

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

  const q = query.trim().toLowerCase()
  const visible = (tasks ?? []).filter((t) => matches(t, filter) && (!q || `${t.title} ${t.brief ?? ''}`.toLowerCase().includes(q)))

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>Your tasks</h1>
          <p className="page-sub">Each task is a question for the table: a team of agents works through it and ends with a written synthesis you can use.</p>
        </div>
        <button type="button" className="btn primary" onClick={() => setFormOpen(true)}>New task</button>
      </header>

      {error && (
        <p role="alert" className="alert bad">
          Couldn't load tasks: {error}
          <button type="button" className="btn sm" style={{ marginLeft: 'var(--space-3)' }} onClick={() => { setError(null); refresh() }}>Retry</button>
        </p>
      )}

      {actionError && <p role="alert" className="alert bad">{actionError}</p>}

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
        <>
          <div className="task-toolbar">
            <div className="task-filters" role="group" aria-label="Filter tasks">
              {FILTERS.map((f) => (
                <button key={f.id} type="button" className="filter-pill" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>{f.label}</button>
              ))}
            </div>
            <input type="search" className="input task-search" aria-label="Search tasks" placeholder="Search tasks" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <ul className="task-list">
            {visible.map((task) => (
              <li key={task.id} className="task-row">
                <div className="task-status">{statusChip(task.status)}</div>
                <div className="task-main">
                  <h2 className="task-title">{task.title.trim() || '(untitled task)'}</h2>
                  {task.brief && <p className="task-brief">{task.brief}</p>}
                </div>
                <div className="task-meta">
                  <span className="task-output">{task.output_type}</span>
                  {task.output_artifact_id && <span className="chip accent">{task.workspace ? 'Change ready' : 'Synthesis ready'}</span>}
                </div>
                <div className="task-actions">{actionsFor(task, archive, setToDelete)}</div>
              </li>
            ))}
          </ul>
          {visible.length === 0 && (
            <p className="empty-note">Nothing matches. Try another filter or search.</p>
          )}
        </>
      )}

      {toDelete && (
        <ConfirmDialog
          title="Delete this session?"
          message="Its conversation, results and cost record are removed for good. The task stays. To keep the record but stop it from shaping later sessions, archive it instead."
          confirmLabel="Delete session"
          cancelLabel="Keep it"
          danger
          onCancel={() => setToDelete(null)}
          onConfirm={() => remove(toDelete)}
        />
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
