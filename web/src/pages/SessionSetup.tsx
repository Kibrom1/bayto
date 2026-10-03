import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { createSession, getAgentTemplates, getModes, type SessionCreateRequest, startSession } from '../api/sessions'
import './TaskBoard.css' // Reuse some styles for now

interface AgentTemplate {
  id: string
  name: string
  system_prompt: string
  stance: string
  model: string
}

interface Mode {
  id: string
  name: string
  phases_json: any
  turn_policy: string
  stop_rules_json: any
}

export function SessionSetup() {
  const { taskId } = useParams<{ taskId: string }>()
  const navigate = useNavigate()

  const [modes, setModes] = useState<Mode[] | null>(null)
  const [templates, setTemplates] = useState<AgentTemplate[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [selectedModeId, setSelectedModeId] = useState<string>('')
  const [selectedAgentIds, setSelectedAgentIds] = useState<string[]>([])
  const [budgetTokens, setBudgetTokens] = useState<string>('')
  const [budgetDollars, setBudgetDollars] = useState<string>('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    Promise.all([getModes(), getAgentTemplates()])
      .then(([m, t]) => {
        setModes(m)
        setTemplates(t)
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  function toggleAgent(id: string) {
    setSelectedAgentIds((prev) =>
      prev.includes(id) ? prev.filter(a => a !== id) : [...prev, id]
    )
  }

  async function handleSubmit() {
    if (!taskId || !selectedModeId || selectedAgentIds.length === 0) {
      setError('Please select a mode and at least one agent.')
      return
    }

    setSubmitting(true)
    setError(null)
    try {
      const roster = selectedAgentIds.map((id, index) => ({
        agent_id: id,
        seat_order: index,
      }))

      const req: SessionCreateRequest = {
        task_id: taskId,
        mode_id: selectedModeId,
        roster,
        budget: {
          tokens: budgetTokens ? parseInt(budgetTokens) : undefined,
          dollars: budgetDollars ? parseFloat(budgetDollars) : undefined,
        },
      }

      const { session } = await createSession(req)
      await startSession(session.id)

      // Navigate to the room
      navigate(`/room/${session.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  if (error) return <div className="task-board-error">{error}</div>
  if (!modes || !templates) return <p>Loading setup options…</p>

  const selectedMode = modes.find(m => m.id === selectedModeId)

  return (
    <section className="task-board">
      <header className="task-board-header">
        <h1>Session Setup</h1>
        <p>Task ID: {taskId}</p>
      </header>

      <div className="setup-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem', padding: '1rem' }}>
        <div className="setup-section">
          <h2 style={{ marginTop: 0 }}>Mode</h2>
          <select
            value={selectedModeId}
            onChange={(e) => setSelectedModeId(e.target.value)}
            style={{ width: '100%', padding: '0.5rem', marginBottom: '1rem' }}
          >
            <option value="">-- Select Mode --</option>
            {modes.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
          {selectedMode && (
            <div className="mode-details" style={{ fontSize: '0.9rem', color: '#666', marginBottom: '1rem' }}>
              <p>Policy: {selectedMode.turn_policy}</p>
            </div>
          )}

          <h2 style={{ marginTop: 0 }}>Roster</h2>
          <div className="agent-list" style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {templates.map(t => (
              <label key={t.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={selectedAgentIds.includes(t.id)}
                  onChange={() => toggleAgent(t.id)}
                />
                <span>{t.name} ({t.model})</span>
              </label>
            ))}
          </div>
        </div>

        <div className="setup-section">
          <h2 style={{ marginTop: 0 }}>Budget</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginBottom: '1rem' }}>
            <div>
              <label>Max Tokens: </label>
              <input
                type="number"
                value={budgetTokens}
                onChange={(e) => setBudgetTokens(e.target.value)}
                style={{ padding: '0.3rem' }}
              />
            </div>
            <div>
              <label>Max Dollars: </label>
              <input
                type="number"
                step="0.01"
                value={budgetDollars}
                onChange={(e) => setBudgetDollars(e.target.value)}
                style={{ padding: '0.3rem' }}
              />
            </div>
          </div>

          <div className="cost-estimate" style={{ padding: '1rem', backgroundColor: '#f9f9f9', borderRadius: '4px', border: '1px solid #ddd' }}>
            <h3 style={{ marginTop: 0 }}>Estimated Cost</h3>
            <p>Based on selected agents and mode defaults.</p>
            <p><strong>Estimate: ${ (selectedAgentIds.length * 0.05).toFixed(2) }</strong></p>
          </div>

          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            style={{
              marginTop: '2rem',
              padding: '1rem 2rem',
              backgroundColor: '#007bff',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: submitting ? 'not-allowed' : 'pointer',
              width: '100%'
            }}
          >
            {submitting ? 'Starting Session...' : 'Create and Start Session'}
          </button>
        </div>
      </div>
    </section>
  )
}
