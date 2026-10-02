import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { listTasks, type Task } from '../api/tasks'
import './SessionSetup.css'

type AgentTemplate = {
  id: string
  name: string
  role: string
  model: string
  stance: string
  summary: string
}

type ModeOption = {
  id: string
  label: string
  description: string
  sample: string
}

const agentTemplates: AgentTemplate[] = [
  {
    id: 'researcher',
    name: 'Researcher',
    role: 'Fact gathering and source work',
    model: 'claude-sonnet',
    stance: 'Neutral',
    summary: 'Finds prior art, checks citations and surfaces missing evidence.',
  },
  {
    id: 'product-owner',
    name: 'Product Owner',
    role: 'Requirements and prioritization',
    model: 'claude-sonnet',
    stance: 'Advocate',
    summary: 'Keeps the task grounded in user value and delivery trade-offs.',
  },
  {
    id: 'architect',
    name: 'Architect',
    role: 'System design and trade-offs',
    model: 'claude-sonnet',
    stance: 'Balanced',
    summary: 'Designs the cleanest path with the right constraints and interfaces.',
  },
  {
    id: 'qa',
    name: 'QA Tester',
    role: 'Risk checking and validation',
    model: 'claude-haiku',
    stance: 'Skeptic',
    summary: 'Looks for failure modes, edge cases and regressions before launch.',
  },
  {
    id: 'security',
    name: 'Security Reviewer',
    role: 'Risk and control review',
    model: 'claude-sonnet',
    stance: 'Devil’s advocate',
    summary: 'Questions assumptions around trust, permissions and safety.',
  },
  {
    id: 'frontend',
    name: 'Frontend Engineer',
    role: 'UI and interaction design',
    model: 'claude-sonnet',
    stance: 'Builder',
    summary: 'Turns the team’s decisions into an understandable product flow.',
  },
]

const modeOptions: ModeOption[] = [
  {
    id: 'debate',
    label: 'Debate',
    description: 'Two or more sides argue fixed positions and converge on a verdict.',
    sample: 'Best when trade-offs need active disagreement.',
  },
  {
    id: 'open-chat',
    label: 'Open chat',
    description: 'Free form discussion with moderated turn-taking and summaries.',
    sample: 'Fastest for brainstorming and low-structure planning.',
  },
  {
    id: 'brainstorm',
    label: 'Brainstorm',
    description: 'Diverge first, then cluster and rank the strongest ideas.',
    sample: 'Good for idea generation and prioritization.',
  },
]

function formatCurrency(value: number) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value)
}

function formatNumber(value: number) {
  return new Intl.NumberFormat('en-US').format(value)
}

export function SessionSetup() {
  const { taskId } = useParams()
  const navigate = useNavigate()
  const [task, setTask] = useState<Task | null>(null)
  const [taskError, setTaskError] = useState<string | null>(null)
  const [selectedAgents, setSelectedAgents] = useState<AgentTemplate[]>([agentTemplates[0], agentTemplates[1]])
  const [mode, setMode] = useState('debate')
  const [rounds, setRounds] = useState(4)
  const [budgetTokens, setBudgetTokens] = useState(25000)

  useEffect(() => {
    if (!taskId) {
      return
    }

    listTasks()
      .then((tasks) => {
        const match = tasks.find((candidate) => candidate.id === taskId)
        setTask(match ?? null)
      })
      .catch((err: unknown) => {
        setTaskError(err instanceof Error ? err.message : String(err))
      })
  }, [taskId])

  const selectedIds = useMemo(
    () => new Set(selectedAgents.map((agent) => agent.id)),
    [selectedAgents],
  )

  const estimate = useMemo(() => {
    const tokens = selectedAgents.length * rounds * 2400
    const cost = (tokens / 1000) * 0.015
    return { tokens, cost }
  }, [selectedAgents, rounds])

  const budgetUsagePercent = budgetTokens > 0 ? Math.min((estimate.tokens / budgetTokens) * 100, 100) : 0
  const nearBudgetLimit = budgetUsagePercent >= 80

  function addAgent(agent: AgentTemplate) {
    setSelectedAgents((current) => {
      if (current.some((item) => item.id === agent.id)) {
        return current
      }

      return [...current, agent]
    })
  }

  function removeAgent(agentId: string) {
    setSelectedAgents((current) => current.filter((agent) => agent.id !== agentId))
  }

  function handleStartSession() {
    if (!taskId) {
      return
    }

    const sessionId = `${taskId}-demo-${Date.now()}`
    navigate(`/sessions/${sessionId}`)
  }

  const chosenMode = modeOptions.find((item) => item.id === mode) ?? modeOptions[0]

  return (
    <main className="session-setup">
      <div className="session-setup-header">
        <div>
          <p className="eyebrow">Session setup</p>
          <h1>{task?.title ?? 'Selected task'}</h1>
        </div>
        <button type="button" className="secondary-action" onClick={() => window.history.back()}>
          Back to tasks
        </button>
      </div>

      {taskError && (
        <p role="alert" className="session-error">
          Couldn't load task: {taskError}
        </p>
      )}

      <div className="session-setup-layout">
        <section className="session-panel">
          <div className="panel-heading">
            <h2>Agent explorer</h2>
            <span>{agentTemplates.length} templates</span>
          </div>

          <div className="agent-grid">
            {agentTemplates.map((agent) => {
              const added = selectedIds.has(agent.id)

              return (
                <article key={agent.id} className="agent-card">
                  <div className="agent-meta-top">
                    <span className="stance-badge">{agent.stance}</span>
                    <span className="model-pill">{agent.model}</span>
                  </div>

                  <h3>{agent.name}</h3>
                  <p className="agent-role">{agent.role}</p>
                  <p className="agent-summary">{agent.summary}</p>

                  <button
                    type="button"
                    className="agent-button"
                    onClick={() => addAgent(agent)}
                    disabled={added}
                  >
                    {added ? 'Added to roster' : 'Add to roster'}
                  </button>
                </article>
              )
            })}
          </div>
        </section>

        <section className="session-panel session-panel-right">
          <div className="panel-heading">
            <h2>Session options</h2>
            <span>{selectedAgents.length} seated</span>
          </div>

          <div className="field-group">
            <label htmlFor="mode-select">Mode</label>
            <div className="mode-grid" id="mode-select">
              {modeOptions.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  className={option.id === mode ? 'mode-option active' : 'mode-option'}
                  onClick={() => setMode(option.id)}
                >
                  <strong>{option.label}</strong>
                  <span>{option.description}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="field-grid">
            <div className="field-group">
              <label htmlFor="rounds">Rounds</label>
              <input
                id="rounds"
                type="number"
                min={1}
                max={10}
                value={rounds}
                onChange={(event) => setRounds(Math.max(1, Number(event.target.value) || 1))}
              />
            </div>

            <div className="field-group">
              <label htmlFor="budget">Budget cap (tokens)</label>
              <input
                id="budget"
                type="number"
                min={1000}
                step={1000}
                value={budgetTokens}
                onChange={(event) => setBudgetTokens(Math.max(1000, Number(event.target.value) || 1000))}
              />
            </div>
          </div>

          <div className="selected-roster">
            <div className="roster-header">
              <h3>Selected roster</h3>
              <span>{selectedAgents.length} agents</span>
            </div>

            {selectedAgents.length === 0 ? (
              <p className="empty-state">Add agents to build a session roster.</p>
            ) : (
              <ul>
                {selectedAgents.map((agent) => (
                  <li key={agent.id}>
                    <div>
                      <strong>{agent.name}</strong>
                      <small>{agent.role}</small>
                    </div>
                    <button type="button" onClick={() => removeAgent(agent.id)}>
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className={`estimate-box ${nearBudgetLimit ? 'warning' : ''}`}>
            <div className="estimate-topline">
              <span>Estimated cost</span>
              <strong>{formatCurrency(estimate.cost)}</strong>
            </div>
            <p>{formatNumber(estimate.tokens)} tokens · {chosenMode.sample}</p>
            <div className="estimate-meter" aria-live="polite">
              <div className="estimate-meter-track">
                <span style={{ width: `${budgetUsagePercent}%` }} />
              </div>
              <small>{Math.round(budgetUsagePercent)}% of budget cap</small>
            </div>
            <small>Budget cap: {formatNumber(budgetTokens)} tokens</small>
          </div>

          <button
            type="button"
            className="primary-action"
            disabled={selectedAgents.length === 0}
            onClick={handleStartSession}
          >
            {selectedAgents.length === 0 ? 'Add an agent to start' : 'Start session'}
          </button>
        </section>
      </div>
    </main>
  )
}
