import { ApiError, parseError } from './errors'

export interface AuditEvent {
  seq: number
  at: string
  actor: string
  action: string
  summary: string
  task_id: string | null
  session_id: string | null
  subject_type: string | null
  subject_id: string | null
  detail: Record<string, unknown>
}

export interface AuditFilter {
  action?: string
  sessionId?: string
  taskId?: string
}

function qs(f: AuditFilter, extra: Record<string, string> = {}) {
  const p = new URLSearchParams(extra)
  if (f.action) p.set('action', f.action)
  if (f.sessionId) p.set('session_id', f.sessionId)
  if (f.taskId) p.set('task_id', f.taskId)
  const s = p.toString()
  return s ? `?${s}` : ''
}

export async function listAudit(f: AuditFilter, before?: number, limit = 50): Promise<AuditEvent[]> {
  const res = await fetch(`/audit${qs(f, { limit: String(limit), ...(before ? { before: String(before) } : {}) })}`)
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  return res.json()
}

export async function verifyAudit(): Promise<{ ok: boolean; checked: number; first_bad_seq: number | null; head_hash: string }> {
  const res = await fetch('/audit/verify')
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  return res.json()
}

export const exportUrl = (f: AuditFilter) => `/audit/export${qs(f)}`
