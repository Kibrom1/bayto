import { ApiError, parseError } from './errors'

export interface Attachment {
  id: string
  kind: 'file' | 'folder' | 'url'
  name: string
  source: string | null
  chars: number
  truncated: boolean
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

export const attachFile = (taskId: string, file: File) =>
  fetch(`/tasks/${taskId}/attachments/file?name=${encodeURIComponent(file.name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file,
  }).then((r) => ok<Attachment>(r))

export const attachFolder = (taskId: string, path: string) =>
  fetch(`/tasks/${taskId}/attachments/folder`, json({ path })).then((r) => ok<Attachment>(r))

export const attachUrl = (taskId: string, url: string) =>
  fetch(`/tasks/${taskId}/attachments/url`, json({ url })).then((r) => ok<Attachment>(r))

export const removeAttachment = (taskId: string, id: string) =>
  fetch(`/tasks/${taskId}/attachments/${id}`, { method: 'DELETE' }).then((r) => ok<null>(r))
