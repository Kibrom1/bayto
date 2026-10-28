// Talks to scripts/bayto-control.py through the Vite proxy (/control -> 127.0.0.1:8787).

export interface ControlState {
  services: Record<'postgres' | 'observability' | 'orchestrator' | 'web', string>
  config: {
    streaming: boolean
    llm_backend: 'subscription' | 'api'
    summary_model: string
    synthesis_model: string
    hand_raise_model: string
    api_key: string // "set" or ""; the key itself is never returned
  }
}

export interface SessionRow { task: string; id: string; status: string }

export async function getSessions(): Promise<SessionRow[]> {
  const res = await fetch('/control/api/sessions')
  if (!res.ok) return []
  const rows = await res.json()
  return Array.isArray(rows) ? rows : []
}

/** Which session actions make sense for a status (the orchestrator rejects the rest with 409). */
export function sessionOps(status: string): ('start' | 'pause' | 'resume' | 'stop')[] {
  if (status === 'created') return ['start']
  if (status === 'active') return ['pause', 'stop']
  if (status === 'paused' || status === 'needs_human') return ['resume', 'stop']
  return []
}

export async function getControlState(): Promise<ControlState> {
  const res = await fetch('/control/api/state')
  if (!res.ok) throw new Error(`control panel not reachable (${res.status}). Run: python3 scripts/bayto-control.py`)
  return res.json()
}

export async function controlAction(body: Record<string, unknown>): Promise<string> {
  const res = await fetch('/control/api/action', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-bayto-control': '1' },
    body: JSON.stringify({ ...body, from_web: true }),
  })
  return res.text()
}

export type LogName = 'orchestrator' | 'web' | 'postgres' | 'observability'

export async function getLog(name: LogName): Promise<string> {
  const res = await fetch(`/control/api/log?name=${name}`)
  return res.ok ? res.text() : ''
}

export interface StartAllResult {
  ok: boolean
  steps: { name: string; ok: boolean; detail: string }[]
  failed: string | null
  log: string[]
}

export function parseStartAll(text: string): StartAllResult | null {
  try {
    const r = JSON.parse(text)
    return r && Array.isArray(r.steps) ? r : null
  } catch {
    return null
  }
}
