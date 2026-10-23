import { ApiError, parseError } from './errors'

export interface Attachment {
  id: string
  kind: 'file' | 'folder' | 'url'
  name: string
  source: string | null
  chars: number
  truncated: boolean
  roles?: string[] | null // null or missing: everyone can read it
  created_at: string
}

async function ok<T>(res: Response): Promise<T> {
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  return res.status === 204 ? (null as T) : res.json()
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const listAttachments = (taskId: string) =>
  fetch(`/tasks/${taskId}/attachments`).then((r) => ok<Attachment[]>(r))

// Context is added and removed only from inside a running room: the team is told, and the record names the session.
const room = (sessionId: string) => (sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : '')
const roomOnly = (sessionId: string) => (sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '')

export const attachFile = (taskId: string, file: File, sessionId: string) =>
  fetch(`/tasks/${taskId}/attachments/file?name=${encodeURIComponent(file.name)}${room(sessionId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file,
  }).then((r) => ok<Attachment>(r))

export const attachFolder = (taskId: string, path: string, sessionId: string) =>
  fetch(`/tasks/${taskId}/attachments/folder${roomOnly(sessionId)}`, json({ path })).then((r) => ok<Attachment>(r))

export const attachUrl = (taskId: string, url: string, sessionId: string) =>
  fetch(`/tasks/${taskId}/attachments/url${roomOnly(sessionId)}`, json({ url })).then((r) => ok<Attachment>(r))

export const removeAttachment = (taskId: string, id: string, sessionId: string) =>
  fetch(`/tasks/${taskId}/attachments/${id}${roomOnly(sessionId)}`, { method: 'DELETE' }).then((r) => ok<null>(r))

// Who can read it: null for everyone, or the roles that can. Takes effect on each agent's next turn.
export const setAttachmentRoles = (taskId: string, id: string, roles: string[] | null, sessionId: string) =>
  fetch(`/tasks/${taskId}/attachments/${id}${roomOnly(sessionId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ roles }),
  }).then((r) => ok<Attachment>(r))
