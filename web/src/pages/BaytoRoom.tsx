import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ConfirmDialog } from '../components/Dialog'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  getAgentTemplates,
  getModes,
  getSessionArtifacts,
  getSessionDetail,
  exportSession,
  interjectSession,
  listTasks,
  archiveSession,
  deleteSession,
  pauseSession,
  startSession,
  continueSession,
  resumeSession,
  stopSession,
  type ArtifactOut,
  type SessionDetailOut,
} from '../api/sessions'
import { ApiError, isNotFound } from '../api/errors'
import { useSessionEvents } from '../hooks/useSessionEvents'
import { isConversational } from './messageKinds'
import { ChangeSet } from './ChangeSet'
import { RoomRail } from '../components/RoomRail'
import { ReadNote } from '../components/ReadNote'
import { Synthesis, synthesisMarkdown } from './Synthesis'
import { agentHue, initials } from '../agentColor'
import { getModels, type ModelCatalog } from '../api/models'
import './BaytoRoom.css'

interface AgentInfo {
  name: string
  stance: string | null
}

const STOP_REASONS: Record<string, string> = {
  max_rounds: 'it reached its round limit',
  staleness: 'the agents stopped adding new arguments',
  converged: 'the agents converged on an answer',
  user_stop: 'it was stopped from the room',
  budget_exceeded: 'it reached its budget',
  build_complete: 'the tests passed and the reviewer approved the change',
}
export const stopReasonText = (r?: string | null) => (r ? STOP_REASONS[r] ?? r.replace(/[-_]+/g, ' ') : null)
const titleCase = (s: string) => s.replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

// Raw sandbox output is for the Details toggle; the banner says what happened in one plain line.
export function friendlyFailure(reason?: string | null): string {
  if (!reason) return 'It stopped before it could start.'
  if (/sbx env create|SandboxProviderError/i.test(reason)) return "The sandbox for the agents couldn't be created."
  if (/budget|cost cap/i.test(reason)) return 'It reached its budget.'
  if (/api[ _-]?key|unauthori[sz]ed|\b401\b|authentication/i.test(reason)) return 'The model provider rejected the API key.'
  const first = reason.split('\n').map((l) => l.trim()).find(Boolean) ?? ''
  return first ? `It stopped with an error: ${first.length > 140 ? `${first.slice(0, 137)}…` : first}` : 'Something went wrong while it was running.'
}

// The sentence for the banner that says how a session ended. Only a limit that cut work short is a caution.
export function endedBanner(reason?: string | null): { text: string; tone: '' | 'ok' | 'warn' } | null {
  const why = stopReasonText(reason)
  if (!why) return null
  if (reason === 'build_complete') return { text: 'The build finished: tests passed and the reviewer approved.', tone: 'ok' }
  return { text: `Session ended because ${why}.`, tone: reason === 'budget_exceeded' ? 'warn' : '' }
}

const PAUSED = ['needs_human', 'paused']
// Agents end Build replies with a "VERDICT: x" protocol line. People get it as a small tag, not as text.
export function splitVerdict(body: string): { text: string; verdict: string | null } {
  const m = body.match(/\n*^[ \t]*VERDICT:[ \t]*([^\n]+?)[ \t]*$/im)
  if (!m) return { text: body, verdict: null }
  return { text: body.replace(m[0], '').trimEnd(), verdict: m[1] }
}

const FINISHED = ['finished', 'completed', 'stopped', 'failed', 'cancelled', 'ended', 'orphaned']

export function BaytoRoom() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const [session, setSession] = useState<SessionDetailOut | null>(null)
  const { messages: allMessages, rollingSummary, live, connected } = useSessionEvents(sessionId || '', session?.status)
  const messages = useMemo(() => allMessages.filter(isConversational), [allMessages])
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [taskTitle, setTaskTitle] = useState<string | null>(null)
  const [modeName, setModeName] = useState<string | null>(null)
  const [agents, setAgents] = useState<Record<string, AgentInfo>>({})
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null) // model names for the roster; raw ids show without it
  useEffect(() => { getModels().then(setCatalog).catch(() => {}) }, [])
  const [draft, setDraft] = useState('')
  const [target, setTarget] = useState('*')
  const [sending, setSending] = useState(false)
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)
  const [confirmStop, setConfirmStop] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const navigate = useNavigate()
  const [artifacts, setArtifacts] = useState<ArtifactOut[]>([])
  const [atBottom, setAtBottom] = useState(true)
  const [seen, setSeen] = useState(0) // turns on screen the last time the reader was at the bottom
  const [railOpen, setRailOpen] = useState(false) // the "Team & context" panel on narrow screens (the rail consumes it)
  const closeRail = useCallback(() => setRailOpen(false), [])
  const [exportOpen, setExportOpen] = useState(false)
  const exportRef = useRef<HTMLDivElement>(null)
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const scrollRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)

  const loadSession = useCallback(() => {
    if (!sessionId) return
    getSessionDetail(sessionId)
      .then((res) => {
        setSession(res.session)
        setError(null)
      })
      .catch((err: unknown) => {
        setNotFound(isNotFound(err))
        setError(err instanceof Error ? err.message : String(err))
      })
  }, [sessionId])

  useEffect(() => {
    loadSession()
    const t = setInterval(loadSession, 5000)
    return () => clearInterval(t)
  }, [loadSession])

  // Refresh counts and usage promptly when a turn finishes.
  useEffect(() => {
    if (messages.length) loadSession()
  }, [messages.length, loadSession])

  // The synthesis is written when the session stops; keep checking until it shows up.
  const sessionStatus = session?.status
  useEffect(() => {
    if (!sessionId || !sessionStatus) return
    if (![...FINISHED, 'stopping'].includes(sessionStatus)) return
    let cancelled = false
    const load = () => getSessionArtifacts(sessionId).then((a) => { if (!cancelled) setArtifacts(a) }).catch(() => {})
    load()
    const t = setInterval(load, 4000)
    return () => { cancelled = true; clearInterval(t) }
  }, [sessionId, sessionStatus])

  // Names, stances, task title and mode are enrichments: the room works without them.
  useEffect(() => {
    if (!session) return
    const taskId = session.task_id
    const modeId = session.mode_id
    listTasks().then((ts) => {
      if (Array.isArray(ts)) setTaskTitle(ts.find((t) => t.id === taskId)?.title ?? null)
    }).catch(() => {})
    getModes().then((ms) => {
      if (Array.isArray(ms)) setModeName(ms.find((m) => m.id === modeId)?.name ?? null)
    }).catch(() => {})
    getAgentTemplates().then((as) => {
      if (!Array.isArray(as)) return
      setAgents(Object.fromEntries(as.map((a) => [a.role, { name: a.name, stance: a.stance ?? null }])))
    }).catch(() => {})
  }, [session?.task_id, session?.mode_id]) // eslint-disable-line react-hooks/exhaustive-deps

  const display = (role: string): AgentInfo => agents[role] ?? { name: titleCase(role), stance: null }

  const liveKey = Object.entries(live).map(([k, v]) => `${k}:${v.text.length}:${v.tool ?? ''}`).join('|')
  useEffect(() => {
    if (atBottom) endRef.current?.scrollIntoView({ block: 'end' })
  }, [messages.length, liveKey, atBottom])

  // A live room follows the conversation. Once the session has ended and left a result (synthesis or
  // change set), the result is what the person came for, so open at it instead of at the last turn.
  const resultShown = useRef(false)
  const hasResult = artifacts.some((a) => a.type === 'synthesis' || a.type === 'change_set')
  useEffect(() => {
    if (!sessionStatus || !FINISHED.includes(sessionStatus) || !hasResult || resultShown.current) return
    resultShown.current = true
    scrollRef.current?.scrollTo({ top: 0 })
    setAtBottom(false)
  }, [sessionStatus, hasResult])

  useEffect(() => { if (atBottom) setSeen(messages.length) }, [atBottom, messages.length])

  useEffect(() => {
    if (!exportOpen) return
    const close = (e: MouseEvent) => { if (!exportRef.current?.contains(e.target as Node)) setExportOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [exportOpen])

  function onScroll() {
    const el = scrollRef.current
    if (el) setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
  }

  function flash(text: string, isError = false) {
    clearTimeout(noticeTimer.current)
    setNotice({ text, error: isError })
    if (!isError) noticeTimer.current = setTimeout(() => setNotice(null), 4000)
  }

  async function copyMarkdown() {
    try {
      await navigator.clipboard.writeText(synthesisMarkdown(taskTitle ?? 'Discussion', artifacts) ?? '')
      flash('Markdown copied')
    } catch {
      flash('Could not copy. Export the decision record instead.', true)
    }
  }

  function downloadTranscript() {
    const title = taskTitle ?? 'Discussion'
    const lines = messages.map((m) => {
      const known = roster.some((tc) => tc.participant === m.from)
      const who = known ? display(m.from).name : titleCase(m.from)
      return `**${who}:** ${splitVerdict(m.body).text}\n`
    })
    const url = URL.createObjectURL(new Blob([`# ${title}: transcript\n\n${lines.join('\n')}`], { type: 'text/markdown' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `${title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'discussion'}-transcript.md`
    a.click()
    URL.revokeObjectURL(url)
    flash('Transcript downloaded')
  }

  async function continueWithSameTeam() {
    try {
      const made = await continueSession(sessionId!)
      await startSession(made.session.id)
      navigate(`/room/${made.session.id}`)
    } catch (err) {
      flash(err instanceof Error ? err.message : 'Could not continue the session', true)
    }
  }

  async function run(action: () => Promise<unknown>, ok: string) {
    try {
      await action()
      flash(ok)
      loadSession()
    } catch (err) {
      flash(err instanceof Error ? err.message : 'Something went wrong', true)
    }
  }

  async function send() {
    const body = draft.trim()
    if (!body || sending || !sessionId || !canType) return
    setSending(true)
    try {
      await interjectSession(sessionId, body, target === '*' ? undefined : [target])
      setDraft('')
    } catch (err) {
      flash(err instanceof ApiError && err.status === 409
        ? "This session isn't running, so your message wasn't delivered. If you restarted the orchestrator, resume the session or start a new one."
        : err instanceof Error ? err.message : 'Could not send', true)
    } finally {
      setSending(false)
    }
  }

  const roster = session?.turn_counts ?? []
  const hue = useMemo(() => (role: string) => agentHue(role), [])

  if (error && !session) {
    return (
      <div className="room-error" role="alert">
        {notFound
          ? <>This session doesn't exist, or the link is wrong. </>
          : <>Couldn't load this session: {error}. </>}
        <Link to="/">Back to tasks</Link>
      </div>
    )
  }
  if (!session) return <p className="room-error" style={{ color: 'var(--ink-3)' }}>Loading session…</p>

  const status = session.status
  const paused = PAUSED.includes(status)
  const finished = FINISHED.includes(status)
  // Only a running session has a runtime that can take a message (the server answers 409 otherwise).
  const canSend = status === 'active' || status === 'running'
  // Archive and Delete are for a session nothing is running in; the server refuses the others too.
  const removable = !['starting', 'active', 'running', 'stopping', 'cancelling'].includes(status)
  // A paused session still accepts a message: it waits in the log and the agents see it after Resume.
  const canType = canSend || paused
  const sendBlocked = finished ? 'ended' : paused ? 'paused' : canSend ? null : 'starting'
  const costCap: number | null = session.budget?.dollars ?? session.budget?.max_cost ?? null
  const liveEntries = Object.entries(live)
  const newTurns = Math.max(0, messages.length - seen)

  return (
    <section className="room" aria-label="Bayto room">
      <header className="room-header">
        <div className="room-id">
          <p className="room-kicker"><Link to="/">Tasks</Link><span aria-hidden="true"> / </span><span>{taskTitle ?? 'Discussion'}</span></p>
          <div className="room-titlerow">
            <h1 className="room-title">{taskTitle ?? 'Discussion'}</h1>
            <span className={`chip status-${status}`}><span className="dot" />{paused ? 'Paused' : status === 'orphaned' ? 'Interrupted' : titleCase(status)}</span>
          </div>
          <div className="room-meta">
            {modeName && <span>{titleCase(modeName)}</span>}
            <span>Round {session.round}</span>
            {canSend && !connected && <span className="live-off" role="status">Reconnecting…</span>}
            <span className="num">Spent ${session.usage.cost.toFixed(2)}{costCap ? ` of $${costCap.toFixed(2)} cap` : ''}</span>
          </div>
        </div>
        <div className="room-actions">
          {paused ? (
            <button type="button" className="btn primary" onClick={() => run(() => resumeSession(sessionId!), 'Session resumed')}>Resume</button>
          ) : status === 'created' ? (
            <button type="button" className="btn primary" onClick={() => run(() => startSession(sessionId!), 'Session starting')}>Start</button>
          ) : !finished && (
            <button type="button" className="btn" disabled={!canSend} onClick={() => run(() => pauseSession(sessionId!), 'Session paused')}>Pause</button>
          )}
          <div className="export-menu" ref={exportRef}>
            <button type="button" className="btn" aria-haspopup="menu" aria-expanded={exportOpen} onClick={() => setExportOpen((o) => !o)}>
              Export<span aria-hidden="true"> ▾</span>
            </button>
            {exportOpen && (
              <div className="menu" role="menu" aria-label="Export" onKeyDown={(e) => { if (e.key === 'Escape') setExportOpen(false) }}>
                <button type="button" role="menuitem" className="menu-item" onClick={() => { setExportOpen(false); run(() => exportSession(sessionId!), 'Decision record downloaded') }}>
                  Decision record (.md)
                </button>
                {messages.length > 0 && (
                  <button type="button" role="menuitem" className="menu-item" onClick={() => { setExportOpen(false); downloadTranscript() }}>
                    Full transcript (.md)
                  </button>
                )}
                {artifacts.some((a) => a.type === 'synthesis') && (
                  <button type="button" role="menuitem" className="menu-item" onClick={() => { setExportOpen(false); void copyMarkdown() }}>
                    Copy synthesis as Markdown
                  </button>
                )}
              </div>
            )}
          </div>
          <button type="button" className="btn rail-toggle" aria-expanded={railOpen} aria-controls="room-rail" onClick={() => setRailOpen((o) => !o)}>
            Team &amp; context
          </button>
          {status === 'finished' && modeName && modeName !== 'build' && (
            <Link className="btn accent" to={`/setup/${session.task_id}?mode=build`}>Continue as Build</Link>
          )}
          {!finished && <button type="button" className="btn danger" onClick={() => setConfirmStop(true)}>End &amp; synthesize</button>}
          {removable && (
            <>
              <button type="button" className="btn" onClick={() => run(() => archiveSession(sessionId!, !session.archived_at), session.archived_at ? 'Session restored' : 'Session archived')}>
                {session.archived_at ? 'Unarchive' : 'Archive'}
              </button>
              <button type="button" className="btn danger" onClick={() => setConfirmDelete(true)}>Delete</button>
            </>
          )}
        </div>
      </header>

      {confirmDelete && (
        <ConfirmDialog
          title="Delete this session?"
          message="Its conversation, results and cost record are removed for good. The task stays. To keep the record but stop it from shaping later sessions, archive it instead."
          confirmLabel="Delete session"
          cancelLabel="Keep it"
          danger
          onCancel={() => setConfirmDelete(false)}
          onConfirm={async () => {
            setConfirmDelete(false)
            try {
              await deleteSession(sessionId!)
              navigate('/')
            } catch (err) {
              flash(err instanceof Error ? err.message : 'Could not delete the session', true)
            }
          }}
        />
      )}
      {session.archived_at && (
        <div className="notice" role="status">
          Archived. It is hidden from the task board, and the agents no longer see it as context for new sessions.
          {session.status !== 'failed' && FINISHED.includes(session.status) && stopReasonText(session.stop_reason) && <> It ended because {stopReasonText(session.stop_reason)}.</>}
        </div>
      )}
      {confirmStop && (
        <ConfirmDialog
          title="End session and synthesize?"
          message="The moderator will stop the discussion and write the final synthesis. This can't be undone."
          confirmLabel="End session"
          cancelLabel="Keep going"
          danger
          onCancel={() => setConfirmStop(false)}
          onConfirm={() => { setConfirmStop(false); run(() => stopSession(sessionId!), 'Session ending…') }}
        />
      )}
      {session.status === 'failed' && (
        <div className="notice error" role="alert">
          <p className="notice-line"><strong>This session failed.</strong> {friendlyFailure(session.failure_reason)}
            {!session.failure_reason && ' Check the orchestrator log on the Services page for the "failed to start" line.'}</p>
          {modeName !== 'build' && (
            <p className="notice-actions"><button type="button" className="btn primary" onClick={continueWithSameTeam}>Retry with same team</button></p>
          )}
          {session.failure_reason && (
            <details className="notice-details">
              <summary>Technical details</summary>
              <pre>{session.failure_reason}</pre>
            </details>
          )}
        </div>
      )}
      {!session.archived_at && session.status !== 'failed' && FINISHED.includes(session.status) && (() => {
        const ended = endedBanner(session.stop_reason)
        return ended && <div className={`notice${ended.tone ? ` ${ended.tone}` : ''}`} role="status">{ended.text}</div>
      })()}
      {status === 'orphaned' && (
        <div className="notice warn" role="status">
          <p className="notice-line"><strong>This session was interrupted.</strong> The app restarted while it was running, so its team is gone. The conversation so far is kept below.
            {modeName !== 'build' && ' The new session is told what was said so far.'}</p>
          {modeName !== 'build' && (
            <p className="notice-actions"><button type="button" className="btn primary" onClick={continueWithSameTeam}>Continue with the same team</button></p>
          )}
        </div>
      )}
      {paused && (
        <div className="notice warn" role="status">Paused. If an agent asked you something, answer below, then press Resume.</div>
      )}
      {session.status === 'starting' && (
        <div className="notice" role="status">
          Starting: {session.stage ?? 'waiting to launch'}. Creating a sandbox can take a few minutes.
        </div>
      )}
      {notice && (
        <div className={`toast${notice.error ? ' error' : ''}`} role={notice.error ? 'alert' : 'status'}>
          <span>{notice.text}</span>
          {notice.error && <button type="button" className="btn sm" onClick={() => setNotice(null)}>Dismiss</button>}
        </div>
      )}

      <div className="room-body" data-rail-open={railOpen || undefined} onKeyDown={(e) => { if (e.key === 'Escape' && railOpen) closeRail() }}>
        <div className="transcript">
          <div className="transcript-scroll" ref={scrollRef} onScroll={onScroll} tabIndex={0} role="region" aria-label="Conversation">
            <h2 className="sr-only">Transcript</h2>
            <ChangeSet artifacts={artifacts} sessionId={sessionId} />
            <Synthesis title={taskTitle ?? 'Discussion'} artifacts={artifacts} />
            {status === 'stopping' && artifacts.length === 0 && <p className="empty">Writing the final synthesis…</p>}
            <div className="transcript-inner" role="log" aria-live="polite" aria-label="Transcript">
              {messages.length === 0 && liveEntries.length === 0 && (
                <p className="empty">{finished ? 'No turns were recorded.' : 'Waiting for the first seat to speak…'}</p>
              )}
              {messages.map((m, i) => {
                const known = roster.some((tc) => tc.participant === m.from)
                const info = display(m.from)
                return (
                  <article className={`msg${known ? '' : ' system'}`} key={i} style={{ ['--agent-h' as string]: hue(m.from) }}>
                    <span className="avatar" aria-hidden="true">{initials(known ? info.name : titleCase(m.from))}</span>
                    <div className="msg-main">
                      <div className="msg-head">
                        <span className="msg-name">{known ? info.name : titleCase(m.from)}</span>
                        {known && info.stance && <span className="msg-stance">{info.stance}</span>}
                      </div>
                      <div className="msg-body">{splitVerdict(m.body).text}</div>
                      {splitVerdict(m.body).verdict && <span className="chip verdict">Verdict: {splitVerdict(m.body).verdict}</span>}
                      {known && <ReadNote meta={m.meta} />}
                    </div>
                  </article>
                )
              })}
              {liveEntries.map(([role, turn]) => (
                <article className="msg live" key={`live-${role}`} style={{ ['--agent-h' as string]: hue(role) }}>
                  <span className="avatar" aria-hidden="true">{initials(display(role).name)}</span>
                  <div className="msg-main">
                    <div className="msg-head"><span className="msg-name">{display(role).name}</span><span className="msg-stance">responding…</span></div>
                    <div className="msg-body">{turn.text}<span className="cursor" aria-hidden="true">▍</span></div>
                    {turn.tool && <div className="tool-line">Using {turn.tool}</div>}
                  </div>
                </article>
              ))}
              <div ref={endRef} />
            </div>
          </div>
          <div className="jump-wrap" role="status" aria-live="polite">
            {!atBottom && (
              <button type="button" className="btn primary jump" onClick={() => { endRef.current?.scrollIntoView({ block: 'end' }); setAtBottom(true) }}>
                {newTurns > 0 ? `${newTurns} new ${newTurns === 1 ? 'turn' : 'turns'}` : 'Jump to latest'}
              </button>
            )}
          </div>

          {sendBlocked === 'ended' ? (
            <div className="ended-bar">
              <span>Session ended.</span>
              <Link className="btn primary" to={`/setup/${session.task_id}`}>Start a new session</Link>
              <button type="button" className="btn" aria-label="Export decision record" onClick={() => run(() => exportSession(sessionId!), 'Decision record downloaded')}>Export</button>
            </div>
          ) : (
          <form className="composer" onSubmit={(e) => { e.preventDefault(); send() }}>
            <div className="composer-row">
              <select aria-label="Send to" disabled={!canType} value={target} onChange={(e) => setTarget(e.target.value)}>
                <option value="*">Everyone</option>
                {roster.map((tc) => (
                  <option key={tc.agent_id} value={tc.participant ?? ''}>{display(tc.participant ?? 'unknown').name}</option>
                ))}
              </select>
              <textarea
                aria-label="Interject"
                rows={1}
                disabled={!canType}
                placeholder={sendBlocked === 'paused' ? 'Answer or steer, then press Resume' : sendBlocked === 'starting' ? 'Waiting for the session to start…' : 'Add your view or steer the discussion…'}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
                }}
              />
              <button type="submit" className="btn primary" disabled={!draft.trim() || sending || !canType}>Send</button>
            </div>
            {sendBlocked === 'paused' ? (
              <p className="composer-hint">Paused. Your message is saved and the agents see it when you press Resume.</p>
            ) : sendBlocked === 'starting' ? (
              <p className="composer-hint">You can write once the session has started.</p>
            ) : (
              <p className="composer-hint">Enter to send, Shift+Enter for a new line</p>
            )}
          </form>
          )}
        </div>

        <RoomRail
          detail={session}
          sessionId={sessionId!}
          railOpen={railOpen}
          onRailClose={closeRail}
          display={display}
          hue={hue}
          live={live}
          catalog={catalog}
          summary={rollingSummary ? splitVerdict(rollingSummary).text : null}
          canEdit={canSend || paused}
          flash={flash}
          onChanged={loadSession}
        />
      </div>
    </section>
  )
}
