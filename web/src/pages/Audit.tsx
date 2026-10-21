import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { exportUrl, listAudit, verifyAudit, type AuditEvent } from '../api/audit'
import './Audit.css'

const GROUPS: { id: string; label: string }[] = [
  { id: '', label: 'Everything' },
  { id: 'session', label: 'Sessions' },
  { id: 'participant', label: 'Roster' },
  { id: 'agent', label: 'Agents' },
  { id: 'attachment', label: 'Context' },
  { id: 'task', label: 'Tasks' },
]
const PAGE = 50

function when(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' })
}

function value(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (Array.isArray(v)) return v.length === 0 ? 'none' : v.map(value).join(', ')
  if (typeof v === 'object') return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k}: ${value(x)}`).join('; ')
  return String(v)
}

function Details({ detail }: { detail: Record<string, unknown> }) {
  const entries = Object.entries(detail)
  if (entries.length === 0) return null
  return (
    <details className="audit-details">
      <summary>Details</summary>
      <dl>
        {entries.map(([k, v]) => (
          <div key={k}>
            <dt>{k.replace(/_/g, ' ')}</dt>
            {k === 'seats' && Array.isArray(v) ? (
              <dd>
                <ul className="audit-seats">
                  {(v as any[]).map((s) => (
                    <li key={s.role}>
                      <strong>{s.name}</strong>
                      <span>Allowed: {value(s.allowed)}</span>
                      <span>Denied: {value(s.denied)}</span>
                    </li>
                  ))}
                </ul>
              </dd>
            ) : (
              <dd>{value(v)}</dd>
            )}
          </div>
        ))}
      </dl>
    </details>
  )
}

export function Audit() {
  const [params, setParams] = useSearchParams()
  const action = params.get('action') ?? ''
  const sessionId = params.get('session') ?? undefined
  const taskId = params.get('task') ?? undefined
  const filter = { action: action || undefined, sessionId, taskId }

  const [events, setEvents] = useState<AuditEvent[] | null>(null)
  const [more, setMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [check, setCheck] = useState<{ text: string; bad: boolean } | null>(null)
  const [checking, setChecking] = useState(false)

  const load = useCallback(() => {
    setEvents(null); setError(null)
    listAudit(filter, undefined, PAGE)
      .then((rows) => { setEvents(rows); setMore(rows.length === PAGE) })
      .catch((e) => { setEvents([]); setError(e instanceof Error ? e.message : String(e)) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, sessionId, taskId])
  useEffect(load, [load])

  async function older() {
    if (!events?.length) return
    try {
      const rows = await listAudit(filter, events[events.length - 1].seq, PAGE)
      setEvents([...events, ...rows]); setMore(rows.length === PAGE)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }

  async function verify() {
    setChecking(true); setCheck(null)
    try {
      const r = await verifyAudit()
      setCheck(r.ok
        ? { bad: false, text: `The log is intact: ${r.checked.toLocaleString('en-US')} events chain correctly. Latest fingerprint ${r.head_hash.slice(0, 12)}.` }
        : { bad: true, text: `The log does not match at event #${r.first_bad_seq}: it was changed or something was removed there. ${r.checked} earlier events are intact.` })
    } catch (e) { setCheck({ bad: true, text: e instanceof Error ? e.message : String(e) }) }
    finally { setChecking(false) }
  }

  function pick(id: string) {
    const next = new URLSearchParams(params)
    if (id) next.set('action', id); else next.delete('action')
    setParams(next, { replace: true })
  }

  return (
    <div className="page audit">
      <header className="page-head">
        <div>
          <h1>Audit trail</h1>
          <p className="muted">A permanent record of what was changed, by whom, and what each agent was allowed to do.
            It records names, sizes and fingerprints, never message text or attached content.</p>
        </div>
        <div className="audit-tools">
          <button type="button" className="btn" onClick={verify} disabled={checking}>{checking ? 'Checking…' : 'Verify the log'}</button>
          <a className="btn" href={exportUrl(filter)} download>Download CSV</a>
        </div>
      </header>

      {check && <div className={`alert ${check.bad ? 'bad' : 'ok'}`} role={check.bad ? 'alert' : 'status'}>{check.text}</div>}

      {(sessionId || taskId) && (
        <p className="audit-scope">Showing {sessionId ? 'one session' : 'one task'}. <Link to="/activity">Show everything</Link></p>
      )}

      <div className="audit-filters" role="group" aria-label="Filter by kind">
        {GROUPS.map((g) => (
          <button key={g.id} type="button" className={`chip-btn${action === g.id ? ' on' : ''}`} aria-pressed={action === g.id} onClick={() => pick(g.id)}>{g.label}</button>
        ))}
      </div>

      {error && <div className="alert bad" role="alert">{error}</div>}
      {events === null ? (
        <div className="skeleton" style={{ height: '10rem' }} aria-busy="true" />
      ) : events.length === 0 ? (
        <p className="muted">Nothing recorded here yet.</p>
      ) : (
        <ol className="audit-list">
          {events.map((e) => (
            <li key={e.seq}>
              <div className="audit-row">
                <time dateTime={e.at}>{when(e.at)}</time>
                <span className={`chip ${e.actor === 'system' ? '' : 'accent'}`}>{e.actor === 'system' ? 'Bayto' : 'You'}</span>
                <span className="audit-summary">{e.summary}</span>
                {e.session_id && !sessionId && <Link className="audit-link" to={`/activity?session=${e.session_id}`}>This session</Link>}
              </div>
              <Details detail={e.detail} />
            </li>
          ))}
        </ol>
      )}
      {more && <button type="button" className="btn" onClick={older}>Load older events</button>}
    </div>
  )
}
