import { useCallback, useEffect, useState } from 'react'
import { controlAction, getControlState, getLog, type ControlState } from '../api/control'
import './Services.css'

type ServiceName = 'postgres' | 'orchestrator' | 'web'

export function Services() {
  const [state, setState] = useState<ControlState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [log, setLog] = useState('')
  const [logName, setLogName] = useState<'orchestrator' | 'web'>('orchestrator')
  const [sessionId, setSessionId] = useState('')
  const [sandboxName, setSandboxName] = useState('')
  const [sandboxOut, setSandboxOut] = useState('')
  const [summaryModel, setSummaryModel] = useState('')
  const [synthesisModel, setSynthesisModel] = useState('')
  const [streaming, setStreaming] = useState(true)
  const [apiKey, setApiKey] = useState('')
  const [, setSeeded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const s = await getControlState()
      setState(s)
      setError(null)
      setSeeded((done) => {
        if (!done) {
          setSummaryModel(s.config.summary_model)
          setSynthesisModel(s.config.synthesis_model)
          setStreaming(s.config.streaming)
        }
        return true
      })
      setLog(await getLog(logName))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [logName])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [refresh])

  async function act(body: Record<string, unknown>) {
    const out = await controlAction(body)
    setMessage(out)
    refresh()
    return out
  }

  async function saveConfig() {
    await act({ action: 'config', summary_model: summaryModel, synthesis_model: synthesisModel, streaming, api_key: apiKey })
    setApiKey('')
  }

  return (
    <section className="services">
      <header className="services-header">
        <h1>Services</h1>
        <a href="/">Tasks</a>
      </header>

      {error && <p role="alert" className="services-error">{error}</p>}
      {message && <p className="services-message">{message}</p>}

      {state && (
        <>
          <h2>Dev stack</h2>
          <table className="services-table">
            <tbody>
              {(Object.keys(state.services) as ServiceName[]).map((name) => (
                <tr key={name}>
                  <td>{name}{name === 'web' ? ' (this page)' : ''}</td>
                  <td className={`svc-${state.services[name]}`}>{state.services[name]}</td>
                  <td>
                    <button type="button" onClick={() => act({ action: 'service', name, op: 'start' })}>Start</button>{' '}
                    <button type="button" disabled={name === 'web'} onClick={() => act({ action: 'service', name, op: 'stop' })}>Stop</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <h2>Orchestrator settings</h2>
          <p className="services-hint">Applied the next time the orchestrator starts. The API key stays in the control script's memory.</p>
          <div className="services-row">
            <label>Summary model <input value={summaryModel} onChange={(e) => setSummaryModel(e.target.value)} /></label>
            <label>Synthesis model <input value={synthesisModel} onChange={(e) => setSynthesisModel(e.target.value)} /></label>
          </div>
          <div className="services-row">
            <label>Anthropic API key <input type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={state.config.api_key ? 'key set' : 'not set'} /></label>
            <label><input type="checkbox" checked={streaming} onChange={(e) => setStreaming(e.target.checked)} /> Streaming turns</label>
            <button type="button" onClick={saveConfig}>Save</button>
          </div>

          <h2>Sessions</h2>
          <table className="services-table">
            <tbody>
              {state.sessions.map((s) => (
                <tr key={s.id}>
                  <td>{s.task}</td>
                  <td><button type="button" className="link" onClick={() => setSessionId(s.id)}>{s.id.slice(0, 8)}</button> <a href={`/room/${s.id}`}>room</a></td>
                  <td className={`svc-${s.status}`}>{s.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="services-row">
            <input value={sessionId} onChange={(e) => setSessionId(e.target.value)} placeholder="session id" />
            {(['start', 'pause', 'resume', 'stop'] as const).map((op) => (
              <button key={op} type="button" onClick={() => act({ action: 'session', id: sessionId.trim(), op })}>{op}</button>
            ))}
          </div>

          <h2>Sandboxes</h2>
          <div className="services-row">
            <button type="button" onClick={async () => setSandboxOut(await act({ action: 'sbx', op: 'ls' }))}>List</button>
            <input value={sandboxName} onChange={(e) => setSandboxName(e.target.value)} placeholder="sandbox name" />
            <button type="button" onClick={async () => setSandboxOut(await act({ action: 'sbx', op: 'stop', name: sandboxName.trim() }))}>Stop sandbox</button>
          </div>
          {sandboxOut && <pre>{sandboxOut}</pre>}

          <h2>Logs</h2>
          <div className="services-row">
            <button type="button" onClick={() => setLogName('orchestrator')}>orchestrator</button>
            <button type="button" onClick={() => setLogName('web')}>web</button>
          </div>
          <pre>{log}</pre>
        </>
      )}
    </section>
  )
}
