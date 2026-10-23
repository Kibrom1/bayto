import { useId, useState, type FormEvent } from 'react'
import type { AgentDraft } from '../api/agents'
import type { ModelCatalog } from '../api/models'
import { Modal } from './Dialog'

interface Props {
  initial?: AgentDraft
  models?: ModelCatalog | null
  submitting: boolean
  error: string | null
  onSubmit: (draft: AgentDraft) => void
  onCancel: () => void
}

// Create or edit a custom agent. The permission line is fixed text, not a control: the server decides what a
// custom agent may do, and today that is one preset.
export function AgentDialog({ initial, models, submitting, error, onSubmit, onCancel }: Props) {
  const [name, setName] = useState(initial?.name ?? '')
  const [stance, setStance] = useState(initial?.stance ?? '')
  const [instructions, setInstructions] = useState(initial?.instructions ?? '')
  const [model, setModel] = useState(models?.models.some((m) => m.id === initial?.model) ? initial?.model ?? '' : '')
  const modelId = useId()
  const nameId = useId()
  const stanceId = useId()
  const instrId = useId()
  const editing = !!initial
  const ok = name.trim().length >= 2 && stance.trim() !== '' && instructions.trim() !== ''

  function submit(e: FormEvent) {
    e.preventDefault()
    onSubmit({ name: name.trim(), stance: stance.trim(), instructions: instructions.trim(), model })
  }

  return (
    <Modal label={editing ? 'Edit agent' : 'Create agent'} onClose={() => { if (!submitting) onCancel() }}>
      <form onSubmit={submit}>
        <div className="modal-body">
          <h2>{editing ? 'Edit agent' : 'Create agent'}</h2>
          <p className="hint" style={{ margin: '0 0 var(--space-4)' }}>
            A specialist for your question, such as a compliance officer or a customer from your own market.
          </p>
          <div className="field">
            <label htmlFor={nameId}>Name</label>
            <input id={nameId} value={name} onChange={(e) => setName(e.target.value)} required maxLength={60} autoFocus placeholder="Compliance Officer" />
          </div>
          <div className="field">
            <label htmlFor={stanceId}>Stance</label>
            <input id={stanceId} value={stance} onChange={(e) => setStance(e.target.value)} required maxLength={60} placeholder="risk-averse" />
            <span className="hint">How the agent pushes back: pragmatist, adversarial, cost-conscious…</span>
          </div>
          <div className="field">
            <label htmlFor={instrId}>Instructions</label>
            <textarea id={instrId} value={instructions} onChange={(e) => setInstructions(e.target.value)} required rows={6} maxLength={4000}
              placeholder="What this agent knows, what it cares about, and how it should argue." />
          </div>
          {models && models.models.length > 0 && (
            <div className="field">
              <label htmlFor={modelId}>Default model</label>
              <select id={modelId} value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">Standard ({models.models.find((m) => m.id === models.default)?.label ?? 'default'})</option>
                {models.models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
              <span className="hint">Used whenever this agent is seated. You can still pick a different model for a seat in a session.</span>
            </div>
          )}
          <div className="field">
            <span className="label">Permissions</span>
            <span className="hint"><strong>Discussion only.</strong> Takes part in the conversation and can read the project folder. It cannot change files, run commands or use the network.</span>
          </div>
          {error && <p role="alert" className="alert bad" style={{ margin: 0 }}>{error}</p>}
        </div>
        <div className="modal-actions">
          {!ok && !submitting && <span className="hint" id="agent-hint">Add a name, a stance and instructions.</span>}
          <button type="button" className="btn" onClick={onCancel} disabled={submitting}>Cancel</button>
          <button type="submit" className="btn primary" disabled={!ok || submitting} aria-describedby={ok ? undefined : 'agent-hint'}>
            {submitting ? 'Saving…' : editing ? 'Save changes' : 'Create agent'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
