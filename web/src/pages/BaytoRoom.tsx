import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getSessionDetail, interjectSession, stopSession, pauseSession, resumeSession } from '../api/sessions'
import { useSessionEvents } from '../hooks/useSessionEvents'
import './TaskBoard.css'

export function BaytoRoom() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const { messages, rollingSummary } = useSessionEvents(sessionId || '')
  const [session, setSession] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const [interjectText, setInterjectText] = useState('')
  const [targetAgent, setTargetAgent] = useState<string>('*')

  useEffect(() => {
    if (sessionId) {
      getSessionDetail(sessionId)
        .then(res => setSession(res.session))
        .catch(err => setError(err instanceof Error ? err.message : String(err)))
    }
  }, [sessionId])

  async function handleInterject() {
    if (!interjectText.trim()) return
    try {
      await interjectSession(sessionId!, interjectText, targetAgent === '*' ? undefined : [targetAgent])
      setInterjectText('')
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to interject')
    }
  }

  async function handleStop() {
    if (!confirm('End session and synthesize results?')) return
    try {
      await stopSession(sessionId!)
      alert('Session stopping...')
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to stop')
    }
  }

  async function handlePause() {
    try {
      await pauseSession(sessionId!)
      alert('Session paused')
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to pause')
    }
  }

  async function handleResume() {
    try {
      await resumeSession(sessionId!)
      alert('Session resumed')
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to resume')
    }
  }

  if (error) return <div className="task-board-error">{error}</div>
  if (!session) return <p>Loading session…</p>

  return (
    <section className="bayto-room" style={{ display: 'grid', gridTemplateColumns: '250px 1fr 300px', height: '100vh' }}>
      <div className="roster-panel" style={{ borderRight: '1px solid #ddd', padding: '1rem', overflowY: 'auto' }}>
        <h2 style={{ marginTop: 0 }}>Roster</h2>
        <div className="agent-list">
          {session.turn_counts.map((tc: any) => (
            <div key={tc.agent_id} style={{ padding: '0.5rem', borderBottom: '1px solid #eee', display: 'flex', justifyContent: 'space-between' }}>
              <span>{tc.participant || 'Unknown'}</span>
              <span>{tc.turns} turns</span>
            </div>
          ))}
        </div>
      </div>

      <div className="transcript-panel" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div className="transcript-content" style={{ flex: 1, overflowY: 'auto', padding: '1rem' }}>
          <h2 style={{ marginTop: 0 }}>Transcript</h2>
          <div className="messages-list" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {messages.map((msg, i) => (
              <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
                <span style={{ fontWeight: 'bold', fontSize: '0.8rem' }}>{msg.from}</span>
                <div style={{ padding: '0.5rem', backgroundColor: '#eee', borderRadius: '4px' }}>{msg.body}</div>
              </div>
            ))}
            {messages.length === 0 && <p>No messages yet. Wait for the moderator to start the session.</p>}
          </div>
        </div>
        <div className="composer-panel" style={{ padding: '1rem', borderTop: '1px solid #ddd', backgroundColor: '#fcfcfc' }}>
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
            <select
              value={targetAgent}
              onChange={(e) => setTargetAgent(e.target.value)}
              style={{ padding: '0.5rem' }}
            >
              <option value="*">Everyone</option>
              {session.turn_counts.map((tc: any) => (
                <option key={tc.agent_id} value={tc.participant}>{tc.participant}</option>
              ))}
            </select>
            <input
              type="text"
              placeholder="Interject..."
              value={interjectText}
              onChange={(e) => setInterjectText(e.target.value)}
              style={{ flex: 1, padding: '0.5rem' }}
            />
            <button onClick={handleInterject} style={{ padding: '0.5rem 1rem' }}>Send</button>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
            <button onClick={handlePause} style={{ padding: '0.3rem 0.6rem', fontSize: '0.8rem' }}>Pause</button>
            <button onClick={handleResume} style={{ padding: '0.3rem 0.6rem', fontSize: '0.8rem' }}>Resume</button>
            <button onClick={handleStop} style={{ padding: '0.3rem 0.6rem', fontSize: '0.8rem', backgroundColor: '#ffcccc', border: '1px solid red' }}>End & Synthesize</button>
          </div>
        </div>
      </div>

      <div className="moderator-panel" style={{ borderLeft: '1px solid #ddd', padding: '1rem', overflowY: 'auto' }}>
        <h2 style={{ marginTop: 0 }}>Moderator</h2>
        <div className="rolling-summary" style={{ backgroundColor: '#f9f9f9', padding: '1rem', borderRadius: '4px', border: '1px solid #ddd', marginBottom: '1rem' }}>
          <h3 style={{ marginTop: 0 }}>Rolling Summary</h3>
          <p>{rollingSummary || 'Waiting for updates...'}</p>
        </div>
        <div className="cost-meter" style={{ padding: '1rem', border: '1px solid #ddd' }}>
          <h3 style={{ marginTop: 0 }}>Usage</h3>
          <p>Tokens: {session.usage.tokens_in + session.usage.tokens_out}</p>
          <p>Cost: ${session.usage.cost.toFixed(4)}</p>
        </div>
      </div>
    </section>
  )
}
