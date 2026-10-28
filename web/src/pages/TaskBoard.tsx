import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { isApiDown } from '../api/errors'
import { archiveSession, deleteSession } from '../api/sessions'
import { ConfirmDialog } from '../components/Dialog'
import { createTask, deleteTask, listTasks, type Task, type TaskCreateRequest } from '../api/tasks'
import { searchTranscripts, snippetParts, type SearchHit } from '../api/search'
import { NewTaskForm } from './NewTaskForm'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import './TaskBoard.css'

const STATUS_LABEL: Record<string, string> = {
  created: 'Not started',
  starting: 'Starting',
  active: 'In progress',
  needs_human: 'Paused',
  stopping: 'Finishing',
  finished: 'Finished',
  completed: 'Finished',
  failed: 'Failed',
  orphaned: 'Interrupted',
}

function statusChip(status: string | null) {
  if (!status) return <span className="chip"><span className="dot" />Not started</span>
  const tone = status === 'active' ? 'ok' : status === 'failed' ? 'bad' : status === 'orphaned' ? 'warn' : status === 'needs_human' || status === 'stopping' ? 'warn' : ''
  return <span className={`chip ${tone}`}><span className="dot" />{STATUS_LABEL[status] ?? status}</span>
}

type Filter = 'all' | 'live' | 'paused' | 'finished' | 'failed' | 'interrupted' | 'ready'
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'live', label: 'Live' },
  { id: 'paused', label: 'Paused' },
  { id: 'finished', label: 'Finished' },
  { id: 'failed', label: 'Failed' },
  { id: 'interrupted', label: 'Interrupted' },
  { id: 'ready', label: 'Not started' },
]
const LIVE = ['active', 'starting', 'stopping']
const DONE = ['finished', 'completed']

function matches(task: Task, f: Filter): boolean {
  if (f === 'all') return true
  if (f === 'live') return LIVE.includes(task.status ?? '')
  if (f === 'paused') return task.status === 'needs_human'
  if (f === 'failed') return task.status === 'failed'
  if (f === 'interrupted') return task.status === 'orphaned'
  if (f === 'finished') return DONE.includes(task.status ?? '') || (!!task.output_artifact_id && !['failed', 'orphaned', 'needs_human', ...LIVE].includes(task.status ?? ''))
  return !task.status || task.status === 'created'
}

const BRANCH_ICON = (
  <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <circle cx="4" cy="3.5" r="1.6" /><circle cx="4" cy="12.5" r="1.6" /><circle cx="12" cy="6" r="1.6" />
    <path d="M4 5.1v5.8M12 7.6c0 2.4-3 2.4-7 3.6" />
  </svg>
)
const isChangeReady = (t: Task) => !!t.output_artifact_id && !!t.workspace

function actionsFor(task: Task, onArchive: (t: Task) => void, onDelete: (t: Task, whole: boolean) => void) {
  const status = task.status ?? ''
  const live = ['active', 'starting', 'stopping'].includes(status)
  const paused = status === 'needs_human'
  const done = ['finished', 'completed'].includes(status)
  const retry = status === 'failed' || status === 'orphaned'
  const room = task.last_session_id ? `/room/${task.last_session_id}` : null
  return (
    <>
      {room && (
        <Link className={`btn ${retry ? 'ghost' : 'primary'}`} to={room}>
          {paused ? 'Resume' : live ? 'Open room' : done ? 'View result' : retry ? 'View session' : 'Open room'}
        </Link>
      )}
      <Link className={`btn ${!room || retry ? 'primary' : 'ghost'}`} to={`/setup/${task.id}`}>
        {!room ? 'Start session' : retry ? 'Retry' : 'New session'}
      </Link>
      {!['active', 'starting', 'stopping'].includes(task.status ?? '') && (
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
          <summary className="btn ghost sm kebab" aria-label={`More actions for ${task.title.trim() || 'untitled task'}`}>
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="currentColor"><circle cx="8" cy="3" r="1.5" /><circle cx="8" cy="8" r="1.5" /><circle cx="8" cy="13" r="1.5" /></svg>
          </summary>
          <div className="row-menu-list" role="group" aria-label="Task actions">
            {task.last_session_id && <button type="button" onClick={() => onArchive(task)}>Archive</button>}
            {task.last_session_id && <button type="button" className="danger" onClick={() => onDelete(task, false)}>Delete session</button>}
            <button type="button" className="danger" onClick={() => onDelete(task, true)}>Delete task</button>
          </div>
        </details>
      )}
    </>
  )
}

const SAMPLE_TASK: TaskCreateRequest = {
  title: 'Should we ship weekly or every two weeks?',
  brief: 'Our team ships every two weeks. Some people want weekly releases for faster feedback; others worry about review time, release notes and support load. Decide on a cadence and say what would change your mind.',
  output_type: 'decision',
}

export function TaskBoard() {
  useDocumentTitle('Tasks')
  const navigate = useNavigate()
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const [toDelete, setToDelete] = useState<{ task: Task; whole: boolean } | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [deepQuery, setDeepQuery] = useState('')
  const [deepHits, setDeepHits] = useState<SearchHit[] | null>(null)
  const [deepError, setDeepError] = useState<string | null>(null)
  const [deepBusy, setDeepBusy] = useState(false)

  async function runDeepSearch(e: React.FormEvent) {
    e.preventDefault()
    const q = deepQuery.trim()
    if (q.length < 2) { setDeepHits(null); setDeepError('Type at least 2 characters to search.'); return }
    setDeepBusy(true); setDeepError(null)
    try { setDeepHits(await searchTranscripts(q)) }
    catch (err) { setDeepHits(null); setDeepError(err instanceof Error ? err.message : String(err)) }
    finally { setDeepBusy(false) }
  }

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

  async function remove(task: Task, whole: boolean) {
    setToDelete(null)
    setActionError(null)
    try {
      if (!whole && task.last_session_id) await deleteSession(task.last_session_id)
      else await deleteTask(task.id)
      refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    }
  }

  async function trySample() {
    setActionError(null)
    try {
      const task = await createTask(SAMPLE_TASK)
      navigate(`/setup/${task.id}?mode=debate`)
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
  // Tasks with a change waiting for review come first; otherwise the server's order is kept.
  const visible = (tasks ?? [])
    .filter((t) => matches(t, filter) && (!q || `${t.title} ${t.brief ?? ''}`.toLowerCase().includes(q)))
    .map((t, i) => ({ t, i }))
    .sort((a, b) => Number(isChangeReady(b.t)) - Number(isChangeReady(a.t)) || a.i - b.i)
    .map(({ t }) => t)
  const filterLabel = FILTERS.find((f) => f.id === filter)?.label ?? ''

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
          {isApiDown(error) && <> <Link to="/services">Open Services &amp; logs</Link></>}
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
          <div className="empty-actions">
            <button type="button" className="btn primary" onClick={() => setFormOpen(true)}>Create your first task</button>
            <button type="button" className="btn" onClick={trySample}>Try a sample discussion</button>
          </div>
          <p className="hint">The sample sets up a real task about release cadence. You choose the agents and start it, so you see the whole flow.</p>
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
            <input type="search" className="input task-search" aria-label="Filter by title" placeholder="Filter by title" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <form className="deep-search" role="search" onSubmit={runDeepSearch}>
            <input type="search" className="input" aria-label="Search past discussions" placeholder="Search what was said in past discussions"
              value={deepQuery} maxLength={200} onChange={(e) => setDeepQuery(e.target.value)} />
            <button type="submit" className="btn" disabled={deepBusy}>{deepBusy ? 'Searching' : 'Search'}</button>
          </form>
          {deepError && <p role="alert" className="alert bad">{deepError}</p>}
          {deepHits && (
            <section className="deep-results" aria-label="Search results">
              {deepHits.length === 0 ? <p className="empty-note">No past discussion matches that.</p> : (
                <ul>
                  {deepHits.map((h) => (
                    <li key={`${h.session_id}-${h.message_id ?? 'synthesis'}`}>
                      <Link to={`/room/${h.session_id}`}>{h.task_title.trim() || '(untitled task)'}</Link>
                      <span className="deep-who"> {h.speaker === 'synthesis' ? 'Synthesis' : h.speaker}{h.created_at ? `, ${new Date(h.created_at).toLocaleDateString()}` : ''}</span>
                      <p className="deep-snippet">{snippetParts(h.snippet).map((part, i) => part.match ? <mark key={i}>{part.text}</mark> : <span key={i}>{part.text}</span>)}</p>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}
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
                  {task.output_artifact_id && (isChangeReady(task)
                    ? <span className="chip change">{BRANCH_ICON}Change ready</span>
                    : <span className="chip accent">Synthesis ready</span>)}
                </div>
                <div className="task-actions">{actionsFor(task, archive, (t, whole) => setToDelete({ task: t, whole }))}</div>
              </li>
            ))}
          </ul>
          {visible.length === 0 && (
            <div className="empty-note" role="status">
              <p>
                {q ? <>No tasks match “{query.trim()}”{filter !== 'all' ? <> under {filterLabel}</> : null}.</> : <>No tasks are {filterLabel.toLowerCase()}.</>}
                {q && ' This box only filters task titles; to search what was said, use the box below.'}
              </p>
              <div className="empty-actions">
                {q && <button type="button" className="btn" onClick={() => setQuery('')}>Clear search</button>}
                {filter !== 'all' && <button type="button" className="btn" onClick={() => setFilter('all')}>Show all tasks</button>}
              </div>
            </div>
          )}
        </>
      )}

      {toDelete && (
        <ConfirmDialog
          title={toDelete.whole ? 'Delete this task?' : 'Delete this session?'}
          message={!toDelete.whole
            ? 'Its conversation, results and cost record are removed for good. The task stays. To keep the record but stop it from shaping later sessions, archive it instead.'
            : toDelete.task.last_session_id
              ? 'The task, all of its sessions, their conversations and cost records, and anything attached are removed for good.'
              : 'The task and anything attached to it are removed for good. It has not run yet, so there is no conversation to lose.'}
          confirmLabel={toDelete.whole ? 'Delete task' : 'Delete session'}
          cancelLabel="Keep it"
          danger
          onCancel={() => setToDelete(null)}
          onConfirm={() => remove(toDelete.task, toDelete.whole)}
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
