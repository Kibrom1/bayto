// Talks to scripts/bayto-control.py through the Vite proxy (/control -> 127.0.0.1:8787).

export interface ControlState {
  services: Record<'postgres' | 'orchestrator' | 'web', string>
  config: {
    streaming: boolean
    summary_model: string
    synthesis_model: string
    hand_raise_model: string
    api_key: string // "set" or ""; the key itself is never returned
  }
  sessions: { task: string; id: string; status: string }[]
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

export async function getLog(name: 'orchestrator' | 'web'): Promise<string> {
  const res = await fetch(`/control/api/log?name=${name}`)
  return res.ok ? res.text() : ''
}
