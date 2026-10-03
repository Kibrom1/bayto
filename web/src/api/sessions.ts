import { UUID } from 'crypto'

export interface Task {
  id: string
  title: string
  brief: string | null
  output_type: string
  status: string | null
  last_session_id: string | null
  output_artifact_id: string | null
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
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json()
    return typeof body?.detail === 'string' ? body.detail : JSON.stringify(body)
  } catch {
    return res.statusText
  }
}

export async function listTasks(): Promise<Task[]> {
  const res = await fetch('/tasks')
  if (!res.ok) {
    throw new Error(`GET /tasks failed: ${res.status} ${await parseError(res)}`)
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
    throw new Error(`POST /tasks failed: ${res.status} ${await parseError(res)}`)
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
    throw new Error(`POST /sessions failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function startSession(sessionId: string): Promise<{ session: { id: string; status: string } }> {
  const res = await fetch(`/sessions/${sessionId}/start`, {
    method: 'POST',
  })
  if (!res.ok) {
    throw new Error(`POST /sessions/${sessionId}/start failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function getSessionDetail(sessionId: string): Promise<{ session: SessionDetailOut }> {
  const res = await fetch(`/sessions/${sessionId}`)
  if (!res.ok) {
    throw new Error(`GET /sessions/${sessionId} failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function getModes(): Promise<any[]> {
  const res = await fetch('/modes')
  if (!res.ok) {
    throw new Error(`GET /modes failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function getAgentTemplates(): Promise<any[]> {
  const res = await fetch('/agents/templates')
  if (!res.ok) {
    throw new Error(`GET /agents/templates failed: ${res.status} ${await parseError(res)}`)
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
    throw new Error(`POST /sessions/${sessionId}/interject failed: ${res.status} ${await parseError(res)}`)
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
    throw new Error(`POST /sessions/${sessionId}/stop failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function pauseSession(sessionId: string) {
  const res = await fetch(`/sessions/${sessionId}/pause`, {
    method: 'POST',
  })
  if (!res.ok) {
    throw new Error(`POST /sessions/${sessionId}/pause failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function resumeSession(sessionId: string) {
  const res = await fetch(`/sessions/${sessionId}/resume`, {
    method: 'POST',
  })
  if (!res.ok) {
    throw new Error(`POST /sessions/${sessionId}/resume failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}
