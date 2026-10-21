import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import {
  attachFile, attachFolder, attachUrl, listAttachments, removeAttachment, type Attachment,
} from '../api/attachments'

const KIND_LABEL = { file: 'File', folder: 'Folder', url: 'Link' } as const
// What one turn can carry for all attachments together; mirrors the orchestrator's cap.
const PROMPT_BUDGET_CHARS = 40_000

type Adding = null | 'folder' | 'url'

function size(chars: number) {
  return chars >= 1000 ? `${(chars / 1000).toFixed(chars >= 10_000 ? 0 : 1)}k characters` : `${chars} characters`
}

// Reference material for a task. Agents read a text snapshot taken when it is attached; they never get the
// folder, the file or the network.
export function ContextCard({ taskId, sessionId }: { taskId: string; sessionId: string }) {
  const [items, setItems] = useState<Attachment[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState<Adding>(null)
  const [value, setValue] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const inputId = useId()

  useEffect(() => {
    listAttachments(taskId).then((list) => setItems(Array.isArray(list) ? list : [])).catch((e) => { setItems([]); setError(e instanceof Error ? e.message : String(e)) })
  }, [taskId])

  async function run(job: () => Promise<Attachment>) {
    setBusy(true); setError(null)
    try {
      const made = await job()
      setItems((cur) => [...(cur ?? []), made])
      setAdding(null); setValue('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function onFiles(files: FileList | null) {
    const list = Array.from(files ?? [])
    if (fileRef.current) fileRef.current.value = ''
    for (const f of list) await run(() => attachFile(taskId, f, sessionId))
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    const v = value.trim()
    if (!v) return
    run(() => (adding === 'folder' ? attachFolder(taskId, v, sessionId) : attachUrl(taskId, v, sessionId)))
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

  const total = (items ?? []).reduce((n, a) => n + a.chars, 0)
  const perTurn = Math.min(total, PROMPT_BUDGET_CHARS)

  return (
    <section className="rail-block" aria-labelledby="context-h">
      <h2 id="context-h">Context</h2>
      <p className="hint">Add a document, folder or web page while the team talks. The team is told, and every agent reads it
        from its next turn. They read a text copy taken now, and can't reach the folder or the web themselves.</p>

      {items && items.length > 0 && (
        <ul className="context-list">
          {items.map((a) => (
            <li key={a.id}>
              <span className="chip">{KIND_LABEL[a.kind]}</span>
              <span className="context-name" title={a.source ?? a.name}>{a.name}</span>
              <span className="muted">{size(a.chars)}{a.truncated ? ' · shortened' : ''}</span>
              <button type="button" className="link-btn" aria-label={`Remove ${a.name}`} onClick={() => remove(a)}>Remove</button>
            </li>
          ))}
        </ul>
      )}

      <div className="context-actions">
        <input ref={fileRef} type="file" multiple hidden accept=".txt,.md,.markdown,.pdf,.csv,.json,.yaml,.yml,.py,.js,.ts,.tsx,.html,.sql,.sh,.log" onChange={(e) => onFiles(e.target.files)} aria-label="Choose files to attach" />
        <button type="button" className="btn sm" disabled={busy} onClick={() => fileRef.current?.click()}>Add file</button>
        <button type="button" className="btn sm" disabled={busy} aria-expanded={adding === 'folder'} onClick={() => { setAdding(adding === 'folder' ? null : 'folder'); setValue(''); setError(null) }}>Add folder</button>
        <button type="button" className="btn sm" disabled={busy} aria-expanded={adding === 'url'} onClick={() => { setAdding(adding === 'url' ? null : 'url'); setValue(''); setError(null) }}>Add link</button>
      </div>

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

      {busy && !adding && <p className="hint" role="status">Reading…</p>}
      {error && <p className="alert bad" role="alert">{error}</p>}
      {total > 0 && (
        <p className="hint">Every agent reads this on every turn, about {Math.round(perTurn / 4).toLocaleString('en-US')} tokens each time
          {total > PROMPT_BUDGET_CHARS ? '. There is more attached than fits, so the longest items are cut' : ''}.</p>
      )}
    </section>
  )
}
