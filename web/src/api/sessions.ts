import { ApiError, parseError } from './errors'
export interface Task {
  id: string
  title: string
  brief: string | null
  output_type: string
  status: string | null
  last_session_id: string | null
  output_artifact_id: string | null
  local_fallback?: { enabled: boolean; model: string | null } | null
  workspace?: { repo_path: string; base_branch: string; test_command?: string | null; allowed_commands?: string[] } | null
}

export interface BudgetIn {
  tokens?: number
  dollars?: number
}

export interface RosterEntry {
  agent_id: string
  seat_order?: number
  muted?: boolean
  harness?: string
  model?: string
  isolation?: 'shared' | 'own'
}

export interface SessionCreateRequest {
  task_id: string
  mode_id: string
  roster: RosterEntry[]
  budget?: BudgetIn
  synthesis_model?: string // the model that writes the final synthesis; omitted = the app default
}

export interface SessionOut {
  id: string
  task_id: string
  mode_id: string
  status: string
  budget: any | null
  started_at: string | null
  ended_at: string | null
}

export interface SessionAgentOut {
  session_id: string
  agent_id: string
  agent_version: string | null
  seat_order: number | null
  muted: boolean
  harness: string | null
  model: string | null
  isolation: string
  removed_at: string | null
}

export interface SessionCreateResponse {
  session: SessionOut
  session_agents: SessionAgentOut[]
}

export interface UsageOut {
  tokens_in: number
  tokens_out: number
  cost: number
}

export interface TurnCountOut {
  participant: string | null
  agent_id: string
  turns: number
  model?: string | null
  muted?: boolean
}

export interface SessionDetailOut {
  id: string
  task_id: string
  mode_id: string
  status: string
  started_at: string | null
  ended_at: string | null
  round: number
  budget: any | null
  usage: UsageOut
  turn_counts: TurnCountOut[]
  failure_reason?: string | null
  stop_reason?: string | null
  archived_at?: string | null
  stage?: string | null
}

export async function listTasks(): Promise<Task[]> {
  const res = await fetch('/tasks')
  if (!res.ok) {
    throw new ApiError(res.status, `GET /tasks failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function createTask(req: any): Promise<Task> {
  const res = await fetch('/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!res.ok) {
    throw new ApiError(res.status, `POST /tasks failed: ${res.status} ${await parseError(res)}`)
  }
  const data = await res.json()
  return data.task
}

export async function createSession(req: SessionCreateRequest): Promise<SessionCreateResponse> {
  const res = await fetch('/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!res.ok) {
    throw new ApiError(res.status, `POST /sessions failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function startSession(sessionId: string): Promise<{ session: { id: string; status: string } }> {
  const res = await fetch(`/sessions/${sessionId}/start`, {
    method: 'POST',
  })
  if (!res.ok) {
    throw new ApiError(res.status, `POST /sessions/${sessionId}/start failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function continueSession(sessionId: string): Promise<SessionCreateResponse> {
  const res = await fetch(`/sessions/${sessionId}/continue`, { method: 'POST' })
  if (!res.ok) {
    throw new ApiError(res.status, `POST /sessions/${sessionId}/continue failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function getSessionDetail(sessionId: string): Promise<{ session: SessionDetailOut }> {
  const res = await fetch(`/sessions/${sessionId}`)
  if (!res.ok) {
    throw new ApiError(res.status, `GET /sessions/${sessionId} failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function getModes(): Promise<any[]> {
  const res = await fetch('/modes')
  if (!res.ok) {
    throw new ApiError(res.status, `GET /modes failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function getAgentTemplates(): Promise<any[]> {
  const res = await fetch('/agents/templates')
  if (!res.ok) {
    throw new ApiError(res.status, `GET /agents/templates failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function interjectSession(sessionId: string, body: string, to?: string[]) {
  const res = await fetch(`/sessions/${sessionId}/interject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body, to }),
  })
  if (!res.ok) {
    throw new ApiError(res.status, `POST /sessions/${sessionId}/interject failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function stopSession(sessionId: string, synthesize: boolean = true, reason?: string) {
  const res = await fetch(`/sessions/${sessionId}/stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ synthesize, reason }),
  })
  if (!res.ok) {
    throw new ApiError(res.status, `POST /sessions/${sessionId}/stop failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function pauseSession(sessionId: string) {
  const res = await fetch(`/sessions/${sessionId}/pause`, {
    method: 'POST',
  })
  if (!res.ok) {
    throw new ApiError(res.status, `POST /sessions/${sessionId}/pause failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function resumeSession(sessionId: string) {
  const res = await fetch(`/sessions/${sessionId}/resume`, {
    method: 'POST',
  })
  if (!res.ok) {
    throw new ApiError(res.status, `POST /sessions/${sessionId}/resume failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function archiveSession(sessionId: string, archive: boolean = true) {
  const op = archive ? 'archive' : 'unarchive'
  const res = await fetch(`/sessions/${sessionId}/${op}`, { method: 'POST' })
  if (!res.ok) {
    throw new ApiError(res.status, `POST /sessions/${sessionId}/${op} failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function deleteSession(sessionId: string) {
  const res = await fetch(`/sessions/${sessionId}`, { method: 'DELETE' })
  if (!res.ok) {
    throw new ApiError(res.status, `DELETE /sessions/${sessionId} failed: ${res.status} ${await parseError(res)}`)
  }
}

export interface ArtifactOut {
  id: string
  type: string // "synthesis" | "minority_report"
  content_json: Record<string, unknown>
  source_turn_ids: string[]
}

export async function getSessionArtifacts(sessionId: string): Promise<ArtifactOut[]> {
  const res = await fetch(`/sessions/${sessionId}/artifacts`)
  if (!res.ok) {
    throw new ApiError(res.status, `GET /sessions/${sessionId}/artifacts failed: ${res.status} ${await parseError(res)}`)
  }
  const body = await res.json()
  return Array.isArray(body?.artifacts) ? body.artifacts : []
}

/** Downloads the session as a Markdown decision record (GET /sessions/{id}/export?format=md). */
export async function exportSession(sessionId: string): Promise<void> {
  const res = await fetch(`/sessions/${sessionId}/export?format=md`)
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  const name = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') ?? '')?.[1] ?? 'bayto-decision-record.md'
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

async function participantCall(sessionId: string, path: string, body?: unknown): Promise<SessionAgentOut> {
  const res = await fetch(`/sessions/${sessionId}/participants${path}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  return (await res.json()).session_agent
}

export const muteParticipant = (sessionId: string, agentId: string) => participantCall(sessionId, `/${agentId}/mute`)
export const unmuteParticipant = (sessionId: string, agentId: string) => participantCall(sessionId, `/${agentId}/unmute`)
/** Permanent for this session: there is no un-remove. */
export const removeParticipant = (sessionId: string, agentId: string) => participantCall(sessionId, `/${agentId}/remove`)
export const addParticipant = (sessionId: string, agentId: string) => participantCall(sessionId, '', { agent_id: agentId })
