import { useEffect, useState } from 'react'
import { Modal, ConfirmDialog } from './Dialog'
import { ContextCard } from './ContextCard'
import { TableRing } from './TableRing'
import { initials } from '../agentColor'
import { modelLabel, type ModelCatalog } from '../api/models'
import {
  addParticipant, getAgentTemplates, muteParticipant, removeParticipant, unmuteParticipant,
  type SessionDetailOut, type TurnCountOut,
} from '../api/sessions'
import './RoomRail.css'

type Tab = 'team' | 'context' | 'usage'
const fmt = (n: number) => n.toLocaleString('en-US')

export interface RoomRailProps {
  detail: SessionDetailOut
  sessionId: string
  /** Mobile only: the bottom sheet is shown. Desktop always shows the rail. */
  railOpen?: boolean
  onRailClose?: () => void
  display: (role: string) => { name: string; stance: string | null }
  hue: (role: string) => number | string
  live: Record<string, unknown>
  catalog: ModelCatalog | null
  /** Moderator summary text, already stripped of protocol lines. */
  summary?: string | null
  /** Context can be added and seats changed. */
  canEdit: boolean
  /** Toast through the room's flash helper. */
  flash: (text: string, isError?: boolean) => void
  /** Reload the session after a seat changes. */
  onChanged?: () => void
}

export function RoomRail({ detail, sessionId, railOpen = false, onRailClose, display, hue, live, catalog, summary, canEdit, flash, onChanged }: RoomRailProps) {
  const roster = detail.turn_counts ?? []
  const [tab, setTab] = useState<Tab>('team')
  const [ctxCount, setCtxCount] = useState(0)
  const [muted, setMuted] = useState<Record<string, boolean>>({})
  const [gone, setGone] = useState<Set<string>>(new Set())
  const [menu, setMenu] = useState<string | null>(null)
  const [removing, setRemoving] = useState<TurnCountOut | null>(null)
  const [adding, setAdding] = useState(false)
  const liveEntries = Object.keys(live)

  useEffect(() => {
    if (!railOpen || !onRailClose) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !document.querySelector('dialog[open]')) onRailClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [railOpen, onRailClose])

  const tokens = detail.usage.tokens_in + detail.usage.tokens_out
  const budgetTokens: number | null = detail.budget?.tokens ?? detail.budget?.max_tokens ?? null
  const pct = budgetTokens ? Math.min(100, Math.round((tokens / budgetTokens) * 100)) : null
  const costCap: number | null = detail.budget?.dollars ?? detail.budget?.max_cost ?? null
  const costPct = costCap ? Math.min(100, Math.round((detail.usage.cost / costCap) * 100)) : null

  const visible = roster.filter((tc) => !gone.has(tc.agent_id))
  const isMuted = (tc: TurnCountOut) => muted[tc.agent_id] ?? tc.muted ?? false
  const roles = visible.map((tc) => tc.participant).filter((p): p is string => !!p)

  async function toggleMute(tc: TurnCountOut) {
    const name = display(tc.participant ?? 'unknown').name
    const next = !isMuted(tc)
    setMenu(null)
    try {
      await (next ? muteParticipant : unmuteParticipant)(sessionId, tc.agent_id)
      setMuted((m) => ({ ...m, [tc.agent_id]: next }))
      flash(next ? `${name} muted. They stay in the room but won't speak.` : `${name} unmuted`)
      onChanged?.()
    } catch (e) { flash(e instanceof Error ? e.message : 'Could not change that seat', true) }
  }

  async function confirmRemove() {
    const tc = removing
    setRemoving(null)
    if (!tc) return
    const name = display(tc.participant ?? 'unknown').name
    try {
      await removeParticipant(sessionId, tc.agent_id)
      setGone((g) => new Set(g).add(tc.agent_id))
      flash(`${name} removed from this session`)
      onChanged?.()
    } catch (e) { flash(e instanceof Error ? e.message : 'Could not remove that seat', true) }
  }

  const tabs: { id: Tab; label: string; badge?: number }[] = [
    { id: 'team', label: 'Team' },
    ...(canEdit ? [{ id: 'context' as Tab, label: 'Context', badge: ctxCount }] : []),
    { id: 'usage', label: 'Usage' },
  ]
  const active = tabs.some((t) => t.id === tab) ? tab : 'team'

  return (
    <aside id="room-rail" className={`rail room-rail${railOpen ? ' open' : ''}`} aria-label="Moderator panel" data-tab={active} tabIndex={0}>
      <div className="rail-sheet-head">
        <div role="tablist" aria-label="Team, context and usage" className="rail-tabs">
          {tabs.map((t) => (
            <button key={t.id} type="button" role="tab" id={`rail-tab-${t.id}`} aria-selected={active === t.id} aria-controls={`rail-panel-${t.id}`}
              className="rail-tab" onClick={() => setTab(t.id)}>
              {t.label}{t.badge ? <span className="rail-badge" aria-label={`, ${t.badge} attached`}>{t.badge}</span> : null}
            </button>
          ))}
        </div>
        {onRailClose && <button type="button" className="btn ghost rail-close" onClick={onRailClose}>Close</button>}
      </div>

      <section className="rail-block rail-panel" id="rail-panel-team" data-panel="team" aria-label="Roster">
        <div className="roster-head">
          <h2>Roster</h2>
          {canEdit && <button type="button" className="btn sm" onClick={() => setAdding(true)}>Add agent</button>}
        </div>
        {visible.length === 0 && <p className="hint">Agents appear here once the session starts.</p>}
        {visible.length > 0 && (
          <TableRing
            size={132}
            seats={visible.map((tc) => {
              const role = tc.participant ?? 'unknown'
              return { key: tc.agent_id, initials: initials(display(role).name), hue: Number(hue(role)), active: role in live, label: display(role).name }
            })}
          >
            {liveEntries.length > 0 ? `${display(liveEntries[0]).name} has the floor` : `Round ${detail.round}`}
          </TableRing>
        )}
        {visible.map((tc) => {
          const role = tc.participant ?? 'unknown'
          const info = display(role)
          const m = isMuted(tc)
          return (
            <div className={`roster-item${m ? ' is-muted' : ''}`} key={tc.agent_id} style={{ ['--agent-h' as string]: hue(role) }}>
              <span className="avatar" aria-hidden="true">{initials(info.name)}</span>
              <div className="roster-text">
                <div className="roster-name">{info.name}{m && <span className="chip muted-chip">Muted</span>}</div>
                <div className="roster-sub">
                  {[info.stance, tc.model ? modelLabel(catalog, tc.model) : null, `${tc.turns} ${tc.turns === 1 ? 'turn' : 'turns'}`]
                    .filter(Boolean).map((part, i) => <span className="roster-part" key={i}>{i > 0 && <span aria-hidden="true"> · </span>}{part}</span>)}
                </div>
              </div>
              {role in live && <span className="speaking" role="img" aria-label="Speaking now" />}
              {canEdit && (
                <div className="roster-menu">
                  <button type="button" className="kebab" aria-label={`More actions for ${info.name}`} aria-haspopup="menu" aria-expanded={menu === tc.agent_id}
                    onClick={() => setMenu(menu === tc.agent_id ? null : tc.agent_id)}>
                    <span aria-hidden="true">⋯</span>
                  </button>
                  {menu === tc.agent_id && (
                    <div className="roster-menu-list" role="menu" aria-label={`Actions for ${info.name}`} onKeyDown={(e) => { if (e.key === 'Escape') setMenu(null) }}>
                      <button type="button" role="menuitem" autoFocus onClick={() => toggleMute(tc)}>{m ? 'Unmute' : 'Mute'}</button>
                      <button type="button" role="menuitem" className="danger" onClick={() => { setMenu(null); setRemoving(tc) }}>Remove</button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </section>

      {canEdit && (
        <div className="rail-panel" id="rail-panel-context" data-panel="context">
          <ContextCard taskId={detail.task_id} sessionId={sessionId} roles={roles} onCount={setCtxCount} />
        </div>
      )}

      <div className="rail-block rail-panel" id="rail-panel-usage" data-panel="usage">
        {summary && (
          <div className="mod-card">
            <h2>Moderator summary</h2>
            <p className="summary">{summary}</p>
          </div>
        )}
        <h2>Usage vs budget</h2>
        {pct !== null && (
          <div className="meter" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Token budget used">
            <div className={`meter-fill${pct >= 100 ? ' over' : pct >= 80 ? ' warn' : ''}`} style={{ width: `${pct}%` }} />
          </div>
        )}
        {budgetTokens !== null && tokens > budgetTokens && <p className="hint">Past the suggested token budget. The session continues; only the dollar cap stops it.</p>}
        <div className="usage-line"><span>Tokens</span><strong>{fmt(tokens)}{budgetTokens ? ` / ${fmt(budgetTokens)} suggested` : ''}</strong></div>
        <div className="usage-line"><span>Cost</span><strong>${detail.usage.cost.toFixed(4)}{costCap ? ` / $${costCap.toFixed(2)} cap` : ''}</strong></div>
        {costPct !== null && (
          <div className="meter" role="progressbar" aria-valuenow={costPct} aria-valuemin={0} aria-valuemax={100} aria-label="Dollar cap used">
            <div className={`meter-fill cost${costPct >= 100 ? ' over' : costPct >= 80 ? ' warn' : ''}`} style={{ width: `${costPct}%` }} />
          </div>
        )}
      </div>

      {removing && (
        <ConfirmDialog
          title={`Remove ${display(removing.participant ?? 'unknown').name}?`}
          message="They leave this session for good and can't be added back to it. What they already said stays in the transcript. To pause them instead, use Mute."
          confirmLabel="Remove"
          cancelLabel="Keep"
          danger
          onCancel={() => setRemoving(null)}
          onConfirm={confirmRemove}
        />
      )}
      {adding && (
        <AddAgentDialog
          taken={roles}
          onClose={() => setAdding(false)}
          onPick={async (id, name) => {
            setAdding(false)
            try { await addParticipant(sessionId, id); flash(`${name} joined the table`); onChanged?.() }
            catch (e) { flash(e instanceof Error ? e.message : 'Could not add that agent', true) }
          }}
        />
      )}
    </aside>
  )
}

interface Template { id: string; role: string; name: string; stance?: string | null }

function AddAgentDialog({ taken, onClose, onPick }: { taken: string[]; onClose: () => void; onPick: (id: string, name: string) => void }) {
  const [list, setList] = useState<Template[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    getAgentTemplates().then((a) => setList(Array.isArray(a) ? a : [])).catch((e) => { setList([]); setErr(e instanceof Error ? e.message : String(e)) })
  }, [])
  const options = (list ?? []).filter((a) => !taken.includes(a.role))
  return (
    <Modal label="Add an agent" onClose={onClose}>
      <div className="modal-body">
        <h2>Add an agent</h2>
        <p className="hint" style={{ margin: 0 }}>They join from their next turn and are told what was said so far.</p>
        {list === null && <p className="hint" role="status">Loading agents…</p>}
        {err && <p className="alert bad" role="alert">{err}</p>}
        {list !== null && !err && options.length === 0 && <p className="hint">Everyone available is already at the table.</p>}
        <ul className="add-agent-list">
          {options.map((a) => (
            <li key={a.id}>
              <button type="button" className="btn" onClick={() => onPick(a.id, a.name)}>
                <strong>{a.name}</strong>{a.stance ? <span className="muted"> {a.stance}</span> : null}
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="modal-actions"><button type="button" className="btn" onClick={onClose}>Cancel</button></div>
    </Modal>
  )
}
