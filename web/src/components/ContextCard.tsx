import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import {
  attachFile, attachFolder, attachUrl, listAttachments, removeAttachment, setAttachmentRoles, type Attachment,
} from '../api/attachments'
import { getRetrieval, setRetrieval, type RetrievalSetting } from '../api/tasks'
import { RolePicker } from './RolePicker'

export const RETRIEVAL_LABEL = 'Search long documents by meaning'
const KIND_LABEL = { file: 'File', folder: 'Folder', url: 'Link' } as const
// What one turn can carry for all attachments together; mirrors the orchestrator's cap.
const PROMPT_BUDGET_CHARS = 40_000
// Under this an attachment is always sent whole, so counting its passages would only add noise.
const WHOLE_BELOW_CHARS = 4_000

type Adding = null | 'folder' | 'url'

function size(chars: number) {
  return chars >= 1000 ? `${(chars / 1000).toFixed(chars >= 10_000 ? 0 : 1)}k characters` : `${chars} characters`
}

// Reference material for a task. Agents read a text snapshot taken when it is attached; they never get the
// folder, the file or the network.
// `roles` are the seats in this session; an attachment can be limited to some of them ("the contract is for the lawyer only").
export function ContextCard({ taskId, sessionId, roles = [], onCount }: { taskId: string; sessionId: string; roles?: string[]; onCount?: (n: number) => void }) {
  const [items, setItems] = useState<Attachment[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState<Adding>(null)
  const [value, setValue] = useState('')
  const [picking, setPicking] = useState<string | null>(null) // the attachment whose visibility is open
  const [chosen, setChosen] = useState<Set<string>>(new Set()) // attachments set to "chosen roles" before any role is ticked
  const [openWho, setOpenWho] = useState(false) // the "who can read what I add next" picker
  const [newRoles, setNewRoles] = useState<string[] | null>(null) // null: everyone
  const [newChosen, setNewChosen] = useState(false) // "Only chosen roles" picked, nothing ticked yet
  const [retrieval, setRetrievalState] = useState<RetrievalSetting | null>(null) // null: unknown, the switch is hidden
  const fileRef = useRef<HTMLInputElement>(null)
  const inputId = useId()

  useEffect(() => {
    listAttachments(taskId).then((list) => setItems(Array.isArray(list) ? list : [])).catch((e) => { setItems([]); setError(e instanceof Error ? e.message : String(e)) })
  }, [taskId])

  useEffect(() => { if (items) onCount?.(items.length) }, [items]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let live = true
    ;(async () => { try { const r = await getRetrieval(taskId); if (live) setRetrievalState(r) } catch { /* switch stays hidden */ } })()
    return () => { live = false }
  }, [taskId])

  async function toggleSemantic(on: boolean) {
    setError(null)
    try {
      setRetrievalState(await setRetrieval(taskId, on))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  // The roles to send with an add: none for Everyone. "Only chosen roles" with nothing ticked is refused here
  // (the API would answer 422 roles_invalid) so nothing is added by mistake.
  function rolesForAdd(): { roles: string[] | null } | null {
    if (newChosen && !newRoles?.length) { setError('Tick at least one role, or choose Everyone.'); setOpenWho(true); return null }
    return { roles: newRoles?.length ? newRoles : null }
  }

  function resetWho() { setNewRoles(null); setNewChosen(false); setOpenWho(false) }

  async function run(job: () => Promise<Attachment>): Promise<boolean> {
    setBusy(true); setError(null)
    try {
      const made = await job()
      setItems((cur) => [...(cur ?? []), made])
      setAdding(null); setValue('')
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return false
    } finally {
      setBusy(false)
    }
  }

  async function onFiles(files: FileList | null) {
    const list = Array.from(files ?? [])
    if (fileRef.current) fileRef.current.value = ''
    const who = rolesForAdd()
    if (!who || list.length === 0) return
    let all = true
    for (const f of list) all = (await run(() => attachFile(taskId, f, sessionId, who.roles))) && all
    if (all) resetWho()
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const v = value.trim()
    if (!v) return
    const who = rolesForAdd()
    if (!who) return
    if (await run(() => (adding === 'folder' ? attachFolder(taskId, v, sessionId, who.roles) : attachUrl(taskId, v, sessionId, who.roles)))) resetWho()
  }

  function toggleNewRole(role: string) {
    setError(null)
    setNewRoles((cur) => { const now = cur ?? []; return now.includes(role) ? now.filter((r) => r !== role) : [...now, role] })
  }

  async function remove(a: Attachment) {
    setError(null)
    try {
      await removeAttachment(taskId, a.id, sessionId)
      setItems((cur) => (cur ?? []).filter((x) => x.id !== a.id))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function setRoles(a: Attachment, next: string[] | null) {
    setError(null)
    try {
      const saved = await setAttachmentRoles(taskId, a.id, next, sessionId)
      setItems((cur) => (cur ?? []).map((x) => (x.id === a.id ? saved : x)))
      setChosen((cur) => { const n = new Set(cur); if (next === null) n.delete(a.id); return n })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  function toggleRole(a: Attachment, role: string) {
    const now = a.roles ?? []
    const next = now.includes(role) ? now.filter((r) => r !== role) : [...now, role]
    if (next.length === 0) { setError('Keep at least one role, or choose Everyone.'); return }
    setRoles(a, next)
  }

  const nameCount = new Map<string, number>()
  for (const a of items ?? []) nameCount.set(a.name, (nameCount.get(a.name) ?? 0) + 1)
  const total = (items ?? []).reduce((n, a) => n + a.chars, 0)
  const perTurn = Math.min(total, PROMPT_BUDGET_CHARS)

  return (
    <section className="rail-block context-card" aria-labelledby="context-h">
      <h2 id="context-h">Context</h2>
      <p className="hint">Add a document, folder or web page while the team talks. The team is told, and every agent reads it
        from its next turn. They read a text copy taken now, and can't reach the folder or the web themselves.</p>

      {items && items.length > 0 && (
        <ul className="context-list">
          {items.map((a) => {
            const audience = a.roles?.length ? `${a.roles.join(', ')} only` : 'everyone'
            return (
              <li key={a.id}>
                <span className="chip">{KIND_LABEL[a.kind]}</span>
                <span className="context-name" title={a.source ?? a.name}>{a.name}</span>
                <span className="muted">{size(a.chars)}{a.passages && a.chars > WHOLE_BELOW_CHARS ? ` · ${a.passages} passages` : ''}{a.truncated ? ' · shortened' : ''}</span>
                {(nameCount.get(a.name) ?? 0) > 1 && <span className="context-dup" role="note">Another attachment has this name. Agents may mix them up.</span>}
                <button type="button" className={`audience-chip${a.roles?.length ? ' restricted' : ''}`}
                  aria-label={`Visible to: ${audience}. Change who can read ${a.name}`} aria-expanded={picking === a.id}
                  onClick={() => setPicking(picking === a.id ? null : a.id)}>
                  {a.roles?.length ? <svg className="lock" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false"><path d="M4 7V5a4 4 0 118 0v2h1v7H3V7h1zm2 0h4V5a2 2 0 10-4 0v2z" fill="currentColor" /></svg> : null}
                  Visible to: {audience}
                </button>
                <button type="button" className="link-btn" aria-label={`Remove ${a.name}`} onClick={() => remove(a)}>Remove</button>
                {picking === a.id && (
                  <RolePicker name={`who-${a.id}`} legend={`Who can read ${a.name}`} roles={roles} value={a.roles ?? null} chosen={chosen.has(a.id)}
                    onEveryone={() => setRoles(a, null)} onChoose={() => setChosen((cur) => new Set(cur).add(a.id))} onToggle={(r) => toggleRole(a, r)} />
                )}
              </li>
            )
          })}
        </ul>
      )}

      <div className="context-actions">
        <input ref={fileRef} type="file" multiple hidden accept=".txt,.md,.markdown,.pdf,.csv,.json,.yaml,.yml,.py,.js,.ts,.tsx,.html,.sql,.sh,.log" onChange={(e) => onFiles(e.target.files)} aria-label="Choose files to attach" />
        <button type="button" className="btn sm" disabled={busy} onClick={() => fileRef.current?.click()}>Add file</button>
        <button type="button" className="btn sm" disabled={busy} aria-expanded={adding === 'folder'} onClick={() => { setAdding(adding === 'folder' ? null : 'folder'); setValue(''); setError(null) }}>Add folder</button>
        <button type="button" className="btn sm" disabled={busy} aria-expanded={adding === 'url'} onClick={() => { setAdding(adding === 'url' ? null : 'url'); setValue(''); setError(null) }}>Add link</button>
      </div>

      <button type="button" className="link-btn" aria-expanded={openWho} onClick={() => setOpenWho(!openWho)}>
        {newRoles?.length ? `New items: only ${newRoles.join(', ')}` : newChosen ? 'New items: only chosen roles' : 'New items: everyone can read'}
      </button>
      {openWho && (
        <RolePicker name="who-new" legend="Who can read what you add next" roles={roles} value={newRoles?.length ? newRoles : null} chosen={newChosen}
          onEveryone={() => { setNewRoles(null); setNewChosen(false); setError(null) }} onChoose={() => setNewChosen(true)} onToggle={toggleNewRole} />
      )}

      {adding && (
        <form className="context-add" onSubmit={submit}>
          <label htmlFor={inputId}>{adding === 'folder' ? 'Folder path on this computer' : 'Web address'}</label>
          <div className="context-add-row">
            <input id={inputId} value={value} onChange={(e) => setValue(e.target.value)} autoFocus
              placeholder={adding === 'folder' ? '/Users/you/project/docs' : 'https://example.com/terms'}
              inputMode={adding === 'url' ? 'url' : undefined} />
            <button type="submit" className="btn primary sm" disabled={busy || !value.trim()}>{busy ? 'Adding…' : 'Add'}</button>
          </div>
          <span className="hint">{adding === 'folder'
            ? 'Text, Markdown and code files only. Hidden files, keys, passwords and dependency folders are left out.'
            : 'Public pages only. Addresses on this computer or your network are refused.'}</span>
        </form>
      )}

      {retrieval && (
        <div className="field retrieval-field">
          <label className="retrieval-label">
            <input type="checkbox" checked={retrieval.semantic_retrieval && retrieval.ready} disabled={!retrieval.ready}
              aria-describedby={`${inputId}-ret`} onChange={(e) => toggleSemantic(e.target.checked)} />{' '}
            {RETRIEVAL_LABEL}
          </label>
          <span className="hint" id={`${inputId}-ret`}>{retrieval.ready
            ? `Used only when the attachments are too long to send whole. ${retrieval.provider === 'ollama' ? 'Their text is sent to your Ollama host.' : 'Their text is sent to the OpenAI embeddings endpoint.'} Any failure falls back to keyword search.`
            : 'Not set up on this server. Keyword search is used instead.'}</span>
        </div>
      )}

      {busy && !adding && <p className="hint" role="status">Reading…</p>}
      {error && <p className="alert bad" role="alert">{error}</p>}
      {total > 0 && (
        <p className="hint">Every agent reads this on every turn, about {Math.round(perTurn / 4).toLocaleString('en-US')} tokens each time
          {total > PROMPT_BUDGET_CHARS ? '. There is more attached than fits, so each agent is shown the passages that best match its role and the discussion, and the rest is left out' : ''}.</p>
      )}
    </section>
  )
}
