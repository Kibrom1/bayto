import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  getAgentTemplates,
  getModes,
  getSessionArtifacts,
  getSessionDetail,
  interjectSession,
  listTasks,
  pauseSession,
  resumeSession,
  stopSession,
  type ArtifactOut,
  type SessionDetailOut,
} from '../api/sessions'
import { useSessionEvents } from '../hooks/useSessionEvents'
import { Synthesis } from './Synthesis'
import './BaytoRoom.css'

interface AgentInfo {
  name: string
  stance: string | null
}

const fmt = (n: number) => n.toLocaleString('en-US')
const initials = (name: string) => name.split(/[\s_-]+/).map((w) => w[0]?.toUpperCase() ?? '').slice(0, 2).join('')
const titleCase = (s: string) => s.replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

function hueFor(key: string): number {
  let h = 0
  for (const c of key) h = (h * 31 + c.charCodeAt(0)) % 360
  return h
}

const PAUSED = ['needs_human', 'paused']
const FINISHED = ['finished', 'completed', 'stopped', 'failed', 'cancelled', 'ended']

export function BaytoRoom() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const { messages, rollingSummary, live, connected } = useSessionEvents(sessionId || '')
  const [session, setSession] = useState<SessionDetailOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [taskTitle, setTaskTitle] = useState<string | null>(null)
  const [modeName, setModeName] = useState<string | null>(null)
  const [agents, setAgents] = useState<Record<string, AgentInfo>>({})
  const [draft, setDraft] = useState('')
  const [target, setTarget] = useState('*')
  const [sending, setSending] = useState(false)
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)
  const [confirmStop, setConfirmStop] = useState(false)
  const [artifacts, setArtifacts] = useState<ArtifactOut[]>([])
  const [atBottom, setAtBottom] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)

  const loadSession = useCallback(() => {
    if (!sessionId) return
    getSessionDetail(sessionId)
      .then((res) => {
        setSession(res.session)
        setError(null)
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
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

  function onScroll() {
    const el = scrollRef.current
    if (el) setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
  }

  function flash(text: string, isError = false) {
    setNotice({ text, error: isError })
    if (!isError) setTimeout(() => setNotice(null), 4000)
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
    if (!body || sending || !sessionId) return
    setSending(true)
    try {
      await interjectSession(sessionId, body, target === '*' ? undefined : [target])
      setDraft('')
    } catch (err) {
      flash(err instanceof Error ? err.message : 'Could not send', true)
    } finally {
      setSending(false)
    }
  }

  const roster = session?.turn_counts ?? []
  const hue = useMemo(() => (role: string) => hueFor(role), [])

  if (error && !session) {
    return (
      <div className="room-error" role="alert">
        Couldn't load this session: {error}. <Link to="/">Back to tasks</Link>
      </div>
    )
  }
  if (!session) return <p className="room-error" style={{ color: 'var(--ink-3)' }}>Loading session…</p>

  const status = session.status
  const paused = PAUSED.includes(status)
  const finished = FINISHED.includes(status)
  const tokens = session.usage.tokens_in + session.usage.tokens_out
  const budgetTokens: number | null = session.budget?.tokens ?? session.budget?.max_tokens ?? null
  const pct = budgetTokens ? Math.min(100, Math.round((tokens / budgetTokens) * 100)) : null
  const liveEntries = Object.entries(live)

  return (
    <section className="room" aria-label="Bayto room">
      <header className="room-header">
        <div>
          <p className="room-kicker">Bayto room</p>
          <h1 className="room-title">{taskTitle ?? 'Discussion'}</h1>
          <div className="room-meta">
            <span className={`chip status-${status}`}><span className="dot" />{paused ? 'Paused' : titleCase(status)}</span>
            {modeName && <span className="chip">{titleCase(modeName)}</span>}
            <span className="chip">Round {session.round}</span>
            <span className="chip" title={connected ? 'Live updates connected' : 'Reconnecting…'}>
              {connected ? 'Live' : 'Offline'}
            </span>
          </div>
        </div>
        <div className="room-actions">
          <Link className="btn" to="/">Tasks</Link>
          {paused ? (
            <button type="button" className="btn primary" onClick={() => run(() => resumeSession(sessionId!), 'Session resumed')}>Resume</button>
          ) : (
            <button type="button" className="btn" disabled={finished} onClick={() => run(() => pauseSession(sessionId!), 'Session paused')}>Pause</button>
          )}
          <button type="button" className="btn danger" disabled={finished} onClick={() => setConfirmStop(true)}>End &amp; synthesize</button>
        </div>
      </header>

      {confirmStop && (
        <div className="confirm-bar" role="alertdialog" aria-label="Confirm end session">
          <span>End the session and write the final synthesis? This can't be undone.</span>
          <button type="button" className="btn danger" onClick={() => { setConfirmStop(false); run(() => stopSession(sessionId!), 'Session ending…') }}>End session</button>
          <button type="button" className="btn" onClick={() => setConfirmStop(false)}>Keep going</button>
        </div>
      )}
      {notice && <div className={`notice${notice.error ? ' error' : ''}`} role={notice.error ? 'alert' : 'status'}>{notice.text}</div>}

      <div className="room-body">
        <aside className="panel side-left" aria-label="Roster panel">
          <h2>Roster</h2>
          {roster.map((tc) => {
            const role = tc.participant ?? 'unknown'
            const info = display(role)
            return (
              <div className="roster-item" key={tc.agent_id} style={{ ['--agent-h' as string]: hue(role) }}>
                <span className="avatar" aria-hidden="true">{initials(info.name)}</span>
                <div>
                  <div className="roster-name">{info.name}</div>
                  <div className="roster-sub">{info.stance ? `${info.stance} · ` : ''}{tc.turns} {tc.turns === 1 ? 'turn' : 'turns'}</div>
                </div>
                {role in live && <span className="speaking" role="img" aria-label="Speaking now" />}
              </div>
            )
          })}
        </aside>

        <main className="transcript">
          <div className="transcript-scroll" ref={scrollRef} onScroll={onScroll}>
            <h2 className="sr-only">Transcript</h2>
            <Synthesis title={taskTitle ?? 'Discussion'} artifacts={artifacts} />
            {status === 'stopping' && artifacts.length === 0 && <p className="empty">Writing the final synthesis…</p>}
            <div className="transcript-inner" role="log" aria-live="polite" aria-label="Transcript">
              {messages.length === 0 && liveEntries.length === 0 && (
                <p className="empty">Waiting for the first seat to speak…</p>
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
                      <div className="msg-body">{m.body}</div>
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
          {!atBottom && (
            <button type="button" className="btn jump" onClick={() => { endRef.current?.scrollIntoView({ block: 'end' }); setAtBottom(true) }}>Jump to latest</button>
          )}

          <form className="composer" onSubmit={(e) => { e.preventDefault(); send() }}>
            <div className="composer-row">
              <select aria-label="Send to" value={target} onChange={(e) => setTarget(e.target.value)}>
                <option value="*">Everyone</option>
                {roster.map((tc) => (
                  <option key={tc.agent_id} value={tc.participant ?? ''}>{display(tc.participant ?? 'unknown').name}</option>
                ))}
              </select>
              <textarea
                aria-label="Interject"
                rows={1}
                placeholder="Add your view or steer the discussion…"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
                }}
              />
              <button type="submit" className="btn primary" disabled={!draft.trim() || sending || finished}>Send</button>
            </div>
            <p className="composer-hint">Enter to send, Shift+Enter for a new line</p>
          </form>
        </main>

        <aside className="panel side-right" aria-label="Moderator panel">
          <h2>Moderator summary</h2>
          <p className="summary">{rollingSummary || 'The summary appears after the first turns.'}</p>
          <h2>Usage vs budget</h2>
          {pct !== null && (
            <div className="meter" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Token budget used">
              <div className={`meter-fill${pct >= 100 ? ' over' : pct >= 80 ? ' warn' : ''}`} style={{ width: `${pct}%` }} />
            </div>
          )}
          <div className="usage-line"><span>Tokens</span><strong>{fmt(tokens)}{budgetTokens ? ` / ${fmt(budgetTokens)}` : ''}</strong></div>
          <div className="usage-line"><span>Cost</span><strong>${session.usage.cost.toFixed(4)}</strong></div>
        </aside>
      </div>
    </section>
  )
}
