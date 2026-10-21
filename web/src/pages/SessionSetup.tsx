import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  createSession,
  getAgentTemplates,
  getModes,
  listTasks,
  startSession,
  type Task,
  type SessionCreateRequest,
} from '../api/sessions'
import { agentHue } from '../agentColor'
import { archiveAgent, createAgent, updateAgent, type AgentDraft } from '../api/agents'
import { setTaskWorkspace } from '../api/tasks'
import { AgentDialog } from '../components/AgentDialog'
import { ConfirmDialog } from '../components/Dialog'
import { TableRing } from '../components/TableRing'
import { EMPTY_WORKSPACE, WorkspaceFields, workspaceBody, type WorkspaceDraft } from '../components/WorkspaceFields'
import './SessionSetup.css'

interface AgentTemplate {
  id: string
  name: string
  role?: string
  system_prompt: string
  stance: string
  model: string
  custom?: boolean
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
  build: 'Plan, code, test and review on a git branch. You review and merge it.',
  review: 'Agents critique a draft and converge on fixes.',
  decision: 'Weigh options and end with a recommendation.',
}

const BUDGET_PRESETS = [
  { label: 'Quick', tokens: 50_000, dollars: 1 },
  { label: 'Standard', tokens: 200_000, dollars: 5 },
  { label: 'Deep', tokens: 600_000, dollars: 15 },
]

// Rough planning figure per seat for a standard-length session; shown as an estimate only.
// Build seats run tools (read the repo, edit, run tests) and loop on failures, so they cost more.
const COST_PER_SEAT = 0.5
const BUILD_COST_PER_SEAT = 3
const BUILD_ROLES = ['build-architect', 'build-developer', 'build-tester', 'build-reviewer']
const isBuildTemplate = (t: { role?: string }) => (t.role ?? '').startsWith('build-')

function titleCase(s: string) {
  return s.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function initials(name: string) {
  return name.split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
}

export function SessionSetup() {
  const { taskId } = useParams<{ taskId: string }>()
  const navigate = useNavigate()
  const [search] = useSearchParams()

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

  // Custom agents: create, edit (dialog) and archive (confirm).
  const [agentDialog, setAgentDialog] = useState<{ edit?: AgentTemplate } | null>(null)
  const [agentBusy, setAgentBusy] = useState(false)
  const [agentError, setAgentError] = useState<string | null>(null)
  const [agentNote, setAgentNote] = useState<string | null>(null)
  const [archiving, setArchiving] = useState<AgentTemplate | null>(null)

  // The route takes any id, so check the task exists. If the lookup itself fails we carry on: the
  // setup options below report their own load errors, and starting a session re-validates the task.
  const [taskMissing, setTaskMissing] = useState(false)
  const [task, setTask] = useState<Task | null>(null)
  const [ws, setWs] = useState<WorkspaceDraft>(EMPTY_WORKSPACE) // for a task that has no repository yet
  useEffect(() => {
    listTasks()
      .then((ts) => {
        if (!Array.isArray(ts)) return
        const found = ts.find((t) => t.id === taskId)
        if (found) setTask(found)
        else setTaskMissing(true)
      })
      .catch(() => {})
  }, [taskId])

  useEffect(() => {
    Promise.all([getModes(), getAgentTemplates()])
      .then(([m, t]) => {
        setModes(m)
        setTemplates(t)
        if (m.length === 1) setModeId(m[0].id)
        // "Continue as Build" links here with ?mode=build
        const wanted = search.get('mode')
        const pick = wanted && m.find((x: Mode) => x.name === wanted)
        if (pick) chooseModeFrom(pick, t)
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)))
  }, [])

  async function saveAgent(draft: AgentDraft) {
    setAgentBusy(true); setAgentError(null)
    try {
      const edit = agentDialog?.edit
      const saved = edit ? await updateAgent(edit.id, draft) : await createAgent(draft)
      setTemplates(await getAgentTemplates())
      if (!edit && !isBuild) setAgentIds((ids) => [...ids, saved.id]) // a new agent joins the roster it was made for
      setAgentNote(edit ? `Saved ${saved.name}.` : `Created ${saved.name} and added it to the roster.`)
      setAgentDialog(null)
    } catch (err) {
      setAgentError(err instanceof Error ? err.message : String(err))
    } finally {
      setAgentBusy(false)
    }
  }
  async function archive(t: AgentTemplate) {
    setArchiving(null)
    try {
      await archiveAgent(t.id)
      setTemplates(await getAgentTemplates())
      setAgentIds((ids) => ids.filter((id) => id !== t.id))
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err))
    }
  }

  const selectedMode = modes?.find((m) => m.id === modeId)
  const isBuild = selectedMode?.name === 'build'
  const visibleTemplates = useMemo(
    () => (templates ?? []).filter((t) => (isBuild ? isBuildTemplate(t) : !isBuildTemplate(t))),
    [templates, isBuild],
  )

  // Build uses its own four roles; discussion modes never show them. Switching between the two kinds starts
  // a fresh roster so the two never mix, and Build starts with its team already seated and a bigger budget.
  function chooseModeFrom(mode: Mode, tpls: AgentTemplate[]) {
    const toBuild = mode.name === 'build'
    setModeId(mode.id)
    setAgentIds(
      toBuild ? BUILD_ROLES.map((r) => tpls.find((t) => t.role === r)?.id).filter((x): x is string => !!x) : [],
    )
    if (toBuild) { setDollars('15'); setTokens('600000') }
  }
  function chooseMode(mode: Mode) {
    if (!templates) return
    if (mode.id === modeId) return
    const wasBuild = isBuild
    if (wasBuild === (mode.name === 'build')) setModeId(mode.id)
    else chooseModeFrom(mode, templates)
    if (wasBuild && mode.name !== 'build') { setDollars('5'); setTokens('200000') }
  }

  const roster = useMemo(
    () => agentIds.map((id) => templates?.find((t) => t.id === id)).filter((t): t is AgentTemplate => !!t),
    [agentIds, templates],
  )

  const cap = parseFloat(dollars)
  const estimate = roster.length * (isBuild ? BUILD_COST_PER_SEAT : COST_PER_SEAT)
  const pct = cap > 0 ? Math.round((estimate / cap) * 100) : null

  const problems: string[] = []
  if (!modeId) problems.push('Choose a mode.')
  if (isBuild) {
    const missing = BUILD_ROLES.filter((r) => !roster.some((t) => t.role === r))
    if (missing.length > 0) problems.push(`Build needs all four roles: add ${missing.map((r) => r.replace('build-', '')).join(', ')}.`)
    if (task && !task.workspace && ws.repo_path.trim() === '') problems.push('Enter the repository path the team should work in.')
  } else if (roster.length < 2) {
    problems.push('Add at least two agents so there is something to discuss.')
  }
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
      if (isBuild && task && !task.workspace) await setTaskWorkspace(taskId, workspaceBody(ws))
      const { session } = await createSession(req)
      await startSession(session.id)
      navigate(`/room/${session.id}`)
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err))
      setSubmitting(false)
    }
  }

  if (taskMissing) {
    return (
      <div className="page">
        <div className="alert bad" role="alert">
          That task doesn't exist, or the link is wrong. <Link to="/">Back to tasks</Link>
        </div>
      </div>
    )
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
          <h1>Session setup</h1>
          <p className="muted">{isBuild
            ? 'Pick who builds, and how much the build may spend. The team works on a new branch in your repository.'
            : 'Pick how the discussion runs, who takes part, and how much it may spend.'}</p>
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
                  <input type="radio" name="mode" value={m.id} checked={modeId === m.id} onChange={() => chooseMode(m)} />
                  <span className="mode-name">{titleCase(m.name)}</span>
                  <span className="mode-blurb">{MODE_BLURBS[m.name.toLowerCase()] ?? titleCase(m.floor_policy ?? m.turn_policy ?? '')}</span>
                </label>
              ))}
            </div>
          </section>

          {isBuild && task && !task.workspace && (
            <section className="card card-pad" aria-labelledby="repo-h">
              <h2 id="repo-h">Repository</h2>
              <p className="hint">This task has no repository yet. The team will work on a new branch in it; nothing is pushed.</p>
              <WorkspaceFields value={ws} onChange={setWs} legend="" />
            </section>
          )}
          {isBuild && task?.workspace && (
            <section className="card card-pad" aria-labelledby="repo-h">
              <h2 id="repo-h">Repository</h2>
              <p className="muted"><code>{task.workspace.repo_path}</code> on <code>{task.workspace.base_branch}</code>
                {task.workspace.test_command ? <> · tests: <code>{task.workspace.test_command}</code></> : ' · no test command set'}</p>
            </section>
          )}


          <section className="card card-pad" aria-labelledby="agents-h">
            <h2 id="agents-h">2. Agent explorer</h2>
            <p className="hint">{isBuild
              ? 'The pipeline decides who speaks: plan, build, test, review. Fixes go back to the developer.'
              : 'Order matters: seats speak in roster order.'}</p>
            {!isBuild && (
              <button type="button" className="btn sm" onClick={() => { setAgentError(null); setAgentNote(null); setAgentDialog({}) }}>Create agent</button>
            )}
            {agentNote && <p className="hint" role="status">{agentNote}</p>}
            <ul className="agent-list">
              {visibleTemplates.map((t) => {
                const on = agentIds.includes(t.id)
                return (
                  <li key={t.id} className={`agent-card${on ? ' on' : ''}`} style={{ ['--agent-h' as string]: agentHue(t.role ?? t.name) }}>
                    <span className="avatar" aria-hidden="true">{initials(t.name)}</span>
                    <div className="agent-meta">
                      <strong>{t.name}</strong>
                      <span className="muted">{t.role ?? t.stance}</span>
                      <span className="agent-tags">
                        {t.stance && <span className="chip" title={`Model: ${t.model}`}>{t.stance}</span>}
                        {t.custom && <span className="chip accent">Custom</span>}
                      </span>
                      {t.custom && (
                        <span className="agent-manage">
                          <button type="button" className="link-btn" aria-label={`Edit ${t.name}`} onClick={() => { setAgentError(null); setAgentDialog({ edit: t }) }}>Edit</button>
                          <button type="button" className="link-btn" aria-label={`Archive ${t.name}`} onClick={() => setArchiving(t)}>Archive</button>
                        </span>
                      )}
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
            {roster.length > 0 && (
              <TableRing size={150} seats={roster.map((t) => ({ key: t.id, initials: initials(t.name), hue: agentHue(t.role ?? t.name), label: t.name }))}>
                {titleCase(modes.find((m) => m.id === modeId)?.name ?? 'Choose a mode')}
              </TableRing>
            )}
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
                <button key={p.label} type="button" className="btn sm" onClick={() => preset(p)}>{p.label} · ${p.dollars}</button>
              ))}
            </div>
            <div className="field">
              <label htmlFor="b-dollars">Dollar cap (hard limit)</label>
              <span className="affix"><span className="affix-mark" aria-hidden="true">$</span><input id="b-dollars" type="number" min="0.01" step="0.01" inputMode="decimal" value={dollars} onChange={(e) => setDollars(e.target.value)} /></span>
            </div>
            <div className="field">
              <label htmlFor="b-tokens">Token budget (suggested)</label>
              <input id="b-tokens" type="number" min="1000" step="1000" inputMode="numeric" value={tokens} onChange={(e) => setTokens(e.target.value)} aria-describedby="b-tokens-hint" />
              <span id="b-tokens-hint" className="hint">{tokens && Number(tokens) > 0 ? `${Number(tokens).toLocaleString('en-US')} tokens. ` : ''}A guide only. The session keeps going past it. The dollar cap is the hard stop.</span>
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
              <p className="hint">A planning estimate, not a quote. The session stops at the dollar cap.</p>
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

      {agentDialog && (
        <AgentDialog
          initial={agentDialog.edit ? { name: agentDialog.edit.name, stance: agentDialog.edit.stance, instructions: agentDialog.edit.system_prompt } : undefined}
          submitting={agentBusy}
          error={agentError}
          onSubmit={saveAgent}
          onCancel={() => setAgentDialog(null)}
        />
      )}
      {archiving && (
        <ConfirmDialog
          title={`Archive ${archiving.name}?`}
          message="It leaves the catalogue and can't be added to new sessions. Past sessions keep it."
          confirmLabel="Archive"
          danger
          onConfirm={() => archive(archiving)}
          onCancel={() => setArchiving(null)}
        />
      )}
    </div>
  )
}
