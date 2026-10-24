import { NotFound } from './NotFound'
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
import { agentHue, initials } from '../agentColor'
import { buildModels, claudeOnly, costFactor, getModels, modelOptionText, type ModelCatalog } from '../api/models'
import { archiveAgent, createAgent, updateAgent, type AgentDraft } from '../api/agents'
import { LOCAL_FALLBACK_LABEL, LOCAL_FALLBACK_NOTE, setLocalFallback, setTaskWorkspace } from '../api/tasks'
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
  raise_hand: 'Agents speak when they have something to add.',
  open_chat: 'Agents speak when they have something to add.',
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

// Server names vary in case and separators ("Raise Hand", raise-hand, raise_hand); look them up in one form.
const modeKey = (name: string) => name.trim().toLowerCase().replace(/[\s-]+/g, '_')
const MODE_LABELS: Record<string, string> = { raise_hand: 'Open Chat', open_chat: 'Open Chat' }
const modeLabel = (name: string) => MODE_LABELS[modeKey(name)] ?? titleCase(name)

function titleCase(s: string) {
  return s.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
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
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null) // null until loaded, or if the server has no /models
  const [synthesisModel, setSynthesisModel] = useState('') // '' = the app default
  const [seatModels, setSeatModels] = useState<Record<string, string>>({}) // agent id -> chosen model; absent = the agent's default
  const [fbOn, setFbOn] = useState(false) // opt-in local model when Claude runs out of usage
  const [fbModel, setFbModel] = useState('') // '' = the server's default local model
  const [dollars, setDollars] = useState('5')
  const [tokens, setTokens] = useState('200000')
  const [touched, setTouched] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [agentFilter, setAgentFilter] = useState('')

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
        if (found?.workspace) {
          setWs({
            repo_path: found.workspace.repo_path, base_branch: found.workspace.base_branch || 'main',
            test_command: found.workspace.test_command ?? '', allowed_commands: (found.workspace.allowed_commands ?? []).join('\n'),
          })
        }
        if (found) {
          setTask(found)
          if (found.local_fallback) { setFbOn(found.local_fallback.enabled); setFbModel(found.local_fallback.model ?? '') }
        }
        else setTaskMissing(true)
      })
      .catch(() => {})
  }, [taskId])

  useEffect(() => { getModels().then(setCatalog).catch(() => {}) }, [])

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
        else if (m.length > 1) {
          const debate = m.find((x: Mode) => x.name.toLowerCase() === 'debate')
          if (debate) setModeId(debate.id)
        }
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

  const shownTemplates = useMemo(() => {
    const q = agentFilter.trim().toLowerCase()
    if (!q) return visibleTemplates
    return visibleTemplates.filter((t) => [t.name, t.role, t.stance].some((v) => (v ?? '').toLowerCase().includes(q)))
  }, [visibleTemplates, agentFilter])

  const roster = useMemo(
    () => agentIds.map((id) => templates?.find((t) => t.id === id)).filter((t): t is AgentTemplate => !!t),
    [agentIds, templates],
  )

  const cap = parseFloat(dollars)
  // Each seat costs the usual per-seat figure times how its model compares with the default (Opus about 2x, Haiku about 0.2x).
  const seatModel = (t: AgentTemplate) => seatModels[t.id] || t.model
  const estimate = roster.reduce((sum, t) => sum + (isBuild ? BUILD_COST_PER_SEAT : COST_PER_SEAT) * costFactor(catalog, seatModel(t)), 0)
  const gatewayBuildSeat = isBuild && roster.some((t) => catalog?.models.find((m) => m.id === seatModel(t))?.provider === 'gateway')
  // The developer writes the code the others judge: a small model there tends to loop or break the build.
  const smallDeveloper = isBuild && roster.some((t) => t.role === 'build-developer' &&
    catalog?.models.find((m) => m.id === seatModel(t))?.tier === 'small')
  const suggested = Math.max(1, Math.ceil(estimate * 1.25))
  const pct = cap > 0 ? Math.round((estimate / cap) * 100) : null

  const seatsReason = !modeId ? 'Choose a mode.' : !isBuild && roster.length < 2 ? 'Choose at least two agents.' : null
  const problems: string[] = []
  if (!modeId) problems.push('Choose a mode.')
  if (isBuild) {
    const missing = BUILD_ROLES.filter((r) => !roster.some((t) => t.role === r))
    if (missing.length > 0) problems.push(`Build needs all four roles: add ${missing.map((r) => r.replace('build-', '')).join(', ')}.`)
    if (task && ws.repo_path.trim() === '') problems.push('Enter the repository path the team should work in.')
    else if (task && ws.test_command.trim() === '') problems.push('Set a test command: without one the developer and tester cannot run any tests.')
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
        roster: agentIds.map((agent_id, seat_order) => ({ agent_id, seat_order, ...(seatModels[agent_id] ? { model: seatModels[agent_id] } : {}) })),
        ...(synthesisModel ? { synthesis_model: synthesisModel } : {}),
        budget: {
          tokens: tokens ? parseInt(tokens, 10) : undefined,
          dollars: cap > 0 ? cap : undefined,
        },
      }
      if (isBuild && task) await setTaskWorkspace(taskId, workspaceBody(ws))
      const before = task?.local_fallback ?? { enabled: false, model: null }
      if (!isBuild && (fbOn !== before.enabled || (fbOn && (fbModel || null) !== (before.model ?? null)))) {
        await setLocalFallback(taskId, fbOn, fbModel || null)
      }
      const { session } = await createSession(req)
      await startSession(session.id)
      navigate(`/room/${session.id}`)
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err))
      setSubmitting(false)
    }
  }

  if (taskMissing) return <NotFound title="Task not found" message="This task doesn't exist, or the link is wrong." />
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
          {task && (
            <div className="setup-task">
              <p className="setup-task-title">{task.title}</p>
              {task.brief && <p className="setup-task-brief">{task.brief}</p>}
            </div>
          )}
          <p className="muted">{isBuild
            ? 'Pick who builds, and how much the build may spend. The team works on a new branch in your repository.'
            : 'Pick how the discussion runs, who takes part, and how much it may spend.'}</p>
        </div>
        <Link className="btn" to="/">← Tasks</Link>
      </header>

      <div className="setup-layout">
        <div className="setup-main">
          <section className="card card-pad s-mode" aria-labelledby="mode-h">
            <h2 id="mode-h">1. Mode</h2>
            <div className="mode-grid" role="radiogroup" aria-label="Mode">
              {modes.map((m) => (
                <label key={m.id} className={`mode-card${modeId === m.id ? ' selected' : ''}`}>
                  <input type="radio" name="mode" value={m.id} checked={modeId === m.id} onChange={() => chooseMode(m)} />
                  <span className="mode-name">{modeLabel(m.name)}</span>
                  <span className="mode-blurb">{MODE_BLURBS[modeKey(m.name)] ?? titleCase(m.floor_policy ?? m.turn_policy ?? '')}</span>
                </label>
              ))}
            </div>
          </section>

          {isBuild && task && (
            <section className="card card-pad s-repo" aria-labelledby="repo-h">
              <h2 id="repo-h">Repository and permissions</h2>
              <p className="hint">The team works on a new branch in this repository; nothing is pushed. What each role may run is fixed when the session starts.</p>
              <WorkspaceFields value={ws} onChange={setWs} legend="" autoCheck={!!task.workspace} />
            </section>
          )}


          <section className="card card-pad s-agents" aria-labelledby="agents-h">
            <h2 id="agents-h">2. Agent explorer</h2>
            <p className="hint">{isBuild
              ? 'The pipeline decides who speaks: plan, build, test, review. Fixes go back to the developer.'
              : 'Order matters: seats speak in roster order.'}</p>
            {!isBuild && (
              <button type="button" className="btn sm" onClick={() => { setAgentError(null); setAgentNote(null); setAgentDialog({}) }}>Create agent</button>
            )}
            {agentNote && <p className="hint" role="status">{agentNote}</p>}
            <div className="field agent-filter">
              <label htmlFor="agent-filter">Filter agents</label>
              <input id="agent-filter" type="search" value={agentFilter} placeholder="Name, role or stance" onChange={(e) => setAgentFilter(e.target.value)} />
            </div>
            {shownTemplates.length === 0 && <p className="muted" role="status">No agents match "{agentFilter}".</p>}
            <ul className="agent-list">
              {shownTemplates.map((t) => {
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
          <section className="card card-pad s-roster" aria-labelledby="roster-h">
            <h2 id="roster-h">Selected roster</h2>
            {roster.length > 0 && (
              <TableRing size={150} seats={roster.map((t) => ({ key: t.id, initials: initials(t.name), hue: agentHue(t.role ?? t.name), label: t.name }))}>
                {modeLabel(modes.find((m) => m.id === modeId)?.name ?? 'Choose a mode')}
              </TableRing>
            )}
            {roster.length === 0 ? (
              <p className="muted">No agents yet. Add at least two from the explorer.</p>
            ) : (
              <ol className="roster-list">
                {roster.map((t, i) => (
                  <li key={t.id}>
                    <span className="seat">{i + 1}</span>
                    <span className="roster-main">
                    <span className="roster-name">{t.name}</span>
                    {catalog && catalog.models.length > 0 && (
                      <select className="seat-model" aria-label={`Model for ${t.name}`} value={seatModels[t.id] ?? ''}
                        onChange={(e) => setSeatModels((m) => ({ ...m, [t.id]: e.target.value }))}>
                        <option value="">Default ({catalog.models.find((m) => m.id === t.model)?.label ?? t.model})</option>
                        {(isBuild ? buildModels(catalog.models) : catalog.models).map((m) => (
                          <option key={m.id} value={m.id} disabled={m.ready === false}>{modelOptionText(m)}</option>
                        ))}
                      </select>
                    )}
                    </span>
                    <button type="button" className="btn ghost sm" aria-label={`Move ${t.name} up`} disabled={i === 0} onClick={() => move(t.id, -1)}>↑</button>
                    <button type="button" className="btn ghost sm" aria-label={`Move ${t.name} down`} disabled={i === roster.length - 1} onClick={() => move(t.id, 1)}>↓</button>
                  </li>
                ))}
              </ol>
            )}
            {gatewayBuildSeat && (
              <p className="hint" role="note">A Build seat is on a gateway model. Tool use through a gateway varies by model, so
                expect more failed or looping turns than on Claude. Permissions do not change: <code>claude</code> itself still
                enforces the same allowed and denied commands for every Build seat. If the gateway fails, the session pauses for
                you; it never switches to another model.</p>
            )}
            {smallDeveloper && (
              <p className="hint" role="note">The developer is on a small model. It is cheaper, but it writes the code the tester and
                reviewer then judge, so a failed build is more likely. Sonnet or Opus is the safer choice for that seat.</p>
            )}
            {!isBuild && (
              <div className="field local-fallback">
                <label>
                  <input type="checkbox" checked={fbOn} onChange={(e) => setFbOn(e.target.checked)} />{' '}
                  {LOCAL_FALLBACK_LABEL}
                </label>
                {fbOn && (
                  <>
                    <label htmlFor="fallback-model">Local model</label>
                    <select id="fallback-model" value={fbModel} onChange={(e) => setFbModel(e.target.value)}>
                      <option value="">Server default</option>
                      {(catalog?.models ?? []).filter((m) => m.provider === 'ollama').map((m) => (
                        <option key={m.id} value={m.id}>{modelOptionText(m)}</option>
                      ))}
                    </select>
                  </>
                )}
                <span className="hint">{LOCAL_FALLBACK_NOTE}</span>
              </div>
            )}
            {catalog && catalog.models.length > 0 && roster.length > 0 && (
              <div className="field">
                <label htmlFor="synthesis-model">Final write-up model</label>
                <select id="synthesis-model" value={synthesisModel} onChange={(e) => setSynthesisModel(e.target.value)}>
                  <option value="">App default</option>
                  {claudeOnly(catalog.models).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                </select>
                <span className="hint">Writes the summary when the session ends. A large model here can write up what smaller seats discussed. Claude models only.</span>
              </div>
            )}
          </section>

          <section className="card card-pad s-budget" aria-labelledby="budget-h">
            <h2 id="budget-h">Budget</h2>
            <div className="preset-row" role="group" aria-label="Budget presets">
              {BUDGET_PRESETS.map((p) => (
                <button key={p.label} type="button" className={`btn sm${Number(dollars) === p.dollars ? ' selected' : ''}`} aria-pressed={Number(dollars) === p.dollars} onClick={() => preset(p)}>{p.label} · ${p.dollars}</button>
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
              {pct !== null && pct > 100 && (
                <p className="alert warn" role="status">
                  This cap is below the estimate, so the session will likely stop early, after a turn or two. Raise the cap or seat fewer agents.
                  {' '}<button type="button" className="btn sm" onClick={() => setDollars(String(suggested))}>Use suggested ${suggested}</button>
                </p>
              )}
              <p className="hint">A planning estimate, not a quote. The session stops before a turn that would cross the dollar cap.</p>
            </div>
          </section>

          {touched && problems.length > 0 && (
            <div className="alert warn" role="alert">
              <ul>{problems.map((p) => <li key={p}>{p}</li>)}</ul>
            </div>
          )}
          {submitError && <div className="alert bad" role="alert">{submitError}</div>}

          <button type="button" className="btn primary lg block start-main" disabled={submitting || !!seatsReason} aria-describedby={seatsReason ? 'start-reason' : undefined} onClick={submit}>
            {submitting ? 'Starting…' : 'Start session'}
          </button>
          {seatsReason && <p id="start-reason" className="hint start-reason">{seatsReason}</p>}
        </aside>
      </div>

      <div className="setup-bar">
        <span className="setup-bar-text" role="status">
          {roster.length} {roster.length === 1 ? 'seat' : 'seats'}, about ${estimate.toFixed(2)} / ${Number.isFinite(cap) ? cap : 0} cap
          {seatsReason && <span className="hint"> {seatsReason}</span>}
        </span>
        <button type="button" className="btn primary" disabled={submitting || !!seatsReason} onClick={submit}>Start</button>
      </div>

      {agentDialog && (
        <AgentDialog
          initial={agentDialog.edit ? { name: agentDialog.edit.name, stance: agentDialog.edit.stance, instructions: agentDialog.edit.system_prompt, model: agentDialog.edit.model } : undefined}
          models={catalog}
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
