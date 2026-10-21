import { ApiError, parseError } from './errors'

export interface AgentDraft {
  name: string
  stance: string
  instructions: string
}

async function send(method: string, path: string, body?: unknown) {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  return res.status === 204 ? null : res.json()
}

export const createAgent = (d: AgentDraft) => send('POST', '/agents', { ...d, preset: 'discussion' })
export const updateAgent = (id: string, d: AgentDraft) => send('PATCH', `/agents/${id}`, d)
export const archiveAgent = (id: string) => send('DELETE', `/agents/${id}`)
