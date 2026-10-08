import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  createSession,
  getAgentTemplates,
  getModes,
  startSession,
  type SessionCreateRequest,
} from '../api/sessions'
import './SessionSetup.css'

interface AgentTemplate {
  id: string
  name: string
  role?: string
  system_prompt: string
  stance: string
  model: string
}

interface Mode {
  id: string
  name: string
  floor_policy?: string
  turn_policy?: string
  stop_rules?: unknown
}

const MODE_BLURBS: Record<string, string> = {
  brainstorm: 'Diverge first: many ideas, little judgement.',
  debate: 'Opposing stances argue; the moderator keeps it fair.',
  review: 'Agents critique a draft and converge on fixes.',
  decision: 'Weigh options and end with a recommendation.',
}

const BUDGET_PRESETS = [
  { label: 'Quick', tokens: 50_000, dollars: 1 },
  { label: 'Standard', tokens: 200_000, dollars: 5 },
  { label: 'Deep', tokens: 600_000, dollars: 15 },
]

// Rough planning figure per seat for a standard-length session; shown as an estimate only.
const COST_PER_SEAT = 0.5

function titleCase(s: string) {
  return s.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function initials(name: string) {
  return name.split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
}

export function SessionSetup() {
  const { taskId } = useParams<{ taskId: string }>()
  const navigate = useNavigate()

  const [modes, setModes] = useState<Mode[] | null>(null)
  const [templates, setTemplates] = useState<AgentTemplate[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const [modeId, setModeId] = useState('')
  const [agentIds, setAgentIds] = useState<string[]>([])
  const [dollars, setDollars] = useState('5')
  const [tokens, setTokens] = useState('200000')
  const [touched, setTouched] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    Promise.all([getModes(), getAgentTemplates()])
      .then(([m, t]) => {
        setModes(m)
        setTemplates(t)
        if (m.length === 1) setModeId(m[0].id)
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)))
  }, [])

  const roster = useMemo(
    () => agentIds.map((id) => templates?.find((t) => t.id === id)).filter((t): t is AgentTemplate => !!t),
    [agentIds, templates],
  )

  const cap = parseFloat(dollars)
  const estimate = roster.length * COST_PER_SEAT
  const pct = cap > 0 ? Math.round((estimate / cap) * 100) : null

  const problems: string[] = []
  if (!modeId) problems.push('Choose a mode.')
  if (roster.length < 2) problems.push('Add at least two agents so there is something to discuss.')
  if (!(cap > 0)) problems.push('Set a dollar cap above zero.')

  function toggle(id: string) {
    setAgentIds((prev) => (prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]))
  }

  function move(id: string, dir: -1 | 1) {
    setAgentIds((prev) => {
      const i = prev.indexOf(id)
      const j = i + dir
      if (i < 0 || j < 0 || j >= prev.length) return prev
      const next = [...prev]
      ;[next[i], next[j]] = [next[j], next[i]]
      return next
    })
  }

  function preset(p: (typeof BUDGET_PRESETS)[number]) {
    setDollars(String(p.dollars))
    setTokens(String(p.tokens))
  }

  async function submit() {
    setTouched(true)
    if (!taskId || problems.length > 0) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const req: SessionCreateRequest = {
        task_id: taskId,
        mode_id: modeId,
        roster: agentIds.map((agent_id, seat_order) => ({ agent_id, seat_order })),
        budget: {
          tokens: tokens ? parseInt(tokens, 10) : undefined,
          dollars: cap > 0 ? cap : undefined,
        },
      }
      const { session } = await createSession(req)
      await startSession(session.id)
      navigate(`/room/${session.id}`)
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err))
      setSubmitting(false)
    }
  }

  if (loadError) {
    return (
      <div className="page">
        <div className="alert bad" role="alert">
          Couldn't load setup options: {loadError}. Is the orchestrator running? <Link to="/services">Open Services</Link>
        </div>
      </div>
    )
  }
  if (!modes || !templates) {
    return (
      <div className="page" aria-busy="true">
        <div className="skeleton" style={{ height: '2rem', width: '14rem', marginBottom: '1rem' }} />
        <div className="skeleton" style={{ height: '12rem' }} />
      </div>
    )
  }

  return (
    <div className="page setup">
      <header className="page-head">
        <div>
          <p className="eyebrow">New session</p>
          <h1>Session setup</h1>
          <p className="muted">Pick how the discussion runs, who takes part, and how much it may spend.</p>
        </div>
        <Link className="btn" to="/">← Tasks</Link>
      </header>

      <div className="setup-layout">
        <div className="setup-main">
          <section className="card card-pad" aria-labelledby="mode-h">
            <h2 id="mode-h">1. Mode</h2>
            <div className="mode-grid" role="radiogroup" aria-label="Mode">
              {modes.map((m) => (
                <label key={m.id} className={`mode-card${modeId === m.id ? ' selected' : ''}`}>
                  <input type="radio" name="mode" value={m.id} checked={modeId === m.id} onChange={() => setModeId(m.id)} />
                  <span className="mode-name">{titleCase(m.name)}</span>
                  <span className="mode-blurb">{MODE_BLURBS[m.name.toLowerCase()] ?? titleCase(m.floor_policy ?? m.turn_policy ?? '')}</span>
                </label>
              ))}
            </div>
          </section>

          <section className="card card-pad" aria-labelledby="agents-h">
            <h2 id="agents-h">2. Agent explorer</h2>
            <p className="hint">Order matters: seats speak in roster order.</p>
            <ul className="agent-list">
              {templates.map((t) => {
                const on = agentIds.includes(t.id)
                return (
                  <li key={t.id} className={`agent-card${on ? ' on' : ''}`}>
                    <span className="avatar" aria-hidden="true">{initials(t.name)}</span>
                    <div className="agent-meta">
                      <strong>{t.name}</strong>
                      <span className="muted">{t.role ?? t.stance}</span>
                      <span className="agent-tags">
                        <span className="chip">{t.model}</span>
                        {t.stance && <span className="chip">{t.stance}</span>}
                      </span>
                    </div>
                    <button type="button" className={`btn sm${on ? '' : ' primary'}`} aria-pressed={on} onClick={() => toggle(t.id)}>
                      {on ? 'Remove' : 'Add to roster'}
                    </button>
                  </li>
                )
              })}
            </ul>
          </section>
        </div>

        <aside className="setup-side">
          <section className="card card-pad" aria-labelledby="roster-h">
            <h2 id="roster-h">Selected roster</h2>
            {roster.length === 0 ? (
              <p className="muted">No agents yet. Add at least two from the explorer.</p>
            ) : (
              <ol className="roster-list">
                {roster.map((t, i) => (
                  <li key={t.id}>
                    <span className="seat">{i + 1}</span>
                    <span className="roster-name">{t.name}</span>
                    <button type="button" className="btn ghost sm" aria-label={`Move ${t.name} up`} disabled={i === 0} onClick={() => move(t.id, -1)}>↑</button>
                    <button type="button" className="btn ghost sm" aria-label={`Move ${t.name} down`} disabled={i === roster.length - 1} onClick={() => move(t.id, 1)}>↓</button>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section className="card card-pad" aria-labelledby="budget-h">
            <h2 id="budget-h">Budget</h2>
            <div className="preset-row" role="group" aria-label="Budget presets">
              {BUDGET_PRESETS.map((p) => (
                <button key={p.label} type="button" className="btn sm" onClick={() => preset(p)}>{p.label}</button>
              ))}
            </div>
            <div className="field">
              <label htmlFor="b-dollars">Dollar cap</label>
              <input id="b-dollars" type="number" min="0.01" step="0.01" inputMode="decimal" value={dollars} onChange={(e) => setDollars(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="b-tokens">Token cap</label>
              <input id="b-tokens" type="number" min="1000" step="1000" inputMode="numeric" value={tokens} onChange={(e) => setTokens(e.target.value)} />
            </div>
            <div className="estimate">
              <h3>Estimated cost</h3>
              <p className="estimate-figure">~${estimate.toFixed(2)}</p>
              <p className="muted">
                {pct === null ? 'Set a cap to compare.' : `${pct}% of budget cap`}
              </p>
              {pct !== null && (
                <div className="meter" role="img" aria-label={`${pct}% of budget cap`}>
                  <span style={{ width: `${Math.min(100, pct)}%` }} className={pct > 80 ? 'warn' : ''} />
                </div>
              )}
              <p className="hint">A planning estimate, not a quote. The session stops at the cap.</p>
            </div>
          </section>

          {touched && problems.length > 0 && (
            <div className="alert warn" role="alert">
              <ul>{problems.map((p) => <li key={p}>{p}</li>)}</ul>
            </div>
          )}
          {submitError && <div className="alert bad" role="alert">{submitError}</div>}

          <button type="button" className="btn primary lg block" disabled={submitting} onClick={submit}>
            {submitting ? 'Starting…' : 'Start session'}
          </button>
        </aside>
      </div>
    </div>
  )
}
