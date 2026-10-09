import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { controlAction, getControlState, getLog, type ControlState } from '../api/control'
import { ConfirmDialog } from '../components/Dialog'
import './Services.css'

type ServiceName = 'postgres' | 'orchestrator' | 'web'

const SERVICE_INFO: Record<ServiceName, { title: string; hint: string }> = {
  postgres: { title: 'Postgres', hint: 'Database, in Docker (container bayto-pg, port 5432)' },
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

export function Services() {
  const [state, setState] = useState<ControlState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirmSbx, setConfirmSbx] = useState(false)
  const [results, setResults] = useState<Record<string, { text: string; bad: boolean }>>({})
  const [log, setLog] = useState('')
  const [logName, setLogName] = useState<'orchestrator' | 'web'>('orchestrator')
  const [sessionId, setSessionId] = useState('')
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
      setLog(await getLog(logName))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [logName, seeded])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [refresh])

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

  const services = state ? (Object.keys(state.services) as ServiceName[]) : []
  const result = (key: string) => results[key]

  return (
    <div className="svc-page">
      <header className="svc-header">
        <div>
          <p className="svc-kicker">Bayto</p>
          <h1>Services</h1>
        </div>
        <Link className="btn" to="/">← Tasks</Link>
      </header>

      {error && (
        <div className="svc-alert bad" role="alert">
          <strong>The control script isn't reachable.</strong>
          <span>{error}</span>
        </div>
      )}

      {state && (
        <>
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
                    {r && <div className={`svc-result${r.bad ? ' bad' : ''}`} role={r.bad ? 'alert' : 'status'}>{r.text}</div>}
                  </div>
                  <StatusPill status={status} />
                  <div className="svc-row-actions">
                    <button type="button" className="btn primary" disabled={running || working}
                      onClick={() => act(`svc:${name}`, { action: 'service', name, op: 'start' })}>
                      {working ? 'Working…' : 'Start'}
                    </button>
                    <button type="button" className="btn" disabled={!running || working || name === 'web'}
                      title={name === 'web' ? 'Stop it with Ctrl+C in the terminal running bayto-control.py' : undefined}
                      onClick={() => act(`svc:${name}`, { action: 'service', name, op: 'stop' })}>
                      Stop
                    </button>
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
            {state.sessions.length === 0 && <p className="svc-note">No sessions yet. Create one from a task.</p>}
            {state.sessions.map((s) => (
              <div className="svc-row" key={s.id}>
                <div className="svc-row-main">
                  <div className="svc-row-title">{s.task}</div>
                  <div className="svc-row-hint">
                    <button type="button" className="svc-link" onClick={() => setSessionId(s.id)}>{s.id.slice(0, 8)}</button>
                    {' · '}<Link to={`/room/${s.id}`}>Open room</Link>
                  </div>
                </div>
                <StatusPill status={s.status} />
              </div>
            ))}
            <div className="svc-actions">
              <input className="svc-input" value={sessionId} onChange={(e) => setSessionId(e.target.value)} placeholder="Session id" aria-label="Session id" />
              {(['start', 'pause', 'resume', 'stop'] as const).map((op) => (
                <button key={op} type="button" className="btn" disabled={!sessionId.trim() || busy === `ses:${op}`}
                  onClick={() => act(`ses:${op}`, { action: 'session', id: sessionId.trim(), op })}>
                  {op[0].toUpperCase() + op.slice(1)}
                </button>
              ))}
            </div>
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

          <section className="svc-card" aria-label="Logs">
            <div className="svc-card-head">
              <h2>Logs</h2>
              <div className="svc-tabs" role="tablist">
                {(['orchestrator', 'web'] as const).map((n) => (
                  <button key={n} type="button" role="tab" aria-selected={logName === n}
                    className={`svc-tab${logName === n ? ' on' : ''}`} onClick={() => setLogName(n)}>{n}</button>
                ))}
              </div>
            </div>
            <pre className="svc-console">{log || 'No output yet.'}</pre>
          </section>
        </>
      )}
    </div>
  )
}
