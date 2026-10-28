import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { controlAction, getControlState, getLog, getSessions, parseStartAll, sessionOps, type SessionRow, type ControlState, type LogName } from '../api/control'
import { ConfirmDialog } from '../components/Dialog'
import './Services.css'

type ServiceName = 'postgres' | 'observability' | 'orchestrator' | 'web'

const SERVICE_INFO: Record<ServiceName, { title: string; hint: string }> = {
  postgres: { title: 'Postgres', hint: 'Database, in Docker (container bayto-pg, port 5432)' },
  observability: { title: 'Observability', hint: 'Traces and the audit trail, in Docker (Jaeger, container bayto-jaeger, viewer on port 16686). Keeps its data between restarts. Start it before the orchestrator.' },
  orchestrator: { title: 'Orchestrator', hint: 'API and moderator, port 8000' },
  web: { title: 'Web app', hint: 'This page, port 5173' },
}

function tone(status: string): 'ok' | 'bad' | 'idle' | 'busy' {
  if (status === 'running' || status === 'active') return 'ok'
  if (status === 'starting' || status === 'stopping') return 'busy'
  if (status === 'failed' || status.startsWith('docker')) return 'bad'
  return 'idle'
}

function StatusPill({ status }: { status: string }) {
  return <span className={`pill pill-${tone(status)}`}><span className="pill-dot" />{status}</span>
}

const LOG_NAMES: LogName[] = ['orchestrator', 'web', 'postgres', 'observability']
const NOISE = /exporting (spans|metrics|logs) batch|Failed to export (spans|metrics|logs)|BadStatusLine/i
// uvicorn access lines for requests that worked (the page itself polls every few seconds, so these bury everything else)
const OK_REQUEST = /"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) [^"]*" [23]\d\d\b/
const LONG_LINE = 300
const logLevel = (line: string): 'err' | 'warn' | '' =>
  /\b(ERROR|CRITICAL|FATAL|Traceback|Exception)\b|command not found|exited with code [1-9]|failed/i.test(line) ? 'err' : /\bWARN(ING)?\b/.test(line) ? 'warn' : ''

/** The last few meaningful lines of a log: what a failed service row shows so the cause is next to the failure. */
export function logTail(log: string, n = 5): string[] {
  return log.split('\n').map((l) => l.trimEnd()).filter((l) => l && !NOISE.test(l) && !OK_REQUEST.test(l)).slice(-n)
}

function LogLine({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const long = text.length > LONG_LINE
  return (
    <span className={`log-line ${logLevel(text)}`}>
      {long && !open ? text.slice(0, LONG_LINE) : text}
      {long && (
        <button type="button" className="log-more" onClick={() => setOpen(!open)}>
          {open ? ' show less' : ` … show ${text.length - LONG_LINE} more characters`}
        </button>
      )}
      {'\n'}
    </span>
  )
}

export function Services() {
  useDocumentTitle('Services & logs')
  const [state, setState] = useState<ControlState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirmSbx, setConfirmSbx] = useState(false)
  const [results, setResults] = useState<Record<string, { text: string; bad: boolean }>>({})
  const [log, setLog] = useState('')
  const [copied, setCopied] = useState(false)
  const [logName, setLogName] = useState<LogName>('orchestrator')
  const [procLogs, setProcLogs] = useState<Record<string, string>>({})
  const [filter, setFilter] = useState('')
  const [hideNoise, setHideNoise] = useState(true)
  const [hideOk, setHideOk] = useState(true)
  const [follow, setFollow] = useState(true)
  const logRef = useRef<HTMLPreElement>(null)
  const logCard = useRef<HTMLElement>(null)
  const [sessions, setSessions] = useState<SessionRow[] | null>(null)
  const loadSessions = useCallback(async () => { try { setSessions(await getSessions()) } catch { /* panel down: the state poll reports it */ } }, [])
  const [sandboxName, setSandboxName] = useState('')
  const [sandboxOut, setSandboxOut] = useState('')
  const [summaryModel, setSummaryModel] = useState('')
  const [synthesisModel, setSynthesisModel] = useState('')
  const [streaming, setStreaming] = useState(true)
  const [apiKey, setApiKey] = useState('')
  const [backend, setBackend] = useState<'subscription' | 'api'>('subscription')
  const [seeded, setSeeded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const s = await getControlState()
      setState(s)
      setError(null)
      if (!seeded) {
        setSummaryModel(s.config.summary_model)
        setSynthesisModel(s.config.synthesis_model)
        setStreaming(s.config.streaming)
        setBackend(s.config.llm_backend)
        setSeeded(true)
      }
      const next = await getLog(logName)
      setLog((prev) => (prev === next ? prev : next))
      const [o, w] = await Promise.all([getLog('orchestrator'), getLog('web')])
      setProcLogs((prev) => (prev.orchestrator === o && prev.web === w ? prev : { orchestrator: o, web: w }))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [logName, seeded])

  async function copyLog() {
    try {
      await navigator.clipboard.writeText(log)
    } catch {
      // Clipboard API unavailable: select the text so Ctrl/Cmd+C works.
      const el = document.getElementById('svc-log')
      if (el) {
        const r = document.createRange()
        r.selectNodeContents(el)
        const sel = window.getSelection()
        sel?.removeAllRanges()
        sel?.addRange(r)
      }
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  useEffect(() => {
    // First load on the next tick (not synchronously in the effect), then poll.
    const first = setTimeout(refresh, 0)
    const t = setInterval(refresh, 3000)
    const firstSessions = setTimeout(loadSessions, 0)
    const ts = setInterval(loadSessions, 20000)
    return () => {
      clearTimeout(first)
      clearTimeout(firstSessions)
      clearInterval(t)
      clearInterval(ts)
    }
  }, [refresh, loadSessions])

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase()
    return log.split('\n').filter((l) => (!hideNoise || !NOISE.test(l)) && (!hideOk || !OK_REQUEST.test(l)) && (!q || l.toLowerCase().includes(q)))
  }, [log, filter, hideNoise, hideOk])

  useEffect(() => {
    if (follow && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [shown, follow])

  function openLog(name: LogName) {
    setLogName(name)
    logCard.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  async function act(key: string, body: Record<string, unknown>): Promise<string> {
    setBusy(key)
    try {
      const out = await controlAction(body)
      const bad = /not running|not installed|not found|failed|error|denied|unreachable|bad request|cannot|no such/i.test(out)
      setResults((r) => ({ ...r, [key]: { text: out, bad } }))
      return out
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e)
      setResults((r) => ({ ...r, [key]: { text, bad: true } }))
      return text
    } finally {
      setBusy(null)
      refresh()
    }
  }

  const down = state ? (Object.keys(state.services) as ServiceName[]).filter((n) => tone(state.services[n]) !== 'ok') : []
  const startAll = parseStartAll(results['all']?.text ?? '')

  const services = state ? (Object.keys(state.services) as ServiceName[]) : []
  const result = (key: string) => results[key]

  return (
    <div className="svc-theme">
    <div className="svc-page">
      <header className="svc-header">
        <div>
          <p className="svc-kicker">For developers</p>
          <h1>Services &amp; logs</h1>
          <p className="svc-lede">Start, stop and inspect the local pieces Bayto runs on: the orchestrator, the web app and the agent sandboxes. You only need this if you run Bayto yourself.</p>
        </div>
        <Link className="btn" to="/">← Tasks</Link>
      </header>

      {error && (
        <div className="svc-alert bad" role="alert">
          <strong>The control panel isn't running.</strong>
          <span>Bayto could not reach the local control script, so there is nothing to show yet. Start it with <code>python3 scripts/bayto-control.py</code>, then try again.</span>
          <details className="svc-details"><summary>Technical details</summary><code>{error}</code></details>
          <button type="button" className="btn sm" onClick={() => { void refresh() }}>Retry</button>
        </div>
      )}

      {state && (
        <>
          <section className={`svc-alert ${down.length ? 'bad' : 'ok'}`} aria-label="Stack health" role="status">
            <strong>{down.length === 0 ? 'Everything is running.' : `${down.length} of ${services.length} not running: ${down.map((n) => SERVICE_INFO[n].title).join(', ')}.`}</strong>
            {down.length > 0 && (
              <button type="button" className="btn primary sm" disabled={busy === 'all'}
                onClick={() => act('all', { action: 'start_all' })}>
                {busy === 'all' ? 'Starting… this can take a minute' : 'Start all'}
              </button>
            )}
            {startAll && (
              <ul className="svc-steps">
                {startAll.steps.map((st) => <li key={st.name} className={st.ok ? '' : 'bad'}>{st.ok ? '✓' : '✗'} {SERVICE_INFO[st.name as ServiceName]?.title ?? st.name}: {st.detail}</li>)}
              </ul>
            )}
            {startAll && !startAll.ok && startAll.log.length > 0 && <pre className="svc-console">{startAll.log.join('\n')}</pre>}
          </section>

          <section className="svc-card" aria-label="Dev stack">
            <h2>Dev stack</h2>
            {services.map((name) => {
              const status = state.services[name]
              const running = tone(status) === 'ok'
              const r = result(`svc:${name}`)
              const working = busy === `svc:${name}`
              return (
                <div className="svc-row" key={name}>
                  <div className="svc-row-main">
                    <div className="svc-row-title">{SERVICE_INFO[name].title}</div>
                    <div className="svc-row-hint">{SERVICE_INFO[name].hint}</div>
                    {name === 'observability' && running && (
                      <div className="svc-row-hint"><a href="http://localhost:16686" target="_blank" rel="noreferrer">Open the viewer</a> and search for the tag <code>bayto.audit=true</code> to see the audit trail.</div>
                    )}
                    {r && <div className={`svc-result${r.bad ? ' bad' : ''}`} role={r.bad ? 'alert' : 'status'}>{r.text}</div>}
                    {!running && !working && (name === 'orchestrator' || name === 'web') && logTail(procLogs[name] ?? '').length > 0 && (
                      <div className="svc-tail" aria-label={`Last log lines of ${SERVICE_INFO[name].title}`}>
                        <pre>{logTail(procLogs[name] ?? '').join('\n')}</pre>
                        <button type="button" className="btn sm" onClick={() => openLog(name)}>View full log</button>
                      </div>
                    )}
                    {!running && !working && (name === 'postgres' || name === 'observability') && (
                      <button type="button" className="btn sm" onClick={() => openLog(name)}>View container log</button>
                    )}
                  </div>
                  <StatusPill status={status} />
                  <div className="svc-row-actions">
                    {!running ? (
                      <button type="button" className="btn primary" disabled={working}
                        onClick={() => act(`svc:${name}`, { action: 'service', name, op: 'start' })}>
                        {working ? 'Working…' : 'Start'}
                      </button>
                    ) : name === 'web' ? (
                      <span className="svc-row-note">Stop it with Ctrl+C in the terminal running bayto-control.py</span>
                    ) : (
                      <>
                        <button type="button" className="btn" disabled={working}
                          onClick={() => act(`svc:${name}`, { action: 'service', name, op: 'restart' })}>
                          {working ? 'Working…' : 'Restart'}
                        </button>
                        <button type="button" className="btn" disabled={working}
                          onClick={() => act(`svc:${name}`, { action: 'service', name, op: 'stop' })}>
                          Stop
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )
            })}
          </section>

          <section className="svc-card" aria-label="Orchestrator settings">
            <h2>Orchestrator settings</h2>
            <p className="svc-note">Applied the next time the orchestrator starts. With the subscription option the moderator's summary and synthesis run through your Claude login (`claude` must be installed and signed in on this computer). An API key stays in the control script's memory and is never written to disk.</p>
            <div className="svc-form">
              <label>Summary model<input value={summaryModel} onChange={(e) => setSummaryModel(e.target.value)} /></label>
              <label>Synthesis model<input value={synthesisModel} onChange={(e) => setSynthesisModel(e.target.value)} /></label>
              <label>Moderator model access
                <select value={backend} onChange={(e) => setBackend(e.target.value as 'subscription' | 'api')}>
                  <option value="subscription">Claude subscription (no API key)</option>
                  <option value="api">Anthropic API key</option>
                </select>
              </label>
              {backend === 'api' && (
                <label>Anthropic API key
                  <input type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
                    placeholder={state.config.api_key ? 'Key is set. Type to replace.' : 'Not set'} />
                </label>
              )}
              <label className="svc-check"><input type="checkbox" checked={streaming} onChange={(e) => setStreaming(e.target.checked)} />Stream turns live</label>
            </div>
            <div className="svc-actions">
              <button type="button" className="btn primary" disabled={busy === 'cfg'}
                onClick={async () => { await act('cfg', { action: 'config', summary_model: summaryModel, synthesis_model: synthesisModel, streaming, llm_backend: backend, api_key: apiKey }); setApiKey('') }}>
                Save settings
              </button>
              {result('cfg') && <span className="svc-result">{result('cfg').text}</span>}
            </div>
          </section>

          <section className="svc-card" aria-label="Sessions">
            <h2>Sessions</h2>
            {sessions === null && <p className="svc-note">Loading sessions…</p>}
            {sessions?.length === 0 && <p className="svc-note">No sessions yet. Create one from a task.</p>}
            {sessions?.map((s) => (
              <div className="svc-row" key={s.id}>
                <div className="svc-row-main">
                  <div className="svc-row-title">{s.task}</div>
                  <div className="svc-row-hint"><Link to={`/room/${s.id}`}>Open room</Link>{' · '}{s.id.slice(0, 8)}</div>
                </div>
                <StatusPill status={s.status} />
                <div className="svc-row-actions">
                  {sessionOps(s.status).map((op) => (
                    <button key={op} type="button" className="btn" disabled={busy === `ses:${s.id}:${op}`}
                      onClick={async () => { await act(`ses:${s.id}:${op}`, { action: 'session', id: s.id, op }); loadSessions() }}>
                      {op[0].toUpperCase() + op.slice(1)}
                    </button>
                  ))}
                </div>
              </div>
            ))}
            {Object.entries(results).filter(([k]) => k.startsWith('ses:')).slice(-1).map(([k, r]) => (
              <div key={k} className={`svc-result${r.bad ? ' bad' : ''}`}>{r.text}</div>
            ))}
          </section>

          <section className="svc-card" aria-label="Sandboxes">
            <h2>Sandboxes</h2>
            <div className="svc-actions">
              <button type="button" className="btn" disabled={busy === 'sbx:ls'}
                onClick={async () => setSandboxOut(await act('sbx:ls', { action: 'sbx', op: 'ls' }))}>List sandboxes</button>
              <input className="svc-input" value={sandboxName} onChange={(e) => setSandboxName(e.target.value)} placeholder="Sandbox name" aria-label="Sandbox name" />
              <button type="button" className="btn danger" disabled={!sandboxName.trim() || busy === 'sbx:stop'}
                onClick={() => setConfirmSbx(true)}>
                Stop sandbox
              </button>
            </div>
            {confirmSbx && (
              <ConfirmDialog
                title="Stop sandbox?"
                message={`Sandbox ${sandboxName.trim()} will be stopped. Any running session using it will fail.`}
                confirmLabel="Stop sandbox"
                danger
                onCancel={() => setConfirmSbx(false)}
                onConfirm={async () => { setConfirmSbx(false); setSandboxOut(await act('sbx:stop', { action: 'sbx', op: 'stop', name: sandboxName.trim() })) }}
              />
            )}
            {sandboxOut && <pre className="svc-console">{sandboxOut}</pre>}
          </section>

          <section className="svc-card" aria-label="Logs" ref={logCard}>
            <div className="svc-card-head">
              <h2>Logs</h2>
              <div className="svc-tabs" role="tablist">
                {LOG_NAMES.map((n) => (
                  <button key={n} type="button" role="tab" aria-selected={logName === n}
                    className={`svc-tab${logName === n ? ' on' : ''}`} onClick={() => setLogName(n)}>{SERVICE_INFO[n].title}</button>
                ))}
              </div>
              <button type="button" className="btn sm" disabled={!log} onClick={copyLog}>{copied ? 'Copied' : 'Copy log'}</button>
            </div>
            <div className="svc-logbar">
              <input className="svc-input" type="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter lines" aria-label="Filter log lines" />
              <label className="svc-check"><input type="checkbox" checked={hideOk} onChange={(e) => setHideOk(e.target.checked)} />Hide successful requests</label>
              <label className="svc-check"><input type="checkbox" checked={hideNoise} onChange={(e) => setHideNoise(e.target.checked)} />Hide telemetry noise</label>
              <label className="svc-check"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />Follow new output</label>
            </div>
            <pre id="svc-log" ref={logRef} className="svc-console" tabIndex={0}>
              {shown.length === 0 || (shown.length === 1 && !shown[0])
                ? (log ? 'No lines match.' : 'No output yet.')
                : shown.map((l, i) => <LogLine key={i} text={l} />)}
            </pre>
          </section>
        </>
      )}
    </div>
    </div>
  )
}
