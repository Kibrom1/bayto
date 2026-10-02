import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import './BaytoRoom.css'

type TranscriptMessage = {
  message_id?: string
  seq?: number
  from?: string
  to?: string[]
  kind?: string
  body?: string
  created_at?: string
  meta?: Record<string, unknown>
}

const defaultRoster = [
  { name: 'Researcher', stance: 'Neutral' },
  { name: 'Product Owner', stance: 'Advocate' },
  { name: 'Architect', stance: 'Balanced' },
  { name: 'QA Tester', stance: 'Skeptic' },
]

export function BaytoRoom() {
  const { sessionId } = useParams()
  const [messages, setMessages] = useState<TranscriptMessage[]>([
    {
      message_id: 'intro-1',
      from: 'Moderator',
      body: 'Welcome to the session. We are checking the brief, setting the round order, and gathering the first arguments.',
      created_at: new Date().toISOString(),
      kind: 'summary',
    },
  ])
  const [summary, setSummary] = useState('The team is narrowing the issue, checking assumptions, and drafting the first position set.')
  const [composer, setComposer] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    if (!sessionId || typeof EventSource === 'undefined') {
      return
    }

    const source = new EventSource(`/sessions/${sessionId}/events`)

    source.onopen = () => {
      setConnected(true)
      setError(null)
    }

    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as TranscriptMessage
        if (!payload || !payload.body) {
          return
        }

        setMessages((prev) => [...prev, payload])
      } catch {
        return
      }
    }

    source.addEventListener('rolling_summary', (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as { summary?: string }
        if (payload.summary) {
          setSummary(payload.summary)
        }
      } catch {
        return
      }
    })

    source.onerror = () => {
      setConnected(false)
      setError('Live transcript is temporarily unavailable; the transcript is replaying the current backlog.')
    }

    return () => source.close()
  }, [sessionId])

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()

    if (!composer.trim() || !sessionId) {
      return
    }

    try {
      await fetch(`/sessions/${sessionId}/interject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body: composer.trim() }),
      })

      setComposer('')
      setMessages((prev) => [
        ...prev,
        {
          message_id: `human-${Date.now()}`,
          from: 'You',
          body: composer.trim(),
          created_at: new Date().toISOString(),
          kind: 'note',
        },
      ])
    } catch {
      setError('Your message could not be sent. Please try again.')
    }
  }

  return (
    <main className="bayto-room">
      <header className="bayto-room-header">
        <div>
          <p className="room-kicker">Bayto room</p>
          <h1>Session {sessionId ?? 'preview'}</h1>
        </div>
        <div className={`live-pill ${connected ? 'connected' : 'disconnected'}`}>
          {connected ? 'Live' : 'Replay'}
        </div>
      </header>

      <div className="bayto-room-layout">
        <aside className="bayto-panel roster-panel">
          <div className="panel-header">
            <h2>Roster</h2>
          </div>

          <ul className="roster-list">
            {defaultRoster.map((person) => (
              <li key={person.name}>
                <div className="avatar" aria-hidden="true">{person.name[0]}</div>
                <div>
                  <strong>{person.name}</strong>
                  <small>{person.stance}</small>
                </div>
              </li>
            ))}
          </ul>
        </aside>

        <section className="bayto-panel transcript-panel">
          <div className="panel-header">
            <h2>Transcript</h2>
          </div>

          <div className="transcript-stream" aria-live="polite">
            {messages.map((message) => (
              <article key={message.message_id ?? `${message.from ?? 'message'}-${message.created_at ?? Math.random()}`} className="message-card">
                <header>
                  <strong>{message.from ?? 'System'}</strong>
                  <time>{message.created_at ? new Date(message.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : 'now'}</time>
                </header>
                <p>{message.body ?? '—'}</p>
              </article>
            ))}
          </div>
        </section>

        <aside className="bayto-panel summary-panel">
          <div className="panel-header">
            <h2>Moderator summary</h2>
          </div>
          <p className="summary-copy">{summary}</p>
          <div className="summary-box">
            <h3>Artifact being built</h3>
            <p>Drafting the final recommendation from the current set of arguments.</p>
          </div>
        </aside>
      </div>

      <footer className="composer-panel bayto-panel">
        <form onSubmit={handleSubmit} className="composer-form">
          <label htmlFor="composer-input" className="sr-only">
            Send a message to the group
          </label>
          <textarea
            id="composer-input"
            value={composer}
            onChange={(event) => setComposer(event.target.value)}
            rows={3}
            placeholder="Ask a specific agent or add a new angle..."
          />
          <div className="composer-actions">
            <button type="button" className="secondary-action">Ask a specific agent</button>
            <button type="submit" className="primary-action">Send</button>
          </div>
        </form>
        {error && <p className="room-error">{error}</p>}
      </footer>
    </main>
  )
}
