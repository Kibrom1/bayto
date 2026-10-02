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

export interface SessionUsageSummary {
  totalTokens: number
  totalCost: number
  maxTokens: number
  maxCost: number
  percentUsed: number
  costPercent: number
  nearLimit: boolean
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json()
    return typeof body?.detail === 'string' ? body.detail : JSON.stringify(body)
  } catch {
    return res.statusText
  }
}

export function getSessionUsageSummary(session: SessionDetail | null | undefined): SessionUsageSummary {
  const usage = session?.usage ?? { tokens_in: 0, tokens_out: 0, cost: 0 }
  const budget = session?.budget ?? {}

  const tokensIn = Number(usage.tokens_in ?? 0)
  const tokensOut = Number(usage.tokens_out ?? 0)
  const totalTokens = tokensIn + tokensOut

  const maxTokens = Number(budget.max_tokens ?? budget.tokens ?? 0)
  const hasMaxTokens = Number.isFinite(maxTokens) && maxTokens > 0
  const safeMaxTokens = hasMaxTokens ? maxTokens : 0
  const percentUsed = safeMaxTokens > 0 ? Math.min((totalTokens / safeMaxTokens) * 100, 100) : 0

  const totalCost = Number(usage.cost ?? 0)
  const maxCost = Number(budget.max_cost ?? budget.dollars ?? 0)
  const hasMaxCost = Number.isFinite(maxCost) && maxCost > 0
  const safeMaxCost = hasMaxCost ? maxCost : 0
  const costPercent = safeMaxCost > 0 ? Math.min((totalCost / safeMaxCost) * 100, 100) : 0

  return {
    totalTokens,
    totalCost,
    maxTokens: safeMaxTokens,
    maxCost: safeMaxCost,
    percentUsed,
    costPercent,
    nearLimit: percentUsed >= 80 || costPercent >= 80,
  }
}

export async function getSession(sessionId: string): Promise<SessionDetailResponse> {
  const res = await fetch(`/sessions/${sessionId}`)
  if (!res.ok) {
    throw new Error(`GET /sessions/${sessionId} failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}
