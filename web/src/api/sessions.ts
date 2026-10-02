export interface SessionUsage {
  tokens_in: number
  tokens_out: number
  cost: number
}

export interface SessionBudget {
  max_tokens?: number | null
  max_cost?: number | null
  tokens?: number | null
  dollars?: number | null
}

export interface SessionDetail {
  id: string
  task_id: string
  mode_id: string
  status: string
  started_at: string | null
  ended_at: string | null
  round: number
  budget: SessionBudget | null
  usage: SessionUsage
  turn_counts: Array<{
    participant: string | null
    agent_id: string
    turns: number
  }>
}

export interface SessionDetailResponse {
  session: SessionDetail
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json()
    return typeof body?.detail === 'string' ? body.detail : JSON.stringify(body)
  } catch {
    return res.statusText
  }
}

export async function getSession(sessionId: string): Promise<SessionDetailResponse> {
  const res = await fetch(`/sessions/${sessionId}`)
  if (!res.ok) {
    throw new Error(`GET /sessions/${sessionId} failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}
